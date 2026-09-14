"""Authenticated overview bootstrap HTTP contracts."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import admin_overview_data
import landing_egress
import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def real_state(tmp_path, monkeypatch):
    paths = {
        name: tmp_path / filename
        for name, filename in {
            'USERS_FILE': 'users.json',
            'META_FILE': 'meta.json',
            'SESSIONS_FILE': 'sessions.json',
            'USER_SESSIONS_FILE': 'user_sessions.json',
            'USAGE_FILE': 'usage.json',
            'USAGE_DAILY_FILE': 'usage_daily.json',
            'USAGE_HOURLY_FILE': 'usage_hourly.json',
            'USAGE_PRESERVED_FILE': 'usage_preserved.json',
            'ONLINE_FILE': 'online.json',
            'USAGE_LOCK_FILE': 'usage.lock',
            'DISPLAY_MULTIPLIER_STATE_FILE': 'display_multiplier.json',
        }.items()
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(landing_egress, 'REGISTRY_FILE', tmp_path / 'landing.json')
    monkeypatch.setattr(ss, 'CONFIGURED_PUBLIC_HOST', 'panel.configured')
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)

    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': 'fictional-admin-password-hash',
            'admin_token': 'fictional-query-token',
            'settlement_day': 1,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-09-01',
        },
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'alice': {
                'sub_token': 'fictional-subscription-token',
                'panel_pass_hash': 'fictional-panel-password-hash',
                'password': 'fictional-proxy-password',
                'monthly_quota_bytes': 1000,
                'max_devices': 3,
                'note': '<strong>plain presentation text</strong>',
                'landing_isp': '电信',
            }
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    _write_json(paths['USAGE_FILE'], {})
    _write_json(
        paths['USAGE_DAILY_FILE'],
        {'2026-09-12': {'alice': {'tx': 10, 'rx': 15, 'total': 25}}},
    )
    _write_json(paths['USAGE_HOURLY_FILE'], {})
    _write_json(
        paths['USAGE_PRESERVED_FILE'],
        {'2026-09-01': {'retired': {'tx': 2, 'rx': 3, 'total': 5}}},
    )
    _write_json(paths['ONLINE_FILE'], {'alice': 2})
    _write_json(
        paths['DISPLAY_MULTIPLIER_STATE_FILE'],
        {'enabled': True, 'multiplier': 2.0},
    )
    _write_json(landing_egress.REGISTRY_FILE, {'version': 1, 'nodes': {}})

    def unexpected_side_effect(*_args, **_kwargs):
        raise AssertionError('overview reads must not sync or reload runtime state')

    monkeypatch.setattr(ss, '_sync_static_access_from_users', unexpected_side_effect)
    monkeypatch.setattr(ss.xray_config, 'reload_async', unexpected_side_effect)
    monkeypatch.setattr(ss.tuic_config, 'reload_async', unexpected_side_effect)
    return paths


@pytest.fixture
def authenticated_client(real_state):
    generation = ss._credential_generation('fictional-admin-password-hash')
    headers = {'Cookie': f'sid={ss.create_session("admin", generation)}'}
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield client, headers


def _user_headers():
    sid = ss.create_user_session(
        'alice',
        ss._credential_generation('fictional-panel-password-hash'),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    return {'Cookie': f'usid={sid}'}


def test_overview_page_route_exists(authenticated_client):
    """Deleting the exact bootstrap route must return 404 instead of data."""
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 200
    assert set(response.json()) == {'cycle', 'users', 'landing_options'}


def test_overview_page_uses_configured_safe_public_url_and_complete_allowlist(
    authenticated_client,
):
    """Trusting Host or serializing runtime config must fail this boundary test."""
    client, headers = authenticated_client
    response = client.get(
        '/api/v1/admin/overview-page',
        headers={
            **headers,
            'Host': 'attacker.invalid',
            'X-Forwarded-Proto': 'https',
            'X-Forwarded-Port': '9444',
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['cycle'] == {
        'key': '2026-09',
        'total_used': 60,
        'range': '09/01 → 09/30 · 第 12/30 天',
        'settlement_day': 1,
        'length_days': 30,
        'length_min': ss.CYCLE_LENGTH_MIN,
        'length_max': ss.CYCLE_LENGTH_MAX,
    }
    assert payload['landing_options'] == []
    user = payload['users'][0]
    assert set(user) == {
        'user',
        'tx',
        'rx',
        'used',
        'total',
        'percent',
        'online',
        'revision',
        'disabled',
        'max_devices',
        'base_quota_gb',
        'quota_extra_gb',
        'metered',
        'tuic_enabled',
        'expires_at',
        'expired',
        'expiry_label',
        'note',
        'landing_isp',
        'landing_region',
        'landing_note',
        'landing_ip',
        'panel_url',
        'subscription_url',
        'spark',
    }
    assert user['panel_url'] == (
        'https://panel.configured:9444/panel/alice?token=fictional-subscription-token'
    )
    assert user['subscription_url'] == (
        'https://panel.configured:9444/sub/alice?token=fictional-subscription-token'
    )
    assert user['note'] == '<strong>plain presentation text</strong>'
    assert user['expires_at'] == ''
    assert user['expiry_label'] == ''
    assert user['spark'][-1] == ['2026-09-12', 50]
    assert 'fictional-panel-password-hash' not in response.text
    assert 'fictional-proxy-password' not in response.text


def test_overview_page_uses_safe_url_fallbacks_for_invalid_forwarding_headers(
    authenticated_client,
):
    """Hand-building the public URL would preserve unsafe forwarded values."""
    client, headers = authenticated_client
    response = client.get(
        '/api/v1/admin/overview-page',
        headers={
            **headers,
            'X-Forwarded-Proto': 'javascript',
            'X-Forwarded-Port': '70000',
        },
    )

    assert response.status_code == 200
    assert response.json()['users'][0]['panel_url'].startswith(
        'http://panel.configured/panel/alice?'
    )


def test_overview_page_authenticates_admin_before_loading_page_data(
    real_state,
    monkeypatch,
):
    """Moving the builder ahead of admin authentication must expose this assertion."""

    def protected_builder(*_args, **_kwargs):
        raise AssertionError('protected overview state was loaded')

    monkeypatch.setattr(admin_overview_data, 'build_page', protected_builder)
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        anonymous = client.get('/api/v1/admin/overview-page')
        user = client.get('/api/v1/admin/overview-page', headers=_user_headers())
        query = client.get('/api/v1/admin/overview-page?token=fictional-query-token')

    for response in (anonymous, user, query):
        assert response.status_code == 401
        assert response.json() == {'error': 'login_required'}
        assert 'fictional-subscription-token' not in response.text


def test_overview_page_models_strip_unknown_private_fields_at_every_level(
    authenticated_client,
    monkeypatch,
):
    """Changing any nested model to a generic blob must leak these canary fields."""
    original = admin_overview_data.build_page

    def builder_with_private_fields(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload['admin_token'] = 'top-level-secret'
        payload['cycle']['password_hash'] = 'cycle-secret'
        payload['users'][0]['sub_token'] = 'user-secret'
        payload['users'][0]['metadata'] = {'password': 'nested-secret'}
        payload['landing_options'].append(
            {'id': 'safe-node', 'name': 'Safe node', 'socks_password': 'landing-secret'}
        )
        return payload

    monkeypatch.setattr(admin_overview_data, 'build_page', builder_with_private_fields)
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 200
    assert set(response.json()) == {'cycle', 'users', 'landing_options'}
    assert set(response.json()['cycle']) == {
        'key',
        'total_used',
        'range',
        'settlement_day',
        'length_days',
        'length_min',
        'length_max',
    }
    assert response.json()['landing_options'][-1] == {'id': 'safe-node', 'name': 'Safe node'}
    assert 'secret' not in response.text


@pytest.mark.parametrize(
    ('field', 'invalid'),
    [
        ('online', True),
        ('percent', '5.0'),
        ('percent', float('inf')),
    ],
)
def test_invalid_overview_output_is_a_sanitized_500(
    authenticated_client,
    monkeypatch,
    field,
    invalid,
):
    """Coercing invalid service output would hide a broken public contract."""
    original = admin_overview_data.build_page

    def invalid_builder(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload['users'][0][field] = invalid
        return payload

    monkeypatch.setattr(admin_overview_data, 'build_page', invalid_builder)
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 500
    assert response.json() == {'error': 'internal_error'}
    assert 'validation' not in response.text


def test_malformed_overview_state_is_sanitized_as_unavailable(
    authenticated_client,
    real_state,
):
    """Falling back from malformed core state would fabricate an empty page."""
    real_state['USERS_FILE'].write_text('{"broken":', encoding='utf-8')
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'broken' not in response.text
    assert str(real_state['USERS_FILE']) not in response.text


def test_known_stale_critical_overview_state_is_sanitized_as_unavailable(
    authenticated_client,
    real_state,
    monkeypatch,
):
    """A repository-level stale core read must not become a generic 500 or leak."""
    original = ss.state_store.load_json_strict

    def stale_core_read(path, default, *, required=False):
        if Path(path) == real_state['USERS_FILE']:
            raise ss.state_store.CriticalStateUnavailable(
                'stale core state contains fictional-protected-value'
            )
        return original(path, default, required=required)

    monkeypatch.setattr(ss.state_store, 'load_json_strict', stale_core_read)
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'stale core state' not in response.text
    assert 'fictional-protected-value' not in response.text


def test_overview_page_read_does_not_write_state_and_polling_shape_stays_small(
    authenticated_client,
    real_state,
):
    """Bootstrap reads must neither mutate state nor broaden five-second polling."""
    client, headers = authenticated_client
    before = {
        path: path.read_bytes()
        for path in (*real_state.values(), landing_egress.REGISTRY_FILE)
        if path.exists()
    }

    page = client.get('/api/v1/admin/overview-page', headers=headers)
    polling = client.get('/api/v1/admin/overview', headers=headers)

    assert page.status_code == polling.status_code == 200
    assert {path: path.read_bytes() for path in before} == before
    assert set(polling.json()) == {'ts', 'total_used', 'users'}
    assert set(polling.json()['users'][0]) == {
        'user',
        'tx',
        'rx',
        'used',
        'total',
        'percent',
        'online',
        'revision',
        'disabled',
    }
    assert 'fictional-subscription-token' not in polling.text
    assert 'panel_url' not in polling.text


def test_overview_page_uses_one_multiplier_snapshot_for_rows_period_and_spark(
    authenticated_client,
    monkeypatch,
):
    """Re-reading a changing multiplier mid-request would mix displayed units."""
    reads = []

    def changing_multiplier(**_kwargs):
        value = 2.0 + len(reads)
        reads.append(value)
        return value

    monkeypatch.setattr(
        ss.display_config,
        'effective_display_multiplier_strict',
        changing_multiplier,
    )
    client, headers = authenticated_client
    response = client.get('/api/v1/admin/overview-page', headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert reads == [2.0]
    assert payload['users'][0]['used'] == 50
    assert payload['users'][0]['spark'][-1] == ['2026-09-12', 50]
    assert payload['cycle']['total_used'] == 60


def test_overview_page_route_boundary_preserves_head_headers_path_and_method(
    authenticated_client,
):
    """Broadening the exact read route must change one of these status contracts."""
    client, headers = authenticated_client
    get_response = client.get('/api/v1/admin/overview-page', headers=headers)
    head_response = client.head('/api/v1/admin/overview-page', headers=headers)
    post_response = client.post('/api/v1/admin/overview-page', headers=headers)
    trailing_response = client.get(
        '/api/v1/admin/overview-page/',
        headers=headers,
        follow_redirects=False,
    )

    assert get_response.status_code == head_response.status_code == 200
    assert dict(head_response.headers) == dict(get_response.headers)
    assert head_response.content == b''
    assert post_response.status_code == 405
    assert post_response.json() == {'error': 'method_not_allowed'}
    assert trailing_response.status_code == 404
    assert trailing_response.json() == {'error': 'not_found'}
    for response in (get_response, head_response, post_response, trailing_response):
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
