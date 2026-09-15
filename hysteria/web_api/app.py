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

from .account_routes import register_account_routes
from .models import (
    AdminLogsResponse,
    AdminOverviewResponse,
    AdminPasswordChangeValidationResponse,
    AdminSessionResponse,
    LoginFailureResponse,
    LoginSuccessResponse,
    LogoutResponse,
    PasswordChangeAccessErrorResponse,
    PasswordChangeSuccessResponse,
    PasswordPageResponse,
    UserPasswordChangeValidationResponse,
    UserSessionResponse,
)
from .operation_routes import register_operation_routes
from .overview_models import AdminOverviewPageResponse
from .requests import FormReadTimeout, RequestHeaders, read_form
from .services import LoginRequired, StateUnavailable, UserAccessDenied
from .usage_models import (
    AdminHealthResponse,
    AdminUsageHistoryResponse,
    AdminUsageResponse,
    AdminUsageSummaryResponse,
)

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


def _overview_page_response(payload):
    model = AdminOverviewPageResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _logs_response(payload):
    model = AdminLogsResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _usage_response(payload, *, include_charts):
    model_class = AdminUsageResponse if include_charts else AdminUsageSummaryResponse
    model = model_class.model_validate(payload)
    return JSONResponse(model.model_dump())


def _usage_history_response(payload):
    model = AdminUsageHistoryResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _health_response(payload):
    model = AdminHealthResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _password_page_response(payload):
    model = PasswordPageResponse.model_validate(payload)
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


def _logout_response(reply):
    model = LogoutResponse(ok=True, redirect_to='/login')
    return JSONResponse(model.model_dump(), headers={'Set-Cookie': reply.cookie})


def _password_change_response(reply, *, realm):
    if realm not in ('admin', 'user'):
        raise ValueError('invalid password-change realm')
    result = reply.result
    if result.outcome == 'success':
        if reply.cookie is None or result.code:
            raise ValueError('invalid password-change success result')
        destination = '/admin/settings?msg=password+changed' if realm == 'admin' else '/user/panel'
        model = PasswordChangeSuccessResponse(ok=True, redirect_to=destination)
        return JSONResponse(model.model_dump(), headers={'Set-Cookie': reply.cookie})
    if result.outcome == 'invalid':
        if reply.cookie is not None:
            raise ValueError('invalid password-change validation result')
        model_class = (
            AdminPasswordChangeValidationResponse
            if realm == 'admin'
            else UserPasswordChangeValidationResponse
        )
        model = model_class(ok=False, code=result.code)
        return JSONResponse(model.model_dump())
    if result.outcome == 'login_required':
        if reply.cookie is not None or result.code:
            raise ValueError('invalid password-change login result')
        model = PasswordChangeAccessErrorResponse(error='login_required')
        return JSONResponse(status_code=401, content=model.model_dump())
    if realm == 'user' and result.outcome in ('forbidden', 'disabled', 'expired'):
        if result.code:
            raise ValueError('invalid password-change access result')
        if (result.outcome == 'forbidden') != (reply.cookie is not None):
            raise ValueError('invalid password-change access cookie')
        model = PasswordChangeAccessErrorResponse(error=result.outcome)
        headers = {'Set-Cookie': reply.cookie} if reply.cookie is not None else None
        return JSONResponse(status_code=403, content=model.model_dump(), headers=headers)
    raise ValueError('invalid password-change result')


def _read_error_response(exc):
    if isinstance(exc, LoginRequired):
        return JSONResponse(status_code=401, content={'error': 'login_required'})
    if isinstance(exc, UserAccessDenied):
        headers = {'Set-Cookie': exc.cookie} if exc.cookie is not None else None
        return JSONResponse(status_code=403, content={'error': exc.code}, headers=headers)
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

    async def prepare_form_write(request, headers):
        origin_request = SimpleNamespace(headers=headers)
        if not http_utils.is_same_origin_post(origin_request):
            raise _CrossSiteRequest
        form = await read_form(request, headers)
        client_address = request.scope.get('client') or ('', 0)
        return {
            'form': form,
            'client_address': tuple(client_address),
        }

    async def dispatch_form_write(function, request, response_builder, **fixed_arguments):
        try:
            reply = await dispatch(
                partial(function, **fixed_arguments),
                request,
                prepare=prepare_form_write,
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
        return response_builder(reply)

    register_account_routes(app, services, dispatch_form_write)
    register_operation_routes(app, services, dispatch_form_write, dispatch)

    @app.post('/api/v1/login')
    async def login(request: Request):
        return await dispatch_form_write(
            services.submit_login,
            request,
            _login_response,
        )

    @app.post('/api/v1/logout')
    async def logout(request: Request):
        return await dispatch_form_write(
            services.submit_logout,
            request,
            _logout_response,
            realm='admin',
        )

    @app.post('/api/v1/user/logout')
    async def user_logout(request: Request):
        return await dispatch_form_write(
            services.submit_logout,
            request,
            _logout_response,
            realm='user',
        )

    @app.post('/api/v1/admin/change-password')
    async def admin_change_password(request: Request):
        return await dispatch_form_write(
            services.submit_password_change,
            request,
            partial(_password_change_response, realm='admin'),
            realm='admin',
        )

    @app.post('/api/v1/user/change-password')
    async def user_change_password(request: Request):
        return await dispatch_form_write(
            services.submit_password_change,
            request,
            partial(_password_change_response, realm='user'),
            realm='user',
        )

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

    @app.api_route('/api/v1/admin/overview-page', methods=['GET', 'HEAD'])
    async def admin_overview_page(request: Request):
        try:
            payload = await dispatch(services.read_admin_overview_page, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _overview_page_response(payload)

    @app.api_route('/api/v1/admin/usage', methods=['GET', 'HEAD'])
    async def admin_usage(request: Request):
        summary = request.query_params.get('summary', '').strip().lower()
        include_charts = summary not in {'1', 'true', 'yes'}
        try:
            payload = await dispatch(
                partial(services.read_admin_usage, include_charts=include_charts),
                request,
            )
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _usage_response(payload, include_charts=include_charts)

    @app.api_route('/api/v1/admin/usage-history', methods=['GET', 'HEAD'])
    async def admin_usage_history(request: Request):
        try:
            payload = await dispatch(services.read_admin_usage_history, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _usage_history_response(payload)

    @app.api_route('/api/v1/admin/health', methods=['GET', 'HEAD'])
    async def admin_health(request: Request):
        try:
            payload = await dispatch(services.read_admin_health, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _health_response(payload)

    @app.api_route('/api/v1/admin/settings', methods=['GET', 'HEAD'])
    async def admin_settings(request: Request):
        try:
            payload = await dispatch(services.read_admin_settings, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _password_page_response(payload)

    @app.api_route('/api/v1/user/password', methods=['GET', 'HEAD'])
    async def user_password(request: Request):
        try:
            payload = await dispatch(services.read_user_password, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _password_page_response(payload)

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
