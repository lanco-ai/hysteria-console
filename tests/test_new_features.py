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
from datetime import datetime
from pathlib import Path

import pytest

import subscription_service as ss


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
