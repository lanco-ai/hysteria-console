"""Authenticated JSON routes for the self-use OpenAI-compatible chat page."""

import json
from functools import partial
from types import SimpleNamespace

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse

from .chat_service import (
    MAX_BODY_BYTES,
    ChatSettingsError,
    ChatSettingsStore,
    ChatUpstreamError,
    forward_chat,
    validate_messages,
)
from .services import LoginRequired, StateUnavailable, UserAccessDenied


def _json_error(code: str, *, status: int = 400):
    return JSONResponse(status_code=status, content={'error': code})


async def _read_json(request: Request):
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        raise ChatSettingsError('JSON content type required')
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ChatSettingsError('request is too large')
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ChatSettingsError('invalid JSON') from None
    if not isinstance(payload, dict):
        raise ChatSettingsError('JSON object required')
    return payload


async def _require_admin(request: Request, services, dispatch):
    try:
        payload = await dispatch(services.read_session, request)
    except (LoginRequired, UserAccessDenied):
        return _json_error('login_required', status=401)
    except StateUnavailable:
        return _json_error('state_unavailable', status=503)
    if isinstance(payload, JSONResponse):
        return payload
    if not isinstance(payload, dict) or payload.get('role') != 'admin':
        return _json_error('login_required', status=401)
    return None


def _same_origin(request: Request) -> bool:
    return http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers))


def register_chat_routes(app, services, dispatch, *, settings_store=None):
    store = settings_store or ChatSettingsStore()

    def read_public(*, headers, path):
        del headers, path
        return store.public()

    def update_settings(*, headers, path, values):
        del headers, path
        return store.update(**values)

    def complete(*, headers, path, messages):
        del headers, path
        settings = store.read()
        return forward_chat(settings, messages)

    @app.get('/api/chat/settings')
    async def get_chat_settings(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(read_public, request)
        except ChatSettingsError:
            return _json_error('settings_unavailable', status=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.put('/api/chat/settings')
    async def put_chat_settings(request: Request):
        if not _same_origin(request):
            return _json_error('cross_site_request', status=403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _read_json(request)
        except ChatSettingsError as exc:
            too_large = 'large' in str(exc)
            return _json_error(
                'request_too_large' if too_large else 'bad_request',
                status=413 if too_large else 400,
            )
        allowed = {'base_url', 'api_key', 'model', 'temperature'}
        if any(key not in allowed for key in payload):
            return _json_error('bad_request')
        values = {key: payload[key] for key in allowed if key in payload}
        try:
            result = await dispatch(partial(update_settings, values=values), request)
        except ChatSettingsError:
            return _json_error('validation_error', status=422)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/chat/completions')
    async def post_chat_completion(request: Request):
        if not _same_origin(request):
            return _json_error('cross_site_request', status=403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _read_json(request)
            if set(payload) != {'messages'}:
                raise ChatSettingsError('invalid request')
            messages = validate_messages(payload['messages'])
        except ChatSettingsError as exc:
            too_large = 'large' in str(exc)
            return _json_error(
                'request_too_large' if too_large else 'validation_error',
                status=413 if too_large else 422,
            )
        try:
            result = await dispatch(partial(complete, messages=messages), request)
        except ChatSettingsError as exc:
            if 'settings unavailable' in str(exc):
                return _json_error('settings_unavailable', status=503)
            code = 'api_key_not_configured' if 'api key' in str(exc) else 'settings_incomplete'
            return _json_error(code, status=422)
        except ChatUpstreamError as exc:
            content = {'error': 'upstream_error'}
            if isinstance(exc.status, int):
                content['upstream_status'] = exc.status
            return JSONResponse(status_code=502, content=content)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)
