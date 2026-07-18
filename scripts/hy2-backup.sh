#!/usr/bin/bash -p
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset CDPATH

HY_DIR="${HY2_HY_DIR:-/root/hysteria}"
XRAY_CONFIG="${HY2_XRAY_CONFIG:-/usr/local/etc/xray/config.json}"
TUIC_CONFIG="${HY2_TUIC_CONFIG:-$HY_DIR/tuic.json}"
BACKUP_DIR="${HY2_BACKUP_DIR:-$HY_DIR/backups}"
BACKUP_KEEP="${HY2_BACKUP_KEEP:-14}"
DEPLOY_LOCK="${HY2_DEPLOY_LOCK:-/run/hy2-locks/deploy.lock}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOCK_EXEC="${HY2_LOCK_EXEC_BIN:-$SCRIPT_DIR/hy2-lock-exec.py}"
DEPLOY_LOCK_MARKER_ENV=HY2_DEPLOY_LOCK_MARKER
USAGE_LOCK="${HY2_USAGE_LOCK:-$HY_DIR/state/usage.lock}"
META_LOCK="${HY2_META_LOCK:-$HY_DIR/subscription_meta.json.lock}"
TEMPLATE_LOCK="${HY2_TEMPLATE_LOCK:-$HY_DIR/state/template.lock}"
XRAY_LOCK="${HY2_XRAY_LOCK:-$XRAY_CONFIG.lock}"
TUIC_LOCK="${HY2_TUIC_LOCK:-$TUIC_CONFIG.lock}"

umask 077
install -d -m 700 "$BACKUP_DIR"

if ! [[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]]; then
  printf 'HY2_BACKUP_KEEP must be a non-negative integer\n' >&2
  exit 2
fi

[[ -f "$LOCK_EXEC" && ! -L "$LOCK_EXEC" ]] || {
  printf 'Hardened lock executor is missing or unsafe\n' >&2
  exit 1
}
if [[ -n "${HY2_DEPLOY_LOCK_MARKER:-}" ]]; then
  /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$DEPLOY_LOCK" \
    --marker-env "$DEPLOY_LOCK_MARKER_ENV" \
    --verify
else
  exec /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$DEPLOY_LOCK" \
    --timeout 30 \
    --marker-env "$DEPLOY_LOCK_MARKER_ENV" \
    -- /usr/bin/bash -p "$0" "$@"
fi

prune_old_backups() {
  local keep="$1"
  [[ "$keep" -gt 0 ]] || return 0
  local count=0
  local line path
  while IFS= read -r line; do
    path="${line#* }"
    count=$((count + 1))
    if [[ "$count" -gt "$keep" ]]; then
      rm -f -- "$path" "$path.sha256"
    fi
  done < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f \
      \( -name 'hy2-backup-*.tar.gz' -o -name 'hy2-backup-*.tar.gz.enc' \) \
      -printf '%T@ %p\n' | sort -rn
  )
}

fsync_backup_pair() {
  # A successful rename is not a durability boundary by itself.  Flush both
  # files and then the containing directory before pruning old recovery
  # points or publishing the new pair off-host.
  python3 - "$1" "$2" <<'PY'
import os
import sys
from pathlib import Path

paths = [Path(value) for value in sys.argv[1:]]
if len(paths) != 2 or paths[0].parent != paths[1].parent:
    raise SystemExit("backup archive and checksum must share one directory")
for path in paths:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
directory = paths[0].parent
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
fd = os.open(directory, flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

ts="$(date -u +%Y%m%dT%H%M%SZ)"
plain_out="$BACKUP_DIR/hy2-backup-$ts.tar.gz"
out="$plain_out"
tmp_tar="$plain_out.tmp"
tmp_enc=""
tmp_sha=""
manifest="$(mktemp)"
pass_arg=()
snapshot_locks_held=0

if [[ -n "${HY2_BACKUP_PASSPHRASE_FILE:-}" ]]; then
  out="$plain_out.enc"
  tmp_enc="$out.tmp"
  pass_arg=(-pass "file:$HY2_BACKUP_PASSPHRASE_FILE")
elif [[ -n "${HY2_BACKUP_PASSPHRASE:-}" ]]; then
  out="$plain_out.enc"
  tmp_enc="$out.tmp"
  pass_arg=(-pass env:HY2_BACKUP_PASSPHRASE)
fi

cleanup() {
  rm -f "$manifest" "$tmp_tar"
  if [[ -n "$tmp_enc" ]]; then
    rm -f "$tmp_enc"
  fi
  if [[ -n "$tmp_sha" ]]; then
    rm -f "$tmp_sha"
  fi
  if [[ "$snapshot_locks_held" == "1" ]]; then
    flock -u 14 || true
    flock -u 13 || true
    flock -u 12 || true
    flock -u 11 || true
    flock -u 10 || true
  fi
}
trap cleanup EXIT

acquire_snapshot_locks() {
  local lock_path
  for lock_path in \
    "$USAGE_LOCK" \
    "$META_LOCK" \
    "$TEMPLATE_LOCK" \
    "$XRAY_LOCK" \
    "$TUIC_LOCK"; do
    mkdir -p -- "$(dirname -- "$lock_path")"
  done

  # Match the runtime mutation order. Holding every lock until tar finishes
  # gives one cross-file snapshot and prevents a deployment from replacing
  # runtime artifacts while they are being read.
  exec 10>"$USAGE_LOCK"
  flock -x 10
  exec 11>"$META_LOCK"
  flock -x 11
  exec 12>"$TEMPLATE_LOCK"
  flock -x 12
  exec 13>"$XRAY_LOCK"
  flock -x 13
  exec 14>"$TUIC_LOCK"
  flock -x 14
  snapshot_locks_held=1
}

release_snapshot_locks() {
  flock -u 14
  flock -u 13
  flock -u 12
  flock -u 11
  flock -u 10
  snapshot_locks_held=0
  IFS=: read -r marker_version deploy_lock_fd _marker_rest \
    <<<"${HY2_DEPLOY_LOCK_MARKER:-}"
  [[ "$marker_version" == hy2-lock-v1 &&
      "$deploy_lock_fd" =~ ^[0-9]+$ ]] &&
    (( deploy_lock_fd >= 3 )) || {
    printf 'Inherited deployment lock marker is invalid\n' >&2
    exit 1
  }
  flock -u "$deploy_lock_fd"
  eval "exec ${deploy_lock_fd}>&-"
  unset HY2_DEPLOY_LOCK_MARKER
}

add_path() {
  local p="$1"
  [[ -e "$p" ]] || return 0
  printf '%s\n' "${p#/}" >> "$manifest"
}

acquire_snapshot_locks

for p in \
  "$HY_DIR/users.json" \
  "$HY_DIR/subscription_meta.json" \
  "$HY_DIR/admin_initial_password.txt" \
  "$HY_DIR/template.yaml" \
  "$HY_DIR/alerts.json" \
  "$HY_DIR/api_secret" \
  "$HY_DIR/server.crt" \
  "$HY_DIR/server.key" \
  "$HY_DIR/config.yaml" \
  "$TUIC_CONFIG" \
  "$XRAY_CONFIG"; do
  add_path "$p"
done

if [[ -d "$HY_DIR/state" ]]; then
  while IFS= read -r p; do
    add_path "$p"
  done < <(find "$HY_DIR/state" -maxdepth 1 -type f \( -name '*.json' -o -name '*.log' \) \
    ! -name 'panel_sessions.json' \
    ! -name 'user_panel_sessions.json' \
    ! -name 'credential_rotation_receipts.json' \
    ! -name 'credential_revocations.json' \
    ! -name 'device_admissions.json' | sort)
fi

if [[ ! -s "$manifest" ]]; then
  printf 'No hy2 runtime files found under %s\n' "$HY_DIR" >&2
  exit 1
fi

tar -C / -czf "$tmp_tar" --files-from "$manifest"
release_snapshot_locks

if [[ ${#pass_arg[@]} -gt 0 ]]; then
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -md sha256 \
    -in "$tmp_tar" -out "$tmp_enc" "${pass_arg[@]}"
  mv "$tmp_enc" "$out"
  rm -f "$tmp_tar"
else
  mv "$tmp_tar" "$out"
fi
chmod 600 "$out"
tmp_sha="$out.sha256.tmp"
(cd "$(dirname "$out")" && sha256sum "$(basename "$out")") > "$tmp_sha"
chmod 600 "$tmp_sha"
mv "$tmp_sha" "$out.sha256"
tmp_sha=""
fsync_backup_pair "$out" "$out.sha256"
prune_old_backups "$BACKUP_KEEP"

if [[ -n "${HY2_BACKUP_REMOTE:-}" ]]; then
  [[ "$out" == *.enc ]] || {
    printf 'Refusing off-host upload of an unencrypted backup\n' >&2
    exit 2
  }
  command -v rclone >/dev/null 2>&1 || {
    printf 'HY2_BACKUP_REMOTE requires rclone\n' >&2
    exit 2
  }
  remote="${HY2_BACKUP_REMOTE%/}"
  rclone copyto "$out" "$remote/$(basename "$out")"
  rclone copyto "$out.sha256" "$remote/$(basename "$out.sha256")"
fi

if [[ -n "${HY2_BACKUP_GIT_REPO:-}" ]]; then
  [[ "$out" == *.enc ]] || {
    printf 'Refusing Git upload of an unencrypted backup\n' >&2
    exit 2
  }
  uploader="${HY2_BACKUP_GIT_UPLOADER:-/usr/local/sbin/hy2-backup-git.sh}"
  [[ -x "$uploader" ]] || {
    printf 'Git backup uploader is not executable: %s\n' "$uploader" >&2
    exit 2
  }
  "$uploader" "$BACKUP_DIR"
  git_marker="${HY2_BACKUP_GIT_MARKER:-$HY_DIR/state/git_backup.last}"
  install -d -m 700 "$(dirname "$git_marker")"
  touch "$git_marker"
  chmod 600 "$git_marker"
fi

printf '%s\n' "$out"
