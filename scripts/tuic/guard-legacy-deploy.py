#!/usr/bin/env python3
"""Read-only guard: the legacy installer must not replace a migrated runtime."""
import json
from pathlib import Path
import sys


def main():
    path = Path(sys.argv[1])
    try:
        state = path.parent / 'state'
        mode_path = state / 'tuic_user_mode.json'
        if mode_path.exists():
            mode = json.loads(mode_path.read_text())
            if mode != {'version': 1, 'mode': 'legacy'}:
                raise ValueError('active or invalid TUIC accounting mode')
        elif any((state / name).exists() for name in ('tuic_user_state.json', 'tuic_user_pending.json')):
            raise ValueError('accounting state exists without explicit legacy rollback')
        if not path.exists():
            return 0
        config = json.loads(path.read_text())
        if not isinstance(config, dict) or 'inbounds' in config:
            raise ValueError('metered or unrecognized TUIC runtime')
    except (OSError, ValueError):
        print('Legacy deploy blocked: use the reviewed TUIC metered maintenance/rollback procedure.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
