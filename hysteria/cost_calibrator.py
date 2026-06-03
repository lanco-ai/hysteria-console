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
MAX_SAMPLES = 288
DEFAULT_WINDOW_HOURS = 24


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


def summarize_state(state, *, current_multiplier, now=None, window_hours=DEFAULT_WINDOW_HOURS):
    samples = _recent_samples(state.get('samples') or [], now=now, window_hours=window_hours)
    app_raw = sum(int(s.get('app_raw_bytes', 0) or 0) for s in samples)
    net_total = sum(int(s.get('net_total_delta', 0) or 0) for s in samples)
    net_tx = sum(int(s.get('net_tx_delta', 0) or 0) for s in samples)

    suggested = (net_total / app_raw) if app_raw > 0 else None
    egress = (net_tx / app_raw) if app_raw > 0 else None
    current = float(current_multiplier)
    delta_pct = ((suggested - current) * 100 / current) if suggested is not None and current > 0 else None

    if app_raw <= 0:
        confidence = 'none'
    elif len(samples) >= 12 and app_raw >= 10 * 1024 ** 3:
        confidence = 'high'
    elif len(samples) >= 3 and app_raw >= 1 * 1024 ** 3:
        confidence = 'medium'
    else:
        confidence = 'low'

    return {
        'window_hours': window_hours,
        'sample_count': len(samples),
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
    }


def summarize(path, *, current_multiplier, now=None, window_hours=DEFAULT_WINDOW_HOURS):
    state = state_store.load_json(path, {})
    return summarize_state(
        state,
        current_multiplier=current_multiplier,
        now=now,
        window_hours=window_hours,
    )
