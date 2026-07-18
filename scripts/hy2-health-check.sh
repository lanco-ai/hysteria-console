#!/usr/bin/env bash
set -euo pipefail

HY_DIR="${HY2_HY_DIR:-/root/hysteria}"
BACKUP_DIR="${HY2_BACKUP_DIR:-$HY_DIR/backups}"
HTTPS_PORT="${HY2_HTTPS_PORT:-}"
TLS_SITE_CONF="${HY2_TLS_SITE_CONF:-/etc/nginx/sites-enabled/hysteria-panel-https.conf}"
HTTPS_REQUIRED_FILE="${HY2_HTTPS_REQUIRED_FILE:-$HY_DIR/state/https_required}"
HTTPS_RENEWAL_PENDING="${HY2_HTTPS_RENEWAL_PENDING:-/var/lib/hysteria/https-activation-recovery/renewal-pending}"
MAX_BACKUP_AGE="${HY2_MAX_BACKUP_AGE_SECONDS:-129600}"
MIN_DISK_FREE_PCT="${HY2_MIN_DISK_FREE_PCT:-15}"
failed=0

if [[ -z "$HTTPS_PORT" ]]; then
  HTTPS_PORT="$(awk '$1 == "listen" && $3 == "ssl;" {gsub(/;/, "", $2); print $2; exit}' \
    "$TLS_SITE_CONF" 2>/dev/null || true)"
  HTTPS_PORT="${HTTPS_PORT:-9444}"
fi

ok() { printf 'OK: %s\n' "$*"; }
bad() { printf 'ERROR: %s\n' "$*" >&2; failed=1; }

for unit in hysteria-auth.service hysteria-server.service hysteria-subscription.service tuic-server.service xray.service nginx.service; do
  if systemctl is-active --quiet "$unit"; then
    ok "$unit active"
  else
    bad "$unit is not active"
  fi
done

if curl --fail --silent --show-error --noproxy '*' \
  --connect-timeout 1 --max-time 3 \
  http://127.0.0.1:8082/readyz >/dev/null; then
  ok "hysteria authentication dependencies ready"
else
  bad "hysteria authentication dependencies are not ready"
fi

free_pct="$(df -P / | awk 'NR==2 {gsub(/%/, "", $5); print 100-$5}')"
if [[ "$free_pct" =~ ^[0-9]+$ ]] && (( free_pct >= MIN_DISK_FREE_PCT )); then
  ok "root disk ${free_pct}% free"
else
  bad "root disk only ${free_pct:-unknown}% free"
fi

latest_backup="$(find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'hy2-backup-*.tar.gz' -o -name 'hy2-backup-*.tar.gz.enc' \) \
  -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
if [[ -n "$latest_backup" ]]; then
  age=$(( $(date +%s) - $(stat -c %Y "$latest_backup") ))
  if (( age <= MAX_BACKUP_AGE )); then
    ok "backup age ${age}s"
  else
    bad "latest backup is ${age}s old"
  fi
else
  bad "no runtime backup found"
fi

git_marker="${HY2_BACKUP_GIT_MARKER:-$HY_DIR/state/git_backup.last}"
if [[ -f "$git_marker" ]]; then
  git_age=$(( $(date +%s) - $(stat -c %Y "$git_marker") ))
  if (( git_age <= MAX_BACKUP_AGE )); then
    ok "Git backup upload age ${git_age}s"
  else
    bad "latest Git backup upload is ${git_age}s old"
  fi
fi

https_required=0
if [[ -f "$HTTPS_REQUIRED_FILE" || -e "$TLS_SITE_CONF" || -L "$TLS_SITE_CONF" ]]; then
  https_required=1
fi

if (( https_required )); then
  if [[ -e "$HTTPS_RENEWAL_PENDING" || -L "$HTTPS_RENEWAL_PENDING" ]]; then
    bad "certificate renewal activation is pending recovery"
  fi
  if systemctl is-active --quiet snap.certbot.renew.timer; then
    ok "certificate renewal timer active"
  else
    bad "certificate renewal timer is not active"
  fi

  panel_target="$(awk '$1 == "server_name" {gsub(/;/, "", $2); print $2; exit}' \
    "$TLS_SITE_CONF" 2>/dev/null || true)"
  panel_cert="$(awk '$1 == "ssl_certificate" {gsub(/;/, "", $2); print $2; exit}' \
    "$TLS_SITE_CONF" 2>/dev/null || true)"
  if [[ -n "$panel_target" && -n "$panel_cert" && -r "$panel_cert" ]] \
    && openssl x509 -checkend 86400 -noout -in "$panel_cert" >/dev/null 2>&1; then
    ok "panel certificate valid for more than 24h"
  else
    bad "configured panel certificate missing or expires within 24h"
  fi

  if [[ -n "$panel_target" ]] && curl --fail --silent --show-error --max-time 5 \
    --noproxy '*' --connect-timeout 2 \
    --resolve "${panel_target}:${HTTPS_PORT}:127.0.0.1" \
    "https://${panel_target}:${HTTPS_PORT}/" >/dev/null; then
    ok "local HTTPS endpoint reachable on ${HTTPS_PORT}/tcp"
  else
    bad "local HTTPS endpoint unavailable on ${HTTPS_PORT}/tcp"
  fi
else
  ok "panel HTTPS intentionally not required"
fi

if (( failed )); then
  exit 1
fi
