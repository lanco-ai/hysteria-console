"""Shared presentation data for reset-audit log readers."""

import json
from collections import deque


def read_reset_logs(path, *, limit=300, action_label, fmt_bytes):
    try:
        with path.open('r', encoding='utf-8') as handle:
            raw_lines = list(deque(handle, maxlen=limit))
    except FileNotFoundError:
        raw_lines = []

    rows = []
    for raw_line in reversed(raw_lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue

        before = entry.get('before', {})
        after = entry.get('after', {})
        if isinstance(before, dict) and 'total' in before:
            after = after if isinstance(after, dict) else {}
            detail = f'{fmt_bytes(before.get("total", 0))} → {fmt_bytes(after.get("total", 0))}'
        else:
            detail = ''
        action = action_label(str(entry.get('action', '')))
        rows.append(
            {
                'time': str(entry.get('time', '')),
                'actor': str(entry.get('actor', '')),
                'ip': str(entry.get('ip', '')),
                'action': str(action),
                'target': str(entry.get('target', '')),
                'month': str(entry.get('month', '')),
                'detail': str(detail),
            }
        )

    return {'limit': limit, 'rows': rows}
