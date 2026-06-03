#!/usr/bin/env bash
set -euo pipefail

HY_DIR="${HY2_HY_DIR:-/root/hysteria}"
XRAY_CONFIG="${HY2_XRAY_CONFIG:-/usr/local/etc/xray/config.json}"
BACKUP_DIR="${HY2_BACKUP_DIR:-$HY_DIR/backups}"

umask 077
install -d -m 700 "$BACKUP_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/hy2-backup-$ts.tar.gz"
tmp_out="$out.tmp"
manifest="$(mktemp)"

cleanup() {
  rm -f "$manifest" "$tmp_out"
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

tar -C / -czf "$tmp_out" --files-from "$manifest"
mv "$tmp_out" "$out"
chmod 600 "$out"
sha256sum "$out" > "$out.sha256"
chmod 600 "$out.sha256"

printf '%s\n' "$out"
