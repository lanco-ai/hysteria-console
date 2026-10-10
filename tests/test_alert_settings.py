"""设置 · 告警通知: console-managed alert channels with write-only secrets."""

import http.server
import io
import json
import shutil
import ssl
import stat
import subprocess
import threading
import urllib.request
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import alerts
import state_store
import subscription_service as ss
from web_api import create_app
from web_api.services import LegacyPanelServices

TOKEN = '123456789:' + 'A' * 35
OTHER_TOKEN = '987654321:' + 'b' * 35
FORM = {
    'telegram_enabled': '1', 'telegram_bot_token': TOKEN, 'telegram_chat_id': '-1001234',
    'webhook_enabled': '', 'webhook_url': '', 'webhook_secret': '', 'webhook_secret_clear': '',
    'anomaly_z_threshold': '3', 'anomaly_min_gib': '1',
}


def test_public_config_reports_channels_without_secrets():
    cfg = {
        'telegram': {'bot_token': TOKEN, 'chat_id': '42'},
        'webhook': {'url': 'https://hooks.example.test/a?key=hidden', 'secret': 'shh'},
        'anomaly_z_threshold': 2.5,
        'anomaly_min_bytes': 3 << 30,
    }
    public = alerts.public_config(cfg)
    assert public == {
        'telegram': {'configured': True, 'chat_id': '42'},
        'webhook': {'configured': True, 'host': 'hooks.example.test', 'signed': True},
        'anomaly_z_threshold': 2.5,
        'anomaly_min_gib': 3.0,
    }
    assert TOKEN not in json.dumps(public) and 'hidden' not in json.dumps(public) and 'shh' not in json.dumps(public)
    assert alerts.public_config(None)['telegram'] == {'configured': False, 'chat_id': ''}


def test_updated_config_keeps_blank_secrets_and_preserves_unmanaged_keys():
    current = {
        'telegram': {'bot_token': TOKEN, 'chat_id': '42'},
        'webhook': {'url': 'https://hooks.example.test/a', 'secret': 'shh'},
        'custom': {'kept': True},
    }
    values = {
        'telegram_enabled': True, 'telegram_bot_token': '', 'telegram_chat_id': '@ops_channel',
        'webhook_enabled': True, 'webhook_url': '', 'webhook_secret': '',
        'anomaly_z_threshold': '4', 'anomaly_min_gib': '0.5',
    }
    result = alerts.updated_config(current, values)
    assert result['telegram'] == {'bot_token': TOKEN, 'chat_id': '@ops_channel'}
    assert result['webhook'] == {'url': 'https://hooks.example.test/a', 'secret': 'shh'}
    assert result['custom'] == {'kept': True}
    assert (result['anomaly_z_threshold'], result['anomaly_min_bytes']) == (4.0, 1 << 29)
    cleared = alerts.updated_config(current, {**values, 'webhook_secret_clear': True})
    assert cleared['webhook'] == {'url': 'https://hooks.example.test/a'}
    # Turning a channel off removes it.
    off = alerts.updated_config(current, {**values, 'telegram_enabled': False, 'webhook_enabled': False})
    assert 'telegram' not in off and 'webhook' not in off


@pytest.mark.parametrize(('changes', 'code'), [
    ({'telegram_bot_token': 'not-a-token'}, 'telegram_token_invalid'),
    ({'telegram_chat_id': 'chat id'}, 'telegram_chat_invalid'),
    ({'webhook_enabled': True, 'webhook_url': 'http://hooks.example.test/a'}, 'webhook_url_invalid'),
    ({'webhook_enabled': True, 'webhook_url': 'https://user:pass@hooks.example.test/'}, 'webhook_url_invalid'),
    ({'webhook_enabled': True, 'webhook_url': 'https://hooks.example.test:99999/'}, 'webhook_url_invalid'),
    ({'webhook_enabled': True, 'webhook_url': 'https://hooks.example.test/', 'webhook_secret': 'x' * 257}, 'webhook_secret_invalid'),
    # Alerts are sent from the server, so this host and private networks are refused.
    *[({'webhook_enabled': True, 'webhook_url': url}, 'webhook_url_private') for url in (
        'https://127.0.0.1:8083/hook', 'https://[::1]/hook', 'https://10.0.0.1/hook', 'https://192.168.1.2/',
        'https://169.254.169.254/latest', 'https://100.64.0.1/', 'https://0.0.0.0/', 'https://[::ffff:127.0.0.1]/',
        'https://[fe80::1]/', 'https://localhost/hook', 'https://LocalHost./hook', 'https://hooks.localhost/',
    )],
    ({'anomaly_z_threshold': '0.5'}, 'z_threshold_invalid'),
    ({'anomaly_min_gib': 'nan'}, 'min_gib_invalid'),
])
def test_updated_config_rejects_invalid_fields(changes, code):
    values = {
        'telegram_enabled': True, 'telegram_bot_token': TOKEN, 'telegram_chat_id': '42',
        'anomaly_z_threshold': '3', 'anomaly_min_gib': '1', **changes,
    }
    with pytest.raises(alerts.AlertConfigError) as error:
        alerts.updated_config({}, values)
    assert error.value.code == code


def test_public_webhook_hosts_are_accepted():
    for url in ('https://hooks.example.test/a?key=1', 'https://203.0.113.9.nip.io/', 'https://8.8.8.8:8443/x'):
        assert alerts.webhook_url_problem(url) == ''


class FakeResponse:
    def __init__(self, status, body=b'ok'):
        self.status = status
        self.body = body

    def read(self, _limit=None):
        return self.body


class FakeConnection:
    instances = []

    def __init__(self, host, port, *, addresses, timeout, status=200):
        self.host, self.port, self.addresses, self.timeout = host, port, addresses, timeout
        self.status = status
        self.requests = []
        self.closed = False
        FakeConnection.instances.append(self)

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return FakeResponse(self.status)

    def close(self):
        self.closed = True


def answers(*addresses):
    return lambda host, port, type=None: [(None, None, None, '', (address, port)) for address in addresses]


def webhook_request(url='https://hooks.example.test/in?key=k'):
    return urllib.request.Request(url, data=b'{}', method='POST', headers={'X-Hy2-Signature': 'sha256=x'})


@pytest.mark.parametrize('addresses', [
    ('10.0.0.5',), ('127.0.0.1',), ('::1',), ('169.254.169.254',), ('::ffff:10.1.2.3',),
    # A name with any private answer is refused, so the connection cannot be steered to it.
    ('93.184.216.34', '192.168.0.10'), (),
])
def test_webhook_transport_refuses_names_resolving_to_private_addresses(addresses):
    FakeConnection.instances = []
    transport = alerts._WebhookTransport(resolve=answers(*addresses), connection=FakeConnection)
    with pytest.raises(OSError):
        transport.urlopen(webhook_request(), timeout=5)
    assert FakeConnection.instances == []


def test_webhook_transport_pins_the_checked_address_and_never_follows_redirects():
    FakeConnection.instances = []
    transport = alerts._WebhookTransport(resolve=answers('93.184.216.34', '93.184.216.35'), connection=FakeConnection)
    assert transport.urlopen(webhook_request(), timeout=5).read() == b'ok'
    connection = FakeConnection.instances[0]
    assert (connection.host, connection.port, connection.addresses, connection.timeout) == ('hooks.example.test', 443, ['93.184.216.34', '93.184.216.35'], 5)
    method, path, body, headers = connection.requests[0]
    assert (method, path, body) == ('POST', '/in?key=k', b'{}')
    assert headers['X-hy2-signature'] == 'sha256=x' and connection.closed

    def redirecting(*args, **kwargs):
        return FakeConnection(*args, **kwargs, status=302)

    FakeConnection.instances = []
    transport = alerts._WebhookTransport(resolve=answers('93.184.216.34'), connection=redirecting)
    with pytest.raises(OSError):
        transport.urlopen(webhook_request(), timeout=5)
    assert len(FakeConnection.instances) == 1 and len(FakeConnection.instances[0].requests) == 1


def test_pinned_connection_dials_only_vetted_addresses_and_verifies_the_url_host(monkeypatch):
    dialed, wrapped = [], []
    sentinel = object()

    def create_connection(address, timeout):
        dialed.append((address, timeout))
        if address[0] == '2001:db8::1':
            raise OSError('network unreachable')
        return sentinel

    monkeypatch.setattr(alerts.socket, 'create_connection', create_connection)
    connection = alerts._PinnedHTTPSConnection('hooks.example.test', 8443, addresses=['2001:db8::1', '93.184.216.34'], timeout=5)
    assert connection._tls.verify_mode == alerts.ssl.CERT_REQUIRED and connection._tls.check_hostname
    connection._tls = SimpleNamespace(wrap_socket=lambda sock, server_hostname: wrapped.append((sock, server_hostname)) or 'tls')
    connection.connect()
    # An unreachable address falls through to the next vetted one, never to a fresh lookup.
    assert dialed == [(('2001:db8::1', 8443), 5), (('93.184.216.34', 8443), 5)]
    assert wrapped == [(sentinel, 'hooks.example.test')] and connection.sock == 'tls'


@pytest.fixture
def tls_receiver(tmp_path):
    """A local HTTPS receiver with a certificate for hooks.example.test."""
    if not shutil.which('openssl'):
        pytest.skip('openssl is not installed')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=hooks.example.test',
         '-addext', 'subjectAltName=DNS:hooks.example.test', '-keyout', str(key), '-out', str(cert)],
        check=True, capture_output=True,
    )
    received = []

    class Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            received.append((self.path, self.headers['Host'], self.headers['X-Hy2-Signature'], body))
            redirect = self.path.startswith('/moved')
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header('Location', 'http://127.0.0.1:8083/admin')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    trust = ssl.create_default_context(cafile=str(cert))

    def connection(host, port, *, addresses, timeout):
        # The guard vetted a public address; deliver to the local receiver while keeping real TLS checks.
        pinned = alerts._PinnedHTTPSConnection(host, server.server_address[1], addresses=['127.0.0.1'], timeout=timeout)
        pinned._tls = trust
        return pinned

    yield alerts._WebhookTransport(resolve=answers('93.184.216.34'), connection=connection), received
    server.shutdown()
    server.server_close()


def test_webhook_transport_delivers_over_verified_tls_without_following_redirects(tls_receiver):
    transport, received = tls_receiver
    assert transport.urlopen(webhook_request(), timeout=5).read() == b'ok'
    path, host, signature, body = received[0]
    assert (path, host.split(':')[0], signature, body) == ('/in?key=k', 'hooks.example.test', 'sha256=x', b'{}')
    with pytest.raises(OSError):
        transport.urlopen(webhook_request('https://hooks.example.test/moved'), timeout=5)
    assert [item[0] for item in received] == ['/in?key=k', '/moved']
    # The certificate must match the URL's host name.
    with pytest.raises(ssl.SSLCertVerificationError):
        transport.urlopen(webhook_request('https://other.example.test/in'), timeout=5)


def test_dispatch_refuses_a_hand_written_private_webhook_before_any_request():
    calls = []
    opener = SimpleNamespace(urlopen=lambda request, timeout: calls.append(request) or io.BytesIO(b'ok'))
    result = alerts.dispatch({'kind': 'test', 'user': 'admin'}, config={'webhook': {'url': 'https://127.0.0.1:8083/x'}}, opener=opener)
    assert result == {'attempted': ['webhook'], 'failed': ['webhook']}
    assert calls == []


def test_dispatch_sends_webhooks_through_the_guarded_transport(monkeypatch):
    seen = []
    monkeypatch.setattr(alerts, '_WEBHOOK_TRANSPORT', SimpleNamespace(urlopen=lambda request, timeout: seen.append(request.full_url) or io.BytesIO(b'ok')))
    result = alerts.dispatch({'kind': 'test', 'user': 'admin'}, config={'webhook': {'url': 'https://hooks.example.test/x'}})
    assert result == {'attempted': ['webhook'], 'failed': []}
    assert seen == ['https://hooks.example.test/x']


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, 'CONFIG_FILE', tmp_path / 'alerts.json')
    monkeypatch.setattr(alerts, 'CONFIG_LOCK_FILE', tmp_path / 'alerts.lock')
    session = {'admin': True, 'api_secret': 'fixture-server-secret'}
    module = SimpleNamespace(
        alerts=alerts,
        state_store=state_store,
        content_revision=ss.content_revision,
        get_hy_api_secret=lambda: session['api_secret'],
        is_logged_in=lambda _request: session['admin'],
        request_multiplier_snapshot=lambda function: function,
        _state_failure_requires_static_stop=lambda *_args, **_kwargs: False,
    )
    legacy = LegacyPanelServices(module)
    # Route through the real app with the real alert services, without the
    # rest of the panel the full LegacyPanelServices app would construct.
    services = SimpleNamespace(read_admin_alerts=legacy.read_admin_alerts, submit_alert_settings=legacy.submit_alert_settings)
    with TestClient(create_app(services), headers={'Origin': 'http://testserver'}) as test_client:
        yield test_client, session, tmp_path / 'alerts.json'


def test_alert_settings_round_trip_never_returns_secrets(client):
    http, session, path = client
    initial = http.get('/api/v1/admin/alerts')
    assert initial.status_code == 200
    assert initial.json()['telegram'] == {'configured': False, 'chat_id': ''}
    assert initial.headers['cache-control'] == 'no-store'

    saved = http.post('/api/v1/admin/alerts/save', data={**FORM, 'revision': initial.json()['revision']})
    assert saved.status_code == 200, saved.text
    assert saved.json()['telegram'] == {'configured': True, 'chat_id': '-1001234'}
    assert TOKEN not in saved.text
    assert json.loads(path.read_text())['telegram'] == {'bot_token': TOKEN, 'chat_id': '-1001234'}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    read = http.get('/api/v1/admin/alerts')
    assert TOKEN not in read.text and read.json()['revision'] == saved.json()['revision']

    # The old revision no longer matches; nothing is written.
    stale = http.post('/api/v1/admin/alerts/save', data={**FORM, 'telegram_bot_token': OTHER_TOKEN, 'revision': initial.json()['revision']})
    assert stale.status_code == 409
    assert json.loads(path.read_text())['telegram']['bot_token'] == TOKEN
    # A blank token keeps the stored one; an invalid chat is refused with its field code.
    kept = http.post('/api/v1/admin/alerts/save', data={**FORM, 'telegram_bot_token': '', 'revision': read.json()['revision']})
    assert kept.status_code == 200 and json.loads(path.read_text())['telegram']['bot_token'] == TOKEN
    invalid = http.post('/api/v1/admin/alerts/save', data={**FORM, 'telegram_chat_id': 'bad chat', 'revision': kept.json()['revision']})
    assert (invalid.status_code, invalid.json()['code']) == (422, 'telegram_chat_invalid')
    off = http.post('/api/v1/admin/alerts/save', data={**FORM, 'telegram_enabled': '', 'revision': kept.json()['revision']})
    assert off.json()['telegram']['configured'] is False and 'telegram' not in json.loads(path.read_text())

    assert http.post('/api/v1/admin/alerts/save', data=FORM, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    session['admin'] = False
    assert http.get('/api/v1/admin/alerts').status_code == 401
    assert http.post('/api/v1/admin/alerts/save', data={**FORM, 'revision': off.json()['revision']}).status_code == 401


def test_alert_revision_cannot_confirm_a_guessed_secret(client):
    http, session, path = client
    stored = {'webhook': {'url': 'https://hooks.example.test/hook', 'secret': 'weak-fixture'},
              'anomaly_z_threshold': 3.0, 'anomaly_min_bytes': 1 << 30}
    path.write_text(json.dumps(stored))
    revision = http.get('/api/v1/admin/alerts').json()['revision']
    # Everything else is known; trying candidate secrets offline must not single out the stored one.
    guesses = [ss.content_revision({**stored, 'webhook': {**stored['webhook'], 'secret': guess}}) for guess in ('wrong', 'weak-fixture')]
    assert revision not in guesses
    assert http.get('/api/v1/admin/alerts').json()['revision'] == revision

    # It still changes when only a secret changes, so concurrent secret edits conflict.
    saved = http.post('/api/v1/admin/alerts/save', data={
        **FORM, 'telegram_enabled': '', 'webhook_enabled': '1', 'webhook_secret': 'new-fixture', 'revision': revision,
    })
    assert saved.status_code == 200, saved.text
    assert json.loads(path.read_text())['webhook']['secret'] == 'new-fixture'
    assert saved.json()['revision'] != revision
    assert http.post('/api/v1/admin/alerts/save', data={**FORM, 'revision': revision}).status_code == 409

    # The key is the server's own secret: with another one the same file has another revision.
    session['api_secret'] = 'another-server-secret'
    assert http.get('/api/v1/admin/alerts').json()['revision'] != saved.json()['revision']


def test_unreadable_alert_config_is_reported_not_overwritten(client):
    http, _session, path = client
    path.write_text('{broken')
    assert http.get('/api/v1/admin/alerts').status_code == 503
    response = http.post('/api/v1/admin/alerts/save', data={**FORM, 'revision': 'x'})
    assert (response.status_code, response.json()['code']) == (422, 'config_unreadable')
    assert path.read_text() == '{broken'
