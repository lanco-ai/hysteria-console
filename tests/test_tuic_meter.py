import json

import tuic_meter as tm


def test_parse_listen_port_accepts_tuic_server_forms():
    assert tm.parse_listen_port('[::]:9443') == 9443
    assert tm.parse_listen_port('0.0.0.0:10443') == 10443
    assert tm.parse_listen_port('9443') == 9443
    assert tm.parse_listen_port('bad') == tm.DEFAULT_PORT


def test_extract_counters_from_nft_ruleset():
    ruleset = {
        'nftables': [
            {'rule': {
                'family': 'inet',
                'table': tm.NFT_TABLE,
                'chain': tm.NFT_INPUT_CHAIN,
                'expr': [
                    {'match': {'left': {'payload': {'protocol': 'udp', 'field': 'dport'}},
                               'op': '==', 'right': 9443}},
                    {'counter': {'packets': 2, 'bytes': 1200}},
                    {'comment': 'hy2-tuic-rx:9443'},
                ],
            }},
            {'rule': {
                'family': 'inet',
                'table': tm.NFT_TABLE,
                'chain': tm.NFT_OUTPUT_CHAIN,
                'expr': [
                    {'match': {'left': {'payload': {'protocol': 'udp', 'field': 'sport'}},
                               'op': '==', 'right': 9443}},
                    {'counter': {'packets': 3, 'bytes': 2400}},
                    {'comment': 'hy2-tuic-tx:9443'},
                ],
            }},
        ],
    }

    assert tm.extract_counters(ruleset, 9443) == {'rx': 1200, 'tx': 2400, 'total': 3600}


def test_get_tuic_traffic_baselines_then_returns_delta(tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    state_file = tmp_path / 'tuic_meter_state.json'
    config_file.write_text(json.dumps({'server': '[::]:9443'}))
    samples = iter([
        {'rx': 1000, 'tx': 2000, 'total': 3000},
        {'rx': 1500, 'tx': 2600, 'total': 4100},
    ])
    monkeypatch.setattr(tm, 'read_nft_counters', lambda port: next(samples))

    first = tm.get_tuic_traffic(config_file=config_file, state_file=state_file)
    second = tm.get_tuic_traffic(config_file=config_file, state_file=state_file)

    assert first == {'rx': 0, 'tx': 0, 'total': 0}
    assert second == {'rx': 500, 'tx': 600, 'total': 1100}
