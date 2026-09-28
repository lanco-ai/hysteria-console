"""Admin-only API for shared AI service profiles and verified model metadata."""

from datetime import datetime, timezone
import json
from functools import partial
from types import SimpleNamespace

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..chat_service import ChatSettings, ChatUpstreamError, list_chat_models
from .assistant_generation import generate_assistant_json, generate_assistant_text
from .assistant_schemas import (
    PLAN_ASSISTANT_TEST_PROMPT, VIDEO_ASSISTANT_TEST_PROMPT,
    plan_assistant_schema, video_assistant_schema,
)
from ..video_models import VideoSettings
from ..video_provider import GrokVideoProvider, ProviderError
from .gemini import GeminiAdapter, GeminiUpstreamError
from .service_store import AIServiceError, AIServiceStore, FEATURE_PROTOCOLS


MAX_BODY_BYTES = 32 * 1024


def _error(code: str, status: int = 400):
    return JSONResponse(status_code=status, content={'error': code})


def _connection_error(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, GeminiUpstreamError):
        status = 504 if exc.code == 'timeout' else 502
        return exc.code, status
    if isinstance(exc, ChatUpstreamError):
        if exc.status == 401:
            return 'authentication_failed', 502
        if exc.status == 403:
            return 'permission_denied', 502
        if exc.status == 404:
            return 'models_endpoint_unavailable', 502
        if exc.status == 429:
            return 'rate_limited', 502
        if exc.status is None:
            return 'timeout', 504
        return 'upstream_unavailable', 502
    if isinstance(exc, ProviderError):
        return (exc.code if exc.code in {
            'authentication_failed', 'rate_limited', 'timeout', 'upstream_unavailable',
            'models_endpoint_unavailable',
        } else 'upstream_unavailable'), 502
    return 'upstream_unavailable', 502


def register_ai_service_routes(
    app,
    services,
    dispatch,
    *,
    store: AIServiceStore | None = None,
    gemini_adapter: GeminiAdapter | None = None,
    openai_models_fetcher=None,
    media_provider_factory=None,
):
    registry = store or AIServiceStore()
    gemini = gemini_adapter or GeminiAdapter()
    fetch_openai_models = openai_models_fetcher or list_chat_models
    make_media_provider = media_provider_factory or (lambda: GrokVideoProvider())

    async def require_admin(request: Request):
        try:
            session = await dispatch(services.read_session, request)
        except Exception as exc:
            from ..services import LoginRequired, StateUnavailable, UserAccessDenied
            if isinstance(exc, (LoginRequired, UserAccessDenied)):
                return _error('login_required' if isinstance(exc, LoginRequired) else 'admin_required', 401 if isinstance(exc, LoginRequired) else 403)
            if isinstance(exc, StateUnavailable):
                return _error('state_unavailable', 503)
            raise
        if isinstance(session, JSONResponse):
            return session
        if not isinstance(session, dict) or session.get('role') != 'admin':
            return _error('admin_required', 403)
        return None

    def parse_body(raw: bytes):
        if len(raw) > MAX_BODY_BYTES:
            raise OverflowError
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError
        return payload

    def read_public(*, headers, path):
        del headers, path
        return registry.public()

    def update_profile(*, headers, path, service_id, values):
        del headers, path
        return registry.update_profile(service_id, **values)

    def update_binding(*, headers, path, values):
        del headers, path
        return registry.set_binding(
            values['feature'], values['profile_id'], revision=values['revision'],
            **({'model_id': values['model_id']} if 'model_id' in values else {}),
        )

    def read_models(*, headers, path, service_id):
        del headers, path
        profile = registry.profile(service_id)
        return {'models': profile['models'], 'last_verified_at': profile['last_verified_at']}

    def test_profile(*, headers, path, service_id):
        del headers, path
        profile = registry.profile(service_id)
        snapshot = registry.public()
        if not profile['api_key']:
            raise AIServiceError('service_not_configured')
        capabilities: list[str] = []
        if profile['protocol'] == 'gemini_native':
            models = gemini.list_models(profile)
            capabilities = ['chat']
        elif profile['protocol'] == 'openai_compatible':
            if not profile['base_url']:
                raise AIServiceError('service_not_configured')
            settings = ChatSettings(
                base_url=profile['base_url'],
                api_key=profile['api_key'],
                temperature=profile['temperature'],
            )
            models = fetch_openai_models(settings)
            capabilities = ['chat']
        elif profile['protocol'] == 'grok_media':
            if not profile['base_url']:
                raise AIServiceError('service_not_configured')
            caps = make_media_provider().capabilities(VideoSettings(
                base_url=profile['base_url'], api_key=profile['api_key'], provider=profile['provider'],
            ))
            ids = list(dict.fromkeys(caps.image_models + caps.video_models))
            models = [{'id': model_id, 'name': model_id} for model_id in ids]
            if caps.image_models:
                capabilities.append('image_generation')
            if caps.video_models:
                capabilities.append('video_generation')
            if caps.first_last_frame.supported:
                capabilities.append('first_last_frame')
            if caps.video_composition.supported:
                capabilities.append('video_composition')
        else:
            raise AIServiceError('unsupported_service_protocol')

        checked_at = datetime.now(timezone.utc).isoformat()
        updated = registry.update_catalog(
            service_id, models, capabilities=capabilities,
            checked_at=checked_at, revision=snapshot['revision'],
        )
        profile_public = next(item for item in updated['profiles'] if item['id'] == service_id)
        return {
            'ok': True,
            'level': 'A',
            'test_level': 'A',
            'service_id': service_id,
            'models_count': len(profile_public['models']),
            'models': profile_public['models'],
            'verified_capabilities': profile_public['verified_capabilities'],
            'last_verified_at': profile_public['last_verified_at'],
            'revision': updated['revision'],
            'tested_at': checked_at,
        }

    def assistant_test(*, headers, path, service_id: str, feature: str, model_id: str, structured: bool):
        del headers, path
        if feature not in {'plan_assistant', 'video_assistant'}:
            raise AIServiceError('invalid assistant feature')
        snapshot = registry.profile_snapshot(service_id)
        profile = snapshot['profile']
        if profile['protocol'] not in FEATURE_PROTOCOLS[feature]:
            raise AIServiceError('incompatible AI service')
        if not profile['api_key'] or not profile['base_url']:
            raise AIServiceError('service_not_configured')
        if not isinstance(model_id, str) or not model_id:
            raise AIServiceError('model_not_selected')
        if model_id not in {item['id'] for item in profile['models']}:
            raise AIServiceError('model_not_available')

        if structured:
            if feature == 'plan_assistant':
                schema = plan_assistant_schema()
                prompt = PLAN_ASSISTANT_TEST_PROMPT
            else:
                schema = video_assistant_schema()
                prompt = VIDEO_ASSISTANT_TEST_PROMPT
            result, output_mode = generate_assistant_json(
                profile, model_id, prompt, schema, gemini_adapter=gemini,
            )
            try:
                if feature == 'plan_assistant':
                    from ..plans_routes import PlanAssistantResult
                    validated = PlanAssistantResult.model_validate(result)
                    if any(item.reminder_offset_minutes and not item.start_time for item in validated.suggestions):
                        raise ValueError
                else:
                    from ..video_routes import VideoAssistantResult
                    validated = VideoAssistantResult.model_validate(result)
                    if (
                        len(validated.shots) != 1
                        or validated.aspect_ratio != '16:9'
                        or any(shot.duration != 5 for shot in validated.shots)
                    ):
                        raise ValueError
            except (ValidationError, ValueError):
                raise AIServiceError('structured_result_invalid') from None
            level = 'C'
        else:
            prompt = 'Reply with the exact words: capability test passed.'
            generate_assistant_text(profile, model_id, prompt, gemini_adapter=gemini)
            output_mode = 'text'
            level = 'B'
        return {
            'ok': True,
            'level': level,
            'feature': feature,
            'service_id': service_id,
            'model_id': model_id,
            'revision': snapshot['revision'],
            'tested_at': datetime.now(timezone.utc).isoformat(),
            **({'structured_output': output_mode} if structured else {}),
        }

    def _run_assistant_test(request, service_id: str, *, structured: bool):
        async def handle():
            if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
                return _error('cross_site_request', 403)
            denied = await require_admin(request)
            if denied is not None:
                return denied
            if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                return _error('json_required', 400)
            try:
                payload = parse_body(await request.body())
            except OverflowError:
                return _error('request_too_large', 413)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return _error('invalid_request', 400)
            if set(payload) != {'feature', 'model_id'} or any(not isinstance(payload[key], str) for key in payload):
                return _error('invalid_request', 400)
            try:
                result = await dispatch(partial(
                    assistant_test, service_id=service_id, feature=payload['feature'],
                    model_id=payload['model_id'], structured=structured,
                ), request)
            except AIServiceError as exc:
                message = str(exc)
                if 'not_configured' in message:
                    return _error('service_not_configured', 422)
                if 'model_not_selected' in message:
                    return _error('model_not_selected', 422)
                if 'model_not_available' in message:
                    return _error('model_not_available', 422)
                if 'structured_result_invalid' in message:
                    return _error('structured_result_invalid', 502)
                if 'incompatible' in message or 'assistant feature' in message:
                    return _error('invalid_feature_binding', 422)
                return _error('ai_services_unavailable', 503)
            except (GeminiUpstreamError, ChatUpstreamError, ProviderError) as exc:
                code, status = _connection_error(exc)
                return JSONResponse({'error': code}, status_code=status)
            if isinstance(result, JSONResponse):
                return result
            return JSONResponse(result)
        return handle()

    @app.get('/api/ai/services')
    async def get_ai_services(request: Request):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            result = await dispatch(read_public, request)
        except AIServiceError:
            return _error('ai_services_unavailable', 503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.put('/api/ai/services/{service_id}')
    async def put_ai_service(request: Request, service_id: str):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return _error('cross_site_request', 403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return _error('json_required', 400)
        try:
            payload = parse_body(await request.body())
        except OverflowError:
            return _error('request_too_large', 413)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _error('invalid_request', 400)
        allowed = {'revision', 'name', 'base_url', 'api_key', 'clear_api_key', 'temperature'}
        if set(payload) - allowed or not isinstance(payload.get('revision'), str):
            return _error('invalid_request', 400)
        values = {key: payload[key] for key in allowed - {'revision'} if key in payload}
        values['revision'] = payload['revision']
        try:
            result = await dispatch(partial(update_profile, service_id=service_id, values=values), request)
        except AIServiceError as exc:
            message = str(exc)
            if 'conflict' in message:
                return _error('revision_conflict', 409)
            if 'unavailable' in message or 'migration' in message:
                return _error('ai_services_unavailable', 503)
            return _error('invalid_service', 422)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.put('/api/ai/service-bindings')
    async def put_ai_service_binding(request: Request):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return _error('cross_site_request', 403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            payload = parse_body(await request.body())
        except OverflowError:
            return _error('request_too_large', 413)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _error('invalid_request', 400)
        if set(payload) not in ({'revision', 'feature', 'profile_id'}, {'revision', 'feature', 'profile_id', 'model_id'}) or any(not isinstance(payload[key], str) for key in payload):
            return _error('invalid_request', 400)
        try:
            result = await dispatch(partial(update_binding, values=payload), request)
        except AIServiceError as exc:
            if 'conflict' in str(exc):
                return _error('revision_conflict', 409)
            if 'model' in str(exc):
                return _error('model_not_available', 422)
            return _error('invalid_binding', 422)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.get('/api/ai/services/{service_id}/models')
    async def get_ai_service_models(request: Request, service_id: str):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            result = await dispatch(partial(read_models, service_id=service_id), request)
        except AIServiceError:
            return _error('ai_services_unavailable', 503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/ai/services/{service_id}/test')
    async def post_ai_service_test(request: Request, service_id: str):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return _error('cross_site_request', 403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            payload = parse_body(await request.body())
            if payload:
                raise ValueError
        except OverflowError:
            return _error('request_too_large', 413)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _error('invalid_request', 400)
        try:
            result = await dispatch(partial(test_profile, service_id=service_id), request)
        except AIServiceError as exc:
            if 'conflict' in str(exc):
                return _error('revision_conflict', 409)
            if 'not_configured' in str(exc):
                return _error('service_not_configured', 422)
            return _error('ai_services_unavailable', 503)
        except (GeminiUpstreamError, ChatUpstreamError, ProviderError) as exc:
            code, status = _connection_error(exc)
            return JSONResponse({'ok': False, 'error': code}, status_code=status)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/ai/services/{service_id}/test/generation')
    async def post_ai_service_generation_test(request: Request, service_id: str):
        return await _run_assistant_test(request, service_id, structured=False)

    @app.post('/api/ai/services/{service_id}/test/structured')
    async def post_ai_service_structured_test(request: Request, service_id: str):
        return await _run_assistant_test(request, service_id, structured=True)
