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
