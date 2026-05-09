"""Integration tests for traffic_limiter.check_alerts.

These tests stub fcntl (via conftest) and stub the network so check_alerts can
run without a real cron environment.
"""
from datetime import datetime, timedelta
from pathlib import Path

import traffic_limiter as tl

GiB = 1 << 30


def _setup(tmp_path, daily, usage, users, online, monkeypatch, alerts_cfg=None):
    monkeypatch.setattr(tl, 'USAGE_DAILY_FILE', str(tmp_path / 'usage_daily.json'),
                        raising=False)
    Path(tl.USAGE_DAILY_FILE).write_text(__import__('json').dumps(daily))

    import alerts
    state_path = tmp_path / 'alert_state.json'
    cfg_path = tmp_path / 'alerts.json'
    monkeypatch.setattr(alerts, 'STATE_FILE', state_path, raising=False)
    monkeypatch.setattr(alerts, 'CONFIG_FILE', cfg_path, raising=False)
    if alerts_cfg is not None:
        cfg_path.write_text(__import__('json').dumps(alerts_cfg))

    sent = []

    class CapturingOpener:
        def urlopen(self, req, timeout=None):
            sent.append({'url': req.full_url, 'body': req.data})
            class _R:
                def read(self_inner): return b''
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
            return _R()

    return sent, CapturingOpener(), state_path


def test_no_op_when_alerts_config_missing(tmp_path, monkeypatch):
    today = datetime(2026, 5, 5)
    daily = {today.strftime('%Y-%m-%d'): {'alice': {'tx': 0, 'rx': 50 * GiB,
                                                    'total': 50 * GiB}}}
    for i in range(1, 8):
        d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        daily[d] = {'alice': {'tx': 0, 'rx': GiB, 'total': GiB}}
    sent, opener, _ = _setup(tmp_path, daily=daily, usage={}, users={'alice': {}},
                             online={}, monkeypatch=monkeypatch, alerts_cfg=None)
    tl.check_alerts(usage={}, users={'alice': {}}, online={}, now=today,
                    month_key='2026-05', _opener=opener)
    assert sent == []


def test_anomaly_fires_once_per_day(tmp_path, monkeypatch):
    today = datetime(2026, 5, 5)
    daily = {today.strftime('%Y-%m-%d'): {'alice': {'tx': 0, 'rx': 50 * GiB,
                                                    'total': 50 * GiB}}}
    for i in range(1, 8):
        d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        daily[d] = {'alice': {'tx': 0, 'rx': GiB, 'total': GiB}}
    sent, opener, state_path = _setup(
        tmp_path, daily=daily, usage={}, users={'alice': {}}, online={},
        monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}})
    tl.check_alerts(usage={}, users={'alice': {}}, online={}, now=today,
                    month_key='2026-05', _opener=opener)
    assert len(sent) == 1, 'anomaly must fire on first tick'
    tl.check_alerts(usage={}, users={'alice': {}}, online={}, now=today,
                    month_key='2026-05', _opener=opener)
    assert len(sent) == 1, 'second tick same day must NOT re-fire'


def test_quota_80_fires_when_crossed(tmp_path, monkeypatch):
    today = datetime(2026, 5, 5)
    quota = 30 * GiB
    # Aim for 90% of quota after scaling, regardless of DISPLAY_MULTIPLIER value.
    raw = int(0.90 * quota / tl._DM)
    sent, opener, _ = _setup(
        tmp_path, daily={},
        usage={'2026-05': {'alice': {'tx': 0, 'rx': raw, 'total': raw}}},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}})
    tl.check_alerts(
        usage={'2026-05': {'alice': {'tx': 0, 'rx': raw, 'total': raw}}},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, now=today, month_key='2026-05', _opener=opener)
    assert len(sent) == 1
    assert b'quota_80' in sent[0]['body']


def test_quota_does_not_refire_same_month(tmp_path, monkeypatch):
    today = datetime(2026, 5, 5)
    quota = 30 * GiB
    raw = int(0.90 * quota / tl._DM)
    sent, opener, _ = _setup(
        tmp_path, daily={},
        usage={'2026-05': {'alice': {'tx': 0, 'rx': raw, 'total': raw}}},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}})
    for _ in range(3):
        tl.check_alerts(
            usage={'2026-05': {'alice': {'tx': 0, 'rx': raw, 'total': raw}}},
            users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
            online={}, now=today, month_key='2026-05', _opener=opener)
    assert len(sent) == 1


def test_reset_paths_do_not_touch_hourly_data():
    """Regression guard: manual-reset code paths must not modify usage_hourly.json
    (per spec %6 + ADR-0001). The reset logic lives inline inside the request
    handler dispatcher; this static check is cheaper than spinning the full HTTP
    flow.
    """
    import re
    src = (Path(__file__).resolve().parents[1] / "hysteria" / "subscription_service.py").read_text(encoding="utf-8")
    blocks = re.findall(
        r"if path == .{1}/admin/reset-usage[^.]*.{1}:[\s\S]+?(?=\n        if path ==|\n    def |\Z)",
        src,
    )
    assert blocks, "could not locate reset handler blocks - test needs updating"
    for b in blocks:
        assert "USAGE_HOURLY_FILE" not in b, (
            "reset handler references USAGE_HOURLY_FILE; "
            "manual reset must leave hourly facts intact (spec section 6)"
        )
