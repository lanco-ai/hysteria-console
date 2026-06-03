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
    state = {
        'last': {'ifaces': ['eth0']},
        'samples': [
            {'ts': '2026-06-03T12:00:00', 'app_raw_bytes': 1000,
             'net_total_delta': 2000, 'net_tx_delta': 900},
            {'ts': '2026-06-03T12:05:00', 'app_raw_bytes': 3000,
             'net_total_delta': 9000, 'net_tx_delta': 3900},
        ],
    }

    summary = cc.summarize_state(state, current_multiplier=2.28)

    assert summary['sample_count'] == 2
    assert summary['suggested_multiplier'] == 2.75
    assert summary['egress_multiplier'] == 1.2
    assert summary['confidence'] == 'low'
