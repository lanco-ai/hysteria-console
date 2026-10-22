"""Private own-stock endpoints; only an explicit POST returns a stock line."""

import asyncio
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from .shop_inventory import ShopInventory
from .shop_store import STOCKED_CATEGORIES


def register_inventory_routes(app, store, guard, body, reply, *, inventory=None):
    inventory = inventory or ShopInventory(Path(store.path).with_name('inventory.json'), clock=store.clock)
    store.inventory = inventory

    def stocked():
        return {row['key'] for row in stock_rows()}

    def stock_rows():
        # Store-owned services are fulfilled manually, not as stocked accounts or codes.
        return [row for row in store._admin()['items']
                if row['provider'] != 'local' and row['category'] in STOCKED_CATEGORIES]

    def add_stock(payload):
        rows = stock_rows()
        if isinstance(payload, dict):
            inventory.remember([row for row in rows if row['key'] == payload.get('sku')])
        return inventory.add(payload, {row['key'] for row in rows})

    async def call(fn):
        return await reply(lambda: asyncio.to_thread(fn))

    async def post(request, operation):
        denied = await guard(request)
        if denied is not None:
            return denied
        payload = await body(request)
        return payload if isinstance(payload, JSONResponse) else await call(lambda: operation(payload))

    @app.get('/api/v1/shop/admin/inventory')
    async def overview(request: Request):
        denied = await guard(request)
        return denied if denied is not None else await call(inventory.view)

    @app.post('/api/v1/shop/admin/inventory/add')
    async def add(request: Request):
        return await post(request, add_stock)

    @app.post('/api/v1/shop/admin/inventory/deliver')
    async def deliver(request: Request):
        return await post(request, lambda payload: inventory.deliver(payload, stocked() | inventory.counts().keys()))

    @app.post('/api/v1/shop/admin/inventory/items/{item_id}/{action}')
    async def item(item_id: str, action: str, request: Request):
        if action not in {'reveal', 'remove', 'restock'}:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        return await post(request, lambda payload: inventory.action(item_id, action, payload))

    return inventory
