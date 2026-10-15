#!/usr/bin/env bash
# Install or update the shop service from this checkout.
#
#   npm ci && npm run build:react     # build frontend/dist first
#   sudo deploy/install.sh            # install code + frontend release, restart
#   sudo deploy/install.sh --enable-scheduler   # at cutover only
#
# nginx routing belongs to the network service (service/network branch); this
# script never changes it. The product-source scheduler stays off until
# --enable-scheduler, so a pre-cutover install never refreshes the same state
# as the running monolith.
set -euo pipefail
SELF=$(realpath -- "${BASH_SOURCE[0]}")
cd -- "$(dirname -- "$SELF")/.."

RUNTIME=${LANCOAI_SHOP_ROOT:-/opt/lancoai/shop}
ENV_FILE=${LANCOAI_SHOP_ENV:-/etc/lancoai/shop.env}
UNIT=lancoai-shop.service
VENV_PYTHON=${LANCOAI_VENV_PYTHON:-/root/hysteria/.venv-web/bin/python}
STATE_DIR=/root/hysteria/state/shop
GROUP=hy2-xray
LOCK_EXEC=/usr/local/sbin/hy2-lock-exec.py
enable_scheduler=0

for arg in "$@"; do
  case "$arg" in
    --enable-scheduler) enable_scheduler=1 ;;
    *) echo "usage: $0 [--enable-scheduler]" >&2; exit 64 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo 'install.sh must run as root' >&2
  exit 1
fi
# Serialize with the network service's deploys when its lock helper exists.
if [[ -z ${LANCOAI_DEPLOY_LOCKED:-} && -x $LOCK_EXEC ]]; then
  exec env LANCOAI_DEPLOY_LOCKED=1 /usr/bin/python3 "$LOCK_EXEC" \
    --lock-file /run/hy2-locks/deploy.lock --timeout 30 -- "$SELF" "$@"
fi
for required in frontend/dist/index.html frontend/dist/manifest.json "$VENV_PYTHON"; do
  if [[ ! -e $required ]]; then
    echo "missing $required (build the frontend and install the Python environment first)" >&2
    exit 1
  fi
done
getent group "$GROUP" >/dev/null || { echo "group $GROUP does not exist" >&2; exit 1; }
[[ -d $STATE_DIR ]] || install -d -m 0750 -o root -g "$GROUP" "$STATE_DIR"

# 1. Python modules: copy the current set, then drop modules that left the repo.
install -d -m 0750 -o root -g "$GROUP" "$RUNTIME" "$RUNTIME/web_api" "$RUNTIME/panel"
for source in server/*.py server/web_api/*.py; do
  install -m 0640 -o root -g "$GROUP" "$source" "$RUNTIME/${source#server/}"
done
for installed in "$RUNTIME"/*.py "$RUNTIME"/web_api/*.py; do
  [[ -e $installed ]] || continue
  relative=${installed#"$RUNTIME"/}
  [[ -e server/$relative ]] || rm -f -- "$installed"
done
find "$RUNTIME" -name __pycache__ -type d -prune -exec rm -rf -- {} +

# 2. Frontend: a verified, content-addressed release, then the current pointer.
release_id=$(python3 scripts/hy2_panel_release.py install frontend/dist "$RUNTIME/panel" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["release_id"])')
python3 scripts/hy2_panel_release.py activate "$RUNTIME/panel" "$release_id" >/dev/null
chgrp -R "$GROUP" "$RUNTIME/panel"

# 3. Configuration: created once, never overwritten except the scheduler flag.
if [[ ! -e $ENV_FILE ]]; then
  install -d -m 0750 -o root -g "$GROUP" "$(dirname -- "$ENV_FILE")"
  install -m 0640 -o root -g "$GROUP" deploy/shop.env.example "$ENV_FILE"
fi
if [[ $enable_scheduler -eq 1 ]]; then
  sed -i 's/^SHOP_SCHEDULER=.*/SHOP_SCHEDULER=1/' "$ENV_FILE"
  grep -q '^SHOP_SCHEDULER=1$' "$ENV_FILE" || echo 'SHOP_SCHEDULER=1' >>"$ENV_FILE"
fi

# 4. Service: install the unit, restart, and wait for the loopback probe.
install -m 0644 deploy/$UNIT /etc/systemd/system/$UNIT
systemctl daemon-reload
systemctl enable $UNIT >/dev/null
systemctl restart $UNIT
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8084/healthz >/dev/null 2>&1; then
    echo "shop service healthy; release $release_id; scheduler $(grep -o '^SHOP_SCHEDULER=.*' "$ENV_FILE")"
    exit 0
  fi
  sleep 1
done
echo 'shop service did not become healthy; see journalctl -u lancoai-shop' >&2
exit 1
