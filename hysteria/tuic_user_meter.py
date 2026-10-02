"""Non-reset TUIC payload counters and a recoverable canonical ledger transaction.

All public *_locked functions require the existing usage.lock. The journal is
an intent record: recovery rolls all targets forward before any other writer
may mutate them. A runtime crash can still lose its unpolled in-memory tail.
"""

import copy
import hashlib
import json
import math
import time
import re
from pathlib import Path

import state_store

STATE_NAME = 'tuic_user_state.json'
MODE_NAME = 'tuic_user_mode.json'
RELOAD_GRACE_SECONDS = 120
PENDING_NAME = 'tuic_user_pending.json'
_STAT_NAME = re.compile(r'^user>>>([^>]+)>>>traffic>>>(uplink|downlink)$')


def invalid(message):
    raise state_store.CriticalStateUnavailable('TUIC accounting: ' + message)


def _integer(value):
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and re.fullmatch(r'[0-9]+', value)
    ):
        invalid('invalid counter')
    number = int(value)
    if number < 0:
        invalid('negative counter')
    return number


def parse_stats(data, known_users):
    if not isinstance(data, dict) or set(data) - {'stat'}:
        invalid('invalid statistics response')
    rows = data.get('stat', [])
    if not isinstance(rows, list):
        invalid('statistics must be a list')
    result, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'name', 'value'}:
            invalid('invalid statistics row')
        name = row.get('name')
        match = _STAT_NAME.fullmatch(name) if isinstance(name, str) else None
        if not match or name in seen or match[1] not in known_users:
            invalid('unknown or duplicate statistics identity')
        seen.add(name)
        entry = result.setdefault(match[1], {'tx': 0, 'rx': 0})
        # Protobuf JSON omits a zero-valued scalar.
        entry['rx' if match[2] == 'uplink' else 'tx'] = _integer(row.get('value', 0))
    return result


def _validate_state(state):
    if (
        not isinstance(state, dict)
        or set(state)
        != {'version', 'activated_at', 'generation', 'counters', 'identities', 'reload_wait'}
        or state.get('version') != 1
        or not isinstance(state.get('activated_at'), str)
        or not state['activated_at']
        or state.get('generation') is not None
        and (not isinstance(state['generation'], str) or not state['generation'])
        or not isinstance(state.get('counters'), dict)
        or not isinstance(state.get('identities'), dict)
    ):
        invalid('invalid source checkpoint')
    if state['reload_wait'] is not None:
        _validate_wait_clock(state['reload_wait'])
    for username, fingerprint in state['identities'].items():
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(fingerprint, str)
            or not re.fullmatch('[a-f0-9]{64}', fingerprint)
        ):
            invalid('invalid generation identity binding')
    if set(state['counters']) - set(state['identities']):
        invalid('counter has no bound authenticated identity')
    for username, row in state['counters'].items():
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(row, dict)
            or set(row) != {'rx', 'tx'}
        ):
            invalid('invalid checkpoint counter')
        for value in row.values():
            _integer(value)
    if state['generation'] is None and (state['counters'] or state['identities']):
        invalid('unactivated checkpoint has counters')
    return state


def load_state(directory):
    return _validate_state(
        state_store.load_json_strict(
            Path(directory) / STATE_NAME,
            {},
            required=True,
        )
    )


def initialize(directory, *, activated_at):
    """Explicit migration only, with TUIC stopped and usage.lock held."""
    path = Path(directory) / STATE_NAME
    if path.exists() or (path.parent / PENDING_NAME).exists():
        invalid('refusing to overwrite an existing checkpoint')
    state = {
        'version': 1,
        'activated_at': activated_at,
        'generation': None,
        'counters': {},
        'identities': {},
        'reload_wait': None,
    }
    _validate_state(state)
    state_store.save_json(path.parent / MODE_NAME, {'version': 1, 'mode': 'active'})
    state_store.save_json(path, state)


def accounting_mode(directory):
    directory = Path(directory)
    path = directory / MODE_NAME
    if not path.exists():
        if (directory / STATE_NAME).exists() or (directory / PENDING_NAME).exists():
            invalid('activation mode missing while accounting state exists')
        return 'legacy'
    value = state_store.load_json_strict(path, {}, required=True)
    if (
        set(value) != {'version', 'mode'}
        or value['version'] != 1
        or value['mode'] not in ('active', 'legacy')
    ):
        invalid('invalid activation mode')
    return value['mode']


def deactivate_locked(daily_path, usage_path):
    """Approved rollback only: caller must stop TUIC and hold usage.lock."""
    recover_locked(daily_path, usage_path)
    state_store.save_json(Path(daily_path).parent / MODE_NAME, {'version': 1, 'mode': 'legacy'})


def credential_id(username, uid, password):
    return hashlib.sha256(
        json.dumps([username, uid, password], separators=(',', ':')).encode()
    ).hexdigest()


def bind_observation(
    checkpoint, generation, counters, *, runtime_identities, canonical_identities, pending
):
    """Bind counters to the identities actually served by this invocation.

    During a deferred restart the file can describe new credentials while the
    process still serves old ones. Drain that old generation, but never credit a
    replacement identity merely because it reused a username. A fresh process
    is attributable only after the reload worker ACKs its config generation.
    """
    same = checkpoint['generation'] == generation
    if pending and not same:
        return None
    identities = checkpoint['identities'] if same else runtime_identities
    if same and not pending and runtime_identities != identities:
        invalid('credential mapping changed without a verified new generation')
    if set(counters) - set(identities):
        invalid('counter identity was not authenticated in this generation')
    owners = {
        name for name, identity in identities.items() if canonical_identities.get(name) == identity
    }
    return owners, identities


def _validate_wait_clock(clock):
    if (
        not isinstance(clock, dict)
        or set(clock) != {'boot', 'wall', 'monotonic'}
        or not isinstance(clock['boot'], str)
        or not clock['boot']
    ):
        invalid('invalid reload wait clock')
    for name in ('wall', 'monotonic'):
        value = clock[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            invalid('invalid reload wait timestamp')


def wait_clock():
    return {
        'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'wall': time.time(),
        'monotonic': time.monotonic(),
    }


def check_reload_wait(checkpoint, *, clock=None):
    first = checkpoint['reload_wait']
    if first is None:
        return
    clock = wait_clock() if clock is None else clock
    _validate_wait_clock(first)
    _validate_wait_clock(clock)
    elapsed = clock['wall'] - first['wall']
    if elapsed < 0:
        invalid('wall clock moved backwards during reload wait')
    if clock['boot'] == first['boot']:
        monotonic_elapsed = clock['monotonic'] - first['monotonic']
        if monotonic_elapsed < 0:
            invalid('monotonic clock moved backwards during reload wait')
        elapsed = max(elapsed, monotonic_elapsed)
    if elapsed >= RELOAD_GRACE_SECONDS:
        invalid('cumulative reload accounting grace expired')


def begin_reload_wait(directory, checkpoint, started, *, clock=None):
    """Persist the first unresolved interval; tokens and restarts cannot renew it.

    Called under usage.lock after journal recovery. This changes only wait
    metadata, never counters or credits. Clearing happens in the next trusted,
    ACKed accounting transaction so partial commits cannot renew the grace.
    """
    if checkpoint['reload_wait'] is None:
        checkpoint = {**checkpoint, 'reload_wait': dict(started)}
        _validate_state(checkpoint)
        state_store.save_json(Path(directory) / STATE_NAME, checkpoint)
    check_reload_wait(checkpoint, clock=clock)
    return checkpoint


def _targets(daily_path, usage_path):
    daily_path, usage_path = Path(daily_path), Path(usage_path)
    if daily_path.parent != usage_path.parent:
        invalid('ledgers must share a state directory')
    return {'daily': daily_path, 'usage': usage_path, 'source': daily_path.parent / STATE_NAME}


def recover_locked(daily_path, usage_path):
    pending = Path(daily_path).parent / PENDING_NAME
    if not pending.exists():
        return
    targets = _targets(daily_path, usage_path)
    journal = state_store.load_json_strict(pending, {}, required=True)
    if set(journal) != {'version', 'before', 'after'} or journal['version'] != 1:
        invalid('invalid pending journal')
    for side in ('before', 'after'):
        if not isinstance(journal[side], dict) or set(journal[side]) != set(targets):
            invalid('invalid pending targets')
        _validate_state(journal[side]['source'])
        # Reuse the canonical ledger schema; import is deferred to avoid cycles.
        import traffic_limiter

        for name in ('daily', 'usage'):
            traffic_limiter._validate_usage_ledger(
                journal[side][name],
                path=targets[name],
                daily=name == 'daily',
            )
    current = {
        name: state_store.load_json_strict(path, {}, required=True)
        for name, path in targets.items()
    }
    for name in targets:
        if current[name] not in (journal['before'][name], journal['after'][name]):
            invalid('pending journal conflicts with another ledger writer')
    # Re-write even a visible after-image to establish directory durability
    # after AtomicReplaceDurabilityUncertain. Never clear intent on uncertainty.
    for name, path in targets.items():
        state_store.save_json(path, journal['after'][name])
    pending.unlink()
    state_store._fsync_dir(pending.parent)


def credit_locked(
    *,
    daily_path,
    usage_path,
    now,
    month_key,
    generation,
    counters,
    users,
    identities,
    clear_reload_wait=False,
):
    recover_locked(daily_path, usage_path)
    targets = _targets(daily_path, usage_path)
    state = load_state(Path(daily_path).parent)
    if not isinstance(generation, str) or not generation:
        invalid('missing runtime generation')
    before = {
        name: state_store.load_json_strict(path, {}, required=True)
        for name, path in targets.items()
    }
    import traffic_limiter

    for name in ('daily', 'usage'):
        traffic_limiter._validate_usage_ledger(
            before[name], path=targets[name], daily=name == 'daily'
        )
    if state['generation'] == generation and state['identities'] != identities:
        invalid('cannot rebind authenticated identities within a generation')
    delta = calculate_delta(state, generation, counters, users)
    after = copy.deepcopy(before)
    for name, bucket in (('daily', now.strftime('%Y-%m-%d')), ('usage', month_key)):
        for username, diff in delta.items():
            rows = after[name].setdefault(bucket, {})
            row = traffic_limiter.normalize_usage_entry(rows.get(username, 0))
            row['rx'] += diff['rx']
            row['tx'] += diff['tx']
            row['total'] = row['rx'] + row['tx']
            rows[username] = row
    after['source'] = {
        **state,
        'generation': generation,
        'counters': counters,
        'identities': identities,
        'reload_wait': None if clear_reload_wait else state['reload_wait'],
    }
    _validate_state(after['source'])
    if after == before:
        return delta
    pending = Path(daily_path).parent / PENDING_NAME
    state_store.save_json(pending, {'version': 1, 'before': before, 'after': after})
    recover_locked(daily_path, usage_path)
    return delta


def calculate_delta(state, generation, counters, users):
    same_generation = state['generation'] == generation
    previous = state['counters'] if same_generation else {}
    delta = {}
    for username in set(previous) | set(counters):
        row = counters.get(username, {'rx': 0, 'tx': 0})
        if not isinstance(row, dict) or set(row) != {'rx', 'tx'}:
            invalid('invalid collected counters')
        old = previous.get(username, {'rx': 0, 'tx': 0})
        diff = {key: _integer(row[key]) - _integer(old[key]) for key in ('rx', 'tx')}
        if min(diff.values()) < 0:
            invalid('counter decreased within one runtime generation')
        if username in users and sum(diff.values()):
            delta[username] = diff
    return delta


def validate_endpoint(endpoint):
    """The unauthenticated stats API must never bind outside literal loopback."""
    import ipaddress

    if not isinstance(endpoint, str):
        invalid('invalid API endpoint')
    host, _, port = endpoint.rpartition(':')
    try:
        address = ipaddress.ip_address(host.strip('[]'))
        number = int(port)
    except ValueError:
        invalid('invalid API endpoint')
    if not address.is_loopback or not 1 <= number <= 65535 or number == 10085:
        invalid('stats require a dedicated loopback port')
    return endpoint


def bounded_command(command, *, timeout=5, limit=2 * 1024 * 1024):
    """Drain both pipes with a joint byte cap; never buffer unlimited output."""
    import os
    import selectors
    import subprocess
    import time

    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, count = bytearray(), 0
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    invalid('command timed out')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > limit:
                        invalid('command output exceeded limit')
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            invalid('command failed')
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise state_store.CriticalStateUnavailable('TUIC accounting command failed') from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()


def runtime_generation():
    """InvocationID plus kernel process start identifies a real service run."""
    raw = bounded_command(
        [
            'systemctl',
            'show',
            'tuic-server.service',
            '--property=InvocationID,MainPID,ActiveState',
        ],
        limit=8192,
    )
    try:
        fields = dict(line.split('=', 1) for line in raw.decode().splitlines())
        invocation, pid = fields['InvocationID'], int(fields['MainPID'])
        if (
            fields['ActiveState'] != 'active'
            or pid <= 0
            or not re.fullmatch('[a-f0-9]{32}', invocation)
        ):
            invalid('service is not a verified active generation')
        proc = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        start = int(proc[19])  # field22 after pid/comm
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return f'{boot}:{invocation}:{pid}:{start}'
    except (ValueError, KeyError, IndexError, UnicodeError, OSError) as exc:
        raise state_store.CriticalStateUnavailable('TUIC generation unavailable') from exc


def observe(endpoint, known_users):
    endpoint = validate_endpoint(endpoint)
    before = runtime_generation()
    raw = bounded_command(
        [
            '/usr/local/bin/xray',
            'api',
            'statsquery',
            '--server=' + endpoint,
            '-pattern',
            'user>>>',
        ]
    )
    after = runtime_generation()
    if before != after:
        invalid('runtime generation changed during observation')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise state_store.CriticalStateUnavailable('invalid TUIC stats JSON') from exc
    return before, parse_stats(data, known_users)
