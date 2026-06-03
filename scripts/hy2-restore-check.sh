#!/usr/bin/env bash
set -euo pipefail

HY_DIR="${HY2_HY_DIR:-/root/hysteria}"
archive="${1:-}"

if [[ -z "$archive" ]]; then
  printf 'Usage: %s /path/to/hy2-backup.tar.gz[.enc]\n' "$0" >&2
  exit 2
fi
if [[ ! -f "$archive" ]]; then
  printf 'Archive not found: %s\n' "$archive" >&2
  exit 2
fi

work="$(mktemp -d)"
manifest="$work/manifest.txt"
tarball="$archive"

cleanup() {
  rm -rf "$work"
}
trap cleanup EXIT

pass_arg=()
if [[ "$archive" == *.enc ]]; then
  if [[ -n "${HY2_RESTORE_PASSPHRASE_FILE:-}" ]]; then
    pass_arg=(-pass "file:$HY2_RESTORE_PASSPHRASE_FILE")
  elif [[ -n "${HY2_BACKUP_PASSPHRASE_FILE:-}" ]]; then
    pass_arg=(-pass "file:$HY2_BACKUP_PASSPHRASE_FILE")
  elif [[ -n "${HY2_RESTORE_PASSPHRASE:-}" ]]; then
    pass_arg=(-pass env:HY2_RESTORE_PASSPHRASE)
  elif [[ -n "${HY2_BACKUP_PASSPHRASE:-}" ]]; then
    pass_arg=(-pass env:HY2_BACKUP_PASSPHRASE)
  else
    printf 'Encrypted archive requires HY2_RESTORE_PASSPHRASE_FILE or HY2_RESTORE_PASSPHRASE\n' >&2
    exit 2
  fi
  tarball="$work/archive.tar.gz"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 \
    -in "$archive" -out "$tarball" "${pass_arg[@]}"
fi

tar -tzf "$tarball" > "$manifest"

python3 - "$manifest" <<'PY'
from pathlib import Path
import sys

manifest = Path(sys.argv[1])
bad = []
for raw in manifest.read_text(encoding='utf-8').splitlines():
    p = raw.strip()
    parts = [part for part in p.split('/') if part]
    if not p or p.startswith('/') or '..' in parts:
        bad.append(p)
if bad:
    print('Unsafe archive paths:', ', '.join(bad), file=sys.stderr)
    raise SystemExit(1)
PY

tar -xzf "$tarball" -C "$work"

python3 - "$work" "$HY_DIR" <<'PY'
import json
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - deploy installs python3-yaml
    yaml = None

root = Path(sys.argv[1])
hy_dir = Path(sys.argv[2])
errors = []
warnings = []

json_files = []
for p in root.rglob('*.json'):
    json_files.append(p)
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        errors.append(f'invalid JSON: {p.relative_to(root)} ({exc})')
        continue
    rel = str(p.relative_to(root))
    if rel.endswith('root/hysteria/users.json') and not isinstance(data, dict):
        errors.append('users.json must be a JSON object')
    if rel.endswith('root/hysteria/subscription_meta.json') and not isinstance(data, dict):
        errors.append('subscription_meta.json must be a JSON object')

template = root / 'root/hysteria/template.yaml'
if template.exists() and yaml is not None:
    try:
        parsed = yaml.safe_load(template.read_text(encoding='utf-8')) or {}
        if not isinstance(parsed, dict):
            errors.append('template.yaml must parse to a mapping')
    except Exception as exc:
        errors.append(f'invalid YAML: root/hysteria/template.yaml ({exc})')

users = root / 'root/hysteria/users.json'
meta = root / 'root/hysteria/subscription_meta.json'
if not users.exists():
    warnings.append('users.json not present in archive')
if not meta.exists():
    warnings.append('subscription_meta.json not present in archive')

archive_files = [p for p in root.rglob('*') if p.is_file()]
overwrites = []
for p in archive_files:
    rel = p.relative_to(root)
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == 'root' and parts[1] == 'hysteria':
        live = hy_dir.joinpath(*parts[2:])
        if live.exists():
            overwrites.append(str(live))

if errors:
    for err in errors:
        print(f'ERROR: {err}', file=sys.stderr)
    raise SystemExit(1)

print('OK: hy2 backup dry-run passed')
print(f'files={len(archive_files)}')
print(f'json_files={len(json_files)}')
print(f'would_overwrite={len(overwrites)}')
if warnings:
    for warning in warnings:
        print(f'WARN: {warning}')
if overwrites:
    print('overwrite_examples=' + ', '.join(overwrites[:5]))
PY
