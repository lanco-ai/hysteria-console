from datetime import datetime, timedelta

import cost_calibrator as cc


def test_parse_netdev_and_public_totals_exclude_virtual_ifaces():
    text = """
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0
  eth0: 1000 0 0 0 0 0 0 0 3000 0 0 0 0 0 0 0
docker0: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0
 veth1: 777 0 0 0 0 0 0 0 777 0 0 0 0 0 0 0
"""
    totals = cc.public_net_totals(cc.parse_netdev(text))

    assert totals['rx'] == 1000
    assert totals['tx'] == 3000
    assert totals['total'] == 4000
    assert totals['ifaces'] == ['eth0']


def test_update_sample_uses_previous_net_counter_as_baseline(tmp_path):
    path = tmp_path / 'cost_calibration.json'
    now = datetime(2026, 6, 3, 12, 0, 0)

    cc.update_sample(
        path,
        app_raw_bytes=100,
        now=now,
        net_totals={'rx': 1000, 'tx': 2000, 'total': 3000, 'ifaces': ['eth0']},
    )
    state = cc.update_sample(
        path,
        app_raw_bytes=500,
        now=now + timedelta(minutes=5),
        net_totals={'rx': 1600, 'tx': 2800, 'total': 4400, 'ifaces': ['eth0']},
    )

    assert len(state['samples']) == 1
    assert state['samples'][0]['app_raw_bytes'] == 500
    assert state['samples'][0]['net_total_delta'] == 1400
    assert state['samples'][0]['net_tx_delta'] == 800


def test_update_sample_writes_compact_json_that_round_trips(tmp_path):
    import json

    path = tmp_path / 'cost_calibration.json'
    now = datetime(2026, 6, 3, 12, 0, 0)
    for minutes, total in ((0, 3000), (5, 4400), (10, 5000)):
        state = cc.update_sample(
            path,
            app_raw_bytes=500,
            now=now + timedelta(minutes=minutes),
            net_totals={'rx': total // 3, 'tx': total - total // 3, 'total': total, 'ifaces': ['eth0']},
        )

    text = path.read_text()
    # One line: the file is rewritten every limiter tick, so no indentation.
    assert text.count('\n') == 1 and text.endswith('\n')
    assert ', ' not in text and ': ' not in text
    assert json.loads(text) == state
    assert len(state['samples']) == 2


def test_summarize_overview_reads_once_and_matches_per_window_summaries(tmp_path, monkeypatch):
    import json

    path = tmp_path / 'cost_calibration.json'
    now = datetime(2026, 6, 3, 12, 0, 0)
    for minutes in range(0, 600, 30):
        cc.update_sample(
            path,
            app_raw_bytes=2 * 1024 ** 2,
            now=now + timedelta(minutes=minutes),
            net_totals={'rx': minutes * 10 ** 6, 'tx': minutes * 2 * 10 ** 6,
                        'total': minutes * 3 * 10 ** 6, 'ifaces': ['eth0']},
        )
    state = json.loads(path.read_text())
    later = now + timedelta(hours=10)
    reads = []
    original = cc.state_store.load_json
    monkeypatch.setattr(cc.state_store, 'load_json', lambda *a, **k: reads.append(a) or original(*a, **k))

    summary, windows = cc.summarize_overview(path, current_multiplier=2.28, now=later)

    assert len(reads) == 1
    assert summary == cc.summarize_state(state, current_multiplier=2.28, now=later)
    assert windows == [
        cc.summarize_state(state, current_multiplier=2.28, now=later, window_hours=hours)
        for hours in cc.WINDOW_HOURS
    ]
    # Callers attach the windows to the default summary; that must stay acyclic.
    summary['windows'] = windows
    json.dumps(summary)


def test_update_sample_keeps_eight_days_of_samples_and_all_nic_bytes_hourly(tmp_path):
    import json

    path = tmp_path / 'cost_calibration.json'
    start = datetime(2026, 6, 1, 0, 0, 0)
    total = 0
    for hour in range(0, 24 * 10 + 1):
        total += 1000
        cc.update_sample(
            path,
            app_raw_bytes=0 if hour % 2 else 500,
            now=start + timedelta(hours=hour),
            net_totals={'rx': total // 2, 'tx': total - total // 2, 'total': total, 'ifaces': ['eth0']},
        )
    state = json.loads(path.read_text())
    newest = start + timedelta(hours=240)

    stamps = [datetime.fromisoformat(sample['ts']) for sample in state['samples']]
    assert min(stamps) == newest - timedelta(hours=cc.SAMPLE_RETENTION_HOURS)
    assert all(sample['app_raw_bytes'] == 500 for sample in state['samples'])
    # The provider meters the whole NIC, so idle ticks still count hourly.
    assert sum(bucket['total'] for bucket in state['net_hourly'].values()) == 1000 * 240


def test_samples_span_idle_ticks_while_the_hourly_ledger_advances_every_tick(tmp_path):
    path = tmp_path / 'cost_calibration.json'
    now = datetime(2026, 6, 3, 12, 0, 0)
    ticks = ((0, 100, 1000), (30, 0, 1600), (90, 100, 2000))
    for minutes, app_raw, total in ticks:
        state = cc.update_sample(
            path,
            app_raw_bytes=app_raw,
            now=now + timedelta(minutes=minutes),
            net_totals={'rx': total, 'tx': 0, 'total': total, 'ifaces': ['eth0']},
        )
        if app_raw == 0:
            # The idle tick moves the ledger baseline but not the sample baseline.
            assert (state['last']['total'], state['sample_last']['total']) == (1600, 1000)

    # The sample still spans the idle tick, so idle NIC bytes count toward the cost ratio.
    assert [sample['net_total_delta'] for sample in state['samples']] == [1000]
    # The ledger books each tick's NIC bytes in that tick's own hour.
    assert state['net_hourly'] == {
        cc._net_hour_key(now + timedelta(minutes=30)): {'rx': 600, 'tx': 0, 'total': 600},
        cc._net_hour_key(now + timedelta(minutes=90)): {'rx': 400, 'tx': 0, 'total': 400},
    }
    assert (state['last']['total'], state['sample_last']['total']) == (2000, 2000)


def test_missing_hourly_totals_are_seeded_from_existing_samples(tmp_path):
    import json

    path = tmp_path / 'cost_calibration.json'
    now = datetime(2026, 6, 3, 12, 0, 0)
    path.write_text(json.dumps({
        'last': {'ts': now.isoformat(timespec='seconds'), 'rx': 100, 'tx': 200, 'total': 300, 'ifaces': ['eth0']},
        'samples': [{
            'ts': (now - timedelta(hours=2)).isoformat(timespec='seconds'), 'app_raw_bytes': 5,
            'net_rx_delta': 10, 'net_tx_delta': 20, 'net_total_delta': 30,
        }],
    }))

    state = cc.update_sample(
        path,
        app_raw_bytes=5,
        now=now + timedelta(minutes=30),
        net_totals={'rx': 110, 'tx': 220, 'total': 330, 'ifaces': ['eth0']},
    )

    assert state['net_hourly'] == {
        '2026-06-03T10': {'rx': 10, 'tx': 20, 'total': 30},
        '2026-06-03T12': {'rx': 10, 'tx': 20, 'total': 30},
    }


def test_cycle_net_totals_sum_utc_hours_from_the_cycle_start(tmp_path):
    import json
    from datetime import timezone
    from zoneinfo import ZoneInfo

    path = tmp_path / 'cost_calibration.json'
    base = datetime(2026, 10, 15, 7, 0, tzinfo=ZoneInfo('Asia/Shanghai'))  # 2026-10-14 23:00 UTC
    for minutes, total in ((0, 0), (30, 100), (90, 300)):
        cc.update_sample(
            path,
            app_raw_bytes=1,
            now=base + timedelta(minutes=minutes),
            net_totals={'rx': total, 'tx': 0, 'total': total, 'ifaces': ['eth0']},
        )
    state = json.loads(path.read_text())
    assert state['net_hourly'] == {
        '2026-10-14T23': {'rx': 100, 'tx': 0, 'total': 100},
        '2026-10-15T00': {'rx': 200, 'tx': 0, 'total': 200},
    }

    cycle_start = datetime(2026, 10, 15, tzinfo=timezone.utc)
    totals = cc.cycle_net_totals(state, since=cycle_start)
    assert (totals['total'], totals['rx'], totals['hours']) == (200, 200, 1)
    summary, _windows = cc.summarize_overview(
        path, current_multiplier=2.28, now=base + timedelta(hours=2), cycle_start=cycle_start)
    assert summary['cycle_net'] == totals


def test_summarize_state_returns_weighted_multiplier():
    mib = 1024 ** 2
    state = {
        'last': {'ifaces': ['eth0']},
        'samples': [
            {'ts': '2026-06-03T12:00:00', 'app_raw_bytes': 1000 * mib,
             'net_total_delta': 2000 * mib, 'net_tx_delta': 900 * mib},
            {'ts': '2026-06-03T12:05:00', 'app_raw_bytes': 3000 * mib,
             'net_total_delta': 9000 * mib, 'net_tx_delta': 3900 * mib},
        ],
    }

    summary = cc.summarize_state(state, current_multiplier=2.28)

    assert summary['sample_count'] == 2
    assert summary['included_sample_count'] == 2
    assert summary['suggested_multiplier'] == 2.75
    assert summary['egress_multiplier'] == 1.2
    assert summary['confidence'] == 'low'


def test_summarize_state_filters_small_and_trims_outlier():
    mib = 1024 ** 2
    samples = [
        {'ts': f'2026-06-03T12:{i:02d}:00', 'app_raw_bytes': 100 * mib,
         'net_total_delta': 200 * mib, 'net_tx_delta': 100 * mib}
        for i in range(10)
    ]
    samples.append({
        'ts': '2026-06-03T12:59:00',
        'app_raw_bytes': 1,
        'net_total_delta': 10 * mib,
        'net_tx_delta': 10 * mib,
    })
    samples.append({
        'ts': '2026-06-03T13:00:00',
        'app_raw_bytes': 100 * mib,
        'net_total_delta': 1500 * mib,
        'net_tx_delta': 100 * mib,
    })

    summary = cc.summarize_state(
        {'last': {'ifaces': ['eth0']}, 'samples': samples},
        current_multiplier=2.0,
        min_sample_app_bytes=1 * mib,
    )

    assert summary['sample_count'] == 12
    assert summary['ignored_sample_count'] == 1
    assert summary['included_sample_count'] == 11
    assert summary['trimmed_sample_count'] == 9
    assert round(summary['suggested_multiplier'], 2) == 2.0


def test_evaluate_multiplier_candidate_applies_with_guardrails():
    summary = {
        'confidence': 'medium',
        'suggested_multiplier': 2.4,
        'egress_multiplier': 1.4,
    }
    policy = {
        'enabled': True, 'mode': 'total', 'min_confidence': 'medium',
        'max_delta_percent': 25, 'min_delta_percent': 3, 'cooldown_hours': 24,
    }

    decision = cc.evaluate_multiplier_candidate(
        summary, 2.28, policy, now=datetime(2026, 6, 22, 16))

    assert decision['apply'] is True
    assert round(decision['candidate'], 2) == 2.4


def test_evaluate_multiplier_candidate_rejects_large_jump():
    summary = {
        'confidence': 'high',
        'suggested_multiplier': 4.0,
        'egress_multiplier': 1.4,
    }

    decision = cc.evaluate_multiplier_candidate(
        summary, 2.0, {'enabled': True, 'max_delta_percent': 25},
        now=datetime(2026, 6, 22, 16))

    assert decision['apply'] is False
    assert decision['reason'] == 'delta_too_large'


def test_maybe_auto_adjust_writes_multiplier_state(tmp_path):
    calibration = tmp_path / 'cost.json'
    runtime = tmp_path / 'display.json'
    policy = tmp_path / 'auto.json'
    now = datetime(2026, 6, 22, 16)
    samples = [
        {'ts': f'2026-06-22T15:{i:02d}:00', 'app_raw_bytes': 1 << 30,
         'net_total_delta': int(2.4 * (1 << 30)), 'net_tx_delta': 1 << 30}
        for i in range(12)
    ]
    calibration.write_text(__import__('json').dumps({'last': {}, 'samples': samples}))
    cc.save_auto_policy({
        'enabled': True, 'mode': 'total', 'min_confidence': 'medium',
        'max_delta_percent': 25, 'min_delta_percent': 3, 'cooldown_hours': 24,
    }, policy)

    result = cc.maybe_auto_adjust(
        calibration, current_multiplier=2.28,
        policy_path=policy, runtime_state_path=runtime, now=now)

    state = __import__('json').loads(runtime.read_text())
    assert result['applied'] is True
    assert state['multiplier'] == 2.4
    assert state['auto'] is True
