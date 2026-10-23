"""End-to-end dynamic quotes using a local, controllable fictional source."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import react_preview_server as preview
from run_shop_gemini_browser import gemini_feed
from run_shop_duolingo_browser import feed as duolingo_feed
from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore


def run_case(product_id):
    with tempfile.TemporaryDirectory(prefix='gemini-pricing-test-') as directory:
        quote_path = Path(directory) / 'quote.json'
        quote_path.write_text(json.dumps({'cost': 209 if product_id == '2000000000003' else 756, 'generation': 0}))

        def quote():
            return json.loads(quote_path.read_text())

        async def feed():
            value = quote()
            if value.get('fail'):
                raise SourceError('fictional unavailable source')
            return [{**row, 'cost_cents': value['cost'], 'available': True}
                    for row in await gemini_feed() + await duolingo_feed()]

        class PricingStore(ShopStore):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, prodseller_fetcher=feed,
                                 clock=lambda: 1_790_000_000 + quote()['generation'] * 901)

        preview.ShopStore = PricingStore
        with preview.preview_server() as server:
            subprocess.run(['node', 'tests/react_shop_dynamic_pricing_browser.cjs'], check=True, timeout=180,
                           env=dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                                    REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
                                    PRICING_TEST_PRODUCT=product_id, GEMINI_TEST_QUOTE_FILE=str(quote_path)))


def main():
    for product_id in ['2000000000001', '2000000000003']:
        run_case(product_id)


if __name__ == '__main__':
    main()
