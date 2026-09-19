"""Administrator-only API boundary for the video workflow."""

import json
from functools import partial
from types import SimpleNamespace
from urllib.parse import quote

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .services import LoginRequired, StateUnavailable, UserAccessDenied
from .video_provider import GrokVideoProvider, ProviderError
from .video_service import AssetStore, RunService, VideoSettingsError, VideoSettingsStore, VideoValidationError, WorkflowStore


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


def register_video_routes(app, services, dispatch, *, settings_store=None, provider_factory=None, workflow_store=None, asset_store=None, run_service=None):
    store = settings_store or VideoSettingsStore()
    workflows = workflow_store or WorkflowStore()
    assets = asset_store or AssetStore()

    def get_run_service():
        if run_service is not None:
            return run_service
        return RunService(workflows, store.read(), factory(), asset_store=assets)
    factory = provider_factory or (lambda: GrokVideoProvider())

    def present_run(run):
        if not isinstance(run, dict):
            return run
        presented = dict(run)
        public_assets = {}
        for node_id, value in (run.get('assets') or {}).items():
            if not isinstance(value, str):
                continue
            if value.startswith('asset://'):
                asset_id = value[len('asset://'):]
                public_assets[node_id] = f'/api/video/assets/{quote(asset_id, safe="")}/content'
            else:
                public_assets[node_id] = (
                    f'/api/video/runs/{quote(str(run.get("id") or ""), safe="")}'
                    f'/assets/{quote(str(node_id), safe="")}/content'
                )
        presented['assets'] = public_assets
        return presented

    def open_provider_media(*, headers, path, asset_url, range_header):
        del headers, path
        settings = store.read()
        return factory().open_asset(asset_url, settings, range_header=range_header)

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

    @app.get('/api/video/workflows')
    async def list_workflows(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            result = await dispatch(lambda **_kwargs: workflows.list(), request)
        except VideoValidationError:
            return _error('storage_unavailable', 503)
        return result if isinstance(result, JSONResponse) else JSONResponse({'workflows': result})

    @app.post('/api/video/workflows')
    async def save_workflow(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = json.loads((await request.body()).decode('utf-8'))
            result = await dispatch(lambda **_kwargs: workflows.save(payload), request)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request')
        except VideoValidationError as exc:
            return _error('invalid_workflow', 422)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

    @app.get('/api/video/workflows/{workflow_id}')
    async def get_workflow(request: Request, workflow_id: str):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: workflows.get(workflow_id), request)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result or {'error': 'not_found'}, status_code=200 if result else 404)

    @app.delete('/api/video/workflows/{workflow_id}')
    async def delete_workflow(request: Request, workflow_id: str):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: workflows.delete(workflow_id), request)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse({'deleted': bool(result)})

    @app.post('/api/video/assets')
    async def upload_asset(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        filename = request.headers.get('x-file-name', '')
        content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        body = await request.body()
        try:
            result = await dispatch(lambda **_kwargs: assets.save_upload(filename, content_type, body), request)
        except VideoValidationError:
            return _error('invalid_asset', 422)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

    @app.get('/api/video/assets/{asset_id}/content')
    async def asset_content(request: Request, asset_id: str):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: assets.open(asset_id), request)
        if isinstance(result, JSONResponse):
            return result
        metadata, path = result
        if metadata is None or path is None:
            return _error('not_found', 404)
        return FileResponse(path, media_type=metadata['content_type'], filename=metadata['filename'])

    @app.post('/api/video/runs')
    async def create_run(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = json.loads((await request.body()).decode('utf-8'))
            workflow_id = payload.get('workflow_id') if isinstance(payload, dict) else None
            shot_id = payload.get('shot_id') if isinstance(payload, dict) else None
            result = await dispatch(lambda **_kwargs: get_run_service().submit(workflow_id, shot_id=shot_id), request)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request')
        except VideoValidationError:
            return _error('invalid_workflow', 422)
        return result if isinstance(result, JSONResponse) else JSONResponse(result, status_code=202)

    @app.get('/api/video/runs')
    async def list_runs(request: Request):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: get_run_service().list(), request)
        return result if isinstance(result, JSONResponse) else JSONResponse({'runs': [present_run(run) for run in result]})

    @app.get('/api/video/runs/{run_id}/assets/{node_id}/content')
    async def run_asset_content(request: Request, run_id: str, node_id: str):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        run = await dispatch(lambda **_kwargs: get_run_service().get(run_id), request)
        if isinstance(run, JSONResponse):
            return run
        source_url = run.get('assets', {}).get(node_id) if isinstance(run, dict) else None
        if not isinstance(source_url, str) or not source_url.startswith(('http://', 'https://')):
            return _error('not_found', 404)
        try:
            media = await dispatch(
                partial(
                    open_provider_media,
                    asset_url=source_url,
                    range_header=request.headers.get('range'),
                ),
                request,
            )
        except VideoSettingsError:
            return _error('settings_unavailable', 503)
        except ProviderError as exc:
            return _provider_error(exc)
        if isinstance(media, JSONResponse):
            return media
        headers = {
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'private, no-store',
            'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'inline',
        }
        if media.content_length is not None:
            headers['Content-Length'] = media.content_length
        if media.content_range is not None:
            headers['Content-Range'] = media.content_range
        return StreamingResponse(
            media.iter_bytes(),
            status_code=media.status_code,
            media_type=media.content_type,
            headers=headers,
            background=BackgroundTask(media.close),
        )

    @app.get('/api/video/runs/{run_id}')
    async def get_run(request: Request, run_id: str):
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: get_run_service().tick(run_id), request)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(present_run(result))

    @app.post('/api/video/runs/{run_id}/cancel')
    async def cancel_run(request: Request, run_id: str):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: get_run_service().cancel(run_id), request)
        return result if isinstance(result, JSONResponse) else JSONResponse(present_run(result))
