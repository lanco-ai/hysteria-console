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
    monkeypatch.setattr(tl, 'META_FILE', str(tmp_path / 'subscription_meta.json'),
                        raising=False)
    Path(tl.META_FILE).write_text('{}')

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
    tl.check_alerts(users={'alice': {}}, now=today,
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
    tl.check_alerts(users={'alice': {}}, now=today,
                    month_key='2026-05', _opener=opener)
    assert len(sent) == 1, 'anomaly must fire on first tick'
    tl.check_alerts(users={'alice': {}}, now=today,
                    month_key='2026-05', _opener=opener)
    assert len(sent) == 1, 'second tick same day must NOT re-fire'


def test_quota_80_fires_when_crossed(tmp_path, monkeypatch):
    today = datetime(2026, 5, 15)  # day >= settlement_day(12) -> cycle is "2026-05"
    quota = 30 * GiB
    # Aim for 90% of quota after scaling, regardless of DISPLAY_MULTIPLIER value.
    raw = int(0.90 * quota / tl._DM)
    daily_with_quota = {today.strftime('%Y-%m-%d'):
                        {'alice': {'tx': 0, 'rx': raw, 'total': raw}}}
    sent, opener, _ = _setup(
        tmp_path, daily=daily_with_quota,
        usage={},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}})
    tl.check_alerts(
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        now=today, month_key='2026-05', _opener=opener)
    assert len(sent) == 1
    assert b'quota_80' in sent[0]['body']


def test_quota_does_not_refire_same_month(tmp_path, monkeypatch):
    today = datetime(2026, 5, 15)
    quota = 30 * GiB
    raw = int(0.90 * quota / tl._DM)
    daily_with_quota = {today.strftime('%Y-%m-%d'):
                        {'alice': {'tx': 0, 'rx': raw, 'total': raw}}}
    sent, opener, _ = _setup(
        tmp_path, daily=daily_with_quota,
        usage={},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}})
    for _ in range(3):
        tl.check_alerts(
            users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
            now=today, month_key='2026-05', _opener=opener)
    assert len(sent) == 1


def test_quota_transport_failure_is_retryable_next_tick(tmp_path, monkeypatch):
    today = datetime(2026, 5, 15)
    quota = 30 * GiB
    raw = int(0.90 * quota / tl._DM)
    daily = {
        today.strftime('%Y-%m-%d'): {
            'alice': {'tx': 0, 'rx': raw, 'total': raw},
        },
    }
    sent, success_opener, state_path = _setup(
        tmp_path, daily=daily, usage={},
        users={'alice': {'guest': True, 'monthly_quota_bytes': quota}},
        online={}, monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}},
    )

    class FailingOpener:
        def urlopen(self, _req, timeout=None):
            del timeout
            raise OSError('temporary failure')

    users = {'alice': {'guest': True, 'monthly_quota_bytes': quota}}
    tl.check_alerts(
        users=users, now=today, month_key='2026-05',
        daily=daily, _opener=FailingOpener(),
    )
    assert 'alice' not in __import__('json').loads(
        state_path.read_text()
    )['quota_80']

    tl.check_alerts(
        users=users, now=today, month_key='2026-05',
        daily=daily, _opener=success_opener,
    )
    assert len(sent) == 1


def test_expiry_soon_alert_fires_once_per_expiry_date(tmp_path, monkeypatch):
    today = datetime(2026, 6, 3)
    sent, opener, _ = _setup(
        tmp_path,
        daily={},
        usage={},
        users={'alice': {'expires_at': '2026-06-05'}},
        online={},
        monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}, 'expiry_warn_days': 3},
    )
    for _ in range(2):
        tl.check_alerts(
            users={'alice': {'expires_at': '2026-06-05'}},
            now=today, month_key='2026-06', _opener=opener)

    assert len(sent) == 1
    assert b'expiry_soon' in sent[0]['body']


def test_expired_alert_fires_once_per_expiry_date(tmp_path, monkeypatch):
    today = datetime(2026, 6, 3)
    sent, opener, _ = _setup(
        tmp_path,
        daily={},
        usage={},
        users={'alice': {'expires_at': '2026-06-02'}},
        online={},
        monkeypatch=monkeypatch,
        alerts_cfg={'webhook': {'url': 'https://example.invalid/'}},
    )
    for _ in range(2):
        tl.check_alerts(
            users={'alice': {'expires_at': '2026-06-02'}},
            now=today, month_key='2026-06', _opener=opener)

    assert len(sent) == 1
    assert b'expiry_expired' in sent[0]['body']


def test_reset_paths_clear_cycle_daily_hourly_for_user(tmp_path, monkeypatch):
    """Actual POST resets must clear current history and preserve old history."""
    import json
    import subscription_service as ss
    from tests.test_admin_mutations_ajax import (
        _configure_state, _json_post, _revision, _running_server,
        _seed_users, _stub_side_effects, _write_json,
    )

    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)
    monkeypatch.setattr(ss, 'local_now', lambda: datetime.fromisoformat('2026-01-15T12:00:00+08:00'))
    original = {'tx': 11, 'rx': 22, 'total': 33}
    zero = {'tx': 0, 'rx': 0, 'total': 0}
    with _running_server() as server:
        for route in ('reset-usage', 'refresh-usage', 'reset-usage-all'):
            _write_json(state['USAGE_DAILY_FILE'], {
                '2026-01-15': {'alice': original},
                '2025-12-01': {'alice': original},
            })
            _write_json(state['USAGE_HOURLY_FILE'], {
                '2026-01-15T12': {'alice': original},
                '2025-12-01T12': {'alice': original},
            })
            status, _, _ = _json_post(
                server,
                f'/admin/{route}?token=admin-token&revision={_revision(state)}',
                {'user': 'alice'},
            )
            assert status == 200
            daily = json.loads(state['USAGE_DAILY_FILE'].read_text())
            hourly = json.loads(state['USAGE_HOURLY_FILE'].read_text())
            assert daily['2026-01-15']['alice'] == zero
            assert hourly['2026-01-15T12']['alice'] == zero
            assert daily['2025-12-01']['alice'] == original
            assert hourly['2025-12-01T12']['alice'] == original
