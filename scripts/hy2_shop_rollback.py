#!/usr/bin/env python3
"""Check/apply a shop_store + frontend rollback without downgrading merchant settings.

Only trusted local shop_store.py revisions with unchanged sibling dependencies are
supported. Check-only is the default; --apply is an explicit production operation.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hysteria'))
import hy2_panel_release as releases
import state_store


class RollbackError(RuntimeError):
    pass


def validate_candidate(source, settings):
    """Use the target version's own schema, not a guess based on field names."""
    candidate = types.ModuleType('web_api._rollback_candidate')
    candidate.__package__ = 'web_api'
    try:
        exec(compile(source, '<shop rollback candidate>', 'exec'), candidate.__dict__)
        candidate.validate_settings(settings)
    except Exception:
        # Never include settings or exception values in operator output.
        raise RollbackError(
            'rollback target is incompatible with current merchant settings'
        ) from None


def install_module(path, source, metadata):
    """Atomic code replacement preserving the runtime module's owner and mode."""
    descriptor, name = tempfile.mkstemp(prefix='.shop-rollback-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source)
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), metadata.st_mode & 0o777)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        state_store._fsync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def service_control(action):
    subprocess.run(['systemctl', action, 'hysteria-react.service'], check=True, capture_output=True)


def check_health():
    for _ in range(20):
        try:
            with urllib.request.urlopen(
                'http://127.0.0.1:8083/api/v1/shop/catalog', timeout=2
            ) as response:
                if response.status == 200 and isinstance(json.load(response).get('products'), list):
                    return
        except (OSError, urllib.error.URLError, ValueError, AttributeError):
            pass
        time.sleep(1)
    raise RollbackError('shop catalog did not become healthy')


def rollback_shop(
    runtime,
    candidate_file,
    release_id,
    *,
    apply=False,
    deploy_lock=Path('/run/hy2-locks/deploy.lock'),
    service=service_control,
    health=check_health,
):
    runtime = Path(runtime).resolve()
    if apply and service is service_control and runtime != Path('/root/hysteria').resolve():
        raise RollbackError(
            'apply requires the live /root/hysteria runtime for hysteria-react.service'
        )
    module = runtime / 'web_api/shop_store.py'
    settings = runtime / 'state/shop/source-merchant.json'
    panel = runtime / 'panel'
    if module.is_symlink() or not module.is_file():
        raise RollbackError('runtime shop module must be a regular file')
    source = Path(candidate_file).read_bytes()
    # Serialize with deployments and ShopStore._update. Hold the settings lock
    # through stop/swap/start/health so a new-model save cannot race the check.
    with state_store.file_lock(deploy_lock, timeout=2):
        with state_store.file_lock(str(settings) + '.lock', timeout=2):
            try:
                current_settings = state_store.load_json_strict(settings, {}, required=True)
            except state_store.InvalidJsonState:
                raise RollbackError('merchant settings are unreadable; rollback refused') from None
            validate_candidate(source, current_settings)
            previous_release = releases.inspect_release(panel).release_id
            releases.inspect_release(panel, release_id)
            previous_source, metadata = module.read_bytes(), module.stat()
            if not apply:
                return 'compatible; check only, no release or service changed'
            try:
                # Stop old workers before switching; pending writes cannot outlive
                # the settings lock and run old validation after a code rollback.
                service('stop')
                install_module(module, source, metadata)
                releases.activate_release(panel, release_id)
                service('start')
                health()
            except Exception:
                try:
                    service('stop')
                    install_module(module, previous_source, metadata)
                    releases.activate_release(panel, previous_release)
                    service('start')
                    health()
                except Exception:
                    raise RollbackError(
                        'rollback and recovery failed; operator intervention required'
                    ) from None
                raise RollbackError(
                    'rollback failed; previous code and frontend restored; settings unchanged'
                ) from None
    return 'rollback complete; merchant settings unchanged'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--store-file', type=Path, required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = rollback_shop(args.runtime_root, args.store_file, args.release, apply=args.apply)
    except RollbackError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (releases.ReleaseError, state_store.StateStoreError, OSError):
        print(
            'Shop rollback refused or failed. Check compatibility and service health; no settings downgrade was performed.',
            file=sys.stderr,
        )
        return 1
    print(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
