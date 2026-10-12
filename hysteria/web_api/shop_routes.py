"""Anonymous retail projection and session/CSRF guarded merchant operations."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import http_utils
import state_store
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .services import LoginRequired, StateUnavailable, UserAccessDenied
from .shop_anli_source import fetch_catalog as fetch_anli_catalog
from .shop_fanai_source import fetch_catalog as fetch_fanai_catalog
from .shop_lorry_source import configured as lorry_configured
from .shop_lorry_source import fetch_catalog as fetch_lorry_catalog
from .shop_prodseller_source import configured as prodseller_configured
from .shop_prodseller_source import fetch_catalog as fetch_prodseller_catalog
from .shop_store import Conflict, ShopStore


def register_shop_routes(app, services, dispatch, *, store=None, scheduler_enabled=False):
    # The Gemini and X / Muse AI feeds need reseller API credentials; without
    # them each feed is simply absent.
    store = store or ShopStore(
        secondary_fetcher=fetch_anli_catalog,
        prodseller_fetcher=fetch_prodseller_catalog if prodseller_configured() else None,
        lorry_fetcher=fetch_lorry_catalog if lorry_configured() else None,
        fanai_fetcher=fetch_fanai_catalog,
    )

    async def scheduler():
        while True:
            try:
                await store.refresh()
            except (OSError, state_store.StateStoreError, ValueError):
                pass
            await asyncio.sleep(60)

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            task = asyncio.create_task(scheduler()) if scheduler_enabled else None
            try:
                yield state
            finally:
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

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

    async def reply(operation):
        try:
            return JSONResponse(await operation(), headers={'Cache-Control': 'no-store'})
        except Conflict:
            return JSONResponse({'error': 'revision_conflict'}, status_code=409)
        except (OSError, state_store.StateStoreError):
            return JSONResponse({'error': 'storage_unavailable'}, status_code=503)
        except (ValueError, TypeError):
            return JSONResponse({'error': 'invalid_settings'}, status_code=422)

    async def body(request):
        if not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers)):
            return JSONResponse({'error': 'cross_site_request'}, status_code=403)
        data = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 65536:
                        return JSONResponse({'error': 'body_too_large'}, status_code=413)
            return json.loads(data)
        except (ValueError, UnicodeError):
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        except TimeoutError:
            return JSONResponse({'error': 'request_timeout'}, status_code=408)

    @app.get('/api/v1/shop/catalog')
    async def catalog():
        return await reply(store.public)

    @app.post('/api/v1/shop/visit')
    async def visit(request: Request):
        # Anonymous, like the catalog: the storefront reports a page view; counting is best effort.
        payload = await body(request)
        if isinstance(payload, JSONResponse):
            return payload
        product = payload.get('product') if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) - {'product'}
            or (product is not None and not (isinstance(product, str) and re.fullmatch(r'[1-9]\d{0,15}', product)))
        ):
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        peer = request.client.host if request.client else ''
        address = http_utils.request_client_ip(SimpleNamespace(client_address=(peer, 0), headers=request.headers))
        try:
            await store.visit(address, product)
        except (OSError, state_store.StateStoreError, ValueError):
            pass
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})

    @app.get('/api/v1/shop/admin')
    async def admin(request: Request):
        denied = await guard(request)
        return denied if denied is not None else await reply(store.admin)

    @app.put('/api/v1/shop/admin')
    async def update(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        if isinstance(payload, JSONResponse):
            return payload
        return await reply(lambda: store.update(payload))

    @app.post('/api/v1/shop/refresh')
    async def refresh(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        if isinstance(payload, JSONResponse):
            return payload
        if payload != {}:
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        return await reply(lambda: store.refresh(manual=True))
