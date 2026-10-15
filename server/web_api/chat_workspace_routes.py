"""Authenticated personal workspace endpoints, independent of provider credentials."""

import asyncio
import json
import sqlite3
from functools import partial
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .chat_documents import MAX_UPLOAD, extract
from .chat_document_jobs import DocumentJobs
from .chat_context import prepare_context
from .chat_tool_routes import register_tool_routes
from .chat_routes import _require_admin, _same_origin
from .chat_service import ChatSettingsStore, ChatSettingsError, forward_chat_stream
from .chat_workspace_store import WorkspaceError, WorkspaceStore, digest
from .journal_service import JournalInput, JournalStore


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Revision(Input):
    revision: int = Field(ge=1, strict=True)


class Project(Input):
    name: str = Field(min_length=1, max_length=80)
    goal: str = Field(default='', max_length=2000)
    instructions: str = Field(default='', max_length=4000)


class ProjectUpdate(Project, Revision):
    pass


class Conversation(Input):
    project_id: str | None = Field(default=None, max_length=80)


class ConversationUpdate(Revision):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    draft: str | None = Field(default=None, max_length=12000)
    model: str | None = Field(default=None, max_length=256)
    reasoningEffort: Literal['auto', 'low', 'medium', 'high'] | None = None


class Turn(Revision):
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    content: str = Field(min_length=1, max_length=12000)
    model: str = Field(min_length=1, max_length=256)
    reasoning_effort: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    document_ids: list[str] = Field(default_factory=list, max_length=8)
    knowledge_scope: Literal['none', 'project', 'all'] = 'none'
    tool_run_ids: list[str] = Field(default_factory=list, max_length=3)


class Preferences(Input):
    # All fields are required: saving replaces the workspace's AI 设置 as a whole.
    revision: int = Field(ge=0, strict=True)
    instructions: str = Field(max_length=2000)
    default_model: str = Field(max_length=256)
    default_reasoning: Literal['auto', 'low', 'medium', 'high']


class KnowledgeSearch(Input):
    query: str = Field(min_length=1, max_length=2000)
    project_id: str | None = Field(default=None, max_length=80)


class Memory(Revision):
    text: str = Field(min_length=1, max_length=800)
    source_conversation_id: str | None = Field(default=None, max_length=80)
    source_message_id: str | None = Field(default=None, max_length=80)


class SummaryEdit(Revision):
    text: str = Field(max_length=4000)


class Stop(Input):
    request_id: str = Field(min_length=8, max_length=80)


class Branch(Revision):
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    message_id: str = Field(min_length=1, max_length=80)
    mode: Literal['continue', 'edit', 'regenerate']
    content: str = Field(default='', max_length=12000)


class LegacyMessage(Input):
    role: Literal['user', 'assistant', 'system']
    content: str = Field(max_length=64000)


class LegacySession(Input):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(default='', max_length=160)
    model: str = Field(default='', max_length=256)
    draft: str = Field(default='', max_length=12000)
    reasoningEffort: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    updatedAt: float = Field(default=0, ge=0, le=4102444800000)
    messages: list[LegacyMessage] = Field(max_length=500)


class Legacy(Input):
    sessions: list[LegacySession] = Field(max_length=100)


class JournalLink(Input):
    message_id: str = Field(max_length=80)
    draft: JournalInput


def sse(event):
    return 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'


def register_workspace_routes(app, services, dispatch, dispatch_stream, *, store=None, settings=None, journal=None):
    store = store or WorkspaceStore()
    settings = settings or ChatSettingsStore()
    journal = journal or JournalStore()
    document_jobs = DocumentJobs(store)
    knowledge = document_jobs.knowledge

    async def guard(request):
        if request.method != 'GET' and not _same_origin(request):
            raise WorkspaceError('cross_site_request', 403)
        denied = await _require_admin(request, services, dispatch)
        if denied is not None:
            raise WorkspaceError('login_required', denied.status_code)

    async def data(request, model, limit=128 * 1024):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise WorkspaceError('json_required', 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise WorkspaceError('request_too_large', 413)
        try:
            return model.model_validate_json(raw).model_dump(mode='json', exclude_none=True)
        except ValidationError:
            raise WorkspaceError('invalid_request') from None

    async def run(request, method, *args, **kwargs):
        def operation(*, headers, path):
            return method(*args, **kwargs)
        try:
            result = await dispatch(operation, request)
        except (OSError, sqlite3.Error):
            raise WorkspaceError('workspace_unavailable', 503) from None
        if isinstance(result, JSONResponse):
            raise WorkspaceError('server_busy', result.status_code)
        return result

    @app.exception_handler(WorkspaceError)
    async def workspace_error(_request, exc):
        return JSONResponse({'error': exc.code}, status_code=exc.status, headers={'Cache-Control': 'no-store'})

    register_tool_routes(app, guard, run, data, store, settings)

    @app.get('/api/chat/projects')
    async def projects(request: Request):
        await guard(request)
        return await run(request, store.projects)

    @app.post('/api/chat/projects')
    async def create_project(request: Request):
        await guard(request)
        return await run(request, store.save_project, await data(request, Project))

    @app.put('/api/chat/projects/{project_id}')
    async def update_project(request: Request, project_id: str):
        await guard(request)
        return await run(request, store.save_project, await data(request, ProjectUpdate), project_id)

    @app.delete('/api/chat/projects/{project_id}')
    async def delete_project(request: Request, project_id: str):
        await guard(request)
        return await run(request, store.delete_project, project_id, (await data(request, Revision))['revision'])

    @app.post('/api/chat/projects/{project_id}/memories')
    async def add_memory(request: Request, project_id: str):
        await guard(request)
        return await run(request, store.save_memory, project_id, await data(request, Memory))

    @app.patch('/api/chat/projects/{project_id}/memories/{memory_id}')
    async def edit_memory(request: Request, project_id: str, memory_id: str):
        await guard(request)
        return await run(request, store.save_memory, project_id, await data(request, Memory), memory_id)

    @app.delete('/api/chat/projects/{project_id}/memories/{memory_id}')
    async def remove_memory(request: Request, project_id: str, memory_id: str):
        await guard(request)
        return await run(request, store.delete_memory, project_id, memory_id, (await data(request, Revision))['revision'])

    @app.get('/api/chat/conversations')
    async def conversations(request: Request, q: str = '', project_id: str | None = None):
        await guard(request)
        if len(q) > 200:
            raise WorkspaceError('invalid_search')
        return await run(request, store.list, q, project_id)

    @app.post('/api/chat/conversations')
    async def create_conversation(request: Request):
        await guard(request)
        return await run(request, store.create, (await data(request, Conversation)).get('project_id'))

    @app.get('/api/chat/workspace/export')
    async def export(request: Request):
        await guard(request)
        return JSONResponse(await run(request, store.export), headers={'Content-Disposition': 'attachment; filename="learning-workspace.json"', 'Cache-Control': 'no-store'})

    @app.get('/api/chat/workspace/usage')
    async def usage(request: Request):
        await guard(request)
        return await run(request, store.usage)

    @app.get('/api/chat/workspace/preferences')
    async def preferences(request: Request):
        await guard(request)
        return await run(request, store.preferences)

    @app.put('/api/chat/workspace/preferences')
    async def save_preferences(request: Request):
        await guard(request)
        return await run(request, store.save_preferences, await data(request, Preferences, limit=16 * 1024))

    @app.post('/api/chat/import/legacy')
    async def import_legacy(request: Request):
        await guard(request)
        return await run(request, store.import_legacy, (await data(request, Legacy))['sessions'])

    @app.get('/api/chat/conversations/{conversation_id}')
    async def conversation(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, store.get, conversation_id)

    @app.patch('/api/chat/conversations/{conversation_id}')
    async def update_conversation(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, store.update, conversation_id, await data(request, ConversationUpdate))

    @app.delete('/api/chat/conversations/{conversation_id}')
    async def delete_conversation(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, store.delete, conversation_id, (await data(request, Revision))['revision'])

    @app.post('/api/chat/conversations/{conversation_id}/branches')
    async def branch_conversation(request: Request, conversation_id: str):
        await guard(request)
        values = await data(request, Branch)
        if values['mode'] == 'edit' and not values['content'].strip():
            raise WorkspaceError('empty_message')
        return await run(request, store.fork, conversation_id, values)

    @app.patch('/api/chat/conversations/{conversation_id}/context')
    async def edit_context(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, store.edit_summary, conversation_id, await data(request, SummaryEdit))

    @app.get('/api/chat/projects/{project_id}/documents')
    async def documents(request: Request, project_id: str):
        await guard(request)
        document_jobs.resume()
        return await run(request, store.documents, project_id)

    @app.get('/api/chat/knowledge/documents')
    async def library(request: Request):
        await guard(request)
        document_jobs.resume()
        return await run(request, store.documents, None)

    @app.get('/api/chat/knowledge/status')
    async def knowledge_status(request: Request):
        await guard(request)
        document_jobs.resume()
        return await run(request, knowledge.status)

    @app.post('/api/chat/knowledge/search')
    async def knowledge_search(request: Request):
        await guard(request)
        values = await data(request, KnowledgeSearch)
        return await run(request, knowledge.search, values['query'], values.get('project_id'))

    @app.post('/api/chat/documents/{document_id}/reindex')
    async def reindex(request: Request, document_id: str):
        await guard(request)
        result = await run(request, store.reindex_document, document_id)
        document_jobs.resume()
        return result

    @app.post('/api/chat/documents/upload')
    async def upload(request: Request, project_id: str, filename: str):
        await guard(request)
        if len(filename) > 200 or '/' in filename or '\\' in filename or any(ord(c) < 32 for c in filename):
            raise WorkspaceError('invalid_filename')
        # Validate scope before spending CPU on extraction.
        await run(request, store.documents, project_id)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_UPLOAD:
                raise WorkspaceError('file_too_large', 413)
        def save():
            if Path(filename).suffix.lower() in ('.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'):
                if not raw:
                    raise WorkspaceError('invalid_document')
                item = store.add_document(project_id, filename, bytes(raw), [], 'application/octet-stream', 'queued')
                document_jobs.resume()
                return item
            pages, media_type = extract(filename, bytes(raw))
            item = store.add_document(project_id, filename, bytes(raw), pages, media_type)
            document_jobs.resume()
            return item
        return await run(request, save)

    @app.post('/api/chat/documents/{document_id}/retry')
    async def retry_document(request: Request, document_id: str):
        await guard(request)
        result = await run(request, store.retry_document, document_id)
        document_jobs.resume()
        return result

    @app.get('/api/chat/documents/{document_id}/file')
    async def document_file(request: Request, document_id: str):
        await guard(request)
        meta, raw = await run(request, store.document, document_id)
        return Response(raw, media_type=meta['media_type'], headers={
            'Content-Disposition': "inline; filename*=UTF-8''" + quote(meta['title'], safe=''),
            'Cache-Control': 'no-store', 'Content-Security-Policy': "sandbox; default-src 'none'",
            'X-Content-Type-Options': 'nosniff',
        })

    @app.delete('/api/chat/documents/{document_id}')
    async def delete_document(request: Request, document_id: str):
        await guard(request)
        return await run(request, store.delete_document, document_id)

    @app.post('/api/chat/conversations/{conversation_id}/journal')
    async def to_journal(request: Request, conversation_id: str):
        await guard(request)
        values = await data(request, JournalLink)
        item = await run(request, store.get, conversation_id)
        message = next((m for m in item['messages'] if m['id'] == values['message_id']), None)
        if not message or message['role'] != 'assistant' or not message['content'] or message['status'] == 'streaming':
            raise WorkspaceError('invalid_journal_source')
        provenance = {'conversation_id': conversation_id, 'message_id': message['id'],
                      'sha256': digest(message['content']), 'title': item['title']}
        return await run(request, journal.create_from_chat, values['draft'], provenance)

    @app.post('/api/chat/conversations/{conversation_id}/stop')
    async def stop(request: Request, conversation_id: str):
        await guard(request)
        values = await data(request, Stop)
        await run(request, store.event, conversation_id, values['request_id'], {'type': 'stopped'})
        return await run(request, store.get, conversation_id)

    @app.post('/api/chat/conversations/{conversation_id}/turns')
    async def turn(request: Request, conversation_id: str):
        await guard(request)
        values = await data(request, Turn)
        replay = await run(request, store.turn_replay, conversation_id, values)
        if replay:
            return StreamingResponse(iter([sse({'type': 'snapshot', 'conversation': replay}), sse({'type': 'done'})]), media_type='text/event-stream')
        semantic = None
        if values['knowledge_scope'] != 'none':
            conversation = await run(request, store.get, conversation_id)
            if values['knowledge_scope'] == 'project' and not conversation['project_id']:
                raise WorkspaceError('knowledge_project_required')
            result = await run(request, knowledge.search, values['content'], conversation['project_id'] if values['knowledge_scope'] == 'project' else None)
            semantic = result['items']
        item, messages = await run(request, store.begin, conversation_id, values, semantic)
        if messages is None:
            return StreamingResponse(iter([sse({'type': 'snapshot', 'conversation': item}), sse({'type': 'done'})]), media_type='text/event-stream')

        async def persist(event):
            # Never abandon an in-flight SQLite transaction on client disconnect.
            with anyio.CancelScope(shield=True):
                return await anyio.to_thread.run_sync(partial(store.event, conversation_id, values['request_id'], event))

        async def generate(*, headers, path):
            upstream = None
            try:
                yield sse({'type': 'snapshot', 'conversation': item})
                prepared, incomplete = await anyio.to_thread.run_sync(partial(prepare_context, store, settings, conversation_id, values['request_id'], values['model'], messages))
                if prepared is None:
                    return
                if incomplete:
                    yield sse({'type': 'notice', 'notice': 'context_summary_incomplete'})
                if callable(getattr(settings, 'stream', None)):
                    upstream = settings.stream(prepared, model=values['model'], reasoning_effort=values['reasoning_effort'])
                else:
                    upstream = forward_chat_stream(settings.read(), prepared, model=values['model'], reasoning_effort=values['reasoning_effort'])
                buffer = ''
                async with asyncio.timeout(480):
                    async for chunk in upstream:
                        buffer += chunk.decode() if isinstance(chunk, bytes) else chunk
                        buffer = buffer.replace('\r\n', '\n')
                        while '\n\n' in buffer:
                            raw, buffer = buffer.split('\n\n', 1)
                            body = '\n'.join(line[5:].strip() for line in raw.splitlines() if line.startswith('data:'))
                            if not body:
                                continue
                            event = json.loads(body)
                            if not await persist(event):
                                return
                            yield sse(event)
                            if event.get('type') in ('done', 'error'):
                                return
            except ChatSettingsError:
                await persist({'type': 'error'})
                yield sse({'type': 'error', 'error': 'settings_incomplete'})
            except Exception:
                await persist({'type': 'error'})
                yield sse({'type': 'error', 'error': 'generation_failed'})
            finally:
                with anyio.CancelScope(shield=True):
                    if upstream is not None:
                        await upstream.aclose()
                    await persist({'type': 'interrupted'})

        result = await dispatch_stream(generate, request)
        if isinstance(result, JSONResponse):
            await persist({'type': 'error'})
            return result
        return StreamingResponse(result, media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
