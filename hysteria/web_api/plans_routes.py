"""Authenticated API routes for administrator daily plans."""

import json
from datetime import date as calendar_date
from functools import partial
from types import SimpleNamespace
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .ai.gemini import GeminiAdapter, GeminiUpstreamError
from .ai.service_store import AIServiceError, AIServiceStore
from .plans_service import PlanStore
from .services import LoginRequired, StateUnavailable, UserAccessDenied


MAX_PLAN_BODY_BYTES = 128 * 1024


class PlanSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(min_length=64, max_length=64)
    items: list[dict]


class ReminderAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(pattern=r'^(dismiss|snooze|complete)$')


class AssistantTask(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    quadrant: Literal['important_urgent', 'important', 'urgent', 'later']
    status: Literal['todo', 'in_progress', 'done'] = 'todo'
    estimate_minutes: int = Field(default=30, ge=5, le=1440)


class PlanAssistantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    timezone: str = Field(min_length=1, max_length=128)
    request: str = Field(min_length=1, max_length=4000)
    existing_tasks: list[AssistantTask] = Field(default_factory=list, max_length=100)

    @field_validator('date')
    @classmethod
    def validate_calendar_date(cls, value):
        try:
            calendar_date.fromisoformat(value)
        except ValueError:
            raise ValueError('invalid calendar date') from None
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('invalid timezone') from None
        return value


class PlanSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    notes: str = Field(default='', max_length=1200)
    quadrant: Literal['important_urgent', 'important', 'urgent', 'later']
    start_time: str = Field(default='', max_length=5)
    estimate_minutes: int = Field(ge=5, le=1440)
    reminder_offset_minutes: int = Field(default=0, ge=0, le=1440)
    reason: str = Field(default='', max_length=500)

    @field_validator('start_time')
    @classmethod
    def validate_start_time(cls, value):
        if value and (len(value) != 5 or value[2] != ':' or not value[:2].isdigit() or not value[3:].isdigit() or int(value[:2]) > 23 or int(value[3:]) > 59):
            raise ValueError('invalid start time')
        return value


class PlanAssistantResult(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=500)
    suggestions: list[PlanSuggestion] = Field(min_length=1, max_length=8)


def register_plans_routes(app, services, dispatch, *, store=None, ai_services_store=None, gemini_adapter=None):
    store = store or PlanStore()
    ai_store = ai_services_store
    gemini = gemini_adapter or GeminiAdapter()

    async def require_admin(request):
        try:
            session = await dispatch(services.read_session, request)
        except (LoginRequired, UserAccessDenied):
            return JSONResponse({'error': 'login_required'}, status_code=401)
        except StateUnavailable:
            return JSONResponse({'error': 'state_unavailable'}, status_code=503)
        if isinstance(session, JSONResponse):
            return session
        if not isinstance(session, dict) or session.get('role') != 'admin':
            return JSONResponse({'error': 'admin_required'}, status_code=403)
        return None

    def read(*, headers, path):
        del headers, path
        return store.read()

    def replace(*, headers, path, values):
        del headers, path
        return store.replace(values['items'], values['revision'])

    def suggest_plan(*, headers, path, values):
        del headers, path
        if ai_store is None:
            raise AIServiceError('service_not_configured')
        profile = ai_store.bound_profile('plan_assistant')
        if profile['protocol'] != 'gemini_native' or not profile['api_key']:
            raise AIServiceError('service_not_configured')
        models = profile.get('models') or gemini.list_models(profile)
        if not models:
            raise GeminiUpstreamError('models_endpoint_unavailable')
        model = models[0].get('id') if isinstance(models[0], dict) else None
        if not isinstance(model, str) or not model:
            raise GeminiUpstreamError('models_endpoint_unavailable')
        task_context = [item.model_dump() for item in values.existing_tasks]
        prompt = (
            '你是私人每日计划助手。根据用户的目标和已有事项，提出 1 到 8 条可执行的计划建议。'
            '只给建议，不执行、不保存、不声称已设置提醒。保持现实、简洁；重要且紧急事项优先。'
            'quadrant 只能是 important_urgent、important、urgent、later。'
            'start_time 必须是当地 24 小时 HH:MM；没有把握时返回空字符串。'
            'reminder_offset_minutes 为 0 表示不建议提醒，否则是开始时间前的分钟数；若没有 start_time 必须为 0。'
            '返回符合给定 JSON schema 的 JSON。\n\n'
            + json.dumps({
                'date': values.date, 'timezone': values.timezone,
                'user_request': values.request, 'existing_tasks': task_context,
            }, ensure_ascii=False, separators=(',', ':'))
        )
        schema = {
            'type': 'OBJECT',
            'properties': {
                'summary': {'type': 'STRING'},
                'suggestions': {
                    'type': 'ARRAY', 'minItems': 1, 'maxItems': 8,
                    'items': {
                        'type': 'OBJECT',
                        'properties': {
                            'title': {'type': 'STRING'}, 'notes': {'type': 'STRING'},
                            'quadrant': {'type': 'STRING', 'enum': ['important_urgent', 'important', 'urgent', 'later']},
                            'start_time': {'type': 'STRING'}, 'estimate_minutes': {'type': 'INTEGER'},
                            'reminder_offset_minutes': {'type': 'INTEGER'}, 'reason': {'type': 'STRING'},
                        },
                        'required': ['title', 'notes', 'quadrant', 'start_time', 'estimate_minutes', 'reminder_offset_minutes', 'reason'],
                    },
                },
            },
            'required': ['summary', 'suggestions'],
        }
        result = gemini.generate_json(profile, model, prompt, schema)
        try:
            validated = PlanAssistantResult.model_validate(result)
            suggestions = [item.model_dump() for item in validated.suggestions]
            if any(item['reminder_offset_minutes'] and not item['start_time'] for item in suggestions):
                raise ValueError('reminder requires start time')
        except (ValidationError, ValueError):
            raise GeminiUpstreamError('invalid_model_response', 200) from None
        return {
            'summary': validated.summary,
            'model': model,
            'service_name': profile['name'],
            'suggestions': suggestions,
        }

    @app.get('/api/plans')
    async def get_plans(request: Request):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            result = await dispatch(read, request)
        except (OSError, ValueError):
            return JSONResponse({'error': 'plans_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/plans/assistant')
    async def post_plan_assistant(request: Request):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return JSONResponse({'error': 'json_required'}, status_code=400)
        try:
            body = await request.body()
            if len(body) > 32 * 1024:
                return JSONResponse({'error': 'request_too_large'}, status_code=413)
            payload = PlanAssistantRequest.model_validate(json.loads(body.decode('utf-8')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        try:
            result = await dispatch(partial(suggest_plan, values=payload), request)
        except AIServiceError as exc:
            if 'not_configured' in str(exc):
                return JSONResponse({'error': 'service_not_configured'}, status_code=422)
            return JSONResponse({'error': 'ai_service_unavailable'}, status_code=503)
        except GeminiUpstreamError as exc:
            if exc.code == 'service_not_configured':
                return JSONResponse({'error': exc.code}, status_code=422)
            if exc.code == 'timeout':
                status = 504
            elif exc.code in {'authentication_failed', 'permission_denied', 'rate_limited'}:
                status = 502
            else:
                status = 502
            return JSONResponse({'error': exc.code}, status_code=status)
        except (OSError, RuntimeError):
            return JSONResponse({'error': 'ai_service_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.put('/api/plans')
    async def put_plans(request: Request):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return JSONResponse({'error': 'json_required'}, status_code=400)
        try:
            body = await request.body()
            if len(body) > MAX_PLAN_BODY_BYTES:
                return JSONResponse({'error': 'request_too_large'}, status_code=413)
            snapshot = PlanSnapshot.model_validate(json.loads(body.decode('utf-8')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        try:
            result = await dispatch(partial(replace, values=snapshot.model_dump()), request)
        except ValueError as exc:
            if str(exc) == 'conflict':
                return JSONResponse({'error': 'revision_conflict'}, status_code=409)
            return JSONResponse({'error': 'invalid_plan'}, status_code=422)
        except (OSError, RuntimeError):
            return JSONResponse({'error': 'plans_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.get('/api/plans/reminders')
    async def get_due_reminders(request: Request):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            result = await dispatch(lambda *, headers, path: {'items': store.due_reminders()}, request)
        except (OSError, ValueError, RuntimeError):
            return JSONResponse({'error': 'plans_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result)

    @app.post('/api/plans/reminders/{task_id}')
    async def act_on_reminder(request: Request, task_id: str):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        denied = await require_admin(request)
        if denied is not None:
            return denied
        try:
            body = await request.body()
            if len(body) > 16 * 1024:
                return JSONResponse({'error': 'request_too_large'}, status_code=413)
            payload = ReminderAction.model_validate(json.loads(body.decode('utf-8')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        try:
            def update(*, headers, path):
                del headers, path
                return store.update_reminder(task_id, payload.action)
            result = await dispatch(update, request)
        except (OSError, ValueError, RuntimeError):
            return JSONResponse({'error': 'plans_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        if result is None:
            return JSONResponse({'error': 'plan_not_found'}, status_code=404)
        return JSONResponse({'item': result})
