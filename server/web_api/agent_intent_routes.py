"""Loopback-only intent completion for the network service's LancoAgent.

``POST /internal/v1/agent-intent`` is the only cross-service call this service
answers.  nginx never routes ``/internal/`` here, the caller must connect
directly on loopback, and it must present the shared internal token.
"""

import hmac
import json
from functools import partial
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from .agent_intent import AgentIntentError, complete_agent_intent
from .chat_service import ChatSettingsStore

INTERNAL_TOKEN_FILE = Path('/etc/lancoai/internal-token')
INTERNAL_TOKEN_HEADER = 'x-lancoai-internal-token'
MIN_TOKEN_LENGTH = 32
# The network service's planner accepts messages up to 16 KiB and sends each rule
# list clipped to the 2800 characters the completion reads.
MAX_REQUEST_BYTES = 128 * 1024
MAX_MESSAGE_CHARS = 16 * 1024
MAX_TARGET_CHARS = 128
MAX_CONTEXT_ITEMS = 1000
MAX_CONTEXT_ITEM_CHARS = 4000
_LOOPBACK = frozenset({'127.0.0.1', '::1'})
_PROXY_HEADERS = ('x-forwarded-for', 'x-real-ip', 'forwarded')
_STATUS = {
    'settings_unavailable': 503,
    'upstream_unavailable': 502,
    'invalid_model_response': 502,
    'unsupported_action': 422,
    'invalid_rule_pack': 422,
    'invalid_rule': 422,
}


def read_internal_token(path=INTERNAL_TOKEN_FILE):
    """Return the shared token, or None when it is missing or too short to trust."""
    try:
        token = Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def _error(code, status):
    return JSONResponse(status_code=status, content={'ok': False, 'error': code})


def _strings(value, *, limit, item_chars):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('context')
    if not all(isinstance(item, str) and len(item) <= item_chars for item in value):
        raise ValueError('context')
    return value


def _parse(body):
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError('shape')
    message = payload.get('message')
    target_user = payload.get('target_user')
    context = payload.get('context')
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
        raise ValueError('message')
    if not isinstance(target_user, str) or not target_user or len(target_user) > MAX_TARGET_CHARS:
        raise ValueError('target_user')
    if not isinstance(context, dict):
        raise ValueError('context')
    limits = {'limit': MAX_CONTEXT_ITEMS, 'item_chars': MAX_CONTEXT_ITEM_CHARS}
    clean = {
        key: _strings(context.get(key, []), **limits) for key in ('packs', 'rules', 'global_rules')
    }
    return message, target_user, clean


async def _read_body(request):
    try:
        claimed = int(request.headers.get('content-length', ''))
    except ValueError:
        return None
    if claimed < 0 or claimed > MAX_REQUEST_BYTES:
        return None
    body = await request.body()
    return body if len(body) == claimed else None


def register_agent_intent_routes(
    app,
    dispatch,
    *,
    settings_store=None,
    completer=complete_agent_intent,
    token_file=INTERNAL_TOKEN_FILE,
):
    settings = settings_store if settings_store is not None else ChatSettingsStore()

    @app.post('/internal/v1/agent-intent', include_in_schema=False)
    async def agent_intent(request: Request):
        client = request.scope.get('client') or ('', 0)
        if client[0] not in _LOOPBACK or any(name in request.headers for name in _PROXY_HEADERS):
            # Present only to direct loopback callers; proxied traffic sees nothing.
            return JSONResponse(status_code=404, content={'error': 'not_found'})
        expected = read_internal_token(token_file)
        if expected is None:
            return _error('internal_token_unavailable', 503)
        provided = request.headers.get(INTERNAL_TOKEN_HEADER, '')
        if not hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8')):
            return _error('forbidden', 403)
        body = await _read_body(request)
        if body is None:
            return _error('bad_request', 400)
        try:
            message, target_user, context = _parse(body)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request', 400)

        def run(*, headers, path):
            del headers, path
            return completer(settings, message=message, target_user=target_user, context=context)

        try:
            result = await dispatch(partial(run), request)
        except AgentIntentError as exc:
            return _error(exc.code, _STATUS.get(exc.code, 422))
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse({'ok': True, 'intent': result})
