"""Private procurement endpoints; no GET performs a purchase or reveals delivered keys."""

import asyncio
from contextlib import asynccontextmanager

import state_store
from fastapi import Request
from fastapi.responses import JSONResponse

from .shop_procurement import Procurement


def register_procurement_routes(app, store, guard, body, reply, *, scheduler_enabled=False, manager=None):
    manager = manager or Procurement(store, scheduler_enabled=scheduler_enabled)

    async def call(fn, *args):
        return await reply(lambda: asyncio.to_thread(fn, *args))

    @app.get('/api/v1/shop/admin/procurement')
    async def overview(request: Request):
        denied = await guard(request)
        return denied if denied is not None else await call(manager.view)

    @app.post('/api/v1/shop/admin/procurement/balance')
    async def balance(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        if isinstance(payload, JSONResponse):
            return payload
        if payload != {}:
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        return await call(manager.balance)

    @app.post('/api/v1/shop/admin/procurement/preorders')
    async def create(request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        return payload if isinstance(payload, JSONResponse) else await call(manager.create, payload)

    @app.post('/api/v1/shop/admin/procurement/preorders/{job_id}/{action}')
    async def action(job_id: str, action: str, request: Request):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        return payload if isinstance(payload, JSONResponse) else await call(manager.action, job_id, action, payload)

    async def worker():
        while True:
            try:
                await asyncio.to_thread(manager.tick)
            except (OSError, state_store.StateStoreError, ValueError):
                # Never log raw upstream replies or delivery content. Durable intent prevents retry.
                pass
            await asyncio.sleep(15)

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            task = asyncio.create_task(worker()) if scheduler_enabled else None
            try:
                yield state
            finally:
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
