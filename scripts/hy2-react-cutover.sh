#!/bin/bash -p
# Explicit, reversible switch for the React panel documents.
#
# This helper is intentionally separate from deploy.sh. Installing the
# opt-in runtime never changes the public nginx route; an operator must pass
# HY_REACT_CUTOVER_APPROVED=1 to apply or roll back this switch.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

action="${1:-status}"
case "$action" in
  apply|rollback|status) ;;
  *)
    printf 'usage: %s {apply|rollback|status}\n' "$0" >&2
    exit 64
    ;;
esac

nginx_root="${HY2_REACT_NGINX_ROOT:-/etc/nginx}"
share_dir="${HY2_REACT_SHARE_DIR:-/usr/local/share/hy2}"
backup_root="${HY2_REACT_BACKUP_ROOT:-/var/lib/hysteria/react-cutover}"
nginx_bin="${HY2_REACT_NGINX_BIN:-/usr/sbin/nginx}"
systemctl_bin="${HY2_REACT_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
curl_bin="${HY2_REACT_CURL_BIN:-/usr/bin/curl}"

http_template="$share_dir/hysteria-panel-react.conf"
https_template="$share_dir/hysteria-panel-react-https.conf"
http_target="$nginx_root/sites-available/hysteria-panel.conf"
https_target="$nginx_root/sites-available/hysteria-panel-https.conf"
http_enabled="$nginx_root/sites-enabled/hysteria-panel.conf"
https_enabled="$nginx_root/sites-enabled/hysteria-panel-https.conf"
current_pointer="$backup_root/current"
cutover_started=0
backup_dir=""

die() {
  printf '[x] %s\n' "$*" >&2
  exit 1
}

warn() {
  printf '[!] %s\n' "$*" >&2
}

[[ $EUID -eq 0 ]] || die 'Must run as root.'

# Command/path overrides are available only to an isolated root-owned test
# harness. Production always uses the fixed system paths above.
test_mode="${HY2_REACT_CUTOVER_TEST_MODE:-0}"
[[ "$test_mode" == 0 || "$test_mode" == 1 ]] ||
  die 'HY2_REACT_CUTOVER_TEST_MODE must be 0 or 1.'
override_names=(
  HY2_REACT_NGINX_ROOT HY2_REACT_SHARE_DIR HY2_REACT_BACKUP_ROOT
  HY2_REACT_NGINX_BIN HY2_REACT_SYSTEMCTL_BIN HY2_REACT_CURL_BIN
)
if [[ "$test_mode" == 0 ]]; then
  for override_name in "${override_names[@]}"; do
    [[ -v "$override_name" ]] || continue
    die "$override_name is accepted only by the isolated test harness."
  done
else
  test_root="${HY2_REACT_TEST_ROOT:-}"
  [[ -n "$test_root" && -d "$test_root" && ! -L "$test_root" ]] ||
    die 'HY2_REACT_TEST_ROOT must be a real directory in test mode.'
  for override_name in "${override_names[@]}"; do
    [[ -v "$override_name" ]] ||
      die "$override_name must be supplied in test mode."
  done
  test_root="$(cd -- "$test_root" && pwd -P)"
  /usr/bin/python3 -I - "$test_root" "${override_names[@]}" <<'PY'
import os
import stat
import sys

root, *names = sys.argv[1:]
metadata = os.lstat(root)
if (
    not stat.S_ISDIR(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or stat.S_IMODE(metadata.st_mode) & 0o077
):
    raise SystemExit("test root must be root-owned and mode 0700")
real_root = os.path.realpath(root)
for name in names:
    value = os.environ.get(name)
    if value is None:
        continue
    if not os.path.isabs(value):
        raise SystemExit(f"{name} must be absolute")
    try:
        if os.path.commonpath((real_root, os.path.realpath(value))) != real_root:
            raise SystemExit(f"{name} escapes the isolated test root")
    except ValueError as exc:
        raise SystemExit(f"{name} has an invalid path") from exc
PY
fi

require_approval() {
  [[ "${HY_REACT_CUTOVER_APPROVED:-0}" == '1' ]] ||
    die 'Refusing public React cutover without HY_REACT_CUTOVER_APPROVED=1.'
}

require_regular_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || die "Expected a regular file: $path"
}

require_enabled_link() {
  local link="$1" target="$2" resolved
  [[ -L "$link" ]] || die "Expected an enabled nginx symlink: $link"
  resolved="$(readlink -f -- "$link")"
  [[ "$resolved" == "$target" ]] ||
    die "Enabled nginx symlink points somewhere unexpected: $link"
}

read_server_host() {
  awk '$1 == "server_name" { sub(/;$/, "", $2); print $2; exit }' "$https_template"
}

read_https_port() {
  awk '$1 == "listen" && $2 ~ /^[0-9]+$/ { sub(/;$/, "", $2); print $2; exit }' \
    "$https_template"
}

validate_templates() {
  local host port
  require_regular_file "$http_template"
  require_regular_file "$https_template"
  ! grep -q '__HY_[A-Z0-9_]*__' "$http_template" "$https_template" ||
    die 'React nginx templates still contain deployment placeholders.'
  host="$(read_server_host)"
  port="$(read_https_port)"
  [[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || die 'React template has no valid server_name.'
  [[ "$port" =~ ^[0-9]+$ && "$port" != 443 ]] ||
    die 'React template has an invalid or conflicting HTTPS port.'
  [[ "$port" == "${HY_HTTPS_PORT:-$port}" ]] ||
    die 'React template HTTPS port does not match HY_HTTPS_PORT.'
}

validate_targets() {
  require_regular_file "$http_target"
  require_regular_file "$https_target"
  require_enabled_link "$http_enabled" "$http_target"
  require_enabled_link "$https_enabled" "$https_target"
}

nginx_test() {
  "$nginx_bin" -t >/dev/null
}

reload_nginx() {
  "$systemctl_bin" reload nginx.service >/dev/null
}

snapshot_443() {
  local destination="$1"
  # Only the 443 listen directives are compared. The React template owns
  # 9444; this invariant ensures the unrelated 443 service is not changed.
  "$nginx_bin" -T 2>/dev/null |
    awk '/^[[:space:]]*listen[[:space:]]+(\[::\]:)?443([[:space:];]|$)/ { print }' \
    >"$destination"
}

atomic_install() {
  local source="$1" target="$2" temporary
  temporary="$target.react-cutover.$$"
  rm -f -- "$temporary"
  install -o root -g root -m 0644 -- "$source" "$temporary"
  mv -fT -- "$temporary" "$target"
}

make_backup() {
  local stamp directory
  install -d -o root -g root -m 0700 "$backup_root"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  directory="$backup_root/$stamp"
  local suffix=0
  while [[ -e "$directory" || -L "$directory" ]]; do
    suffix=$((suffix + 1))
    directory="$backup_root/$stamp-$suffix"
  done
  install -d -o root -g root -m 0700 "$directory"
  cp -a --no-target-directory "$http_target" "$directory/hysteria-panel.conf"
  cp -a --no-target-directory "$https_target" "$directory/hysteria-panel-https.conf"
  snapshot_443 "$directory/443.before"
  printf '%s\n' "$directory" >"$directory/README"
  chmod 0600 "$directory/README"
  printf '%s\n' "$directory"
}

write_current_pointer() {
  local directory="$1" temporary
  temporary="$current_pointer.tmp.$$"
  printf '%s\n' "$directory" >"$temporary"
  chmod 0600 "$temporary"
  mv -fT -- "$temporary" "$current_pointer"
}

read_current_pointer() {
  local value base
  [[ -f "$current_pointer" && ! -L "$current_pointer" ]] || return 1
  value="$(head -n 1 "$current_pointer")"
  [[ "$value" == "$backup_root/"* ]] || return 1
  [[ "$(dirname -- "$value")" == "$backup_root" ]] || return 1
  base="$(basename -- "$value")"
  [[ "$base" =~ ^20[0-9]{6}T[0-9]{6}Z(-[0-9]+)?$ ]] || return 1
  [[ -d "$value" && ! -L "$value" ]] || return 1
  printf '%s\n' "$value"
}

verify_loopback_react() {
  "$systemctl_bin" is-active --quiet hysteria-react.service ||
    die 'hysteria-react.service is not active.'
  "$curl_bin" --fail --silent --show-error --noproxy '*' --max-time 5 \
    http://127.0.0.1:8083/ >/dev/null ||
    die 'React loopback health check failed.'
}

verify_public_panel() {
  local host port code
  host="$(read_server_host)"
  port="$(read_https_port)"
  code="$("$curl_bin" --silent --show-error --noproxy '*' --insecure --max-time 8 \
    --resolve "$host:$port:127.0.0.1" \
    -o /dev/null -w '%{http_code}' "https://$host:$port/admin")" ||
    die 'HTTPS React panel probe failed.'
  [[ "$code" =~ ^[234][0-9][0-9]$ ]] ||
    die "HTTPS React panel probe returned HTTP $code."
}

restore_backup() {
  local directory="$1"
  require_regular_file "$directory/hysteria-panel.conf"
  require_regular_file "$directory/hysteria-panel-https.conf"
  atomic_install "$directory/hysteria-panel.conf" "$http_target"
  atomic_install "$directory/hysteria-panel-https.conf" "$https_target"
}

cleanup_on_failure() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "$cutover_started" == 1 && -n "$backup_dir" ]]; then
    warn 'Cutover failed; restoring the exact pre-cutover nginx files.'
    restore_backup "$backup_dir"
    nginx_test && reload_nginx || warn 'Automatic nginx rollback needs operator attention.'
  fi
  return "$rc"
}

apply_cutover() {
  local after_443
  require_approval
  validate_templates
  validate_targets
  verify_loopback_react
  nginx_test
  backup_dir="$(make_backup)"
  cutover_started=1
  atomic_install "$http_template" "$http_target"
  atomic_install "$https_template" "$https_target"
  nginx_test
  reload_nginx
  after_443="$(mktemp "$backup_dir/443.after.XXXXXX")"
  snapshot_443 "$after_443"
  cmp -s "$backup_dir/443.before" "$after_443" ||
    die '443 listen directives changed during React cutover.'
  verify_public_panel
  write_current_pointer "$backup_dir"
  cutover_started=0
  trap - EXIT
  rm -f -- "$after_443"
  printf 'React panel cutover applied; nginx 443 directives are unchanged.\n'
  printf 'Rollback backup: %s\n' "$backup_dir"
}

rollback_cutover() {
  local directory after_443
  require_approval
  validate_targets
  [[ -d "$backup_root" && ! -L "$backup_root" ]] ||
    die 'No React cutover backup directory exists.'
  directory="$(read_current_pointer 2>/dev/null)" ||
    die 'No active React cutover marker is available for rollback.'
  require_regular_file "$directory/hysteria-panel.conf"
  require_regular_file "$directory/hysteria-panel-https.conf"
  backup_dir="$(make_backup)"
  cutover_started=1
  restore_backup "$directory"
  nginx_test
  reload_nginx
  after_443="$(mktemp "$backup_dir/443.after.XXXXXX")"
  snapshot_443 "$after_443"
  cmp -s "$backup_dir/443.before" "$after_443" ||
    die '443 listen directives changed during React rollback.'
  rm -f -- "$after_443"
  rm -f -- "$current_pointer"
  cutover_started=0
  trap - EXIT
  printf 'React panel cutover rolled back using: %s\n' "$directory"
}

status_cutover() {
  local directory
  if directory="$(read_current_pointer 2>/dev/null)"; then
    printf 'React panel cutover backup: %s\n' "$directory"
  else
    printf 'React panel cutover: no active cutover marker\n'
  fi
}

case "$action" in
  apply)
    trap cleanup_on_failure EXIT
    apply_cutover
    ;;
  rollback)
    trap cleanup_on_failure EXIT
    rollback_cutover
    ;;
  status)
    status_cutover
    ;;
esac
