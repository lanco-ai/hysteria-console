"""Authenticated routes for the floating Lanco Agent."""

import json
from functools import partial
from types import SimpleNamespace

import http_utils
from fastapi import Request
from fastapi.responses import JSONResponse
import state_store

from .agent_service import AgentOrchestrator, complete_agent_intent
from .agent_rule_service import AgentServiceError
from .chat_service import MAX_BODY_BYTES
from .services import LoginRequired, StateUnavailable, UserAccessDenied


def _error(code, status=400):
    return JSONResponse(status_code=status, content={'ok': False, 'error': code})


async def _json(request):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise ValueError('json')
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise OverflowError
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('json')
    return value


def _same_origin(request):
    return http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers))


async def _require_admin(request, services, dispatch):
    try:
        session = await dispatch(services.read_session, request)
    except (LoginRequired, UserAccessDenied):
        return _error('login_required', 401)
    except StateUnavailable:
        return _error('state_unavailable', 503)
    if not isinstance(session, dict) or session.get('role') != 'admin':
        return _error('login_required', 401)
    return None


def _status(exc):
    return {
        'target_required': 422,
        'user_not_found': 404,
        'revision_conflict': 409,
        'upstream_unavailable': 502,
        'state_unavailable': 503,
        'invalid_change': 422,
        'change_not_pending': 409,
        'change_not_undoable': 409,
        'rule_not_found': 404,
    }.get(exc.code, 422)


def register_agent_routes(app, services, dispatch, *, orchestrator=None):
    service_module = getattr(services, 'service_module', None)
    if orchestrator is not None:
        agent = orchestrator
    elif service_module is not None:
        agent = AgentOrchestrator(service_module, completion=complete_agent_intent)
    else:
        class UnavailableAgent:
            def plan(self, **_kwargs):
                raise AgentServiceError('state_unavailable')

            def apply_change(self, _change_id, **_kwargs):
                raise AgentServiceError('state_unavailable')

            def undo_change(self, _change_id, **_kwargs):
                raise AgentServiceError('state_unavailable')

        agent = UnavailableAgent()
        agent.rule_service = agent

    @app.post('/api/v1/admin/agent/plan')
    async def plan(request: Request):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _json(request)
            message = payload.get('message')
            target_user = payload.get('target_user')
            if not isinstance(message, str) or not isinstance(target_user, str):
                raise ValueError('shape')
        except OverflowError:
            return _error('request_too_large', 413)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request', 400)
        try:
            def run_plan(*, headers, path):
                client = request.scope.get('client') or ('', 0)
                actor_fn = getattr(service_module, '_admin_actor', None)
                actor = 'admin'
                if callable(actor_fn) and hasattr(services, '_bridge'):
                    actor = str(actor_fn(services._bridge(headers=headers, path=path)) or 'admin')[:128]
                return agent.plan(
                    message=message,
                    target_user=target_user,
                    operator=actor,
                    source_ip=str(client[0] or '')[:128],
                )
            result = await dispatch(run_plan, request)
        except AgentServiceError as exc:
            return _error(exc.code, _status(exc))
        except (OSError, state_store.StateStoreError, StateUnavailable):
            return _error('state_unavailable', 503)
        return JSONResponse(result)

    async def change(request: Request, operation):
        if not _same_origin(request):
            return _error('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            return denied
        try:
            payload = await _json(request)
            change_id = payload.get('change_id')
            if not isinstance(change_id, str):
                raise ValueError('shape')
        except OverflowError:
            return _error('request_too_large', 413)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return _error('bad_request', 400)
        try:
            def run_change(*, headers, path):
                client = request.scope.get('client') or ('', 0)
                actor_fn = getattr(service_module, '_admin_actor', None)
                actor = 'admin'
                if callable(actor_fn) and hasattr(services, '_bridge'):
                    actor = str(actor_fn(services._bridge(headers=headers, path=path)) or 'admin')[:128]
                return operation(
                    change_id,
                    operator=actor,
                    source_ip=str(client[0] or '')[:128],
                )
            result = await dispatch(run_change, request)
        except AgentServiceError as exc:
            return _error(exc.code, _status(exc))
        except (OSError, state_store.StateStoreError, StateUnavailable):
            return _error('state_unavailable', 503)
        return JSONResponse({'ok': True, 'result': result})

    @app.post('/api/v1/admin/agent/apply')
    async def apply(request: Request):
        return await change(request, agent.rule_service.apply_change)

    @app.post('/api/v1/admin/agent/undo')
    async def undo(request: Request):
        return await change(request, agent.rule_service.undo_change)
