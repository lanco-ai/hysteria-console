"""Administrator-only API boundary for the video workflow."""

import json
from functools import partial
from types import SimpleNamespace

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse

from .services import LoginRequired, StateUnavailable, UserAccessDenied
from .video_provider import GrokVideoProvider, ProviderError
from .video_service import VideoSettingsError, VideoSettingsStore


def _error(code: str, status: int = 400):
    return JSONResponse(status_code=status, content={'error': code})


async def _require_admin(request, services, dispatch):
    try:
        payload = await dispatch(services.read_session, request)
    except (LoginRequired, UserAccessDenied):
        return _error('login_required', 401)
    except StateUnavailable:
        return _error('state_unavailable', 503)
    if isinstance(payload, JSONResponse):
        return payload
    if not isinstance(payload, dict) or payload.get('role') != 'admin':
        return _error('login_required', 401)
    return None


def _same_origin(request: Request) -> bool:
    return http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers))


def _provider_error(exc: ProviderError):
    status = 502
    code = exc.code
    if code == 'authentication_failed':
        status = 502
    return _error(code, status)


def register_video_routes(app, services, dispatch, *, settings_store=None, provider_factory=None):
    store = settings_store or VideoSettingsStore()
    factory = provider_factory or (lambda: GrokVideoProvider())

    def read_settings(*, headers, path):
        del headers, path
        return store.public()

    def update_settings(*, headers, path, values):
        del headers, path
        return store.update(**values)

    def capabilities(*, headers, path):
        del headers, path
        settings = store.read()
        if not settings.base_url or not settings.api_key:
            raise VideoSettingsError('settings incomplete')
        caps = factory().capabilities(settings)
        return {
            'image_models': list(caps.image_models),
            'video_models': list(caps.video_models),
            'first_last_frame': {
                'supported': bool(caps.first_last_frame.supported),
                'reason': getattr(caps.first_last_frame, 'reason', None),
            },
            'video_composition': {
                'supported': bool(caps.video_composition.supported),
                'reason': getattr(caps.video_composition, 'reason', None),
            },
        }

    @app.get('/api/video/settings')
    async def get_settings(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(read_settings, request)
        except VideoSettingsError:
            return _error('settings_unavailable', 503)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

    @app.put('/api/video/settings')
    async def put_settings(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = json.loads((await request.body()).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request')
        if not isinstance(payload, dict) or set(payload) - {'base_url', 'api_key', 'provider'}:
            return _error('bad_request')
        try:
            result = await dispatch(partial(update_settings, values=payload), request)
        except VideoSettingsError as exc:
            return _error('validation_error' if 'unavailable' not in str(exc) else 'settings_unavailable', 422)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

    @app.get('/api/video/capabilities')
    async def get_capabilities(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(capabilities, request)
        except VideoSettingsError:
            return _error('settings_incomplete', 422)
        except ProviderError as exc:
            return _provider_error(exc)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

    @app.post('/api/video/connection/test')
    async def test_connection(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(capabilities, request)
        except VideoSettingsError:
            return _error('settings_incomplete', 422)
        except ProviderError as exc:
            return _provider_error(exc)
        if isinstance(result, JSONResponse):
            return result
        models = list(dict.fromkeys(result['image_models'] + result['video_models']))
        return JSONResponse({'ok': True, 'models_count': len(models)})
