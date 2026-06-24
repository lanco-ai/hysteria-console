#!/usr/bin/env bash
set -euo pipefail

HY_DIR="${HY2_HY_DIR:-/root/hysteria}"
XRAY_CONFIG="${HY2_XRAY_CONFIG:-/usr/local/etc/xray/config.json}"
BACKUP_DIR="${HY2_BACKUP_DIR:-$HY_DIR/backups}"
BACKUP_KEEP="${HY2_BACKUP_KEEP:-14}"

umask 077
install -d -m 700 "$BACKUP_DIR"

if ! [[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]]; then
  printf 'HY2_BACKUP_KEEP must be a non-negative integer\n' >&2
  exit 2
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

ts="$(date -u +%Y%m%dT%H%M%SZ)"
plain_out="$BACKUP_DIR/hy2-backup-$ts.tar.gz"
out="$plain_out"
tmp_tar="$plain_out.tmp"
tmp_enc=""
manifest="$(mktemp)"
pass_arg=()

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
}
trap cleanup EXIT

add_path() {
  local p="$1"
  [[ -e "$p" ]] || return 0
  printf '%s\n' "${p#/}" >> "$manifest"
}

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
  "$HY_DIR/tuic.json" \
  "$XRAY_CONFIG"; do
  add_path "$p"
done

if [[ -d "$HY_DIR/state" ]]; then
  while IFS= read -r p; do
    add_path "$p"
  done < <(find "$HY_DIR/state" -maxdepth 1 -type f \( -name '*.json' -o -name '*.log' \) ! -name 'panel_sessions.json' | sort)
fi

if [[ ! -s "$manifest" ]]; then
  printf 'No hy2 runtime files found under %s\n' "$HY_DIR" >&2
  exit 1
fi

tar -C / -czf "$tmp_tar" --files-from "$manifest"
if [[ ${#pass_arg[@]} -gt 0 ]]; then
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -md sha256 \
    -in "$tmp_tar" -out "$tmp_enc" "${pass_arg[@]}"
  mv "$tmp_enc" "$out"
  rm -f "$tmp_tar"
else
  mv "$tmp_tar" "$out"
fi
chmod 600 "$out"
sha256sum "$out" > "$out.sha256"
chmod 600 "$out.sha256"
prune_old_backups "$BACKUP_KEEP"

printf '%s\n' "$out"
