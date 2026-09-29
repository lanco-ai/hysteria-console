"""Authenticated journal endpoints. Journal bodies never enter AI services."""

import json
from functools import partial
from types import SimpleNamespace

import http_utils
import state_store
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .journal_service import JournalInput, JournalStore, JournalUpdate
from .services import LoginRequired, StateUnavailable, UserAccessDenied


MAX_JOURNAL_BODY_BYTES = 64 * 1024


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)


def register_journal_routes(app, services, dispatch, *, store=None):
    store = store or JournalStore()

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

    async def write_precheck(request):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        return await require_admin(request)

    async def parse_json(request, model):
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return JSONResponse({'error': 'json_required'}, status_code=400)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_JOURNAL_BODY_BYTES:
                return JSONResponse({'error': 'request_too_large'}, status_code=413)
        try:
            return model.model_validate(json.loads(body.decode('utf-8')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return JSONResponse({'error': 'invalid_request'}, status_code=422)

    def call_store(method, **kwargs):
        def operation(*, headers, path):
            del headers, path
            return method(**kwargs)
        return operation

    async def execute(request, operation, *, created=False):
        try:
            result = await dispatch(operation, request)
        except KeyError:
            return JSONResponse({'error': 'journal_not_found'}, status_code=404)
        except ValueError as exc:
            if str(exc) == 'conflict':
                return JSONResponse({'error': 'revision_conflict'}, status_code=409)
            return JSONResponse({'error': 'invalid_journal'}, status_code=422)
        except (OSError, RuntimeError, state_store.StateStoreError):
            return JSONResponse({'error': 'journal_unavailable'}, status_code=503)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=201 if created else 200)

    @app.get('/api/journal')
    async def list_journal(request: Request, date: str | None = None, kind: str | None = None,
                           q: str | None = None, start: str | None = None, end: str | None = None):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        return await execute(request, call_store(store.read, date=date, kind=kind, q=q, start=start, end=end))

    @app.get('/api/journal/summary')
    async def journal_summary(request: Request, week_start: str):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        return await execute(request, call_store(store.week_summary, week_start=week_start))

    @app.get('/api/journal/export')
    async def export_journal(request: Request):
        denied = await require_admin(request)
        if denied is not None:
            return denied
        response = await execute(request, call_store(store.read))
        if response.status_code == 200:
            response.headers['Content-Disposition'] = 'attachment; filename="life-learning-journal.json"'
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/journal')
    async def create_journal(request: Request):
        denied = await write_precheck(request)
        if denied is not None:
            return denied
        payload = await parse_json(request, JournalInput)
        if isinstance(payload, JSONResponse):
            return payload
        return await execute(request, call_store(store.create, values=payload.model_dump(mode='json')), created=True)

    @app.put('/api/journal/{record_id}')
    async def update_journal(request: Request, record_id: str):
        denied = await write_precheck(request)
        if denied is not None:
            return denied
        payload = await parse_json(request, JournalUpdate)
        if isinstance(payload, JSONResponse):
            return payload
        return await execute(request, call_store(store.update, record_id=record_id, values=payload.model_dump(mode='json')))

    @app.delete('/api/journal/{record_id}')
    async def delete_journal(request: Request, record_id: str):
        denied = await write_precheck(request)
        if denied is not None:
            return denied
        payload = await parse_json(request, DeleteRequest)
        if isinstance(payload, JSONResponse):
            return payload
        return await execute(request, call_store(store.delete, record_id=record_id, revision=payload.revision))
