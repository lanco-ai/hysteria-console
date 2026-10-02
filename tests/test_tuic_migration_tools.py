import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_deploy_guard_rejects_metered_and_corrupt_configs(tmp_path):
    script = ROOT / 'scripts/tuic/guard-legacy-deploy.py'
    for data, expected in [('{}', 0), ('{"inbounds": []}', 1), ('{', 1)]:
        path = tmp_path / 'tuic.json'
        path.write_text(data)
        result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True)
        assert result.returncode == expected


def test_runtime_gate_rejects_unsupported_artifact_without_starting_server(tmp_path):
    binary = tmp_path / 'unsupported'
    binary.write_text('#!/bin/sh\nprintf "sing-box version 1.14.2\\nTags: with_quic\\n"\n')
    binary.chmod(0o700)
    result = subprocess.run([
        sys.executable, str(ROOT / 'scripts/tuic/runtime-gate.py'),
        '--binary', str(binary), '--sha256', hashlib.sha256(binary.read_bytes()).hexdigest(),
    ], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'unsupported artifact' in result.stdout


def test_prepare_creates_only_offline_candidate_with_quota_plan(tmp_path):
    import json
    import tuic_config
    binary = tmp_path / 'fixture-binary'
    binary.write_text('#!/bin/sh\nif [ "$1" = version ]; then printf "sing-box version 1.14.2\\nTags: with_quic,with_v2ray_api\\n"; fi\n')
    binary.chmod(0o700)
    values = {
        'legacy': tuic_config._base_config(),
        'users': {'alice': {'vless_uuid': '11111111-1111-4111-8111-111111111111',
                            'sub_token': 'fixture', 'tuic_enabled': True},
                  'expired': {'vless_uuid': '22222222-2222-4222-8222-222222222222',
                              'sub_token': 'fixture', 'expires_at': '2000-01-01'}},
        'daily': {}, 'meta': {'settlement_day': 1},
    }
    args = []
    for name, value in values.items():
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        args.extend(['--' + name, str(path)])
    output = tmp_path / 'candidate'
    result = subprocess.run([
        sys.executable, str(ROOT / 'scripts/tuic/prepare-migration.py'),
        '--binary', str(binary), '--sha256', hashlib.sha256(binary.read_bytes()).hexdigest(),
        '--output', str(output), '--accept-runtime-defaults', *args,
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    candidate = json.loads((output / 'tuic.json').read_text())
    assert [row['name'] for row in candidate['inbounds'][0]['users']] == ['alice']
    assert candidate['inbounds'][0]['listen_port'] == 9443
    for name, value in values.items():
        assert json.loads((tmp_path / (name + '.json')).read_text()) == value
    assert not (tmp_path / 'tuic_user_state.json').exists()


def test_legacy_deploy_guard_cannot_bypass_active_mode_by_removing_config(tmp_path):
    import json
    script = ROOT / 'scripts/tuic/guard-legacy-deploy.py'
    state = tmp_path / 'state'
    state.mkdir()
    mode = state / 'tuic_user_mode.json'
    mode.write_text(json.dumps({'version': 1, 'mode': 'active'}))
    args = [sys.executable, str(script), str(tmp_path / 'tuic.json')]
    assert subprocess.run(args, capture_output=True).returncode == 1
    mode.write_text(json.dumps({'version': 1, 'mode': 'legacy'}))
    assert subprocess.run(args, capture_output=True).returncode == 0


def runtime_gate_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('tuic_runtime_gate', ROOT / 'scripts/tuic/runtime-gate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_fixture_listeners_are_both_loopback_only():
    import ipaddress
    gate = runtime_gate_module()
    rows = gate.loopback_inbounds({'type': 'tuic', 'listen_port': 12345, 'users': []})
    assert {row['listen'] for row in rows} == {'127.0.0.1', '::1'}
    assert all(ipaddress.ip_address(row['listen']).is_loopback for row in rows)
    assert all(row['listen_port'] == 12345 and row['type'] == 'tuic' for row in rows)


def test_runtime_counter_gate_waits_for_visibility_but_remains_bounded():
    import pytest
    gate = runtime_gate_module()
    states = iter([{}, {'alice': {'rx': 10, 'tx': 20}}])
    assert gate.await_counters(lambda: next(states), 'alice', {'rx': 0, 'tx': 0},
                               {'rx': 10, 'tx': 20}, timeout=.5) == {'alice': {'rx': 10, 'tx': 20}}
    with pytest.raises(RuntimeError, match='counter visibility'):
        gate.await_counters(lambda: {}, 'alice', {'rx': 0, 'tx': 0}, {'rx': 10, 'tx': 20}, timeout=.01)
