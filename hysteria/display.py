import json
import os
from pathlib import Path

DEFAULT_DISPLAY_MULTIPLIER = 2.28
DISPLAY_MULTIPLIER_MIN = 0.1
DISPLAY_MULTIPLIER_MAX = 20.0
DISPLAY_MULTIPLIER_STATE_FILE = os.environ.get(
    'HY_DISPLAY_MULTIPLIER_FILE',
    '/root/hysteria/state/display_multiplier.json',
)


def parse_display_multiplier(raw, default=DEFAULT_DISPLAY_MULTIPLIER):
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)
    if value < DISPLAY_MULTIPLIER_MIN or value > DISPLAY_MULTIPLIER_MAX:
        return float(default)
    return value


def parse_optional_display_multiplier(raw):
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < DISPLAY_MULTIPLIER_MIN or value > DISPLAY_MULTIPLIER_MAX:
        return None
    return value


DEPLOYED_DISPLAY_MULTIPLIER = parse_display_multiplier('__HY_DISPLAY_MULTIPLIER__')


def runtime_display_multiplier(path=DISPLAY_MULTIPLIER_STATE_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get('enabled', True):
        return None
    return parse_optional_display_multiplier(data.get('multiplier'))


def load_display_multiplier(default=DEPLOYED_DISPLAY_MULTIPLIER):
    env_value = os.environ.get('HY_DISPLAY_MULTIPLIER')
    if env_value:
        return parse_display_multiplier(env_value, default=default)
    runtime_value = runtime_display_multiplier()
    if runtime_value is not None:
        return runtime_value
    return float(default)


DISPLAY_MULTIPLIER = load_display_multiplier()


def fmt_bytes(num):
    n = float(max(0, int(num)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    return f"{n:.2f} {units[idx]}"
