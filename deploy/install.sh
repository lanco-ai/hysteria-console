#!/usr/bin/env bash
# Stage and validate a complete release; restore all old files on failure.
# Fresh installations remain in standby until --enable-scheduler(s).
set -euo pipefail
SELF=$(realpath -- "${BASH_SOURCE[0]}")
cd -- "$(dirname -- "$SELF")/.."
LOCK_EXEC=/usr/local/sbin/hy2-lock-exec.py
if [[ $EUID -ne 0 ]]; then
  echo 'install.sh must run as root' >&2
  exit 1
fi
if [[ -z ${LANCOAI_DEPLOY_LOCKED:-} && -x $LOCK_EXEC ]]; then
  exec env LANCOAI_DEPLOY_LOCKED=1 /usr/bin/python3 "$LOCK_EXEC" \
    --lock-file /run/hy2-locks/deploy.lock --timeout 30 -- "$SELF" "$@"
fi
exec /usr/bin/python3 scripts/install_service.py shop "$@"
