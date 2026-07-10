#!/usr/bin/env bash
# One-shot installer for hy2 — Hysteria2 + Xray + subscription panel.
# Run as root on a fresh Debian/Ubuntu VPS.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HY_DIR=/root/hysteria
XRAY_ETC=/usr/local/etc/xray
SYSTEMD_DIR=/etc/systemd/system

log()  { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Must run as root."

# ---------- 1. Load .env ----------
ENV_FILE="$REPO_DIR/.env"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found. Copy .env.example → .env and fill it in."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

REQUIRED=(
  HY_SERVER_HOST HY_API_SECRET HY_OBFS_PASSWORD
  XRAY_REALITY_PRIVATE_KEY XRAY_REALITY_PUBLIC_KEY
  XRAY_REALITY_SHORT_ID XRAY_CLIENT_UUID
)
for v in "${REQUIRED[@]}"; do
  val="${!v:-}"
  [[ -n "$val" && "$val" != replace_me* && "$val" != your.server* ]] || die "$v is not set in .env"
done

# ---------- 2. OS packages ----------
log "Installing OS packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y curl openssl iptables nftables ca-certificates python3 python3-yaml nginx qrencode logrotate fail2ban >/dev/null

HY_DISPLAY_MULTIPLIER="${HY_DISPLAY_MULTIPLIER:-2.28}"
HY_HYSTERIA_VERSION="${HY_HYSTERIA_VERSION:-v2.9.3}"
HY_XRAY_VERSION="${HY_XRAY_VERSION:-v26.6.27}"
HY_ENABLE_HTTPS="${HY_ENABLE_HTTPS:-1}"
HY_HTTPS_PORT="${HY_HTTPS_PORT:-9444}"
python3 - "$HY_DISPLAY_MULTIPLIER" <<'PY'
import sys

raw = sys.argv[1]
try:
    value = float(raw)
except ValueError:
    raise SystemExit("HY_DISPLAY_MULTIPLIER must be a number")
if not (0.1 <= value <= 20.0):
    raise SystemExit("HY_DISPLAY_MULTIPLIER must be between 0.1 and 20.0")
PY

# ---------- 3. Install/upgrade hysteria binary ----------
[[ "$HY_HYSTERIA_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  die "HY_HYSTERIA_VERSION must look like v2.9.3"
case "$(uname -m)" in
  x86_64) hysteria_asset=hysteria-linux-amd64 ;;
  aarch64|arm64) hysteria_asset=hysteria-linux-arm64 ;;
  *) die "Unsupported architecture for Hysteria: $(uname -m)" ;;
esac
installed_hysteria_version="$(hysteria version 2>/dev/null | sed -n 's/^Version:[[:space:]]*//p' | head -1 || true)"
if [[ "$installed_hysteria_version" != "$HY_HYSTERIA_VERSION" ]]; then
  log "Installing Hysteria $HY_HYSTERIA_VERSION (was ${installed_hysteria_version:-missing})..."
  tmpdir="$(mktemp -d)"
  hysteria_base="https://github.com/apernet/hysteria/releases/download/app%2F${HY_HYSTERIA_VERSION}"
  curl -fL "$hysteria_base/$hysteria_asset" -o "$tmpdir/$hysteria_asset"
  curl -fL "$hysteria_base/hashes.txt" -o "$tmpdir/hashes.txt"
  expected_hash="$(awk -v asset="build/$hysteria_asset" '$2 == asset {print $1}' "$tmpdir/hashes.txt")"
  [[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || die "Release checksum not found for $hysteria_asset"
  printf '%s  %s\n' "$expected_hash" "$tmpdir/$hysteria_asset" | sha256sum -c -
  install -m 755 "$tmpdir/$hysteria_asset" /usr/local/bin/hysteria
  rm -rf "$tmpdir"
else
  log "Hysteria already at $HY_HYSTERIA_VERSION"
fi

# Disable the stock hysteria-server@ instance — we use our own unit.
systemctl stop hysteria-traffic-limiter.timer 2>/dev/null || true
systemctl disable --now hysteria-server.service 2>/dev/null || true

# ---------- 4. Install/upgrade xray binary ----------
[[ "$HY_XRAY_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  die "HY_XRAY_VERSION must look like v26.6.27"
installed_xray_version="$(xray version 2>/dev/null | awk 'NR == 1 {print "v" $2}' || true)"
if [[ "$installed_xray_version" != "$HY_XRAY_VERSION" ]]; then
  log "Installing Xray $HY_XRAY_VERSION (was ${installed_xray_version:-missing})..."
  bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" \
    @ install --version "$HY_XRAY_VERSION"
else
  log "Xray already at $HY_XRAY_VERSION"
fi

# ---------- 4b. Install TUIC server binary ----------
if ! command -v tuic-server >/dev/null 2>&1; then
  log "Installing tuic-server..."
  tmpdir="$(mktemp -d)"
  tuic_url="https://github.com/tuic-protocol/tuic/releases/download/tuic-server-1.0.0/tuic-server-1.0.0-x86_64-unknown-linux-gnu"
  curl -fL "$tuic_url" -o "$tmpdir/tuic-server"
  curl -fL "$tuic_url.sha256sum" -o "$tmpdir/tuic-server.sha256sum"
  (cd "$tmpdir" && sed 's/tuic-server-1.0.0-x86_64-unknown-linux-gnu/tuic-server/' tuic-server.sha256sum | sha256sum -c -)
  install -m 755 "$tmpdir/tuic-server" /usr/local/bin/tuic-server
  rm -rf "$tmpdir"
else
  log "tuic-server already installed: $(tuic-server --version 2>/dev/null || true)"
fi

# ---------- 5. Render templates ----------
render() {
  # render <src_template> <dest>
  local src="$1" dst="$2"
  sed \
    -e "s|__HY_API_SECRET__|${HY_API_SECRET}|g" \
    -e "s|__HY_OBFS_PASSWORD__|${HY_OBFS_PASSWORD}|g" \
    -e "s|__HY_SERVER_HOST__|${HY_SERVER_HOST}|g" \
    -e "s|__HY_DISPLAY_MULTIPLIER__|${HY_DISPLAY_MULTIPLIER}|g" \
    -e "s|__XRAY_REALITY_PRIVATE_KEY__|${XRAY_REALITY_PRIVATE_KEY}|g" \
    -e "s|__XRAY_REALITY_PUBLIC_KEY__|${XRAY_REALITY_PUBLIC_KEY}|g" \
    -e "s|__XRAY_REALITY_SHORT_ID__|${XRAY_REALITY_SHORT_ID}|g" \
    -e "s|__XRAY_CLIENT_UUID__|${XRAY_CLIENT_UUID}|g" \
    "$src" > "$dst"
}

install -d -m 755 "$HY_DIR" "$HY_DIR/state" "$XRAY_ETC"

# Runtime secret file — read at module load by the three .py services. Means a
# later `git pull` of the source files can't accidentally overwrite a deployed
# secret with the literal placeholder string and break the API auth header.
log "Writing $HY_DIR/api_secret"
umask 077
printf '%s\n' "$HY_API_SECRET" > "$HY_DIR/api_secret"
chmod 600 "$HY_DIR/api_secret"
umask 022

log "Rendering hysteria config and sources..."
render "$REPO_DIR/hysteria/config.yaml.tpl"          "$HY_DIR/config.yaml"
render "$REPO_DIR/hysteria/auth_backend.py"          "$HY_DIR/auth_backend.py"
render "$REPO_DIR/hysteria/subscription_service.py"  "$HY_DIR/subscription_service.py"
render "$REPO_DIR/hysteria/traffic_limiter.py"       "$HY_DIR/traffic_limiter.py"
render "$REPO_DIR/hysteria/alerts.py"                "$HY_DIR/alerts.py"
render "$REPO_DIR/hysteria/anomaly.py"               "$HY_DIR/anomaly.py"
render "$REPO_DIR/hysteria/charts.py"                "$HY_DIR/charts.py"
render "$REPO_DIR/hysteria/cost_calibrator.py"       "$HY_DIR/cost_calibrator.py"
render "$REPO_DIR/hysteria/cycle.py"                 "$HY_DIR/cycle.py"
render "$REPO_DIR/hysteria/health.py"                "$HY_DIR/health.py"
render "$REPO_DIR/hysteria/health_widgets.py"        "$HY_DIR/health_widgets.py"
render "$REPO_DIR/hysteria/http_utils.py"            "$HY_DIR/http_utils.py"
render "$REPO_DIR/hysteria/incident_console.py"      "$HY_DIR/incident_console.py"
render "$REPO_DIR/hysteria/state_store.py"           "$HY_DIR/state_store.py"
render "$REPO_DIR/hysteria/subscription_profiles.py" "$HY_DIR/subscription_profiles.py"
render "$REPO_DIR/hysteria/xray_config.py"           "$HY_DIR/xray_config.py"
render "$REPO_DIR/hysteria/tuic_config.py"           "$HY_DIR/tuic_config.py"
render "$REPO_DIR/hysteria/tuic_meter.py"            "$HY_DIR/tuic_meter.py"
render "$REPO_DIR/hysteria/usage_dashboard.py"       "$HY_DIR/usage_dashboard.py"
render "$REPO_DIR/hysteria/user_compat.py"           "$HY_DIR/user_compat.py"
render "$REPO_DIR/hysteria/display.py"               "$HY_DIR/display.py"
render "$REPO_DIR/hysteria/timeutil.py"              "$HY_DIR/timeutil.py"
install -m 644 "$REPO_DIR/hysteria/admin.css"        "$HY_DIR/admin.css"
install -m 644 "$REPO_DIR/hysteria/admin_poll.js"    "$HY_DIR/admin_poll.js"
install -m 644 "$REPO_DIR/hysteria/usage.js"         "$HY_DIR/usage.js"
chmod 700 "$HY_DIR"/*.py
chmod 600 "$HY_DIR/config.yaml"

log "Rendering clash subscription template → $HY_DIR/template.yaml"
render "$REPO_DIR/hysteria/clash-default.yaml.tpl" "$HY_DIR/template.yaml"

log "Rendering xray config.json..."
render "$REPO_DIR/xray/config.json.tpl" "$XRAY_ETC/config.json"
if ! getent group hy2-xray >/dev/null; then
  groupadd --system hy2-xray
fi
if ! id -u hy2-xray >/dev/null 2>&1; then
  useradd --system --gid hy2-xray --home-dir /nonexistent --shell /usr/sbin/nologin hy2-xray
fi
install -d -o hy2-xray -g hy2-xray -m 750 /var/log/xray
chown -R hy2-xray:hy2-xray /var/log/xray
chown root:hy2-xray "$XRAY_ETC/config.json"
chmod 640 "$XRAY_ETC/config.json"

# ---------- 6. Initial users.json ----------
if [[ ! -f "$HY_DIR/users.json" ]]; then
  log "Creating empty users.json"
  echo '{}' > "$HY_DIR/users.json"
  chmod 600 "$HY_DIR/users.json"
fi

log "Rendering tuic user map → $HY_DIR/tuic.json"
PYTHONPATH="$HY_DIR" python3 -c 'import tuic_config; tuic_config.sync_all()'

# ---------- 7. Self-signed TLS cert ----------
if [[ ! -f "$HY_DIR/server.crt" || ! -f "$HY_DIR/server.key" ]]; then
  log "Generating self-signed TLS certificate..."
  openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$HY_DIR/server.key" -out "$HY_DIR/server.crt" \
    -subj "/CN=hysteria2" -days 3650 >/dev/null 2>&1
  chmod 600 "$HY_DIR/server.key"
fi

# ---------- 8. Port hopping script ----------
install -m 755 "$REPO_DIR/scripts/hysteria-porthop.sh" /usr/local/sbin/hysteria-porthop.sh
install -m 755 "$REPO_DIR/scripts/hysteria-tcp-mss.sh" /usr/local/sbin/hysteria-tcp-mss.sh
install -m 755 "$REPO_DIR/scripts/hy2-backup.sh" /usr/local/sbin/hy2-backup.sh
install -m 755 "$REPO_DIR/scripts/hy2-backup-git.sh" /usr/local/sbin/hy2-backup-git.sh
install -m 755 "$REPO_DIR/scripts/hy2-restore-check.sh" /usr/local/sbin/hy2-restore-check.sh
install -m 755 "$REPO_DIR/scripts/hy2-enable-https.sh" /usr/local/sbin/hy2-enable-https.sh
install -m 755 "$REPO_DIR/scripts/hy2-cert-renew-hook.sh" /usr/local/sbin/hy2-cert-renew-hook.sh
install -m 755 "$REPO_DIR/scripts/hy2-health-check.sh" /usr/local/sbin/hy2-health-check.sh
install -m 644 "$REPO_DIR/logrotate/xray" /etc/logrotate.d/xray
install -d -m 755 /usr/local/share/hy2
install -m 644 "$REPO_DIR/nginx/hysteria-panel-https.conf" /usr/local/share/hy2/hysteria-panel-https.conf
install -m 644 "$REPO_DIR/nginx/hysteria-panel-redirect.conf" /usr/local/share/hy2/hysteria-panel-redirect.conf
install -m 644 "$REPO_DIR/scripts/hy2-cert-renew-hook.sh" /usr/local/share/hy2/hy2-cert-renew-hook.sh

# ---------- 8b. Network tuning ----------
log "Installing network tuning..."
cat >/etc/sysctl.d/99-hysteria-udp.conf <<'SYSCTL'
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.core.netdev_max_backlog = 16384
net.ipv4.udp_rmem_min = 8192
net.ipv4.udp_wmem_min = 8192
net.ipv4.tcp_mtu_probing = 1
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
SYSCTL
modprobe tcp_bbr 2>/dev/null || true
printf 'tcp_bbr\n' >/etc/modules-load.d/tcp-bbr.conf
sysctl --system >/dev/null

# ---------- 9. nginx reverse proxy for the admin panel ----------
# The subscription service only listens on 127.0.0.1:8081; nginx on :80 fronts it.
log "Installing nginx site for hysteria-panel..."
render "$REPO_DIR/nginx/hysteria-panel.conf" /etc/nginx/sites-available/hysteria-panel.conf
chmod 644 /etc/nginx/sites-available/hysteria-panel.conf
ln -sf /etc/nginx/sites-available/hysteria-panel.conf /etc/nginx/sites-enabled/hysteria-panel.conf
# Remove the default site if it's still there (it would clash on :80).
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx.service
systemctl reload nginx.service

if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then
  /usr/local/sbin/hy2-enable-https.sh "$HY_SERVER_HOST" "${HY_CERTBOT_EMAIL:-}" "$HY_HTTPS_PORT"
fi

# ---------- 10. Systemd units ----------
log "Installing systemd units..."
install -m 644 "$REPO_DIR/systemd/hysteria-server.service"           "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hysteria-subscription.service"     "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hysteria-traffic-limiter.service"  "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hysteria-traffic-limiter.timer"    "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hy2-backup.service"                "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hy2-backup.timer"                  "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hysteria-porthop.service"          "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hysteria-tcp-mss.service"          "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/tuic-server.service"               "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hy2-health-check.service"          "$SYSTEMD_DIR/"
install -m 644 "$REPO_DIR/systemd/hy2-health-check.timer"            "$SYSTEMD_DIR/"
install -d -m 755 "$SYSTEMD_DIR/xray.service.d"
install -m 644 "$REPO_DIR/systemd/xray.service.d/20-hy2-hardening.conf" \
  "$SYSTEMD_DIR/xray.service.d/20-hy2-hardening.conf"

install -d -m 755 /etc/fail2ban/filter.d /etc/fail2ban/jail.d
install -m 644 "$REPO_DIR/fail2ban/filter.d/tuic-auth.conf" /etc/fail2ban/filter.d/tuic-auth.conf
install -m 644 "$REPO_DIR/fail2ban/jail.d/tuic-auth.conf" /etc/fail2ban/jail.d/tuic-auth.conf

install -d -m 755 /etc/systemd/journald.conf.d
install -m 644 "$REPO_DIR/journald/60-hy2-limits.conf" /etc/systemd/journald.conf.d/60-hy2-limits.conf

systemctl daemon-reload
systemctl restart systemd-journald.service
systemctl restart fail2ban.service

# ---------- 11. Enable + start ----------
log "Enabling and starting services..."
systemctl enable --now hysteria-porthop.service
systemctl enable --now hysteria-tcp-mss.service
systemctl enable --now hysteria-server.service
systemctl enable --now hysteria-subscription.service
systemctl enable --now hysteria-traffic-limiter.timer
systemctl enable --now hy2-backup.timer
systemctl enable --now hy2-health-check.timer
systemctl enable --now xray.service
systemctl enable --now tuic-server.service

# Existing active units do not automatically reload changed configs or sandbox
# settings after daemon-reload. Restart them explicitly during an in-place deploy.
systemctl restart hysteria-subscription.service
systemctl restart xray.service
systemctl restart tuic-server.service
systemctl restart hysteria-traffic-limiter.timer
if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then
  sleep 2
  systemctl start hy2-health-check.service
fi

sleep 1
log "Status:"
for u in hysteria-server hysteria-subscription hysteria-traffic-limiter.timer hy2-backup.timer hy2-health-check.timer hysteria-tcp-mss xray tuic-server; do
  printf '  %-40s %s\n' "$u" "$(systemctl is-active "$u" || true)"
done

cat <<EOF

Done. Open the admin panel at:
  $(if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then printf 'https://%s:%s/admin' "$HY_SERVER_HOST" "$HY_HTTPS_PORT"; else printf 'http://%s/admin' "$HY_SERVER_HOST"; fi)

First-time setup:
  1. Log in. If no admin password was preconfigured, the first service start
     writes root-only credentials to $HY_DIR/admin_initial_password.txt.
  2. Create users. Each user gets a /sub/<name>?token=... URL to import into Clash.
  3. Hysteria auth flows through $HY_DIR/auth_backend.py which reads users.json.

Keep $HY_DIR/{users.json,subscription_meta.json,server.key} safe — they are NOT in git.
EOF
