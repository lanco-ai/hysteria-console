"""Shared administrator overview presentation-data contracts."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import admin_overview_data
import landing_egress
import pytest
import subscription_service as ss

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))
GIB = 1024**3


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def _landing_node(node_id, name, *, enabled):
    return {
        'id': node_id,
        'name': name,
        'socks_ip': '8.8.8.8',
        'socks_port': 1080,
        'socks_username': 'private-user',
        'socks_password': 'private-password',
        'expected_exit_ip': '8.8.4.4',
        'isp': 'private-isp',
        'region': 'private-region',
        'enabled': enabled,
    }


@pytest.fixture
def overview_state(tmp_path, monkeypatch):
    paths = {
        name: tmp_path / filename
        for name, filename in {
            'USERS_FILE': 'users.json',
            'META_FILE': 'meta.json',
            'USAGE_FILE': 'usage.json',
            'USAGE_DAILY_FILE': 'usage_daily.json',
            'USAGE_HOURLY_FILE': 'usage_hourly.json',
            'USAGE_PRESERVED_FILE': 'usage_preserved.json',
            'ONLINE_FILE': 'online.json',
            'DISPLAY_MULTIPLIER_STATE_FILE': 'display_multiplier.json',
        }.items()
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(landing_egress, 'REGISTRY_FILE', tmp_path / 'landing.json')
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)

    users = {
        'alice': {
            'sub_token': 'fictional-alice-token',
            'panel_pass_hash': 'private-panel-hash',
            'password': 'private-proxy-password',
            'monthly_quota_bytes': 10 * GIB,
            'quota_extra_bytes': 2 * GIB,
            'max_devices': 2,
            'guest': True,
            'tuic_enabled': False,
            'expires_at': '2026-09-15',
            'note': '<b>operator note</b>',
            'landing_isp': ' 电信 ',
            'landing_region': ' 西安 ',
            'landing_note': ' 家宽 ',
            'landing_ip': '2001:0db8:0:0:0:0:0:1',
        },
        'bob': {
            'sub_token': 'fictional-bob-token',
            'monthly_quota_bytes': 0,
            'max_devices': 0,
            'metered': False,
            'tuic_enabled': True,
            'disabled': True,
            'expires_at': '2026-09-10',
        },
    }
    _write_json(
        paths['META_FILE'],
        {
            'settlement_day': 1,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-09-01',
        },
    )
    _write_json(paths['USERS_FILE'], users)
    _write_json(paths['USAGE_FILE'], {})
    _write_json(paths['USAGE_HOURLY_FILE'], {})
    _write_json(
        paths['USAGE_DAILY_FILE'],
        {
            '2026-09-12': {
                'alice': {'tx': 10, 'rx': 15, 'total': 25},
                'bob': {'tx': 2, 'rx': 3, 'total': 5},
            }
        },
    )
    _write_json(
        paths['USAGE_PRESERVED_FILE'],
        {'2026-09-01': {'retired': {'tx': 2, 'rx': 3, 'total': 5}}},
    )
    _write_json(paths['ONLINE_FILE'], {'alice': 2, 'bob': 0})
    _write_json(
        paths['DISPLAY_MULTIPLIER_STATE_FILE'],
        {'enabled': True, 'multiplier': 2.0},
    )
    _write_json(
        landing_egress.REGISTRY_FILE,
        {
            'version': 1,
            'nodes': {
                'enabled-node': _landing_node('enabled-node', 'Visible node', enabled=True),
                'disabled-node': _landing_node('disabled-node', 'Hidden node', enabled=False),
            },
        },
    )

    def unexpected_side_effect(*_args, **_kwargs):
        raise AssertionError('overview reads must not sync or reload runtime state')

    monkeypatch.setattr(ss, '_sync_static_access_from_users', unexpected_side_effect)
    monkeypatch.setattr(ss.xray_config, 'reload_async', unexpected_side_effect)
    monkeypatch.setattr(ss.tuic_config, 'reload_async', unexpected_side_effect)
    return {'paths': paths, 'users': users}


USER_FIELDS = {
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


def test_build_user_returns_unescaped_allowlisted_presentation_data(overview_state):
    """Inlining HTML or leaking the config must fail the data-boundary contract."""
    data = admin_overview_data.build_user(
        ss._admin_views_context(),
        'alice',
        overview_state['users']['alice'],
        {'alice': 2},
        'https://panel.invalid:9444',
        daily=json.loads(overview_state['paths']['USAGE_DAILY_FILE'].read_text()),
        now=FIXED_NOW,
    )

    assert set(data) == USER_FIELDS
    assert data == {
        'user': 'alice',
        'tx': 20,
        'rx': 30,
        'used': 50,
        'total': 12 * GIB,
        'percent': 50 * 100.0 / (12 * GIB),
        'online': 2,
        'revision': ss.user_config_revision(overview_state['users']['alice']),
        'disabled': False,
        'max_devices': 2,
        'base_quota_gb': 10,
        'quota_extra_gb': 2,
        'metered': True,
        'tuic_enabled': False,
        'expires_at': '2026-09-15',
        'expired': False,
        'expiry_label': '3 天后到期',
        'note': '<b>operator note</b>',
        'landing_isp': '电信',
        'landing_region': '西安',
        'landing_note': '家宽',
        'landing_ip': '2001:db8::1',
        'panel_url': 'https://panel.invalid:9444/panel/alice?token=fictional-alice-token',
        'subscription_url': 'https://panel.invalid:9444/sub/alice?token=fictional-alice-token',
        'spark': data['spark'],
    }
    assert len(data['spark']) == 30
    assert data['spark'][0] == ('2026-08-14', 0)
    assert data['spark'][-1] == ('2026-09-12', 50)
    assert '<td' not in str(data)
    assert '<svg' not in str(data)


def test_build_user_without_daily_retains_the_legacy_no_trend_contract(overview_state):
    """Forcing a sparkline into standalone row_form would change its HTML shape."""
    data = admin_overview_data.build_user(
        ss._admin_views_context(),
        'bob',
        overview_state['users']['bob'],
        {'bob': 0},
        'http://panel.invalid',
        now=FIXED_NOW,
    )

    assert data['spark'] is None
    assert data['total'] == 0
    assert data['percent'] == 0.0
    assert data['max_devices'] == 0
    assert data['disabled'] is True
    assert data['expired'] is True
    assert data['expiry_label'] == '已过期 2 天'


def test_build_page_reuses_authoritative_cycle_data_and_public_landing_choices(
    overview_state,
):
    """Duplicate billing math or raw landing nodes must change these hand-derived values."""
    page = admin_overview_data.build_page(
        ss._admin_views_context(),
        'https://panel.invalid',
    )

    assert set(page) == {'cycle', 'users', 'landing_options'}
    assert page['cycle'] == {
        'key': '2026-09',
        'total_used': 70,
        'range': '09/01 → 09/30 · 第 12/30 天',
        'settlement_day': 1,
        'length_days': 30,
        'length_min': ss.CYCLE_LENGTH_MIN,
        'length_max': ss.CYCLE_LENGTH_MAX,
    }
    assert [user['user'] for user in page['users']] == ['alice', 'bob']
    assert all(set(user) == USER_FIELDS for user in page['users'])
    assert page['landing_options'] == [{'id': 'enabled-node', 'name': 'Visible node'}]
    assert 'private-user' not in str(page)
    assert 'private-password' not in str(page)
    assert 'Hidden node' not in str(page)


def test_build_page_keeps_a_complete_cycle_when_there_are_no_users(
    overview_state,
):
    """Treating an empty account set as a missing page would drop cycle controls."""
    _write_json(overview_state['paths']['USERS_FILE'], {})

    page = admin_overview_data.build_page(
        ss._admin_views_context(),
        'https://panel.invalid',
    )

    assert page['users'] == []
    assert page['cycle']['total_used'] == 10
    assert page['cycle']['range'] == '09/01 → 09/30 · 第 12/30 天'


def test_build_page_uses_calendar_settlement_range_for_30_day_cycle(overview_state, monkeypatch):
    """A stale 30-day anchor must not make the displayed range drift."""
    _write_json(
        overview_state['paths']['META_FILE'],
        {
            'settlement_day': 15,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-07-15',
        },
    )
    now = datetime(2026, 9, 17, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(ss, 'local_now', lambda: now)

    page = admin_overview_data.build_page(
        ss._admin_views_context(),
        'https://panel.invalid',
    )

    assert page['cycle']['range'] == '09/15 → 10/14 · 第 3/30 天'


def test_render_admin_characterization_preserves_html_and_sensitive_draft_filtering(
    overview_state,
    monkeypatch,
):
    """The extraction must keep the representative legacy content byte-for-byte."""
    monkeypatch.setattr(
        ss,
        'render_admin_shell',
        lambda _active, _title, content, **_kwargs: content,
    )
    page = ss.render_admin(
        'panel.invalid',
        'https://panel.invalid',
        flash='err:username_invalid',
        create_draft={
            'user': 'safe-user',
            'quota_gb': 12,
            'quota_extra_gb': 3,
            'note': 'draft <note>',
            'landing_initial_egress_id': 'enabled-node',
            'guest': True,
            'tuic_enabled': True,
            'password': 'never-render-proxy-password',
            'panel_password': 'never-render-panel-password',
        },
        create_error_field='create-user',
    )

    assert (
        hashlib.sha256(page.encode()).hexdigest()
        == '6fd8bfc14aa34fcf390976092e186da841833e36176dc55af70363bd908c6b19'
    )
    assert '<b>operator note</b>' not in page
    assert '&lt;b&gt;operator note&lt;/b&gt;' in page
    assert 'value="draft &lt;note&gt;"' in page
    assert '<option value="enabled-node" selected>Visible node</option>' in page
    assert 'never-render-proxy-password' not in page
    assert 'never-render-panel-password' not in page
    assert 'class="spark-cell"' in page
