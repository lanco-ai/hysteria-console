#!/usr/bin/env bash
set -euo pipefail

domain="${1:-${HY_SERVER_HOST:-}}"
email="${2:-${HY_CERTBOT_EMAIL:-}}"

die() {
  printf '[x] %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Must run as root."
[[ -n "$domain" ]] || die "Usage: hy2-enable-https.sh <domain> <email>"
[[ -n "$email" ]] || die "Usage: hy2-enable-https.sh <domain> <email>"

case "$domain" in
  *[!A-Za-z0-9.-]* | .* | *..* | *.)
    die "Invalid domain: $domain"
    ;;
esac
[[ "$domain" == *.* ]] || die "Certbot requires a DNS hostname, not a single-label host: $domain"
if [[ "$domain" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
  die "Certbot requires a DNS hostname, not an IP address: $domain"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y certbot python3-certbot-nginx >/dev/null

panel_conf=/etc/nginx/sites-available/hysteria-panel.conf
if [[ -f "$panel_conf" ]] && ! awk -v domain="$domain" '
  $1 == "server_name" {
    for (i = 2; i <= NF; i++) {
      gsub(/;/, "", $i)
      if ($i == domain) found = 1
    }
  }
  END { exit found ? 0 : 1 }
' "$panel_conf"; then
  cp "$panel_conf" "$panel_conf.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  sed -i "0,/^[[:space:]]*server_name[[:space:]].*;/{s//    server_name ${domain} _;/}" "$panel_conf"
fi

nginx -t
certbot --nginx \
  --non-interactive \
  --agree-tos \
  --redirect \
  --keep-until-expiring \
  -m "$email" \
  -d "$domain"

nginx -t
systemctl reload nginx.service

printf 'HTTPS enabled for https://%s/admin\n' "$domain"
