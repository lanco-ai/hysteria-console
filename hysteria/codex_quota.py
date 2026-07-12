#!/usr/bin/env python3
"""Collect and serve a bounded history of Codex account rate limits.

The collector deliberately talks to the locally installed Codex app-server
instead of reading authentication files.  Only a small allow-list of quota
fields is persisted; credentials and raw protocol responses never leave the
Codex process.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import select
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import state_store


STATE_VERSION = 1
POLL_INTERVAL_SECONDS = 180
STATE_FILE = Path(os.environ.get(
    'HY2_CODEX_QUOTA_STATE', '/root/hysteria/state/codex_quota.json',
))
LEGACY_CSV_FILE = Path('/root/hysteria/state/codex_quota.csv')
CODEX_BIN = os.environ.get('HY2_CODEX_BIN', '/usr/bin/codex')
MAX_PROTOCOL_LINE_CHARS = 2 * 1024 * 1024

FIVE_HOUR_MINUTES = 300
WEEK_MINUTES = 7 * 24 * 60

# Recent points stay at the requested three-minute cadence.  Older data is
# progressively compacted so a year of history remains around eight thousand
# tiny records instead of growing without bound.
RETENTION_TIERS = (
    (2 * 86400, 180),
    (31 * 86400, 900),
    (400 * 86400, 7200),
)

RANGES = {
    'day': {'seconds': 86400, 'bucket': 180, 'label': '24 小时'},
    'week': {'seconds': 7 * 86400, 'bucket': 1800, 'label': '7 天'},
    'month': {'seconds': 31 * 86400, 'bucket': 7200, 'label': '31 天'},
    'year': {'seconds': 366 * 86400, 'bucket': 86400, 'label': '1 年'},
}


class CodexQuotaError(RuntimeError):
    """A safe-to-log collector error without protocol payloads or secrets."""


def _rpc_send(process, message):
    if process.stdin is None:
        raise CodexQuotaError('Codex app-server stdin is unavailable')
    try:
        process.stdin.write(json.dumps(message, separators=(',', ':')) + '\n')
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise CodexQuotaError('Codex app-server closed its input') from exc


def _rpc_response(process, request_id, deadline):
    if process.stdout is None:
        raise CodexQuotaError('Codex app-server stdout is unavailable')
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], min(0.5, remaining))
        if not ready:
            if process.poll() is not None:
                raise CodexQuotaError(
                    f'Codex app-server exited early ({process.returncode})',
                )
            continue
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                raise CodexQuotaError(
                    f'Codex app-server exited early ({process.returncode})',
                )
            continue
        if len(line) > MAX_PROTOCOL_LINE_CHARS:
            raise CodexQuotaError('Codex app-server response was unexpectedly large')
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexQuotaError('Codex app-server returned invalid JSON') from exc
        if isinstance(message, dict) and message.get('id') == request_id:
            return message
    raise CodexQuotaError('Codex quota query timed out')


def _response_result(message, operation):
    if not isinstance(message, dict):
        raise CodexQuotaError(f'Codex {operation} returned an invalid response')
    error = message.get('error')
    if isinstance(error, dict):
        detail = str(error.get('message') or 'unknown error').strip()[:180]
        raise CodexQuotaError(f'Codex {operation} failed: {detail}')
    result = message.get('result')
    if not isinstance(result, dict):
        raise CodexQuotaError(f'Codex {operation} returned no result')
    return result


def query_rate_limits(*, codex_bin=CODEX_BIN, timeout=20):
    """Return the app-server rate-limit response using the existing login.

    A short-lived app-server process owns all credential handling.  stderr is
    discarded intentionally because it may contain unrelated local details;
    callers receive only our bounded, sanitized error messages.
    """
    command = [str(codex_bin), 'app-server', '--stdio']
    env = os.environ.copy()
    env.setdefault('HOME', '/root')
    process = None
    deadline = time.monotonic() + max(3, float(timeout))
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            close_fds=True,
        )
        _rpc_send(process, {
            'id': 1,
            'method': 'initialize',
            'params': {
                'clientInfo': {
                    'name': 'hy2-codex-quota',
                    'title': 'Hysteria Codex quota collector',
                    'version': '1.0.0',
                },
                'capabilities': {'experimentalApi': True},
            },
        })
        _response_result(_rpc_response(process, 1, deadline), 'initialize')
        _rpc_send(process, {'method': 'initialized'})
        _rpc_send(process, {
            'id': 2,
            'method': 'account/rateLimits/read',
            'params': None,
        })
        return _response_result(
            _rpc_response(process, 2, deadline), 'rate-limit query',
        )
    except FileNotFoundError as exc:
        raise CodexQuotaError(f'Codex executable not found: {codex_bin}') from exc
    finally:
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value):
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _clamped_percent(value):
    number = _number(value)
    if number is None:
        return None
    return round(max(0.0, min(100.0, number)), 2)


def _safe_window(raw):
    if not isinstance(raw, dict):
        return None
    used = _clamped_percent(raw.get('usedPercent'))
    if used is None:
        used = _clamped_percent(raw.get('used_percent'))
    if used is None:
        return None
    minutes = _integer(raw.get('windowDurationMins'))
    if minutes is None:
        minutes = _integer(raw.get('window_minutes'))
    resets_at = _integer(raw.get('resetsAt'))
    if resets_at is None:
        resets_at = _integer(raw.get('resets_at'))
    if resets_at is not None and resets_at <= 0:
        resets_at = None
    return {
        'used_percent': used,
        'remaining_percent': round(100.0 - used, 2),
        'window_minutes': minutes,
        'resets_at': resets_at,
    }


def _classify_window(window, position):
    if not window:
        return None
    minutes = window.get('window_minutes')
    if minutes is not None:
        if abs(minutes - FIVE_HOUR_MINUTES) <= 15:
            return 'five_hour'
        if abs(minutes - WEEK_MINUTES) <= 120:
            return 'weekly'
        return None
    # Compatibility with older responses where primary/secondary omitted the
    # explicit duration but retained their historical ordering.
    return 'five_hour' if position == 'primary' else 'weekly'


def _base_snapshot(result):
    by_id = result.get('rateLimitsByLimitId')
    if isinstance(by_id, dict) and isinstance(by_id.get('codex'), dict):
        return by_id['codex'], sorted(str(key) for key in by_id)[:32]
    legacy = result.get('rateLimits')
    if isinstance(legacy, dict):
        return legacy, []
    raise CodexQuotaError('Codex returned no account rate-limit snapshot')


def normalize_rate_limits(result, *, captured_at=None):
    """Reduce a raw response to the quota fields safe for panel storage."""
    if not isinstance(result, dict):
        raise CodexQuotaError('Codex returned an invalid rate-limit payload')
    snapshot, available_limit_ids = _base_snapshot(result)
    windows = {'five_hour': None, 'weekly': None}
    for position in ('primary', 'secondary'):
        window = _safe_window(snapshot.get(position))
        kind = _classify_window(window, position)
        if kind and windows[kind] is None:
            windows[kind] = window

    credits_raw = snapshot.get('credits')
    credits = None
    if isinstance(credits_raw, dict):
        balance = credits_raw.get('balance')
        credits = {
            'has_credits': bool(credits_raw.get('hasCredits')),
            'unlimited': bool(credits_raw.get('unlimited')),
            'balance': None if balance is None else str(balance)[:64],
        }

    reset_summary = result.get('rateLimitResetCredits')
    reset_credit_count = None
    if isinstance(reset_summary, dict):
        reset_credit_count = _integer(reset_summary.get('availableCount'))

    return {
        'captured_at': int(time.time() if captured_at is None else captured_at),
        'limit_id': str(snapshot.get('limitId') or 'codex')[:64],
        'limit_name': (
            str(snapshot.get('limitName'))[:120]
            if snapshot.get('limitName') is not None else None
        ),
        'plan_type': str(snapshot.get('planType') or 'unknown')[:64],
        'five_hour': windows['five_hour'],
        'weekly': windows['weekly'],
        'credits': credits,
        'reset_credits_available': reset_credit_count,
        'available_limit_ids': available_limit_ids,
    }


def sample_from_latest(latest):
    def value(window_name, field):
        window = latest.get(window_name)
        return window.get(field) if isinstance(window, dict) else None

    return {
        'ts': int(latest['captured_at']),
        'five_hour_remaining': value('five_hour', 'remaining_percent'),
        'weekly_remaining': value('weekly', 'remaining_percent'),
        'five_hour_resets_at': value('five_hour', 'resets_at'),
        'weekly_resets_at': value('weekly', 'resets_at'),
    }


def _clean_sample(raw, *, now):
    if not isinstance(raw, dict):
        return None
    ts = _integer(raw.get('ts'))
    if ts is None or ts <= 0 or ts > now + 86400:
        return None
    sample = {'ts': ts}
    for key in ('five_hour_remaining', 'weekly_remaining'):
        sample[key] = _clamped_percent(raw.get(key))
    for key in ('five_hour_resets_at', 'weekly_resets_at'):
        value = _integer(raw.get(key))
        sample[key] = value if value is not None and value > 0 else None
    if sample['five_hour_remaining'] is None and sample['weekly_remaining'] is None:
        return None
    return sample


def compact_samples(samples, *, now=None):
    """Return chronological, deduplicated samples under a fixed size bound."""
    now = int(time.time() if now is None else now)
    clean = []
    for raw in samples if isinstance(samples, list) else []:
        sample = _clean_sample(raw, now=now)
        if sample is not None:
            clean.append(sample)
    clean.sort(key=lambda row: row['ts'])

    buckets = {}
    for sample in clean:
        age = max(0, now - sample['ts'])
        resolution = None
        for max_age, tier_resolution in RETENTION_TIERS:
            if age <= max_age:
                resolution = tier_resolution
                break
        if resolution is None:
            continue
        key = (resolution, sample['ts'] // resolution)
        buckets[key] = sample
    return sorted(buckets.values(), key=lambda row: row['ts'])


def _empty_state():
    return {
        'version': STATE_VERSION,
        'source': 'codex-app-server/account/rateLimits/read',
        'poll_interval_seconds': POLL_INTERVAL_SECONDS,
        'last_attempt_at': None,
        'last_success_at': None,
        'last_error': None,
        'consecutive_failures': 0,
        'latest': None,
        'samples': [],
    }


def _load_state(path):
    state = state_store.load_json(path, {})
    if not isinstance(state, dict):
        state = {}
    base = _empty_state()
    base.update(state)
    return base


def _safe_error(exc):
    text = ' '.join(str(exc).strip().split()) or exc.__class__.__name__
    return text[:240]


def collect_once(*, state_file=STATE_FILE, query_fn=None, now=None):
    """Query Codex once, update state atomically, and return the new state."""
    state_path = Path(state_file)
    lock_path = state_path.with_suffix(state_path.suffix + '.lock')
    captured_at = int(time.time() if now is None else now)
    query_fn = query_fn or query_rate_limits
    with state_store.file_lock(lock_path):
        state = _load_state(state_path)
        state['version'] = STATE_VERSION
        state['source'] = 'codex-app-server/account/rateLimits/read'
        state['poll_interval_seconds'] = POLL_INTERVAL_SECONDS
        state['last_attempt_at'] = captured_at
        try:
            latest = normalize_rate_limits(query_fn(), captured_at=captured_at)
            samples = list(state.get('samples') or [])
            samples.append(sample_from_latest(latest))
            state['latest'] = latest
            state['samples'] = compact_samples(samples, now=captured_at)
            state['last_success_at'] = captured_at
            state['last_error'] = None
            state['consecutive_failures'] = 0
            state_store.save_json(state_path, state)
            return state
        except Exception as exc:
            state['last_error'] = _safe_error(exc)
            state['consecutive_failures'] = max(
                0, _integer(state.get('consecutive_failures')) or 0,
            ) + 1
            state['samples'] = compact_samples(
                state.get('samples') or [], now=captured_at,
            )
            state_store.save_json(state_path, state)
            if isinstance(exc, CodexQuotaError):
                raise
            raise CodexQuotaError(state['last_error']) from exc


def _iso_epoch(value):
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _legacy_sample(row):
    if not isinstance(row, dict) or str(row.get('status') or '') != 'success':
        return None
    ts = _iso_epoch(row.get('timestamp'))
    if ts is None:
        return None
    session_remaining = _clamped_percent(row.get('session_remaining'))
    weekly_remaining = _clamped_percent(row.get('weekly_remaining'))
    session_reset = _iso_epoch(row.get('session_reset'))
    weekly_reset = _iso_epoch(row.get('weekly_reset'))
    five_remaining = None
    five_reset = None

    if weekly_remaining is not None:
        # The legacy two-window response used session=5h and weekly=7d.
        five_remaining = session_remaining
        five_reset = session_reset
    elif session_remaining is not None:
        # Newer one-window responses place the explicit 7-day window in the
        # legacy "session" columns.  A reset more than six hours away cannot
        # be the five-hour window, so migrate it as weekly instead.
        reset_distance = session_reset - ts if session_reset is not None else None
        if reset_distance is not None and -300 <= reset_distance <= 6 * 3600:
            five_remaining = session_remaining
            five_reset = session_reset
        else:
            weekly_remaining = session_remaining
            weekly_reset = session_reset

    return {
        'ts': ts,
        'five_hour_remaining': five_remaining,
        'weekly_remaining': weekly_remaining,
        'five_hour_resets_at': five_reset,
        'weekly_resets_at': weekly_reset,
    }


def import_legacy_csv(*, csv_file=LEGACY_CSV_FILE, state_file=STATE_FILE, now=None):
    """Safely merge history from the retired runtime-only CSV collector."""
    csv_path = Path(csv_file)
    state_path = Path(state_file)
    if not csv_path.exists():
        return 0
    if csv_path.stat().st_size > 10 * 1024 * 1024:
        raise CodexQuotaError('Legacy Codex quota CSV is unexpectedly large')
    imported = []
    try:
        with csv_path.open(newline='', encoding='utf-8') as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                if index >= 250_000:
                    raise CodexQuotaError('Legacy Codex quota CSV has too many rows')
                sample = _legacy_sample(row)
                if sample is not None:
                    imported.append(sample)
    except (OSError, csv.Error) as exc:
        raise CodexQuotaError('Legacy Codex quota CSV could not be read') from exc

    merged_at = int(time.time() if now is None else now)
    lock_path = state_path.with_suffix(state_path.suffix + '.lock')
    with state_store.file_lock(lock_path):
        state = _load_state(state_path)
        before = len(compact_samples(state.get('samples') or [], now=merged_at))
        state['samples'] = compact_samples(
            list(state.get('samples') or []) + imported,
            now=merged_at,
        )
        state['legacy_migrated_at'] = merged_at
        state['legacy_source_records'] = len(imported)
        state_store.save_json(state_path, state)
        return max(0, len(state['samples']) - before)


def _public_window(raw, *, label):
    if not isinstance(raw, dict):
        return {
            'label': label,
            'available': False,
            'used_percent': None,
            'remaining_percent': None,
            'window_minutes': None,
            'resets_at': None,
        }
    used = _clamped_percent(raw.get('used_percent'))
    remaining = _clamped_percent(raw.get('remaining_percent'))
    if remaining is None and used is not None:
        remaining = round(100.0 - used, 2)
    return {
        'label': label,
        'available': used is not None and remaining is not None,
        'used_percent': used,
        'remaining_percent': remaining,
        'window_minutes': _integer(raw.get('window_minutes')),
        'resets_at': _integer(raw.get('resets_at')),
    }


def _aggregate_samples(samples, *, start, bucket_seconds, now):
    buckets = {}
    for raw in samples if isinstance(samples, list) else []:
        sample = _clean_sample(raw, now=now)
        if sample is None or sample['ts'] < start:
            continue
        buckets[sample['ts'] // bucket_seconds] = sample
    return [buckets[key] for key in sorted(buckets)]


def build_dashboard_payload(*, state_file=STATE_FILE, range_key='day', now=None):
    """Build the compact authenticated JSON contract consumed by the UI."""
    now = int(time.time() if now is None else now)
    selected = range_key if range_key in RANGES else 'day'
    config = RANGES[selected]
    state = _load_state(Path(state_file))
    latest = state.get('latest') if isinstance(state.get('latest'), dict) else {}
    last_attempt = _integer(state.get('last_attempt_at'))
    last_success = _integer(state.get('last_success_at'))
    last_error = state.get('last_error')
    age = max(0, now - last_success) if last_success is not None else None

    if last_success is None:
        status = 'error' if last_error else 'empty'
    elif last_error and (last_attempt or 0) > last_success:
        status = 'error'
    elif age <= POLL_INTERVAL_SECONDS * 2:
        status = 'live'
    elif age <= POLL_INTERVAL_SECONDS * 5:
        status = 'delayed'
    else:
        status = 'stale'

    samples = compact_samples(state.get('samples') or [], now=now)
    points = _aggregate_samples(
        samples,
        start=now - config['seconds'],
        bucket_seconds=config['bucket'],
        now=now,
    )
    credits = latest.get('credits') if isinstance(latest.get('credits'), dict) else None
    history_started_at = samples[0]['ts'] if samples else None
    return {
        'version': STATE_VERSION,
        'generated_at': now,
        'range': selected,
        'range_label': config['label'],
        'poll_interval_seconds': POLL_INTERVAL_SECONDS,
        'freshness': {
            'status': status,
            'last_attempt_at': last_attempt,
            'last_success_at': last_success,
            'age_seconds': age,
            'next_poll_at': (
                last_attempt + POLL_INTERVAL_SECONDS
                if last_attempt is not None else None
            ),
            'last_error': str(last_error)[:240] if last_error else None,
            'consecutive_failures': max(
                0, _integer(state.get('consecutive_failures')) or 0,
            ),
        },
        'account': {
            'plan_type': str(latest.get('plan_type') or 'unknown')[:64],
            'limit_id': str(latest.get('limit_id') or 'codex')[:64],
            'limit_name': (
                str(latest.get('limit_name'))[:120]
                if latest.get('limit_name') is not None else None
            ),
            'credits': credits,
            'reset_credits_available': _integer(
                latest.get('reset_credits_available'),
            ),
            'available_limit_ids': (
                latest.get('available_limit_ids')
                if isinstance(latest.get('available_limit_ids'), list) else []
            ),
        },
        'windows': {
            'five_hour': _public_window(latest.get('five_hour'), label='5 小时额度'),
            'weekly': _public_window(latest.get('weekly'), label='周额度'),
        },
        'history': {
            'started_at': history_started_at,
            'retention_days': 400,
            'bucket_seconds': config['bucket'],
            'point_count': len(points),
        },
        'points': points,
    }


def _format_percent(window):
    if not isinstance(window, dict):
        return 'not provided'
    value = window.get('remaining_percent')
    return f'{value:g}%' if isinstance(value, (int, float)) else 'not provided'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect Codex quota history')
    parser.add_argument(
        'command', nargs='?', default='collect',
        choices=('collect', 'show', 'migrate-legacy'),
    )
    parser.add_argument('--state', default=str(STATE_FILE))
    parser.add_argument('--legacy-csv', default=str(LEGACY_CSV_FILE))
    args = parser.parse_args(argv)
    state_path = Path(args.state)
    if args.command == 'show':
        payload = build_dashboard_payload(state_file=state_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'migrate-legacy':
        try:
            count = import_legacy_csv(
                csv_file=Path(args.legacy_csv), state_file=state_path,
            )
        except CodexQuotaError as exc:
            print(f'Codex quota migration failed: {_safe_error(exc)}', file=sys.stderr)
            return 1
        print(f'Codex quota legacy history migrated: {count} retained points')
        return 0
    try:
        state = collect_once(state_file=state_path)
    except CodexQuotaError as exc:
        print(f'Codex quota collection failed: {_safe_error(exc)}', file=sys.stderr)
        return 1
    latest = state.get('latest') or {}
    print(
        'Codex quota collected: '
        f'5h={_format_percent(latest.get("five_hour"))}, '
        f'week={_format_percent(latest.get("weekly"))}',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
