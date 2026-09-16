"""FastAPI transport for Hysteria's loopback HTTP authentication contract."""

import time
from functools import partial

import anyio
import auth_backend
import auth_service
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


_AUTH_METHODS = ['GET', 'POST', 'DELETE', 'HEAD', 'OPTIONS', 'PATCH', 'PUT']


def _json(payload, status_code=200, *, allow=None):
    headers = {'Allow': allow} if allow else None
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def _content_type_is_json(request: Request) -> bool:
    raw = request.headers.get('content-type', '')
    parts = [part.strip().lower() for part in raw.split(';')]
    if not parts or parts[0] != 'application/json':
        return False
    return all(part == 'charset=utf-8' for part in parts[1:])


def register_auth_routes(app):
    """Register `/auth` and readiness endpoints on the shared application."""

    router = APIRouter()
    limiter = auth_service.PasswordWorkLimiter()

    @router.api_route('/livez', methods=['GET', 'HEAD'])
    async def livez():
        return _json({'ok': True})

    @router.api_route('/readyz', methods=['GET', 'HEAD'])
    async def readyz():
        deadline = time.monotonic() + auth_service.READY_DEADLINE_SECONDS
        ready = await anyio.to_thread.run_sync(
            partial(auth_backend.deep_authorization_state_ready, deadline=deadline)
        )
        return _json({'ok': bool(ready)}, 200 if ready else 503)

    @router.api_route('/healthz', methods=['GET', 'HEAD'])
    async def healthz():
        deadline = time.monotonic() + auth_service.READY_DEADLINE_SECONDS
        ready = await anyio.to_thread.run_sync(
            partial(auth_backend.deep_authorization_state_ready, deadline=deadline)
        )
        return _json({'ok': bool(ready)}, 200 if ready else 503)

    @router.api_route('/auth', methods=_AUTH_METHODS)
    async def auth(request: Request):
        if request.method != 'POST':
            return _json({'ok': False}, 405, allow='GET, POST')
        if request.headers.get('transfer-encoding') is not None:
            return _json({'ok': False}, 400)
        lengths = request.headers.getlist('content-length')
        if len(lengths) != 1:
            return _json({'ok': False}, 411)
        raw_length = lengths[0].strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            return _json({'ok': False}, 400)
        content_length = int(raw_length)
        if content_length > auth_service.MAX_BODY_BYTES:
            return _json({'ok': False}, 413)
        if content_length <= 0:
            return _json({'ok': False}, 400)
        if not _content_type_is_json(request):
            return _json({'ok': False}, 415)
        try:
            with anyio.fail_after(auth_service.READ_TIMEOUT_SECONDS):
                body = await request.body()
        except TimeoutError:
            return _json({'ok': False}, 408)
        if len(body) != content_length:
            return _json({'ok': False}, 400)
        try:
            decoded = auth_service.decode_auth_request(body)
        except (auth_service.InvalidRequest, UnicodeError):
            return _json({'ok': False}, 400)

        deadline = time.monotonic() + auth_service.REQUEST_DEADLINE_SECONDS
        try:
            user_id = await anyio.to_thread.run_sync(
                partial(
                    auth_backend.authenticate_payload,
                    decoded.auth,
                    addr=decoded.addr,
                    source=decoded.source,
                    deadline=deadline,
                    password_limiter=limiter,
                )
            )
        except Exception:
            # Authentication dependencies fail closed without exposing details.
            user_id = None
        payload = {'ok': user_id is not None}
        if user_id is not None:
            payload['id'] = user_id
        return _json(payload)

    app.include_router(router)
