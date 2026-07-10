#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-${HY2_BACKUP_DIR:-/root/hysteria/backups}}"
REMOTE="${HY2_BACKUP_GIT_REPO:-}"
KEEP="${HY2_BACKUP_GIT_KEEP:-14}"

[[ -n "$REMOTE" ]] || {
  printf 'HY2_BACKUP_GIT_REPO is required\n' >&2
  exit 2
}
[[ "$KEEP" =~ ^[0-9]+$ ]] && (( KEEP >= 1 && KEEP <= 100 )) || {
  printf 'HY2_BACKUP_GIT_KEEP must be between 1 and 100\n' >&2
  exit 2
}
[[ -d "$BACKUP_DIR" ]] || {
  printf 'Backup directory not found: %s\n' "$BACKUP_DIR" >&2
  exit 2
}

mapfile -t archives < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'hy2-backup-*.tar.gz.enc' \
    -printf '%T@ %p\n' | sort -rn | head -n "$KEEP" | cut -d' ' -f2-
)
(( ${#archives[@]} > 0 )) || {
  printf 'No encrypted hy2 backups found in %s\n' "$BACKUP_DIR" >&2
  exit 1
}

work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

git init -q -b main "$work"
git -C "$work" config user.name hy2-backup
git -C "$work" config user.email hy2-backup@localhost
git -C "$work" remote add origin "$REMOTE"
install -d -m 700 "$work/backups"

for archive in "${archives[@]}"; do
  checksum="$archive.sha256"
  [[ -f "$checksum" ]] || {
    printf 'Checksum missing for %s\n' "$archive" >&2
    exit 1
  }
  (cd "$BACKUP_DIR" && sha256sum -c "$(basename "$checksum")" >/dev/null)
  install -m 600 "$archive" "$work/backups/$(basename "$archive")"
  (cd "$work/backups" && sha256sum "$(basename "$archive")") \
    > "$work/backups/$(basename "$checksum")"
  chmod 600 "$work/backups/$(basename "$checksum")"
done

printf '%s\n' \
  '# hy2 encrypted backup snapshots' \
  '' \
  'This private repository is rewritten as a single rolling snapshot.' \
  'It contains only AES-256 encrypted runtime archives and SHA-256 checksums.' \
  'The decryption passphrase is intentionally stored outside GitHub.' \
  > "$work/README.md"

git -C "$work" add README.md backups
git -C "$work" commit -q -m "Encrypted backup snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git -C "$work" push --quiet --force origin HEAD:main

printf 'Uploaded %d encrypted backup(s) to %s\n' "${#archives[@]}" "$REMOTE"
