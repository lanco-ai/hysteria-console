"""Side-effect-free FastAPI application factory."""

import threading
from functools import partial
from types import SimpleNamespace

import anyio
import auth_views
import http_utils
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException

from .models import (
    AdminLogsResponse,
    AdminOverviewResponse,
    AdminSessionResponse,
    LoginFailureResponse,
    LoginSuccessResponse,
    UserSessionResponse,
)
from .requests import FormReadTimeout, RequestHeaders, read_form
from .services import LoginRequired, StateUnavailable, UserAccessDenied

_API_SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    **http_utils.SECURITY_HEADERS,
}


class _CrossSiteRequest(Exception):
    pass


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


def _session_response(payload):
    if isinstance(payload, dict) and payload.get('role') == 'admin':
        model = AdminSessionResponse.model_validate(payload)
    else:
        model = UserSessionResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _overview_response(payload):
    model = AdminOverviewResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _logs_response(payload):
    model = AdminLogsResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _login_response(reply):
    result = reply.result
    if result.outcome == 'success':
        model = LoginSuccessResponse(ok=True, redirect_to=result.redirect_to)
        headers = {'Set-Cookie': reply.cookie} if reply.cookie is not None else None
        return JSONResponse(model.model_dump(), headers=headers)
    model = LoginFailureResponse(
        ok=False,
        message=auth_views.login_feedback_message(result.outcome, realm=result.realm),
    )
    status = 429 if result.outcome == 'throttled' else 200
    headers = {'Retry-After': str(result.retry_after)} if result.outcome == 'throttled' else None
    return JSONResponse(status_code=status, content=model.model_dump(), headers=headers)


def _read_error_response(exc):
    if isinstance(exc, LoginRequired):
        return JSONResponse(status_code=401, content={'error': 'login_required'})
    if isinstance(exc, UserAccessDenied):
        return JSONResponse(status_code=403, content={'error': exc.code})
    if isinstance(exc, StateUnavailable):
        return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
    raise exc


def create_app(services, *, max_requests=32):
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
        raise ValueError('max_requests must be a positive integer')

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
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

    async def prepare_login(request, headers):
        origin_request = SimpleNamespace(headers=headers)
        if not http_utils.is_same_origin_post(origin_request):
            raise _CrossSiteRequest
        form = await read_form(request, headers)
        client_address = request.scope.get('client') or ('', 0)
        return {
            'form': form,
            'client_address': tuple(client_address),
        }

    @app.post('/api/v1/login')
    async def login(request: Request):
        try:
            reply = await dispatch(
                services.submit_login,
                request,
                prepare=prepare_login,
            )
        except _CrossSiteRequest:
            return JSONResponse(status_code=403, content={'error': 'cross_site_request'})
        except http_utils.RequestTooLarge:
            return JSONResponse(status_code=413, content={'error': 'request_too_large'})
        except http_utils.BadRequest:
            return JSONResponse(status_code=400, content={'error': 'bad_request'})
        except FormReadTimeout:
            return JSONResponse(status_code=408, content={'error': 'request_timeout'})
        except StateUnavailable:
            return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
        if isinstance(reply, JSONResponse):
            return reply
        return _login_response(reply)

    @app.api_route('/api/v1/session', methods=['GET', 'HEAD'])
    async def session(request: Request):
        try:
            payload = await dispatch(services.read_session, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _session_response(payload)

    @app.api_route('/api/v1/admin/overview', methods=['GET', 'HEAD'])
    async def admin_overview(request: Request):
        try:
            payload = await dispatch(services.read_admin_overview, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _overview_response(payload)

    @app.api_route('/api/v1/admin/logs', methods=['GET', 'HEAD'])
    async def admin_logs(request: Request):
        try:
            payload = await dispatch(services.read_admin_logs, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _logs_response(payload)

    return app
