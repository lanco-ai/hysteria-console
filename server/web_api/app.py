"""Side-effect-free FastAPI application factory for the workbench + AI service."""

import threading
from contextlib import asynccontextmanager
from functools import partial

import anyio
import http_utils
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException

from .agent_intent_routes import INTERNAL_TOKEN_FILE, register_agent_intent_routes
from .ai.routes import register_ai_service_routes
from .ai.service_store import AIServiceStore
from .chat_routes import register_chat_routes
from .document_routes import register_react_document_routes
from .github_trending_routes import register_github_trending_routes
from .journal_routes import register_journal_routes
from .plans_routes import register_plans_routes
from .requests import RequestHeaders
from .service_center import register_service_center_routes
from .video_routes import register_video_routes

_API_SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    **http_utils.SECURITY_HEADERS,
}


class _SecurityAndErrorBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        response_started = False

        async def hardened_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
                headers = MutableHeaders(scope=message)
                for name, value in _API_SECURITY_HEADERS.items():
                    headers[name] = value
            elif message['type'] == 'http.response.body' and scope['method'] == 'HEAD':
                message = {**message, 'body': b''}
            await send(message)

        try:
            await self.app(scope, receive, hardened_send)
        except Exception:
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={'error': 'internal_error'},
            )
            await response(scope, receive, hardened_send)


def _request_headers(request):
    return RequestHeaders(request.scope.get('headers', request.headers))


def create_app(
    services,
    *,
    max_requests=32,
    react_dist=None,
    lifespan=None,
    video_settings_store=None,
    video_provider_factory=None,
    video_workflow_store=None,
    video_asset_store=None,
    video_run_service=None,
    video_scheduler_enabled=False,
    video_scheduler_interval=5.0,
    service_center_store=None,
    github_trending_store=None,
    github_trending_avatar_proxy=None,
    github_trending_scheduler_enabled=False,
    plans_store=None,
    schedule_store=None,
    journal_store=None,
    chat_settings_store=None,
    chat_workspace_store=None,
    chat_workspace_settings=None,
    ai_services_store: AIServiceStore | None = None,
    openai_models_fetcher=None,
    media_provider_factory=None,
    agent_intent_completer=None,
    agent_settings_store=None,
    internal_token_file=INTERNAL_TOKEN_FILE,
):
    """Build the workbench + AI app.

    ``services`` only needs ``read_session(headers=, path=)``; production passes
    ``RemoteSessionServices`` so the network service stays the session authority.
    """

    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
        raise ValueError('max_requests must be a positive integer')
    if not isinstance(video_scheduler_enabled, bool):
        raise ValueError('video_scheduler_enabled must be a boolean')
    if (
        isinstance(video_scheduler_interval, bool)
        or not isinstance(video_scheduler_interval, (int, float))
        or video_scheduler_interval <= 0
        or video_scheduler_interval > 60
    ):
        raise ValueError('video_scheduler_interval must be greater than 0 and at most 60 seconds')

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    if lifespan is not None:
        route_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def combined_lifespan(app):
            # Keep route-owned workers inside the service's lifecycle.
            async with lifespan(app) as state:
                async with route_lifespan(app):
                    yield state

        app.router.lifespan_context = combined_lifespan
    app.add_middleware(_SecurityAndErrorBoundary)
    capacity = threading.BoundedSemaphore(max_requests)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        if exc.status_code == 404:
            error = 'not_found'
        elif exc.status_code == 405:
            error = 'method_not_allowed'
        else:
            error = 'request_error'
        return JSONResponse(status_code=exc.status_code, content={'error': error})

    async def dispatch(function, request, *, prepare=None):
        if not capacity.acquire(blocking=False):
            return JSONResponse(
                status_code=503,
                content={'error': 'server_busy'},
                headers={'Retry-After': '1'},
            )
        released = False

        def release_capacity():
            nonlocal released
            if not released:
                released = True
                capacity.release()

        try:
            headers = _request_headers(request)
            arguments = {
                'headers': headers,
                'path': request.scope['path'],
            }
            if prepare is not None:
                arguments.update(await prepare(request, headers))
            call = partial(function, **arguments)
            outcome = {}
            complete = anyio.Event()

            async def run_and_release():
                try:
                    outcome['payload'] = await anyio.to_thread.run_sync(
                        call,
                        abandon_on_cancel=False,
                    )
                except Exception as exc:
                    outcome['error'] = exc
                finally:
                    release_capacity()
                    complete.set()

            async with anyio.create_task_group() as workers:
                workers.start_soon(run_and_release)
                await complete.wait()
            if 'error' in outcome:
                raise outcome['error']
            return outcome['payload']
        finally:
            release_capacity()

    async def dispatch_stream(function, request, *, prepare=None):
        """Run a streaming handler while retaining one request slot."""
        if not capacity.acquire(blocking=False):
            return JSONResponse(
                status_code=503,
                content={'error': 'server_busy'},
                headers={'Retry-After': '1'},
            )
        released = False

        def release_capacity():
            nonlocal released
            if not released:
                released = True
                capacity.release()

        try:
            headers = _request_headers(request)
            arguments = {
                'headers': headers,
                'path': request.scope['path'],
            }
            if prepare is not None:
                arguments.update(await prepare(request, headers))
            iterator = function(**arguments)
        except Exception:
            release_capacity()
            raise

        async def retain_capacity():
            try:
                async for chunk in iterator:
                    yield chunk
            finally:
                try:
                    await iterator.aclose()
                finally:
                    release_capacity()

        return retain_capacity()

    @app.api_route('/healthz', methods=['GET', 'HEAD'], include_in_schema=False)
    async def healthz():
        return JSONResponse({'ok': True, 'service': 'workbench-ai'})

    register_chat_routes(
        app,
        services,
        dispatch,
        dispatch_stream=dispatch_stream,
        settings_store=chat_settings_store,
    )
    register_video_routes(
        app,
        services,
        dispatch,
        settings_store=video_settings_store,
        provider_factory=video_provider_factory,
        workflow_store=video_workflow_store,
        asset_store=video_asset_store,
        run_service=video_run_service,
        scheduler_enabled=video_scheduler_enabled,
        scheduler_interval=float(video_scheduler_interval),
        ai_services_store=ai_services_store,
    )
    register_service_center_routes(app, services, dispatch, service_center_store)
    register_github_trending_routes(
        app,
        services,
        dispatch,
        store=github_trending_store,
        avatar_proxy=github_trending_avatar_proxy,
        scheduler_enabled=github_trending_scheduler_enabled,
    )
    register_plans_routes(
        app,
        services,
        dispatch,
        store=plans_store,
        schedule_store=schedule_store,
        ai_services_store=ai_services_store,
    )
    register_journal_routes(app, services, dispatch, store=journal_store)
    from .chat_workspace_routes import register_workspace_routes

    register_workspace_routes(
        app,
        services,
        dispatch,
        dispatch_stream,
        store=chat_workspace_store,
        settings=chat_workspace_settings or chat_settings_store,
        journal=journal_store,
    )
    if ai_services_store is not None:
        register_ai_service_routes(
            app,
            services,
            dispatch,
            store=ai_services_store,
            openai_models_fetcher=openai_models_fetcher,
            media_provider_factory=media_provider_factory,
        )
    # The agent reads the plain chat settings file, as it did inside the monolith.
    intent_options = {'settings_store': agent_settings_store, 'token_file': internal_token_file}
    if agent_intent_completer is not None:
        intent_options['completer'] = agent_intent_completer
    register_agent_intent_routes(app, dispatch, **intent_options)
    if react_dist is not None:
        register_react_document_routes(app, services, dispatch, react_dist)
    return app
