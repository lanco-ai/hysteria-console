"""Public discovery reads, an administrator-only refresh, and lifecycle-owned tasks."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import http_utils
import state_store
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .github_trending_avatar import AvatarProxy, valid_owner
from .github_trending_source import PERIODS
from .github_trending_store import TrendingStore
from .services import LoginRequired, StateUnavailable, UserAccessDenied


def register_github_trending_routes(
    app, services, dispatch, *, store=None, scheduler_enabled=False, avatar_proxy=None
):
    store = store or TrendingStore()
    avatar_proxy = avatar_proxy or AvatarProxy()
    tasks = {}

    async def run(period, manual):
        try:
            await store.refresh(period, manual=manual)
        except (OSError, state_store.StateStoreError):
            # Optional discovery must not take down the scheduler or application.
            pass

    def start(period, manual=False):
        if period not in tasks or tasks[period].done():
            tasks[period] = asyncio.create_task(run(period, manual))

    async def scheduler():
        while True:
            for period in PERIODS:
                start(period)
            await asyncio.sleep(60)

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            worker = asyncio.create_task(scheduler()) if scheduler_enabled else None
            try:
                yield state
            finally:
                pending = ([worker] if worker else []) + list(tasks.values())
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    app.router.lifespan_context = lifespan

    async def guard(request):
        try:
            session = await dispatch(services.read_session, request)
        except LoginRequired:
            return JSONResponse({'error': 'login_required'}, status_code=401)
        except UserAccessDenied:
            return JSONResponse({'error': 'admin_required'}, status_code=403)
        except StateUnavailable:
            return JSONResponse({'error': 'state_unavailable'}, status_code=503)
        if isinstance(session, JSONResponse):
            return session
        if not isinstance(session, dict) or session.get('role') != 'admin':
            return JSONResponse({'error': 'admin_required'}, status_code=403)
        return None

    async def reply(period, *, manual=False):
        try:
            value = await store.read(period)
            eligible = not value['refreshing'] and not value['retry_after_seconds']
            if eligible and (
                (manual and not value['cooldown_seconds']) or (not manual and value['is_stale'])
            ):
                start(period, manual)
                value['refreshing'] = True
            return JSONResponse(value)
        except (OSError, state_store.StateStoreError, ValueError, TypeError):
            return JSONResponse({'error': 'storage_unavailable'}, status_code=503)

    async def listed(owner):
        # Anonymous visitors may only fetch avatars of owners on a current board,
        # so the proxy cannot be used to fetch arbitrary GitHub accounts.
        for period in PERIODS:
            try:
                items = (await store.read(period))['items']
            except (OSError, state_store.StateStoreError, ValueError, TypeError):
                continue
            if any(item['full_name'].split('/')[0].lower() == owner.lower() for item in items):
                return True
        return False

    # The boards are public GitHub data, so reads need no session.
    @app.get('/api/v1/github-trending')
    async def get_trending(request: Request):
        period = request.query_params.get('period', 'weekly')
        if period not in PERIODS:
            return JSONResponse({'error': 'invalid_period'}, status_code=422)
        return await reply(period)

    @app.get('/api/v1/github-trending/avatar/{owner}')
    async def get_trending_avatar(owner: str):
        if not valid_owner(owner):
            return JSONResponse({'error': 'invalid_owner'}, status_code=422)
        if not await listed(owner):
            return JSONResponse({'error': 'avatar_unavailable'}, status_code=404)
        image = await avatar_proxy.get(owner)
        if image is None:
            return JSONResponse({'error': 'avatar_unavailable'}, status_code=502, headers={'Cache-Control': 'private, max-age=60'})
        body, media_type = image
        return Response(body, media_type=media_type, headers={'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'})

    @app.post('/api/v1/github-trending/refresh')
    async def refresh_trending(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        return JSONResponse({'error': 'body_too_large'}, status_code=413)
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        except TimeoutError:
            return JSONResponse({'error': 'request_timeout'}, status_code=408)
        if (
            not isinstance(payload, dict)
            or set(payload) != {'period'}
            or payload['period'] not in PERIODS
        ):
            return JSONResponse({'error': 'invalid_period'}, status_code=422)
        return await reply(payload['period'], manual=True)
