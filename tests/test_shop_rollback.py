"""A rollback must preserve merchant data and reject incompatible code before stopping service."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_react_release import _dist, release

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'hy2_shop_rollback', ROOT / 'scripts/hy2_shop_rollback.py'
)
rollback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollback)


@pytest.fixture
def deployment(tmp_path):
    runtime = tmp_path / 'runtime'
    module = runtime / 'web_api/shop_store.py'
    module.parent.mkdir(parents=True)
    module.write_bytes((ROOT / 'hysteria/web_api/shop_store.py').read_bytes())
    module.chmod(0o640)
    settings = runtime / 'state/shop/source-merchant.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                'revision': 7,
                'telegram': 'my_shop',
                'wechat': 'my_shop',
                'skus': {
                    '2:7': {'price_cents': 12800, 'promo_price_cents': 11800, 'published': True}
                },
                'display': {'2': {'badge': '学生价', 'sold': 36}},
            }
        )
    )
    dist = _dist(tmp_path)
    previous = release.install_release(dist, runtime / 'panel').release_id
    (dist / 'assets/index.js').write_text('export default 2;')
    current = release.install_release(dist, runtime / 'panel').release_id
    release.activate_release(runtime / 'panel', current)
    candidate = tmp_path / 'candidate.py'
    candidate.write_bytes(module.read_bytes() + b'\n# compatible rollback target\n')
    return runtime, candidate, previous, current


def run_rollback(deployment, **kwargs):
    runtime, candidate, previous, _ = deployment
    return rollback.rollback_shop(
        runtime, candidate, previous, deploy_lock=runtime / 'deploy.lock', **kwargs
    )


def snapshot(runtime):
    return (
        (runtime / 'web_api/shop_store.py').read_bytes(),
        (runtime / 'state/shop/source-merchant.json').read_bytes(),
        (runtime / 'panel/current').readlink(),
    )


def test_historical_schema_rejected_before_service_or_files_change(deployment):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_d767a70.py').read_bytes())
    before = snapshot(runtime)
    calls = []
    with pytest.raises(rollback.RollbackError, match='incompatible'):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls == []
    assert snapshot(runtime) == before


def test_local_names_block_pre_names_rollback_only_after_a_name_was_saved(deployment):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_8fedc2e.py').read_bytes())
    # Existing promo/display settings alone still pass the previous strict schema.
    run_rollback(deployment)
    settings = runtime / 'state/shop/source-merchant.json'
    saved = json.loads(settings.read_text())
    saved['local_names'] = {'products': {'6000000000001': '自定义指导'},
                            'variants': {'6000000000001:1': '自定义规格'}}
    settings.write_text(json.dumps(saved))
    before = snapshot(runtime)
    calls = []
    with pytest.raises(rollback.RollbackError, match='incompatible'):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls == [] and snapshot(runtime) == before
    # The new validator can itself run inside the rollback compatibility guard.
    candidate.write_bytes((ROOT / 'hysteria/web_api/shop_store.py').read_bytes())
    run_rollback(deployment)
    assert snapshot(runtime) == before


def test_cli_defaults_to_check_only(deployment):
    runtime, candidate, previous, _ = deployment
    before = snapshot(runtime)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / 'scripts/hy2_shop_rollback.py'),
            '--runtime-root',
            str(runtime),
            '--store-file',
            str(candidate),
            '--release',
            previous,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert 'check only' in result.stdout
    assert snapshot(runtime) == before


def test_disabled_sources_block_previous_schema_before_any_rollback_mutation(deployment):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_f143cea.py').read_bytes())
    run_rollback(deployment)
    settings = runtime / 'state/shop/source-merchant.json'
    value = json.loads(settings.read_text())
    value['source_enabled'] = {'gpt': False}
    settings.write_text(json.dumps(value))
    before, calls = snapshot(runtime), []
    with pytest.raises(rollback.RollbackError, match='incompatible'):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls == [] and snapshot(runtime) == before
    candidate.write_bytes((ROOT / 'hysteria/web_api/shop_store.py').read_bytes())
    run_rollback(deployment)
    assert snapshot(runtime) == before


@pytest.mark.parametrize('bookmark_settings', [
    {'source_enabled': {'wzyp': False}},
    {'source_lifecycle': {'wzyp': {'generation': 1, 'deleted': True}}},
    {'source_lifecycle': {'wzyp': {'generation': 2, 'deleted': False}}},
])
def test_bookmark_settings_block_pre_bookmark_rollback_before_service_or_files_change(deployment, bookmark_settings):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_9608aeb.py').read_bytes())
    run_rollback(deployment)
    settings = runtime / 'state/shop/source-merchant.json'
    value = json.loads(settings.read_text())
    settings.write_text(json.dumps({**value, **bookmark_settings}))
    before, calls = snapshot(runtime), []
    with pytest.raises(rollback.RollbackError, match='incompatible'):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls == [] and snapshot(runtime) == before
    candidate.write_bytes((ROOT / 'hysteria/web_api/shop_store.py').read_bytes())
    run_rollback(deployment)
    assert snapshot(runtime) == before


def test_legacy_target_is_compatible_only_without_new_price_fields(deployment):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_d767a70.py').read_bytes())
    settings = runtime / 'state/shop/source-merchant.json'
    saved = json.loads(settings.read_text())
    del saved['skus']['2:7']['promo_price_cents']
    settings.write_text(json.dumps(saved))
    before = snapshot(runtime)
    run_rollback(deployment)
    assert snapshot(runtime) == before


def test_cli_rejects_incompatible_schema_without_printing_state(deployment):
    runtime, candidate, previous, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_d767a70.py').read_bytes())
    before = snapshot(runtime)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / 'scripts/hy2_shop_rollback.py'),
            '--runtime-root',
            str(runtime),
            '--store-file',
            str(candidate),
            '--release',
            previous,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert 'incompatible' in result.stderr
    assert 'my_shop' not in result.stdout + result.stderr
    assert snapshot(runtime) == before


def test_apply_cannot_restart_live_service_for_a_different_runtime(deployment):
    runtime, candidate, previous, _ = deployment
    before = snapshot(runtime)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / 'scripts/hy2_shop_rollback.py'),
            '--runtime-root',
            str(runtime),
            '--store-file',
            str(candidate),
            '--release',
            previous,
            '--apply',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert 'apply requires the live' in result.stderr
    assert snapshot(runtime) == before


def test_check_only_does_not_switch_code_or_restart_service(deployment):
    runtime, _, _, _ = deployment
    before = snapshot(runtime)
    calls = []
    run_rollback(deployment, service=calls.append, health=lambda: None)
    assert calls == []
    assert snapshot(runtime) == before


def test_compatible_rollback_keeps_settings_bytes_and_lock_until_ready(deployment):
    runtime, candidate, previous, _ = deployment
    settings = runtime / 'state/shop/source-merchant.json'
    before = settings.read_bytes()
    calls = []

    def service(action):
        calls.append(action)
        with pytest.raises(rollback.state_store.LockTimeout):
            with rollback.state_store.file_lock(str(settings) + '.lock', timeout=0):
                pytest.fail('merchant write lock not held')

    def health():
        assert (runtime / 'web_api/shop_store.py').read_bytes() == candidate.read_bytes()
        assert (runtime / 'panel/current').readlink() == Path('releases') / previous

    run_rollback(deployment, apply=True, service=service, health=health)
    assert calls == ['stop', 'start']
    assert settings.read_bytes() == before
    assert (runtime / 'web_api/shop_store.py').stat().st_mode & 0o777 == 0o640


def test_failed_health_restores_previous_code_and_frontend_without_reverting_edits(deployment):
    runtime, _, _, _ = deployment
    before = snapshot(runtime)
    calls = []
    probes = []

    def health():
        probes.append(True)
        if len(probes) == 1:
            raise RuntimeError('candidate unhealthy')

    with pytest.raises(rollback.RollbackError, match='restored'):
        run_rollback(deployment, apply=True, service=calls.append, health=health)
    assert snapshot(runtime) == before
    assert calls == ['stop', 'start', 'stop', 'start']
    assert len(probes) == 2


def test_invalid_target_release_cannot_stop_service(deployment):
    runtime, candidate, _, current = deployment
    calls = []
    before = snapshot(runtime)
    with pytest.raises(release.ReleaseError):
        run_rollback(
            (runtime, candidate, '0' * 24, current),
            apply=True,
            service=calls.append,
            health=lambda: None,
        )
    assert calls == []
    assert snapshot(runtime) == before


def test_failed_recovery_is_never_reported_as_restored(deployment):
    calls = []

    def health():
        raise RuntimeError('unhealthy')

    with pytest.raises(rollback.RollbackError, match='operator intervention'):
        run_rollback(deployment, apply=True, service=calls.append, health=health)
    assert calls == ['stop', 'start', 'stop', 'start']


@pytest.mark.parametrize('contents', ['{broken', '{"revision": 8, "skus": {}}'])
def test_unreadable_settings_cannot_trigger_a_rollback(deployment, contents):
    runtime, _, _, _ = deployment
    (runtime / 'state/shop/source-merchant.json').write_text(contents)
    before = snapshot(runtime)
    calls = []
    with pytest.raises(rollback.RollbackError):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls == []
    assert snapshot(runtime) == before


@pytest.mark.parametrize('deleted', [True, False])
def test_lifecycle_blocks_pre_lifecycle_rollback_even_after_source_is_readded(deployment, deleted):
    runtime, candidate, _, _ = deployment
    candidate.write_bytes((ROOT / 'tests/fixtures/shop/legacy_settings_b0017f5.py').read_bytes())
    run_rollback(deployment)
    settings=runtime / 'state/shop/source-merchant.json'
    saved=json.loads(settings.read_text())
    saved['source_lifecycle']={'gpt': {'generation': 1 if deleted else 2, 'deleted': deleted}}
    settings.write_text(json.dumps(saved))
    before, calls=snapshot(runtime), []
    with pytest.raises(rollback.RollbackError, match='incompatible'):
        run_rollback(deployment, apply=True, service=calls.append, health=lambda: None)
    assert calls==[] and snapshot(runtime)==before
    candidate.write_bytes((ROOT / 'hysteria/web_api/shop_store.py').read_bytes())
    run_rollback(deployment)
    assert snapshot(runtime)==before
