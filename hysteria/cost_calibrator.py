"""Provider-cost multiplier calibration helpers.

The display multiplier is operator-controlled. This module only produces an
observed suggestion by comparing app-level raw traffic with public NIC deltas.
It never changes configuration by itself.
"""
from datetime import datetime, timedelta
from pathlib import Path

import state_store


EXCLUDED_IFACES = {'lo'}
EXCLUDED_PREFIXES = (
    'docker', 'br-', 'veth', 'virbr', 'tun', 'tap', 'wg',
    'tailscale', 'zt', 'kube', 'cni',
)
MAX_SAMPLES = 20160  # 7 days at 30-second traffic-limiter cadence.
DEFAULT_WINDOW_HOURS = 72
WINDOW_HOURS = (24, 72, 168)
DEFAULT_MIN_SAMPLE_APP_BYTES = 1 * 1024 ** 2
TRIM_FRACTION = 0.10
DEFAULT_AUTO_POLICY = {
    'enabled': False,
    'mode': 'total',
    'window_hours': 72,
    'min_confidence': 'medium',
    'max_delta_percent': 25.0,
    'min_delta_percent': 3.0,
    'cooldown_hours': 24,
}
AUTO_POLICY_FILE = '/root/hysteria/state/display_multiplier_auto.json'
DISPLAY_MULTIPLIER_STATE_FILE = '/root/hysteria/state/display_multiplier.json'
CONFIDENCE_RANK = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}


def include_iface(name):
    if not name or name in EXCLUDED_IFACES:
        return False
    return not any(name.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def parse_netdev(text):
    stats = {}
    for line in str(text or '').splitlines():
        if ':' not in line:
            continue
        name, rest = line.split(':', 1)
        name = name.strip()
        fields = rest.split()
        if len(fields) < 16:
            continue
        try:
            stats[name] = {
                'rx': int(fields[0]),
                'tx': int(fields[8]),
            }
        except ValueError:
            continue
    return stats


def public_net_totals(stats):
    rx = 0
    tx = 0
    ifaces = []
    for name, values in (stats or {}).items():
        if not include_iface(name):
            continue
        rx += int((values or {}).get('rx', 0) or 0)
        tx += int((values or {}).get('tx', 0) or 0)
        ifaces.append(name)
    return {
        'rx': rx,
        'tx': tx,
        'total': rx + tx,
        'ifaces': sorted(ifaces),
    }


def read_net_totals(path='/proc/net/dev'):
    return public_net_totals(parse_netdev(Path(path).read_text(encoding='utf-8')))


def _iso(now):
    return (now or datetime.utcnow()).isoformat(timespec='seconds')


def _non_negative_delta(cur, prev, key):
    cur_v = int((cur or {}).get(key, 0) or 0)
    prev_v = int((prev or {}).get(key, 0) or 0)
    delta = cur_v - prev_v
    return delta if delta >= 0 else None


def update_sample(path, *, app_raw_bytes, now=None, net_totals=None, max_samples=MAX_SAMPLES):
    """Update calibration state and append one sample when a previous baseline exists."""
    path = str(path)
    app_raw = int(app_raw_bytes or 0)
    net = net_totals or read_net_totals()
    last = {
        'ts': _iso(now),
        'rx': int(net.get('rx', 0) or 0),
        'tx': int(net.get('tx', 0) or 0),
        'total': int(net.get('total', 0) or 0),
        'ifaces': list(net.get('ifaces') or []),
    }
    state = state_store.load_json(path, {})
    samples = list(state.get('samples') or [])
    prev = state.get('last') or {}

    if prev and app_raw > 0:
        rx_delta = _non_negative_delta(last, prev, 'rx')
        tx_delta = _non_negative_delta(last, prev, 'tx')
        total_delta = _non_negative_delta(last, prev, 'total')
        if rx_delta is not None and tx_delta is not None and total_delta is not None:
            samples.append({
                'ts': last['ts'],
                'app_raw_bytes': app_raw,
                'net_rx_delta': rx_delta,
                'net_tx_delta': tx_delta,
                'net_total_delta': total_delta,
            })

    state = {
        'last': last,
        'samples': samples[-int(max_samples):],
    }
    state_store.save_json(path, state)
    return state


def _parse_ts(raw):
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _recent_samples(samples, *, now=None, window_hours=DEFAULT_WINDOW_HOURS):
    if now is None:
        return list(samples or [])
    cutoff = now - timedelta(hours=window_hours)
    out = []
    for sample in samples or []:
        ts = _parse_ts(sample.get('ts'))
        if ts is None:
            out.append(sample)
            continue
        try:
            if ts.tzinfo is not None and now.tzinfo is not None:
                keep = ts >= cutoff
            elif ts.tzinfo is None and now.tzinfo is None:
                keep = ts >= cutoff
            else:
                keep = True
        except TypeError:
            keep = True
        if keep:
            out.append(sample)
    return out


def _sample_ratio(sample, key):
    app = int((sample or {}).get('app_raw_bytes', 0) or 0)
    net = int((sample or {}).get(key, 0) or 0)
    if app <= 0 or net < 0:
        return None
    return net / app


def _filtered_samples(samples, *, min_sample_app_bytes=DEFAULT_MIN_SAMPLE_APP_BYTES):
    out = []
    ignored = 0
    for sample in samples or []:
        app = int((sample or {}).get('app_raw_bytes', 0) or 0)
        if app < int(min_sample_app_bytes):
            ignored += 1
            continue
        total_ratio = _sample_ratio(sample, 'net_total_delta')
        egress_ratio = _sample_ratio(sample, 'net_tx_delta')
        if total_ratio is None or egress_ratio is None:
            ignored += 1
            continue
        if not (0.1 <= total_ratio <= 20.0 and 0.0 <= egress_ratio <= 20.0):
            ignored += 1
            continue
        out.append(sample)
    return out, ignored


def _trimmed_weighted_ratio(samples, key, *, trim_fraction=TRIM_FRACTION):
    ratios = []
    for sample in samples or []:
        ratio = _sample_ratio(sample, key)
        if ratio is None:
            continue
        ratios.append((ratio, int(sample.get('app_raw_bytes', 0) or 0), sample))
    if not ratios:
        return None, []
    ratios.sort(key=lambda item: item[0])
    trim_n = int(len(ratios) * float(trim_fraction))
    if len(ratios) >= 10 and trim_n > 0 and len(ratios) > (trim_n * 2):
        ratios = ratios[trim_n:-trim_n]
    weight = sum(item[1] for item in ratios)
    if weight <= 0:
        return None, [item[2] for item in ratios]
    value = sum(item[0] * item[1] for item in ratios) / weight
    return value, [item[2] for item in ratios]


def _confidence(sample_count, app_raw):
    if app_raw <= 0:
        return 'none'
    if sample_count >= 24 and app_raw >= 10 * 1024 ** 3:
        return 'high'
    if sample_count >= 6 and app_raw >= 1 * 1024 ** 3:
        return 'medium'
    return 'low'


def summarize_state(state, *, current_multiplier, now=None, window_hours=DEFAULT_WINDOW_HOURS,
                    min_sample_app_bytes=DEFAULT_MIN_SAMPLE_APP_BYTES):
    samples = _recent_samples(state.get('samples') or [], now=now, window_hours=window_hours)
    included, ignored = _filtered_samples(
        samples, min_sample_app_bytes=min_sample_app_bytes)
    suggested, total_ratio_samples = _trimmed_weighted_ratio(included, 'net_total_delta')
    egress, egress_ratio_samples = _trimmed_weighted_ratio(included, 'net_tx_delta')
    used_for_totals = total_ratio_samples or included
    app_raw = sum(int(s.get('app_raw_bytes', 0) or 0) for s in used_for_totals)
    net_total = sum(int(s.get('net_total_delta', 0) or 0) for s in used_for_totals)
    net_tx = sum(int(s.get('net_tx_delta', 0) or 0) for s in used_for_totals)

    current = float(current_multiplier)
    delta_pct = ((suggested - current) * 100 / current) if suggested is not None and current > 0 else None

    confidence = _confidence(len(used_for_totals), app_raw)

    return {
        'window_hours': window_hours,
        'sample_count': len(samples),
        'included_sample_count': len(included),
        'trimmed_sample_count': len(used_for_totals),
        'ignored_sample_count': ignored,
        'min_sample_app_bytes': int(min_sample_app_bytes),
        'app_raw_bytes': app_raw,
        'net_total_bytes': net_total,
        'net_tx_bytes': net_tx,
        'current_multiplier': current,
        'suggested_multiplier': suggested,
        'egress_multiplier': egress,
        'delta_percent': delta_pct,
        'confidence': confidence,
        'ifaces': list((state.get('last') or {}).get('ifaces') or []),
        'last_ts': (state.get('last') or {}).get('ts', ''),
        'method': 'trimmed_weighted_ratio',
        'egress_sample_count': len(egress_ratio_samples),
    }


def summarize(path, *, current_multiplier, now=None, window_hours=DEFAULT_WINDOW_HOURS,
              min_sample_app_bytes=DEFAULT_MIN_SAMPLE_APP_BYTES):
    state = state_store.load_json(path, {})
    return summarize_state(
        state,
        current_multiplier=current_multiplier,
        now=now,
        window_hours=window_hours,
        min_sample_app_bytes=min_sample_app_bytes,
    )


def summarize_windows(path, *, current_multiplier, now=None, windows=WINDOW_HOURS,
                      min_sample_app_bytes=DEFAULT_MIN_SAMPLE_APP_BYTES):
    state = state_store.load_json(path, {})
    return [
        summarize_state(
            state,
            current_multiplier=current_multiplier,
            now=now,
            window_hours=hours,
            min_sample_app_bytes=min_sample_app_bytes,
        )
        for hours in windows
    ]


def _as_float(value, default, *, low=None, high=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if low is not None:
        out = max(float(low), out)
    if high is not None:
        out = min(float(high), out)
    return out


def _as_int(value, default, *, low=None, high=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        return int(default)
    if low is not None:
        out = max(int(low), out)
    if high is not None:
        out = min(int(high), out)
    return out


def normalize_auto_policy(raw):
    src = raw if isinstance(raw, dict) else {}
    policy = dict(DEFAULT_AUTO_POLICY)
    policy['enabled'] = bool(src.get('enabled', policy['enabled']))
    mode = str(src.get('mode') or policy['mode']).strip().lower()
    policy['mode'] = mode if mode in ('total', 'egress') else 'total'
    policy['window_hours'] = _as_int(src.get('window_hours'), policy['window_hours'], low=24, high=168)
    min_conf = str(src.get('min_confidence') or policy['min_confidence']).strip().lower()
    policy['min_confidence'] = min_conf if min_conf in CONFIDENCE_RANK else 'medium'
    policy['max_delta_percent'] = _as_float(
        src.get('max_delta_percent'), policy['max_delta_percent'], low=1.0, high=100.0)
    policy['min_delta_percent'] = _as_float(
        src.get('min_delta_percent'), policy['min_delta_percent'], low=0.0, high=50.0)
    policy['cooldown_hours'] = _as_float(
        src.get('cooldown_hours'), policy['cooldown_hours'], low=1.0, high=168.0)
    for key in ('last_checked_at', 'last_decision', 'last_reason', 'last_candidate',
                'last_applied_at'):
        if key in src:
            policy[key] = src[key]
    return policy


def load_auto_policy(path=AUTO_POLICY_FILE):
    return normalize_auto_policy(state_store.load_json(path, {}))


def save_auto_policy(policy, path=AUTO_POLICY_FILE):
    state_store.save_json(path, normalize_auto_policy(policy))


def _candidate_from_summary(summary, mode):
    if mode == 'egress':
        return summary.get('egress_multiplier')
    return summary.get('suggested_multiplier')


def _parse_dt(raw):
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def evaluate_multiplier_candidate(summary, current_multiplier, policy, *,
                                  runtime_state=None, now=None, manual=False):
    policy = normalize_auto_policy(policy)
    now = now or datetime.utcnow()
    if not manual and not policy.get('enabled'):
        return {'apply': False, 'reason': 'disabled'}
    confidence = summary.get('confidence') or 'none'
    if CONFIDENCE_RANK.get(confidence, 0) < CONFIDENCE_RANK.get(policy['min_confidence'], 2):
        return {'apply': False, 'reason': 'low_confidence', 'confidence': confidence}
    candidate = _candidate_from_summary(summary, policy['mode'])
    try:
        candidate = float(candidate)
        current = float(current_multiplier)
    except (TypeError, ValueError):
        return {'apply': False, 'reason': 'invalid_candidate'}
    if candidate <= 0 or current <= 0:
        return {'apply': False, 'reason': 'invalid_candidate'}
    delta_pct = (candidate - current) * 100.0 / current
    if abs(delta_pct) > float(policy['max_delta_percent']):
        return {
            'apply': False, 'reason': 'delta_too_large',
            'candidate': candidate, 'delta_percent': delta_pct,
        }
    if not manual and abs(delta_pct) < float(policy['min_delta_percent']):
        return {
            'apply': False, 'reason': 'delta_too_small',
            'candidate': candidate, 'delta_percent': delta_pct,
        }
    if not manual and runtime_state:
        last = _parse_dt(runtime_state.get('applied_at'))
        if last is not None:
            if last.tzinfo is not None and now.tzinfo is None:
                now_cmp = now.replace(tzinfo=last.tzinfo)
            elif last.tzinfo is None and now.tzinfo is not None:
                last = last.replace(tzinfo=now.tzinfo)
                now_cmp = now
            else:
                now_cmp = now
            elapsed_hours = (now_cmp - last).total_seconds() / 3600
            if elapsed_hours < float(policy['cooldown_hours']):
                return {
                    'apply': False, 'reason': 'cooldown',
                    'candidate': candidate, 'delta_percent': delta_pct,
                    'hours_left': float(policy['cooldown_hours']) - elapsed_hours,
                }
    return {
        'apply': True,
        'reason': 'ok',
        'candidate': candidate,
        'delta_percent': delta_pct,
        'confidence': confidence,
    }


def write_multiplier_state(path, *, multiplier, previous_multiplier, summary,
                           mode, actor, now=None, auto=False):
    now = now or datetime.utcnow()
    payload = {
        'enabled': True,
        'multiplier': round(float(multiplier), 4),
        'previous_multiplier': float(previous_multiplier),
        'confidence': summary.get('confidence'),
        'sample_count': int(summary.get('sample_count') or 0),
        'included_sample_count': int(summary.get('included_sample_count') or 0),
        'window_hours': int(summary.get('window_hours') or 0),
        'mode': mode,
        'auto': bool(auto),
        'applied_at': now.isoformat(timespec='seconds'),
        'actor': actor or ('auto' if auto else 'admin'),
    }
    state_store.save_json(path, payload)
    return payload


def maybe_auto_adjust(calibration_path, *, current_multiplier,
                      policy_path=AUTO_POLICY_FILE,
                      runtime_state_path=DISPLAY_MULTIPLIER_STATE_FILE,
                      now=None):
    now = now or datetime.utcnow()
    policy = load_auto_policy(policy_path)
    if not policy.get('enabled'):
        return {'applied': False, 'reason': 'disabled', 'policy': policy}
    summary = summarize(
        calibration_path,
        current_multiplier=current_multiplier,
        now=now,
        window_hours=policy['window_hours'],
    )
    runtime_state = state_store.load_json(runtime_state_path, {})
    decision = evaluate_multiplier_candidate(
        summary, current_multiplier, policy,
        runtime_state=runtime_state, now=now, manual=False)
    policy['last_checked_at'] = now.isoformat(timespec='seconds')
    policy['last_decision'] = 'applied' if decision.get('apply') else 'skipped'
    policy['last_reason'] = decision.get('reason')
    if decision.get('candidate') is not None:
        policy['last_candidate'] = round(float(decision['candidate']), 4)
    if decision.get('apply'):
        write_multiplier_state(
            runtime_state_path,
            multiplier=decision['candidate'],
            previous_multiplier=current_multiplier,
            summary=summary,
            mode=policy['mode'],
            actor='auto',
            now=now,
            auto=True,
        )
        policy['last_applied_at'] = now.isoformat(timespec='seconds')
    save_auto_policy(policy, policy_path)
    return {
        'applied': bool(decision.get('apply')),
        'reason': decision.get('reason'),
        'candidate': decision.get('candidate'),
        'summary': summary,
        'policy': policy,
    }
