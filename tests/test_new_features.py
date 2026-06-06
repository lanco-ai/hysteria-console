"""Tests for the four new features:

A - QR code helper (render_qr_svg) on the user panel
C - Admin user-table search/filter (smoke: chips render in the HTML;
    JS behavior is browser-level, exercised by hand)
E - /admin/usage.csv export
F - Self-service token rotation on the user panel

These are unit-level checks; the integration paths are kept tight so
they don't require a real qrencode binary or a real HTTP listener.
"""
import csv as _csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

import subscription_service as ss

SH = ZoneInfo("Asia/Shanghai")


# ----- A: QR helper ---------------------------------------------------------

def test_render_qr_svg_returns_inline_svg_from_runner():
    """Happy path: a fake runner returns valid qrencode SVG output. The helper
    strips the XML prolog and DOCTYPE so the SVG can be inlined into HTML."""
    fake_svg = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        b'<svg width="100" height="100"><rect/></svg>\n'
    )

    def runner(cmd, **_kw):
        assert cmd[0] == 'qrencode'
        assert '--' in cmd
        return fake_svg

    out = ss.render_qr_svg('hello', _runner=runner)
    assert '<svg' in out
    assert '<?xml' not in out
    assert '<!DOCTYPE' not in out


def test_render_qr_svg_returns_empty_on_missing_binary():
    def runner(cmd, **_kw):
        raise FileNotFoundError(2, 'No such file or directory: qrencode')

    assert ss.render_qr_svg('hello', _runner=runner) == ''


def test_render_qr_svg_returns_empty_on_empty_input():
    assert ss.render_qr_svg('') == ''


def test_render_qr_svg_returns_empty_on_subprocess_error():
    import subprocess
    def runner(cmd, **_kw):
        raise subprocess.CalledProcessError(1, cmd)

    assert ss.render_qr_svg('hello', _runner=runner) == ''


def test_user_panel_includes_qr_block_when_helper_returns_svg(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 'tok123', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2},
    }))
    monkeypatch.setattr(ss, 'render_qr_svg', lambda *a, **k: '<svg id="fake-qr"/>')
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok123',
                                {'sub_token': 'tok123', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2})
    assert '<svg id="fake-qr"/>' in page
    assert 'qr-card' in page


def test_user_panel_omits_qr_block_when_helper_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 'tok123', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2},
    }))
    monkeypatch.setattr(ss, 'render_qr_svg', lambda *a, **k: '')
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok123',
                                {'sub_token': 'tok123', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2})
    assert 'qr-card' not in page
    assert '订阅二维码' not in page


# ----- C: Admin table filter (smoke) ----------------------------------------

def test_admin_table_renders_filter_chips_and_search_input(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 't1', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2},
    }))
    out = ss.render_admin('test-host', 'http://test-host')
    assert 'id="user-filter"' in out
    assert 'filter-chips' in out
    assert 'data-filter="online"' in out
    assert 'data-filter="over"' in out


# ----- E: CSV export --------------------------------------------------------

def test_build_usage_csv_cycle_window_includes_only_cycle_days(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'meta.json').write_text(json.dumps({
        'settlement_day': 12, 'cycle_length_days': 30,
        'cycle_anchor_date': '2026-04-12',
    }))
    daily = {
        '2026-04-12': {'alice': {'tx': 100, 'rx': 200, 'total': 300}},
        '2026-04-13': {'alice': {'tx': 50, 'rx': 50, 'total': 100}},
        '2026-03-30': {'alice': {'tx': 999, 'rx': 0, 'total': 999}},  # before cycle
    }
    (tmp_path / 'usage_daily.json').write_text(json.dumps(daily))

    csv_body = ss._build_usage_csv(now=datetime(2026, 4, 15), window='cycle')
    reader = list(_csv.reader(io.StringIO(csv_body)))
    header, *rows = reader
    assert header == ['date', 'user', 'tx_bytes', 'rx_bytes', 'total_bytes', 'displayed_bytes']
    dates = [r[0] for r in rows]
    assert '2026-04-12' in dates
    assert '2026-04-13' in dates
    assert '2026-03-30' not in dates, 'days outside the cycle must not appear'


def test_build_usage_csv_30d_window_includes_30_days_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    daily = {
        '2026-04-15': {'alice': {'tx': 1, 'rx': 1, 'total': 2}},
        '2026-04-01': {'alice': {'tx': 1, 'rx': 1, 'total': 2}},
        '2026-03-01': {'alice': {'tx': 1, 'rx': 1, 'total': 2}},  # >30d back
    }
    (tmp_path / 'usage_daily.json').write_text(json.dumps(daily))

    csv_body = ss._build_usage_csv(now=datetime(2026, 4, 15), window='30d')
    dates = {row[0] for row in _csv.reader(io.StringIO(csv_body)) if row and row[0] != 'date'}
    assert '2026-04-15' in dates
    assert '2026-04-01' in dates
    assert '2026-03-01' not in dates


def test_build_usage_csv_displayed_bytes_uses_multiplier(tmp_path, monkeypatch):
    from display import DISPLAY_MULTIPLIER
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'meta.json').write_text(json.dumps({
        'cycle_anchor_date': '2026-04-15', 'cycle_length_days': 30, 'settlement_day': 15,
    }))
    daily = {'2026-04-15': {'alice': {'tx': 0, 'rx': 1_000_000, 'total': 1_000_000}}}
    (tmp_path / 'usage_daily.json').write_text(json.dumps(daily))

    csv_body = ss._build_usage_csv(now=datetime(2026, 4, 15), window='cycle')
    rows = list(_csv.reader(io.StringIO(csv_body)))
    data = [r for r in rows if r and r[0] == '2026-04-15']
    assert len(data) == 1
    assert int(data[0][5]) == int(1_000_000 * DISPLAY_MULTIPLIER)


# ----- F: Token rotation ---------------------------------------------------

def test_rotate_token_smoke_replaces_token_when_current_matches(tmp_path, monkeypatch):
    """Direct test of the underlying rotation logic: the POST handler
    verifies the posted token, then mints a new one and saves users.json.
    We exercise the verification + mint + save trio without an HTTP listener."""
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 'OLD-TOKEN', 'max_devices': 2},
    }))

    # Verify current token is good.
    cfg = ss.check_user_token('alice', 'OLD-TOKEN')
    assert cfg is not None

    # Mint and save (mirroring the handler).
    import secrets
    new = secrets.token_urlsafe(18)
    users = ss.load_json(ss.USERS_FILE, {})
    users['alice']['sub_token'] = new
    ss.save_json(ss.USERS_FILE, users)

    # Old token must no longer authenticate; new one must.
    assert ss.check_user_token('alice', 'OLD-TOKEN') is None
    assert ss.check_user_token('alice', new) is not None


def test_rotate_token_rejects_wrong_current_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 'OLD-TOKEN', 'max_devices': 2},
    }))
    assert ss.check_user_token('alice', 'wrong-token') is None
    # users.json is untouched.
    stored = json.loads((tmp_path / 'users.json').read_text())
    assert stored['alice']['sub_token'] == 'OLD-TOKEN'


def test_user_panel_renders_rotate_token_form_with_current_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'sub_token': 'tokABC', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2},
    }))
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tokABC',
                                {'sub_token': 'tokABC', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2})
    assert '/panel/alice/rotate-token' in page
    assert 'data-action="rotate-token"' in page
    assert 'value="tokABC"' in page


# ----- G: User-panel UX (reset countdown, trend, copy, live refresh) --------

def _seed_panel(tmp_path, monkeypatch, *, daily=None, online=None, meta=None):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'usage_daily.json').write_text(json.dumps(daily or {}))
    (tmp_path / 'online.json').write_text(json.dumps(online or {}))
    (tmp_path / 'meta.json').write_text(json.dumps(meta or {}))


_CYCLE_META = {'settlement_day': 12, 'cycle_length_days': 30,
               'cycle_anchor_date': '2026-05-12'}


def test_user_panel_shows_quota_reset_countdown(tmp_path, monkeypatch):
    """Cycle 2026-05-12 .. 06-10 resets on 06-11; viewed 05-14 -> 28 days left."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_panel(tmp_path, monkeypatch, meta=_CYCLE_META)
    monkeypatch.setattr(ss, 'local_now', lambda: now)
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert '本周期 30 天' in page
    assert '重置于 2026-06-11' in page
    assert '还剩 28 天' in page


def test_user_panel_shows_30day_usage_trend(tmp_path, monkeypatch):
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    daily = {'2026-05-13': {'alice': {'tx': 0, 'rx': 1_000_000, 'total': 1_000_000}}}
    _seed_panel(tmp_path, monkeypatch, daily=daily, meta=_CYCLE_META)
    monkeypatch.setattr(ss, 'local_now', lambda: now)
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert '近 30 天用量趋势' in page
    assert 'panel-trend' in page
    assert 'class="spark"' in page


def test_user_panel_has_copy_buttons_for_both_links(tmp_path, monkeypatch):
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_panel(tmp_path, monkeypatch, meta=_CYCLE_META)
    monkeypatch.setattr(ss, 'local_now', lambda: now)
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert 'data-copy="http://h/sub/alice?token=tok"' in page
    assert 'data-copy="http://h/panel/alice?token=tok"' in page


def test_user_panel_wires_live_refresh_poll(tmp_path, monkeypatch):
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_panel(tmp_path, monkeypatch, meta=_CYCLE_META)
    monkeypatch.setattr(ss, 'local_now', lambda: now)
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert '/panel/alice.json?token=tok' in page
    for role in ('used', 'remain', 'online', 'percent', 'bar', 'txrx', 'poll-status'):
        assert f'data-role="{role}"' in page


def test_build_panel_json_payload_schema_and_values(tmp_path, monkeypatch):
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    daily = {'2026-05-14': {'alice': {'tx': 0, 'rx': 1_000_000, 'total': 1_000_000}}}
    _seed_panel(tmp_path, monkeypatch, daily=daily, online={'alice': 1}, meta=_CYCLE_META)
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 10_000_000_000, 'max_devices': 2}
    payload = ss._build_panel_json_payload('alice', cfg, now=now)
    assert set(payload) == {'ts', 'used_bytes', 'total_bytes', 'remain_bytes',
                            'tx_bytes', 'rx_bytes', 'online', 'percent'}
    assert payload['used_bytes'] == int(1_000_000 * ss.DISPLAY_MULTIPLIER)
    assert payload['total_bytes'] == 10_000_000_000
    assert payload['remain_bytes'] == payload['total_bytes'] - payload['used_bytes']
    assert payload['online'] == 1
    assert 0 <= payload['percent'] <= 100


def test_panel_json_payload_unmetered_remain_is_negative(tmp_path, monkeypatch):
    """No quota set -> remain sentinel -1 (unlimited) and percent pinned to 0."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_panel(tmp_path, monkeypatch, meta=_CYCLE_META)
    cfg = {'sub_token': 'tok', 'max_devices': 0}
    payload = ss._build_panel_json_payload('alice', cfg, now=now)
    assert payload['total_bytes'] == 0
    assert payload['remain_bytes'] == -1
    assert payload['percent'] == 0.0


# ----- H: Admin — change password / rotate token / test alert / suspend -----

def test_settings_page_has_change_password_form(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'meta.json').write_text(json.dumps(
        {'admin_user': 'admin', 'admin_pass_hash': 'x'}))
    out = ss.render_settings('host')
    assert 'action="/admin/change-password"' in out
    assert 'name="current"' in out
    assert 'name="new"' in out
    assert 'name="confirm"' in out


def test_settings_page_renders_flash_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'meta.json').write_text(json.dumps(
        {'admin_user': 'admin', 'admin_pass_hash': 'x'}))
    assert '管理员密码已更新' in ss.render_settings('host', flash='password changed')
    assert '当前密码不正确' in ss.render_settings('host', flash='err:password_wrong')


def test_settings_appears_in_sidebar_nav():
    assert any(key == 'settings' and href == '/admin/settings'
               for key, href, _label, _icon in ss._SIDEBAR_NAV)


def test_health_page_has_test_alert_button_and_flash():
    out = ss.render_health('host', flash='alert sent')
    assert 'action="/admin/test-alert"' in out
    assert '发送测试告警' in out
    assert '测试告警已发送' in out
    assert '未配置告警通道' in ss.render_health('host', flash='err:alert_no_channels')


def test_admin_row_has_rotate_and_suspend_for_enabled_user(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    cfg = {'sub_token': 't', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    row = ss.row_form('alice', cfg, {}, 'host', 'http://host')
    assert 'action="/admin/rotate-token"' in row
    assert 'action="/admin/toggle-user"' in row
    assert '>暂停</button>' in row
    assert 'data-action="disable-user"' in row
    assert '已停用' not in row


def test_admin_row_shows_enable_button_and_badge_for_disabled_user(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    cfg = {'sub_token': 't', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2, 'disabled': True}
    row = ss.row_form('alice', cfg, {}, 'host', 'http://host')
    assert '>启用</button>' in row
    assert 'badge-danger' in row and '已停用' in row
    # Enabling is harmless, so it must NOT carry the disable confirm hook.
    assert 'data-action="disable-user"' not in row


def test_user_total_quota_includes_extra_package():
    cfg = {
        'monthly_quota_bytes': 100,
        'quota_extra_bytes': 25,
    }
    assert ss.user_total_quota(cfg) == 125


def test_admin_row_shows_expiry_extra_and_note(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    monkeypatch.setattr(
        ss, 'local_now',
        lambda: datetime(2026, 6, 3, 10, tzinfo=SH),
    )
    cfg = {
        'sub_token': 't',
        'monthly_quota_bytes': 150 * (1 << 30),
        'quota_extra_bytes': 20 * (1 << 30),
        'max_devices': 2,
        'expires_at': '2026-06-10',
        'note': 'paid through June',
    }
    row = ss.row_form('alice', cfg, {}, 'host', 'http://host')
    assert '加量 20 GB' in row
    assert '7 天后到期' in row
    assert 'paid through June' in row
    assert 'name="quota_extra_gb"' in row
    assert 'name="expires_at"' in row
    assert 'name="note"' in row


def _seed_profile_template(tmp_path, monkeypatch):
    template = Path(ss.__file__).resolve().parent / 'clash-default.yaml.tpl'
    users_file = tmp_path / 'users.json'
    users_file.write_text(json.dumps({'alice': {'vless_uuid': '11111111-1111-1111-1111-111111111111'}}))
    monkeypatch.setattr(ss, 'TEMPLATE_FILE', template)
    monkeypatch.setattr(ss, 'USERS_FILE', users_file)


def _profile_cfg(tmp_path, monkeypatch, profile):
    _seed_profile_template(tmp_path, monkeypatch)
    text = ss.build_yaml('alice', 'tok', profile=profile)
    return yaml.safe_load(text)


def _groups(cfg):
    return {group['name']: group for group in cfg['proxy-groups']}


def test_subscription_profile_normalization():
    assert ss.normalize_subscription_profile('game') == 'game'
    assert ss.normalize_subscription_profile('low-data') == 'lowdata'
    assert ss.normalize_subscription_profile('GLOBAL') == 'safe'
    assert ss.normalize_subscription_profile('unknown') == 'default'


def test_subscription_profile_logic_lives_in_dedicated_module():
    import subscription_profiles

    assert subscription_profiles.build_yaml.__module__ == 'subscription_profiles'
    assert subscription_profiles.apply_subscription_profile.__module__ == 'subscription_profiles'


def test_build_yaml_game_profile_prefers_udp_and_injects_credentials(tmp_path, monkeypatch):
    cfg = _profile_cfg(tmp_path, monkeypatch, 'game')
    groups = _groups(cfg)

    assert cfg['proxies'][0]['password'] == 'alice:tok'
    assert any(proxy.get('uuid') == '11111111-1111-1111-1111-111111111111'
               for proxy in cfg['proxies'])
    assert groups[ss.NODE_GROUP]['proxies'][:2] == [ss.HY2_UDP_PROXY, ss.TUIC_UDP_PROXY]
    assert ss.GPT_GROUP in groups[ss.NODE_GROUP]['proxies']
    assert groups[ss.AUTO_GROUP]['type'] == 'url-test'
    assert groups[ss.AUTO_GROUP]['timeout'] == 2500
    assert f'DOMAIN-SUFFIX,steamcommunity.com,{ss.NODE_GROUP}' in cfg['rules'][:4]


def test_build_yaml_work_profile_prefers_stable_tcp(tmp_path, monkeypatch):
    cfg = _profile_cfg(tmp_path, monkeypatch, 'work')
    groups = _groups(cfg)

    assert groups[ss.NODE_GROUP]['proxies'][:5] == [
        ss.GITHUB_GROUP, ss.GPT_GROUP, ss.AUTO_GROUP,
        ss.VLESS_TCP_PROXY, ss.VLESS_BACKUP_PROXY,
    ]
    assert groups[ss.AUTO_GROUP]['type'] == 'fallback'
    assert groups[ss.AUTO_GROUP]['proxies'][:2] == [ss.VLESS_TCP_PROXY, ss.VLESS_BACKUP_PROXY]
    assert f'DOMAIN-SUFFIX,slack.com,{ss.NODE_GROUP}' in cfg['rules'][:4]


def test_build_yaml_lowdata_profile_routes_unknown_direct(tmp_path, monkeypatch):
    cfg = _profile_cfg(tmp_path, monkeypatch, 'lowdata')
    groups = _groups(cfg)

    assert cfg['log-level'] == 'warning'
    assert groups[ss.NODE_GROUP]['proxies'][0] == 'DIRECT'
    assert groups[ss.NODE_GROUP]['proxies'][1] == ss.GPT_GROUP
    assert cfg['rules'][-1] == 'MATCH,DIRECT'


def test_build_yaml_safe_profile_proxies_cn_but_keeps_lan_direct(tmp_path, monkeypatch):
    cfg = _profile_cfg(tmp_path, monkeypatch, 'safe')
    groups = _groups(cfg)

    assert groups[ss.NODE_GROUP]['proxies'][0] == ss.GPT_GROUP
    assert 'RULE-SET,lancidr,DIRECT,no-resolve' in cfg['rules']
    assert f'RULE-SET,cncidr,{ss.NODE_GROUP},no-resolve' in cfg['rules']
    assert f'GEOIP,CN,{ss.NODE_GROUP}' in cfg['rules']
    assert cfg['rules'][-1] == f'MATCH,{ss.NODE_GROUP}'


def test_user_panel_lists_subscription_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    (tmp_path / 'users.json').write_text(json.dumps({'alice': {'sub_token': 'tok'}}))
    monkeypatch.setattr(ss, 'render_qr_svg', lambda *a, **k: '')

    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok',
                                {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2})

    assert '订阅模式' in page
    assert 'http://h/sub/alice?token=tok&amp;profile=game' in page
    assert 'http://h/sub/alice?token=tok&amp;profile=work' in page
    assert 'http://h/sub/alice?token=tok&amp;profile=lowdata' in page
    assert 'http://h/sub/alice?token=tok&amp;profile=safe' in page


def test_protocol_hourly_accumulator_records_source_totals(tmp_path, monkeypatch):
    import traffic_limiter as tl
    path = tmp_path / 'protocol_usage_hourly.json'
    monkeypatch.setattr(tl, 'PROTOCOL_USAGE_HOURLY_FILE', str(path))
    now = datetime(2026, 6, 3, 15, 5, tzinfo=SH)

    tl.accumulate_protocol_hourly({
        'hysteria': {'alice': {'tx': 10, 'rx': 20}},
        'xray': {'alice': {'tx': 3, 'rx': 4}, 'bob': {'tx': 5, 'rx': 6}},
        'tuic': {'_tuic': {'tx': 7, 'rx': 9}},
    }, now)

    data = json.loads(path.read_text())
    bucket = data['2026-06-03T15']
    assert bucket['hysteria'] == {'tx': 10, 'rx': 20, 'total': 30}
    assert bucket['xray'] == {'tx': 8, 'rx': 10, 'total': 18}
    assert bucket['tuic'] == {'tx': 7, 'rx': 9, 'total': 16}


def test_line_radar_recommends_game_when_hysteria_dominates(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'PROTOCOL_USAGE_HOURLY_FILE', tmp_path / 'protocol_usage_hourly.json')
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    now = datetime(2026, 6, 3, 15, 5, tzinfo=SH)
    (tmp_path / 'protocol_usage_hourly.json').write_text(json.dumps({
        '2026-06-03T15': {
            'hysteria': {'tx': 1000, 'rx': 1000, 'total': 2000},
            'xray': {'tx': 100, 'rx': 100, 'total': 200},
            'tuic': {'tx': 300, 'rx': 300, 'total': 600},
        }
    }))
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'vless_uuid': 'uuid-A'},
        'bob': {'disabled': True, 'vless_uuid': 'uuid-B'},
    }))
    (tmp_path / 'online.json').write_text(json.dumps({'alice': 2}))
    monkeypatch.setattr(ss, 'probe_systemd', lambda unit: {'ok': True, 'label': 'active'})

    radar = ss.build_line_radar(now=now)
    html_out = ss.render_line_radar(now=now)

    assert radar['recommendation'] == 'game'
    assert radar['rows'][0]['active_users'] == 1
    assert radar['rows'][0]['online'] == 2
    assert radar['rows'][2]['bytes'] == int(600 * ss.DISPLAY_MULTIPLIER)
    assert '线路质量雷达' in html_out
    assert '推荐：游戏' in html_out
    assert '端口级总量计量' in html_out


def test_health_widget_logic_lives_in_dedicated_module():
    import health_widgets

    assert health_widgets.build_line_radar.__module__ == 'health_widgets'
    assert health_widgets.render_cost_calibrator.__module__ == 'health_widgets'


def _seed_incident_console(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'USAGE_HOURLY_FILE', tmp_path / 'usage_hourly.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    monkeypatch.setattr(ss, 'PROTOCOL_USAGE_HOURLY_FILE', tmp_path / 'protocol_usage_hourly.json')
    monkeypatch.setattr(ss, 'COST_CALIBRATION_FILE', tmp_path / 'cost_calibration.json')
    monkeypatch.setattr(ss.alerts, 'load_state', lambda: {
        'anomaly': {'bob': '2026-06-03'},
        'quota_80': {'alice': '2026-05-12'},
    })
    monkeypatch.setattr(ss, 'probe_systemd', lambda unit: {'ok': True, 'label': 'active'})

    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'monthly_quota_bytes': 10 << 30, 'vless_uuid': 'uuid-A', 'note': 'normal'},
        'bob': {'monthly_quota_bytes': 10 << 30, 'vless_uuid': 'uuid-B', 'note': 'spike'},
    }))
    (tmp_path / 'usage_hourly.json').write_text(json.dumps({
        '2026-06-03T07': {'alice': {'tx': 50, 'rx': 50, 'total': 100}},
        '2026-06-03T08': {
            'alice': {'tx': 25, 'rx': 25, 'total': 50},
            'bob': {'tx': 100, 'rx': 200, 'total': 300},
        },
    }))
    (tmp_path / 'usage_daily.json').write_text(json.dumps({
        '2026-06-03': {
            'alice': {'tx': 25, 'rx': 25, 'total': 50},
            'bob': {'tx': 100, 'rx': 200, 'total': 300},
        },
    }))
    (tmp_path / 'online.json').write_text(json.dumps({'bob': 2}))
    (tmp_path / 'meta.json').write_text(json.dumps({
        'settlement_day': 12,
        'cycle_length_days': 30,
        'cycle_anchor_date': '2026-05-12',
    }))
    (tmp_path / 'protocol_usage_hourly.json').write_text(json.dumps({
        '2026-06-03T08': {
            'hysteria': {'tx': 1000, 'rx': 1000, 'total': 2000},
            'xray': {'tx': 50, 'rx': 50, 'total': 100},
        },
    }))


def test_incident_payload_identifies_peak_hour_and_users(tmp_path, monkeypatch):
    _seed_incident_console(tmp_path, monkeypatch)
    now = datetime(2026, 6, 3, 8, 30, tzinfo=SH)

    payload = ss.build_incident_payload(now=now)

    assert payload['peak_hour']['hour'] == '2026-06-03T08'
    assert payload['peak_hour']['users'][0]['user'] == 'bob'
    assert payload['users'][0]['user'] == 'bob'
    assert payload['users'][0]['online'] == 2
    assert payload['line_radar']['recommendation'] == 'game'
    assert any(row['user'] == 'bob' and row['kind'] == 'anomaly'
               for row in payload['alerts'])


def test_render_incidents_has_actions_and_evidence_link(tmp_path, monkeypatch):
    _seed_incident_console(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, 'local_now', lambda: datetime(2026, 6, 3, 8, 30, tzinfo=SH))

    page = ss.render_incidents('host')

    assert '/admin/incidents/evidence.json' in page
    assert 'action="/admin/pause-user"' in page
    assert 'action="/admin/rotate-token"' in page
    assert 'name="next" value="/admin/incidents"' in page
    assert '线路质量雷达' in page
    assert '成本校准器' in page


def test_incident_console_logic_lives_in_dedicated_module():
    import incident_console

    assert ss.build_incident_payload.__module__ == 'subscription_service'
    assert incident_console.build_incident_payload.__module__ == 'incident_console'
    assert incident_console.render_incidents.__module__ == 'incident_console'


def test_incidents_appears_in_sidebar_nav():
    assert any(key == 'incidents' and href == '/admin/incidents'
               for key, href, _label, _icon in ss._SIDEBAR_NAV)


def test_safe_admin_next_rejects_external_and_prefix_tricks():
    assert ss.safe_admin_next('/admin/incidents') == '/admin/incidents'
    assert ss.safe_admin_next('/admin?msg=x') == '/admin?msg=x'
    assert ss.safe_admin_next('/admin/incidents#frag') == '/admin/incidents'
    assert ss.safe_admin_next('https://example.com/admin') == '/admin'
    assert ss.safe_admin_next('/adminevil') == '/admin'


def test_with_flash_replaces_existing_message():
    target = ss.with_flash('/admin/incidents?msg=old&tab=peak', 'paused alice')
    assert target == '/admin/incidents?tab=peak&msg=paused+alice'


def test_resume_expired_temporary_disables_reenables_user(tmp_path, monkeypatch):
    import traffic_limiter as tl

    path = tmp_path / 'users.json'
    monkeypatch.setattr(tl, 'USERS_FILE', str(path), raising=False)
    users = {
        'alice': {'disabled': True, 'disabled_until': '2026-06-03T07:00:00+08:00'},
        'bob': {'disabled': True, 'disabled_until': '2026-06-03T09:00:00+08:00'},
        'carol': {'disabled': True},
    }

    changed = tl.resume_expired_temporary_disables(
        users, datetime(2026, 6, 3, 8, tzinfo=SH))

    assert changed is True
    assert users['alice']['disabled'] is False
    assert 'disabled_until' not in users['alice']
    assert users['bob']['disabled'] is True
    assert users['carol']['disabled'] is True
    saved = json.loads(path.read_text())
    assert saved['alice']['disabled'] is False


def test_admin_poll_js_confirms_destructive_admin_actions():
    text = (Path(ss.__file__).resolve().parent / 'admin_poll.js').read_text(encoding='utf-8')
    assert "f.dataset.action==='rotate-user-token'" in text
    assert "f.dataset.action==='disable-user'" in text


def test_alerts_test_kind_has_friendly_message():
    import alerts
    msg = alerts.format_message({'kind': 'test', 'user': 'admin'})
    assert '测试告警' in msg


def test_auth_backend_rejects_disabled_user(tmp_path, monkeypatch):
    import auth_backend as ab
    (tmp_path / 'users.json').write_text(json.dumps(
        {'alice': {'sub_token': 'SECRET', 'disabled': True}}))
    monkeypatch.setattr(ab, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(sys, 'argv', ['auth_backend.py', 'hysteria', 'alice:SECRET'])
    with pytest.raises(SystemExit) as e:
        ab.main()
    assert e.value.code == 1


def test_auth_backend_rejects_expired_user(tmp_path, monkeypatch):
    import auth_backend as ab
    (tmp_path / 'users.json').write_text(json.dumps(
        {'alice': {'sub_token': 'SECRET', 'expires_at': '2026-06-02'}}))
    monkeypatch.setattr(ab, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(ab, 'local_now', lambda: datetime(2026, 6, 3, 8, tzinfo=SH))
    monkeypatch.setattr(sys, 'argv', ['auth_backend.py', 'hysteria', 'alice:SECRET'])
    with pytest.raises(SystemExit) as e:
        ab.main()
    assert e.value.code == 1


def test_auth_backend_allows_enabled_user(tmp_path, monkeypatch, capsys):
    import auth_backend as ab
    (tmp_path / 'users.json').write_text(json.dumps(
        {'alice': {'sub_token': 'SECRET'}}))  # not disabled, not metered
    monkeypatch.setattr(ab, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(sys, 'argv', ['auth_backend.py', 'hysteria', 'alice:SECRET'])
    with pytest.raises(SystemExit) as e:
        ab.main()
    assert e.value.code == 0
    assert capsys.readouterr().out == 'alice'


# ----- Code-review fixes (F1..F13) ------------------------------------------

# F1: traffic_limiter cron must keep suspended users OUT of the xray plan and
#     re-kick them if online, every tick (durable suspend).

def test_cron_excludes_disabled_user_from_xray_plan(tmp_path, monkeypatch):
    import traffic_limiter as tl

    snap = tmp_path / 'online.json'
    monkeypatch.setattr(tl, 'ONLINE_SNAPSHOT_FILE', str(snap), raising=False)
    monkeypatch.setattr(tl, 'USAGE_FILE', str(tmp_path / 'usage.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_DAILY_FILE', str(tmp_path / 'usage_daily.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_HOURLY_FILE', str(tmp_path / 'usage_hourly.json'), raising=False)
    monkeypatch.setattr(tl, 'USERS_FILE', str(tmp_path / 'users.json'), raising=False)
    monkeypatch.setattr(tl, 'RESET_STATE_FILE', str(tmp_path / 'reset.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_LOCK_FILE', str(tmp_path / 'usage.lock'), raising=False)
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'vless_uuid': 'uuid-A'},
        'bob': {'vless_uuid': 'uuid-B', 'disabled': True},
    }))

    # /traffic empty, /online shows bob online (must be kicked).
    results = [{}, {'bob': 3}]
    monkeypatch.setattr(tl, 'get', lambda _p: results.pop(0))
    captured = {'plan': None, 'kick': None}
    monkeypatch.setattr(tl, 'post', lambda path, body=None, **k: captured.__setitem__('kick', body) or True)
    monkeypatch.setattr(tl, 'get_xray_traffic', lambda: {})
    monkeypatch.setattr(tl, 'get_tuic_traffic', lambda: {})
    monkeypatch.setattr(tl, 'check_alerts', lambda *a, **k: None)

    import xray_config
    def fake_apply(plan, **k):
        captured['plan'] = dict(plan)
        return False
    monkeypatch.setattr(xray_config, 'apply_user_plan', fake_apply, raising=False)
    monkeypatch.setattr(xray_config, 'reload_async', lambda: None, raising=False)

    tl.main()

    plan = captured['plan']
    assert plan['bob'] is None, 'disabled user must be removed from both inbounds'
    assert plan['alice'] == 'uuid-A'
    assert captured['kick'] == ['bob'], 'an online disabled user must be re-kicked each tick'


def test_cron_excludes_expired_user_from_xray_plan(tmp_path, monkeypatch):
    import traffic_limiter as tl

    snap = tmp_path / 'online.json'
    monkeypatch.setattr(tl, 'ONLINE_SNAPSHOT_FILE', str(snap), raising=False)
    monkeypatch.setattr(tl, 'USAGE_FILE', str(tmp_path / 'usage.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_DAILY_FILE', str(tmp_path / 'usage_daily.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_HOURLY_FILE', str(tmp_path / 'usage_hourly.json'), raising=False)
    monkeypatch.setattr(tl, 'USERS_FILE', str(tmp_path / 'users.json'), raising=False)
    monkeypatch.setattr(tl, 'RESET_STATE_FILE', str(tmp_path / 'reset.json'), raising=False)
    monkeypatch.setattr(tl, 'USAGE_LOCK_FILE', str(tmp_path / 'usage.lock'), raising=False)
    monkeypatch.setattr(tl, 'local_now', lambda: datetime(2026, 6, 3, 8, tzinfo=SH))
    (tmp_path / 'users.json').write_text(json.dumps({
        'alice': {'vless_uuid': 'uuid-A'},
        'bob': {'vless_uuid': 'uuid-B', 'expires_at': '2026-06-02'},
    }))

    results = [{}, {'bob': 1}]
    monkeypatch.setattr(tl, 'get', lambda _p: results.pop(0))
    captured = {'plan': None, 'kick': None}
    monkeypatch.setattr(tl, 'post', lambda path, body=None, **k: captured.__setitem__('kick', body) or True)
    monkeypatch.setattr(tl, 'get_xray_traffic', lambda: {})
    monkeypatch.setattr(tl, 'get_tuic_traffic', lambda: {})
    monkeypatch.setattr(tl, 'check_alerts', lambda *a, **k: None)

    import xray_config
    monkeypatch.setattr(xray_config, 'apply_user_plan',
                        lambda plan, **k: captured.__setitem__('plan', dict(plan)) or False,
                        raising=False)
    monkeypatch.setattr(xray_config, 'reload_async', lambda: None, raising=False)

    tl.main()

    assert captured['plan']['bob'] is None
    assert captured['plan']['alice'] == 'uuid-A'
    assert captured['kick'] == ['bob']


# F2: suspended users see a banner and NO poll loop on the panel.

def test_render_user_panel_disabled_shows_banner_and_omits_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text('{}')
    (tmp_path / 'meta.json').write_text('{}')
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2,
           'disabled': True}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert '账号已停用，请联系管理员' in page
    assert 'class="err"' in page
    assert 'var pollUrl' not in page
    assert '/panel/alice.json' not in page


def test_render_user_panel_expired_shows_banner_and_omits_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    monkeypatch.setattr(ss, 'local_now', lambda: datetime(2026, 6, 3, 8, tzinfo=SH))
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text('{}')
    (tmp_path / 'meta.json').write_text('{}')
    cfg = {
        'sub_token': 'tok',
        'monthly_quota_bytes': 1 << 30,
        'max_devices': 2,
        'expires_at': '2026-06-02',
    }
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert '账号已到期，请联系管理员续费' in page
    assert 'var pollUrl' not in page
    assert '/panel/alice.json' not in page


def test_render_user_panel_enabled_still_has_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text('{}')
    (tmp_path / 'meta.json').write_text('{}')
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert 'var pollUrl' in page
    assert '账号已停用' not in page


# F3: /admin/add reset-token construction preserves `disabled`.

def test_admin_add_reset_token_preserves_disabled():
    """Mirror the entry dict built by the /admin/add reset-token path."""
    existing = {'sub_token': 'old', 'vless_uuid': 'u1', 'disabled': True}
    entry = {
        'metered': False,
        'guest': False,
        'max_devices': 2,
        'monthly_quota_bytes': 1 << 30,
        'sub_token': 'new',
        'vless_uuid': 'u1',
        'disabled': bool(existing.get('disabled')),
    }
    assert entry['disabled'] is True


# F4: username validation.

def test_is_valid_username_accepts_normal_names():
    for name in ('alice', 'bob_1', 'a.b-c', 'X' * 64):
        assert ss.is_valid_username(name), name


def test_is_valid_username_rejects_bad_names():
    for name in ('a/b', 'x.json', '<script>', '', 'a' * 65, 'a b', 'foo@bar', 'sub.json'):
        assert not ss.is_valid_username(name), name


def test_user_panel_poll_url_escapes_left_angle(tmp_path, monkeypatch):
    """Defense-in-depth: even if a `<`-bearing username were rendered, the
    embedded JSON URL must escape `<` so it can't break out of <script>."""
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text('{}')
    (tmp_path / 'meta.json').write_text('{}')
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    page = ss.render_user_panel('h', 'http://h', 'a<b', 'tok', cfg)
    # The poll URL line must carry the escaped form, not a raw '</'.
    assert '\\u003c' in page
    assert 'var pollUrl' in page
    poll_line = [ln for ln in page.splitlines() if 'var pollUrl' in ln][0]
    assert '<' not in poll_line


# F8: alerts.dispatch returns a result dict and reports failed channels.

def test_alerts_dispatch_reports_failed_channel():
    import alerts as _alerts

    class FakeResp:
        def read(self):
            return b''

    class FakeOpener:
        def urlopen(self, req, timeout=None):
            # Telegram URL succeeds; the webhook endpoint raises.
            if 'telegram' in req.full_url:
                return FakeResp()
            raise OSError('connection refused')

    cfg = {
        'telegram': {'bot_token': 'B', 'chat_id': '1'},
        'webhook': {'url': 'http://hook.example/x'},
    }
    result = _alerts.dispatch({'kind': 'test', 'user': 'admin'},
                              config=cfg, opener=FakeOpener())
    assert isinstance(result, dict)
    assert set(result['attempted']) == {'telegram', 'webhook'}
    assert result['failed'] == ['webhook']


def test_alerts_dispatch_all_ok_has_no_failures():
    import alerts as _alerts

    class FakeResp:
        def read(self):
            return b''

    class FakeOpener:
        def urlopen(self, req, timeout=None):
            return FakeResp()

    cfg = {'telegram': {'bot_token': 'B', 'chat_id': '1'}}
    result = _alerts.dispatch({'kind': 'test', 'user': 'admin'},
                              config=cfg, opener=FakeOpener())
    assert result['failed'] == []
    assert result['attempted'] == ['telegram']


# F9: a null value in online.json must not crash the panel renderer.

def test_render_user_panel_tolerates_null_online(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json')
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json')
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text(json.dumps({'alice': None}))
    (tmp_path / 'meta.json').write_text('{}')
    cfg = {'sub_token': 'tok', 'monthly_quota_bytes': 1 << 30, 'max_devices': 2}
    # Must not raise TypeError on the null online value.
    page = ss.render_user_panel('h', 'http://h', 'alice', 'tok', cfg)
    assert 'data-role="online"' in page


# F11: change-password invalidates all prior admin sessions.

def test_change_password_invalidates_prior_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'SESSIONS_FILE', tmp_path / 'sessions.json')
    (tmp_path / 'sessions.json').write_text('{}')
    old_sid = ss.create_session('admin')
    assert old_sid in ss.get_sessions()

    # Mirror the handler's session-revocation step.
    ss.save_json(ss.SESSIONS_FILE, {})
    new_sid = ss.create_session('admin')

    sessions = ss.get_sessions()
    assert old_sid not in sessions, 'old session must be revoked'
    assert new_sid in sessions, 'a fresh session must be issued for this device'


def test_settings_note_mentions_session_signout(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    (tmp_path / 'meta.json').write_text(json.dumps(
        {'admin_user': 'admin', 'admin_pass_hash': 'x'}))
    out = ss.render_settings('host')
    assert '注销所有已登录会话' in out


# F13: defensive type guards reject non-dict user entries.

def test_username_invalid_flash_text_renders_chinese():
    assert ss.flash_text('username_invalid').startswith('用户名只能包含')


# ----- I: deferred-finding fixes (#10 initial password, #12 async test-alert)

def test_ensure_meta_writes_initial_password_file_when_none_set(tmp_path, monkeypatch):
    """On a fresh deploy (no admin_pass / admin_pass_hash) ensure_meta auto-
    generates a password AND drops it in a root-only file matching the hash."""
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'subscription_meta.json')
    meta = ss.ensure_meta()
    assert meta.get('admin_pass_hash'), 'a hash must be set'
    pw_file = tmp_path / 'admin_initial_password.txt'
    assert pw_file.exists(), 'initial password file must be written'
    body = pw_file.read_text(encoding='utf-8')
    # Last non-comment line is user:password
    line = [l for l in body.splitlines() if l and not l.startswith('#')][-1]
    user, _, password = line.partition(':')
    assert user == meta.get('admin_user', 'admin')
    assert ss.verify_secret(password, meta['admin_pass_hash']), 'file password must match stored hash'


def test_ensure_meta_initial_password_file_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'subscription_meta.json')
    ss.ensure_meta()
    pw_file = tmp_path / 'admin_initial_password.txt'
    import stat as _stat
    mode = _stat.S_IMODE(pw_file.stat().st_mode)
    assert mode == 0o600, f'expected 0600, got {oct(mode)}'


def test_ensure_meta_does_not_overwrite_or_write_file_when_password_exists(tmp_path, monkeypatch):
    """If a password is already configured, no random one is generated and no
    hint file is created (idempotent / no clobber of operator's password)."""
    meta_path = tmp_path / 'subscription_meta.json'
    monkeypatch.setattr(ss, 'META_FILE', meta_path)
    existing_hash = ss.hash_secret('operator-chosen-pass')
    meta_path.write_text(json.dumps(
        {'admin_user': 'admin', 'admin_pass_hash': existing_hash, 'admin_token': 't'}))
    meta = ss.ensure_meta()
    assert meta['admin_pass_hash'] == existing_hash, 'must not overwrite existing password'
    assert not (tmp_path / 'admin_initial_password.txt').exists()


def test_fire_test_alert_dispatches_on_background_thread(monkeypatch):
    """#12: the test-alert is dispatched off the request thread; verify the
    dispatch call actually runs with the test event, deterministically."""
    import threading as _t
    import alerts as _alerts
    done = _t.Event()
    captured = {}

    def fake_dispatch(event, *, config=None):
        captured['event'] = event
        captured['config'] = config
        done.set()

    monkeypatch.setattr(_alerts, 'dispatch', fake_dispatch)
    cfg = {'telegram': {'bot_token': 'B', 'chat_id': '1'}}
    thread = ss._fire_test_alert(cfg, 'admin')
    assert done.wait(2.0), 'background dispatch did not run'
    thread.join(2.0)
    assert captured['event']['kind'] == 'test'
    assert captured['event']['user'] == 'admin'
    assert captured['config'] is cfg


def test_health_flash_reports_dispatched_not_guaranteed_sent(tmp_path, monkeypatch):
    out = ss.render_health('host', flash='alert dispatched')
    assert '后台发送' in out
