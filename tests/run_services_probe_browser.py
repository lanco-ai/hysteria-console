"""Browser -> real authenticated API -> bounded probe -> fake upstream."""
import os
import subprocess
from pathlib import Path

import httpx
import react_preview_server as preview
import web_api.service_center as center
from web_api.service_probe import run_probe


def respond(request):
    if request.headers.get('authorization') != 'Bearer sk-valid-for-test':
        return httpx.Response(401, json={'error': 'invalid key'})
    if request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
    return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}]})


async def fake_upstream(value):
    return await run_probe(value, transport=httpx.MockTransport(respond))


if __name__ == '__main__':
    center.run_probe = fake_upstream
    if os.environ.get('SERVICES_TEST_DIST'):
        preview.DIST = Path(os.environ['SERVICES_TEST_DIST'])
    with preview.preview_server() as server:
        subprocess.run(['node', 'tests/react_services_probe_browser.cjs'],
                       env=dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                                REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie),
                       check=True, timeout=75)
