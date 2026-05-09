DISPLAY_MULTIPLIER = 2.28


def fmt_bytes(num):
    n = float(max(0, int(num)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    return f"{n:.2f} {units[idx]}"
