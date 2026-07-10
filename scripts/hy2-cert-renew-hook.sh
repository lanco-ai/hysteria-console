#!/usr/bin/env bash
set -euo pipefail

nginx -t
systemctl reload nginx.service
if [[ -x /usr/local/sbin/hy2-health-check.sh ]]; then
  /usr/local/sbin/hy2-health-check.sh
fi
