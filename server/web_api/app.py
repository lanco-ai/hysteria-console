"""Side-effect-free FastAPI application factory for the shop service."""

import threading
from contextlib import asynccontextmanager
from functools import partial

import anyio
import http_utils
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException

from .document_routes import register_react_document_routes
from .requests import RequestHeaders
from .shop_routes import register_shop_routes

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
    shop_store=None,
    shop_scheduler_enabled=False,
):
    """Build the shop app.

    ``services`` only needs ``read_session(headers=, path=)``; production passes
    ``RemoteSessionServices`` so the network service stays the session authority.
    """

    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
        raise ValueError('max_requests must be a positive integer')
    if not isinstance(shop_scheduler_enabled, bool):
        raise ValueError('shop_scheduler_enabled must be a boolean')

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

    @app.api_route('/healthz', methods=['GET', 'HEAD'], include_in_schema=False)
    async def healthz():
        return JSONResponse({'ok': True, 'service': 'shop'})

    register_shop_routes(
        app, services, dispatch, store=shop_store, scheduler_enabled=shop_scheduler_enabled
    )
    if react_dist is not None:
        register_react_document_routes(app, services, dispatch, react_dist)
    return app
