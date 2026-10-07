#!/usr/bin/env python3
"""Produce a reviewable offline candidate; never alter production or systemd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hysteria'))
import state_store
import traffic_limiter
import tuic_config
import tuic_user_meter
from timeutil import local_now


# Fixed installed binary path; no user-controlled systemd command interpolation.
# sing-box opens a netlink socket for its interface/route monitor at startup and
# exits under the legacy unit's AF_UNIX/AF_INET/AF_INET6-only allow-list. The
# unit grants no CAP_NET_ADMIN, so netlink stays read-only.
OVERRIDE = (
    '[Service]\n'
    'ExecStart=\n'
    'ExecStart=/usr/local/lib/hy2/sing-box-tuic-1.14.2 run -c /root/hysteria/tuic.json\n'
    'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n'
)


def prepare(args):
    output = Path(args.output).resolve()
    if output.exists() or output == Path('/root/hysteria') or Path('/root/hysteria') in output.parents:
        raise ValueError('output must be a new directory outside production')
    binary = Path(args.binary).resolve()
    with binary.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != args.sha256:
        raise ValueError('runtime artifact checksum does not match trusted build evidence')
    version = tuic_user_meter.bounded_command([str(binary), 'version']).decode()
    if '1.14.2' not in version or any(tag not in version for tag in ('with_quic', 'with_v2ray_api')):
        raise ValueError('runtime requires pinned version and both QUIC/V2Ray API build tags')
    legacy = state_store.load_json_strict(args.legacy, {}, required=True)
    users = state_store.load_json_strict(args.users, {}, required=True)
    daily = state_store.load_json_strict(args.daily, {}, required=True)
    meta = state_store.load_json_strict(args.meta, {}, required=True)
    traffic_limiter._validate_users(users)
    traffic_limiter._validate_usage_ledger(daily, path=args.daily, daily=True)
    traffic_limiter._validate_meta(meta, path=args.meta)
    now = local_now()
    plan, _ = traffic_limiter.build_static_access_plan(users, daily, now=now, meta=meta)
    candidate = tuic_config.render_metered_runtime(
        legacy, users, plan, endpoint=args.endpoint,
        accept_runtime_defaults=args.accept_runtime_defaults,
    )
    output.mkdir(mode=0o700)
    config = output / 'tuic.json'
    state_store.save_json(config, candidate)
    config.chmod(0o600)
    subprocess.run([str(binary), 'check', '-c', str(config)], check=True, timeout=15,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {'status': 'candidate-only; activation NOT performed', 'binary_sha256': digest,
              'version': version, 'created_at': now.isoformat(),
              'adopt_runtime_defaults': args.accept_runtime_defaults,
              'users': len(candidate['inbounds'][0]['users']),
              'remaining_gate': 'runtime-gate.py plus independent review and explicit migration authorization'}
    state_store.save_json(output / 'report.json', report)
    (output / 'tuic-server.override.conf').write_text(OVERRIDE)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('binary', 'sha256', 'legacy', 'users', 'daily', 'meta', 'output'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--endpoint', default='127.0.0.1:10086')
    parser.add_argument('--accept-runtime-defaults', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        prepare(args)
    except (ValueError, OSError, state_store.StateStoreError, subprocess.SubprocessError) as exc:
        print(f'Preflight blocked: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
