"""设置 · 告警通知: console-managed alert channels with write-only secrets."""

import json
import stat
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
    ({'webhook_enabled': True, 'webhook_url': 'https://hooks.example.test/', 'webhook_secret': 'x' * 257}, 'webhook_secret_invalid'),
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, 'CONFIG_FILE', tmp_path / 'alerts.json')
    monkeypatch.setattr(alerts, 'CONFIG_LOCK_FILE', tmp_path / 'alerts.lock')
    session = {'admin': True}
    module = SimpleNamespace(
        alerts=alerts,
        state_store=state_store,
        content_revision=ss.content_revision,
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


def test_unreadable_alert_config_is_reported_not_overwritten(client):
    http, _session, path = client
    path.write_text('{broken')
    assert http.get('/api/v1/admin/alerts').status_code == 503
    response = http.post('/api/v1/admin/alerts/save', data={**FORM, 'revision': 'x'})
    assert (response.status_code, response.json()['code']) == (422, 'config_unreadable')
    assert path.read_text() == '{broken'
