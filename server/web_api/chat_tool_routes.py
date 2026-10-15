"""Tool configuration and explicit review/execution endpoints under admin auth."""
from typing import Literal
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .chat_tools import ToolService
from .chat_workspace_store import WorkspaceError


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class Server(Input):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    token: str | None = Field(default=None, max_length=4096, pattern=r'^[!-~]*$', repr=False)
    allow_private: bool = False
    revision: int | None = Field(default=None, ge=1)


class Revision(Input):
    revision: int = Field(ge=1)


class Proposed(Input):
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    tool: str = Field(min_length=1, max_length=256)
    arguments: dict


class Approved(Input):
    approved: Literal[True]


class ToolPlan(Input):
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    question: str = Field(min_length=1, max_length=12000)
    model: str = Field(min_length=1, max_length=256)
    enabled: list[str] = Field(min_length=1, max_length=12)


def register_tool_routes(app, guard, run, data, store, settings):
    service = ToolService(store)

    @app.get('/api/chat/tools/catalog')
    async def catalog(request: Request):
        await guard(request)
        return await run(request, service.catalog)

    @app.get('/api/chat/tools/servers')
    async def servers(request: Request):
        await guard(request)
        return await run(request, service.servers)

    @app.post('/api/chat/tools/servers')
    async def add_server(request: Request):
        await guard(request)
        return await run(request, service.save_server, await data(request, Server))

    @app.put('/api/chat/tools/servers/{server_id}')
    async def edit_server(request: Request, server_id: str):
        await guard(request)
        values = await data(request, Server)
        if not values.get('revision'):
            raise WorkspaceError('invalid_revision')
        return await run(request, service.save_server, values, server_id)

    @app.delete('/api/chat/tools/servers/{server_id}')
    async def delete_server(request: Request, server_id: str):
        await guard(request)
        return await run(request, service.delete_server, server_id, (await data(request, Revision))['revision'])

    @app.post('/api/chat/tools/servers/{server_id}/discover')
    async def discover(request: Request, server_id: str):
        await guard(request)
        try:
            return await service.discover(server_id)
        except WorkspaceError:
            raise
        except Exception:
            raise WorkspaceError('mcp_unavailable', 502) from None

    @app.get('/api/chat/conversations/{conversation_id}/tools')
    async def runs(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, service.runs, conversation_id)

    @app.post('/api/chat/conversations/{conversation_id}/tools')
    async def propose(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, service.propose, conversation_id, await data(request, Proposed))

    @app.post('/api/chat/conversations/{conversation_id}/tool-plans')
    async def plan(request: Request, conversation_id: str):
        await guard(request)
        return await run(request, service.plan, settings, conversation_id, await data(request, ToolPlan))

    @app.post('/api/chat/tools/runs/{run_id}/execute')
    async def execute(request: Request, run_id: str):
        await guard(request)
        await data(request, Approved)
        return await service.execute(run_id)

    @app.delete('/api/chat/tools/runs/{run_id}')
    async def delete_run(request: Request, run_id: str):
        await guard(request)
        return await run(request, service.delete_run, run_id)

    @app.get('/api/chat/tools/artifacts/{artifact_id}')
    async def artifact(request: Request, artifact_id: str):
        await guard(request)
        item, raw = await run(request, service.artifact, artifact_id)
        disposition = 'inline' if item['media_type'] == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n') else 'attachment'
        return Response(raw, media_type=item['media_type'], headers={'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(item['name'], safe=''),
            'Cache-Control': 'no-store', 'Content-Security-Policy': "sandbox; default-src 'none'", 'X-Content-Type-Options': 'nosniff'})
