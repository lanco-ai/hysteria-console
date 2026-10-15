"""Exercise real TLS listeners, canonical documents and same-origin APIs."""
import http.client
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'upstream': self.server.owner, 'method': self.command,
                           'port': self.headers.get('X-Forwarded-Port'),
                           'origin': self.headers.get('Origin')}).encode()
        length = int(self.headers.get('Content-Length', '0'))
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    do_POST = do_GET
    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def request(port, path, method='GET'):
    conn = http.client.HTTPSConnection('127.0.0.1', port,
                                      context=ssl._create_unverified_context(), timeout=3)
    origin = 'https://ports.test' + (f':{port}' if port != 19444 else '')
    conn.request(method, path, headers={'Host': origin.removeprefix('https://'), 'Origin': origin})
    response = conn.getresponse()
    data = response.read()
    result = response.status, response.getheader('Location'), json.loads(data) if response.status == 200 and data else None
    conn.close()
    return result


@pytest.mark.skipif(not shutil.which('nginx'), reason='nginx is needed for real listener integration')
@pytest.mark.parametrize('template', ['hysteria-panel-https.conf', 'hysteria-panel-react-https.conf'])
def test_three_https_sites_preserve_login_api_and_download_boundaries(tmp_path, template):
    servers = []
    process = None
    try:
        text = (ROOT / 'nginx' / template).read_text()
        for owner in (8083, 8084, 8085):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
            server.owner = owner
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            text = text.replace(f'http://127.0.0.1:{owner}', f'http://127.0.0.1:{server.server_port}')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout',
                        str(tmp_path / 'key'), '-out', str(tmp_path / 'cert'), '-subj', '/CN=ports.test',
                        '-days', '1'], check=True, capture_output=True)
        replacements = {'__HY_HTTPS_PORT__': '19444', '__HY_SERVER_HOST__': 'ports.test',
                        '__HY_TLS_CERT__': str(tmp_path / 'cert'), '__HY_TLS_KEY__': str(tmp_path / 'key'),
                        '/var/log/nginx/hysteria-panel.access.log': str(tmp_path / 'access.log'),
                        '/var/log/nginx/hysteria-panel.error.log': str(tmp_path / 'error.log')}
        for old, new in replacements.items():
            text = text.replace(old, new)
        config = tmp_path / 'nginx.conf'
        config.write_text(f'''daemon off; master_process off; pid {tmp_path}/pid;
error_log {tmp_path}/nginx.log; events {{}} http {{
log_format hy2_no_args "$request_method $uri $status";
limit_conn_zone $binary_remote_addr zone=hy2_panel_conn:1m;
limit_req_zone $binary_remote_addr zone=hy2_panel_req:1m rate=10000r/s;
{text}
}}
''')
        subprocess.run(['nginx', '-t', '-p', str(tmp_path)+'/', '-c', str(config)], check=True, capture_output=True)
        process = subprocess.Popen(['nginx', '-p', str(tmp_path)+'/', '-c', str(config)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(.15)
        assert process.poll() is None, 'test listener did not start; do not contact another process'
        for port in (19444, 8444, 8445):
            for path, owner in [('/api/v1/login',8083),('/api/v1/session',8083),('/api/plans',8085),
                                ('/api/chat/completions',8085),('/api/v1/shop/admin',8084)]:
                for method in ('GET','POST'):
                    status, location, body = request(port, path, method)
                    assert (status, location, body['upstream']) == (200, None, owner)
                    assert body['port'] == ('443' if port == 19444 else str(port))
                    assert body['origin'].endswith('ports.test' + (f':{port}' if port != 19444 else ''))
            for path in ('/sub/opaque', '/panel/opaque', '/admin/usage.json', '/admin/user/alice.json'):
                assert request(port,path)[0] == 200
            assert request(port,'/internal/v1/agent-intent','POST')[0] == 404
            for path, target, owner in [('/admin',8444,8083),('/admin/usage?range=7d',8444,8083),
                                         ('/admin/user/alice',8444,8083),('/user/panel',8444,8083),
                                         ('/admin/plans',8445,8085),('/trending?period=weekly',8445,8085),
                                         ('/admin/shop',19444,8084)]:
                status, location, body = request(port,path)
                if port == target:
                    assert (status,body['upstream']) == (200,owner)
                else:
                    expected = 'https://ports.test'+(f':{target}' if target!=19444 else '')+path
                    assert (status,location) == (302,expected)
                    assert request(target,urlsplit(location).path+'?'+urlsplit(location).query)[2]['upstream'] == owner
                assert request(port,path,'POST')[0] == 200, 'do not redirect legacy write requests'
        assert request(19444,'/')[2]['upstream'] == 8084
        assert request(8444,'/')[:2] == (302,'https://ports.test:8444/admin')
        assert request(8445,'/')[:2] == (302,'https://ports.test:8445/admin/plans')
        assert request(19444,'/admin','HEAD')[:2] == (302,'https://ports.test:8444/admin')
    finally:
        if process:
            process.terminate()
            process.wait(timeout=5)
        for server in servers:
            server.shutdown()
            server.server_close()
