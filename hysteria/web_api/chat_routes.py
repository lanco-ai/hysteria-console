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
    list_chat_models,
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


def _settings_error_code(error: ChatSettingsError) -> str:
    message = str(error)
    if 'api key' in message:
        return 'api_key_not_configured'
    if 'settings unavailable' in message:
        return 'settings_unavailable'
    return 'settings_incomplete'


def _connection_error_code(error: ChatUpstreamError) -> str:
    if error.status in (401, 403):
        return 'authentication_failed'
    if error.status == 404:
        return 'models_endpoint_unavailable'
    if error.status == 429:
        return 'rate_limited'
    if isinstance(error.status, int) and error.status >= 500:
        return 'upstream_unavailable'
    if error.status is None:
        return 'timeout'
    return 'upstream_error'


def register_chat_routes(app, services, dispatch, *, settings_store=None):
    store = settings_store or ChatSettingsStore()

    def read_public(*, headers, path):
        del headers, path
        return store.public()

    def update_settings(*, headers, path, values):
        del headers, path
        return store.update(**values)

    def complete(*, headers, path, messages, model, reasoning_effort):
        del headers, path
        settings = store.read()
        return forward_chat(settings, messages, model=model, reasoning_effort=reasoning_effort)

    def models(*, headers, path):
        del headers, path
        settings = store.read()
        return list_chat_models(settings)

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
        allowed = {'base_url', 'api_key', 'temperature'}
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

    @app.get('/api/chat/models')
    async def get_chat_models(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(models, request)
        except ChatSettingsError as exc:
            code = _settings_error_code(exc)
            return _json_error(code, status=503 if code == 'settings_unavailable' else 422)
        except ChatUpstreamError as exc:
            content = {'error': _connection_error_code(exc)}
            if isinstance(exc.status, int):
                content['upstream_status'] = exc.status
            return JSONResponse(status_code=502, content=content)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/chat/test')
    async def test_chat_connection(request: Request):
        if not _same_origin(request):
            return _json_error('cross_site_request', status=403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _read_json(request)
            if payload:
                raise ChatSettingsError('invalid request')
        except ChatSettingsError as exc:
            too_large = 'large' in str(exc)
            return _json_error(
                'request_too_large' if too_large else 'bad_request',
                status=413 if too_large else 400,
            )
        try:
            models_result = await dispatch(models, request)
        except ChatSettingsError as exc:
            code = _settings_error_code(exc)
            return JSONResponse(
                status_code=503 if code == 'settings_unavailable' else 422,
                content={'ok': False, 'error': code},
            )
        except ChatUpstreamError as exc:
            return JSONResponse(
                status_code=502,
                content={'ok': False, 'error': _connection_error_code(exc)},
            )
        if isinstance(models_result, JSONResponse):
            return models_result
        return JSONResponse({
            'ok': True,
            'message': 'Connected',
            'models_count': len(models_result),
        })

    @app.post('/api/chat/completions')
    async def post_chat_completion(request: Request):
        if not _same_origin(request):
            return _json_error('cross_site_request', status=403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _read_json(request)
            if set(payload) - {'messages', 'model', 'reasoning_effort'} or 'model' not in payload:
                raise ChatSettingsError('invalid request')
            messages = validate_messages(payload['messages'])
            model = payload['model']
            if not isinstance(model, str) or not model.strip() or len(model.strip()) > 256:
                raise ChatSettingsError('model is invalid')
            reasoning_effort = payload.get('reasoning_effort', 'auto')
            if not isinstance(reasoning_effort, str) or reasoning_effort not in ('auto', 'low', 'medium', 'high'):
                raise ChatSettingsError('reasoning_effort is invalid')
        except ChatSettingsError as exc:
            too_large = 'large' in str(exc)
            return _json_error(
                'request_too_large' if too_large else 'validation_error',
                status=413 if too_large else 422,
            )
        try:
            result = await dispatch(partial(complete, messages=messages, model=model.strip(), reasoning_effort=reasoning_effort), request)
        except ChatSettingsError as exc:
            code = _settings_error_code(exc)
            return _json_error(code, status=503 if code == 'settings_unavailable' else 422)
        except ChatUpstreamError as exc:
            content = {'error': _connection_error_code(exc)}
            if isinstance(exc.status, int):
                content['upstream_status'] = exc.status
            return JSONResponse(status_code=502, content=content)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)
