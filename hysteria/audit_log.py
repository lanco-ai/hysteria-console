"""Durable private audit-log append with post-commit failure reporting."""

import json
import os
import sys
from datetime import datetime


def append_reset_log(path, actor, action, target, before, after, *, client_ip, month):
    line = {
        'time': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'actor': actor,
        'ip': client_ip,
        'action': action,
        'target': target,
        'month': month(),
        'before': before,
        'after': after,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(line, ensure_ascii=True) + '\n')
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        # The authorization/accounting mutation has already committed.
        # Do not misreport it as failed and invite a destructive retry.
        print(
            f'CRITICAL: audit log append failed: {exc}',
            file=sys.stderr,
        )
