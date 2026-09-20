"""Administrator-only API boundary for the video workflow."""

import asyncio
import json
import threading
from functools import partial
from types import SimpleNamespace
from urllib.parse import quote

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .ai.assistant_generation import generate_assistant_json
from .ai.assistant_schemas import video_assistant_schema
from .ai.gemini import GeminiAdapter, GeminiUpstreamError
from .ai.service_store import AIServiceError, AIServiceStore
from .chat_service import ChatUpstreamError
from .services import LoginRequired, StateUnavailable, UserAccessDenied
from .video_provider import GrokVideoProvider, ProviderError
from .video_service import AssetStore, RunService, VideoSettingsError, VideoSettingsStore, VideoStorageFullError, VideoValidationError, WorkflowStore


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


class VideoAssistantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    idea: str = Field(min_length=1, max_length=6000)
    style_prompt: str = Field(default='', max_length=1200)
    aspect_ratio: str = Field(pattern=r'^(9:16|16:9|1:1)$')
    shot_count: int = Field(ge=1, le=12)
    shot_duration: int = Field(ge=1, le=30)


class VideoAssistantShot(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    script: str = Field(default='', max_length=1200)
    shot_type: str = Field(pattern=r'^(特写|近景|中景|全景)$')
    character: str = Field(default='', max_length=120)
    scene: str = Field(default='', max_length=500)
    duration: int = Field(ge=1, le=30)
    image_prompt: str = Field(min_length=1, max_length=3000)
    motion_prompt: str = Field(min_length=1, max_length=3000)
    dialogue: str = Field(default='', max_length=2000)


class VideoAssistantResult(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    rewritten_text: str = Field(min_length=1, max_length=6000)
    style_prompt: str = Field(default='', max_length=1200)
    aspect_ratio: str = Field(pattern=r'^(9:16|16:9|1:1)$')
    shots: list[VideoAssistantShot] = Field(min_length=1, max_length=12)


def _provider_error(exc: ProviderError):
    if exc.code == 'range_not_satisfiable':
        headers = {'Content-Range': exc.content_range} if exc.content_range else {}
        return Response(status_code=416, headers=headers)
    status = 502
    code = exc.code
    if code == 'authentication_failed':
        status = 502
    return _error(code, status)


def register_video_routes(
    app,
    services,
    dispatch,
    *,
    settings_store=None,
    provider_factory=None,
    workflow_store=None,
    asset_store=None,
    run_service=None,
    scheduler_enabled=False,
    scheduler_interval=5.0,
    ai_services_store: AIServiceStore | None = None,
    gemini_adapter: GeminiAdapter | None = None,
):
    store = settings_store or VideoSettingsStore()
    workflows = workflow_store or WorkflowStore()
    assets = asset_store or AssetStore()
    ai_store = ai_services_store
    gemini = gemini_adapter or GeminiAdapter()
    run_tick_lock = threading.Lock()

    def get_run_service():
        if run_service is not None:
            return run_service
        return RunService(workflows, store.read(), factory(), asset_store=assets)

    def tick_run(run_id):
        # The scheduler and an open browser can observe the same pending run.
        # Serialize ticks so neither can issue the same paid submission twice.
        with run_tick_lock:
            return get_run_service().tick(run_id)

    def read_run(run_id):
        if scheduler_enabled:
            return get_run_service().get(run_id)
        return tick_run(run_id)

    def perform_cancel(run_id):
        with run_tick_lock:
            runner = get_run_service()
            if runner.get(run_id) is None:
                return None
            return runner.cancel(run_id)

    def tick_pending_runs():
        runner = get_run_service()
        settings = getattr(runner, 'settings', None)
        if settings is not None and (not settings.base_url or not settings.api_key):
            return
        for run in runner.resume_pending():
            run_id = run.get('id') if isinstance(run, dict) else None
            if not isinstance(run_id, str) or not run_id:
                continue
            try:
                tick_run(run_id)
            except Exception:
                # A transient storage/provider error must not kill the loop;
                # the persisted record will be reconsidered on the next tick.
                continue

    if scheduler_enabled:
        async def scheduler_loop():
            while True:
                try:
                    await asyncio.to_thread(tick_pending_runs)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep the service alive; no payload or provider details are
                    # logged by the background worker.
                    pass
                await asyncio.sleep(scheduler_interval)

        async def start_scheduler():
            app.state.video_scheduler_task = asyncio.create_task(
                scheduler_loop(), name='video-run-scheduler'
            )

        async def stop_scheduler():
            task = getattr(app.state, 'video_scheduler_task', None)
            if task is None:
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        app.router.add_event_handler('startup', start_scheduler)
        app.router.add_event_handler('shutdown', stop_scheduler)
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

    def draft_video_storyboard(*, headers, path, values):
        del headers, path
        if ai_store is None:
            raise AIServiceError('service_not_configured')
        selection = ai_store.bound_assistant('video_assistant')
        profile = selection['profile']
        if profile['protocol'] not in {'gemini_native', 'openai_compatible'} or not profile['api_key']:
            raise AIServiceError('service_not_configured')
        model = selection['model_id']
        if not model:
            raise AIServiceError('model_not_selected')
        if model not in {item['id'] for item in profile['models']}:
            raise AIServiceError('model_not_available')
        prompt = (
            '你是短视频/漫剧分镜编剧。根据用户创意生成分镜草稿，数量必须与 shot_count 一致。'
            '图片提示词只描述单帧视觉；运动提示词描述镜头与动作；故事必须连续且角色、场景一致。'
            '只返回 JSON，不生成图片/视频，不调用素材服务，也不声称任务已经开始。'
            'shot_type 只能是特写、近景、中景、全景；画幅使用请求给定的值。'
            '所有图像和运动提示词必须是可直接编辑后提交给媒体模型的具体描述。\n\n'
            + json.dumps({
                'idea': values.idea, 'style_prompt': values.style_prompt,
                'aspect_ratio': values.aspect_ratio, 'shot_count': values.shot_count,
                'shot_duration_seconds': values.shot_duration,
            }, ensure_ascii=False, separators=(',', ':'))
        )
        schema = video_assistant_schema()
        result, output_mode = generate_assistant_json(
            profile, model, prompt, schema, gemini_adapter=gemini,
        )
        try:
            validated = VideoAssistantResult.model_validate(result)
            if (
                len(validated.shots) != values.shot_count
                or validated.aspect_ratio != values.aspect_ratio
                or any(shot.duration != values.shot_duration for shot in validated.shots)
            ):
                raise ValueError('response does not match requested storyboard shape')
        except (ValidationError, ValueError):
            raise GeminiUpstreamError('invalid_model_response', 200) from None
        return {
            'model': model, 'service_name': profile['name'],
            'structured_output': output_mode,
            **validated.model_dump(),
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

    @app.post('/api/video/assistant/draft')
    async def draft_video_assistant(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return _error('json_required', 400)
        try:
            body = await request.body()
            if len(body) > 16 * 1024:
                return _error('request_too_large', 413)
            payload = VideoAssistantRequest.model_validate(json.loads(body.decode('utf-8')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return _error('invalid_request', 400)
        try:
            result = await dispatch(partial(draft_video_storyboard, values=payload), request)
        except AIServiceError as exc:
            if 'not_configured' in str(exc):
                return _error('service_not_configured', 422)
            if 'model_not_selected' in str(exc):
                return _error('model_not_selected', 422)
            if 'model_not_available' in str(exc):
                return _error('model_not_available', 422)
            return _error('ai_service_unavailable', 503)
        except ChatUpstreamError as exc:
            if exc.code == 'invalid_model_response':
                return _error(exc.code, 502)
            if exc.status == 401:
                code = 'authentication_failed'
            elif exc.status == 403:
                code = 'permission_denied'
            elif exc.status == 404:
                code = 'model_not_available'
            elif exc.status == 429:
                code = 'rate_limited'
            elif exc.status is None:
                code = 'timeout'
            elif exc.status >= 500:
                code = 'upstream_unavailable'
            else:
                code = 'upstream_error'
            return _error(code, 504 if code == 'timeout' else 502)
        except GeminiUpstreamError as exc:
            if exc.code == 'service_not_configured':
                return _error(exc.code, 422)
            return _error(exc.code, 504 if exc.code == 'timeout' else 502)
        except (OSError, RuntimeError):
            return _error('ai_service_unavailable', 503)
        return result if isinstance(result, JSONResponse) else JSONResponse(result)

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
        max_bytes = int(getattr(assets, 'max_bytes', 20 * 1024 * 1024))
        content_length = request.headers.get('content-length')
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return _error('bad_request', 400)
            if declared_length < 0:
                return _error('bad_request', 400)
            if declared_length > max_bytes:
                return _error('asset_too_large', 413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > max_bytes:
                return _error('asset_too_large', 413)
            body.extend(chunk)
        try:
            result = await dispatch(
                lambda **_kwargs: assets.save_upload(filename, content_type, bytes(body)),
                request,
            )
        except VideoStorageFullError:
            return _error('asset_storage_full', 507)
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
        result = await dispatch(lambda **_kwargs: read_run(run_id), request)
        if isinstance(result, JSONResponse):
            return result
        if result is None:
            return _error('not_found', 404)
        return JSONResponse(present_run(result))

    @app.post('/api/video/runs/{run_id}/cancel')
    async def cancel_run(request: Request, run_id: str):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        result = await dispatch(lambda **_kwargs: perform_cancel(run_id), request)
        if result is None:
            return _error('not_found', 404)
        return result if isinstance(result, JSONResponse) else JSONResponse(present_run(result))
