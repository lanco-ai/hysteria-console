DEFAULT_DISPLAY_MULTIPLIER = 2.28
DISPLAY_MULTIPLIER_MIN = 0.1
DISPLAY_MULTIPLIER_MAX = 20.0


def parse_display_multiplier(raw, default=DEFAULT_DISPLAY_MULTIPLIER):
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)
    if value < DISPLAY_MULTIPLIER_MIN or value > DISPLAY_MULTIPLIER_MAX:
        return float(default)
    return value


DISPLAY_MULTIPLIER = parse_display_multiplier('__HY_DISPLAY_MULTIPLIER__')


def fmt_bytes(num):
    n = float(max(0, int(num)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    return f"{n:.2f} {units[idx]}"
