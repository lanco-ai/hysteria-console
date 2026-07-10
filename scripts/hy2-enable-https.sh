#!/usr/bin/env bash
set -euo pipefail

target="${1:-${HY_SERVER_HOST:-}}"
email="${2:-${HY_CERTBOT_EMAIL:-}}"
https_port="${3:-${HY_HTTPS_PORT:-9444}}"
share_dir="${HY2_SHARE_DIR:-/usr/local/share/hy2}"
webroot="${HY2_CERTBOT_WEBROOT:-/var/www/certbot}"

die() {
  printf '[x] %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Must run as root."
[[ -n "$target" ]] || die "Usage: hy2-enable-https.sh <domain-or-ip> [email] [https-port]"
[[ "$https_port" =~ ^[0-9]+$ ]] || die "HTTPS port must be numeric."
(( https_port >= 1024 && https_port <= 65535 )) || die "HTTPS port must be between 1024 and 65535."
[[ "$https_port" != 8443 && "$https_port" != 9443 ]] || die "HTTPS port conflicts with an existing VPN listener."

is_ipv4=0
if [[ "$target" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
  is_ipv4=1
  IFS=. read -r a b c d <<<"$target"
  for octet in "$a" "$b" "$c" "$d"; do
    (( octet >= 0 && octet <= 255 )) || die "Invalid IPv4 address: $target"
  done
else
  case "$target" in
    *[!A-Za-z0-9.-]* | .* | *..* | *.) die "Invalid DNS hostname: $target" ;;
  esac
  [[ "$target" == *.* ]] || die "A DNS hostname must contain a dot: $target"
fi

for template in hysteria-panel-https.conf hysteria-panel-redirect.conf; do
  [[ -r "$share_dir/$template" ]] || die "Missing template: $share_dir/$template"
done

export DEBIAN_FRONTEND=noninteractive
if [[ ! -x /snap/bin/certbot ]]; then
  command -v snap >/dev/null 2>&1 || {
    apt-get update -y >/dev/null
    apt-get install -y snapd >/dev/null
  }
  snap install certbot --classic
fi

certbot_bin=/snap/bin/certbot
if (( is_ipv4 )); then
  certbot_version="$($certbot_bin --version | awk '{print $2}')"
  dpkg --compare-versions "$certbot_version" ge 5.4 || \
    die "Certbot 5.4 or newer is required for IP certificates (found $certbot_version)."
fi

install -d -m 755 "$webroot/.well-known/acme-challenge"
nginx -t
systemctl reload nginx.service

account_args=(--register-unsafely-without-email)
if [[ -n "$email" ]]; then
  account_args=(-m "$email")
fi

identifier_args=(-d "$target")
if (( is_ipv4 )); then
  identifier_args=(--preferred-profile shortlived --ip-address "$target")
fi

"$certbot_bin" certonly \
  --non-interactive \
  --agree-tos \
  --keep-until-expiring \
  --webroot \
  --webroot-path "$webroot" \
  "${account_args[@]}" \
  "${identifier_args[@]}"

cert_dir="/etc/letsencrypt/live/$target"
cert="$cert_dir/fullchain.pem"
key="$cert_dir/privkey.pem"
[[ -r "$cert" && -r "$key" ]] || die "Certificate files were not created under $cert_dir"
openssl x509 -checkend 86400 -noout -in "$cert" || die "New certificate expires too soon."

render_tls() {
  local src="$1" dst="$2"
  sed \
    -e "s|__HY_SERVER_HOST__|${target}|g" \
    -e "s|__HY_HTTPS_PORT__|${https_port}|g" \
    -e "s|__HY_TLS_CERT__|${cert}|g" \
    -e "s|__HY_TLS_KEY__|${key}|g" \
    "$src" > "$dst"
}

panel_conf=/etc/nginx/sites-available/hysteria-panel.conf
tls_conf=/etc/nginx/sites-available/hysteria-panel-https.conf
[[ ! -f "$panel_conf" ]] || cp -a "$panel_conf" "$panel_conf.bak.$(date -u +%Y%m%dT%H%M%SZ)"
render_tls "$share_dir/hysteria-panel-redirect.conf" "$panel_conf"
render_tls "$share_dir/hysteria-panel-https.conf" "$tls_conf"
chmod 644 "$panel_conf" "$tls_conf"
ln -sf "$tls_conf" /etc/nginx/sites-enabled/hysteria-panel-https.conf

install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 755 "$share_dir/hy2-cert-renew-hook.sh" \
  /etc/letsencrypt/renewal-hooks/deploy/hy2-cert-renew-hook.sh

nginx -t
systemctl reload nginx.service
systemctl enable --now snap.certbot.renew.timer 2>/dev/null || true

printf 'HTTPS enabled for https://%s:%s/admin\n' "$target" "$https_port"
printf 'Certificate expires: '
openssl x509 -enddate -noout -in "$cert" | cut -d= -f2-
