#!/usr/bin/env bash
# Install or update the workbench + AI service from this checkout.
#
#   npm ci && npm run build:react      # build frontend/dist first
#   sudo deploy/install.sh             # install code + frontend release, restart
#   sudo deploy/install.sh --enable-schedulers   # at cutover only
#
# nginx routing belongs to the network service (service/network branch); this
# script never changes it. The AI video and GitHub trending schedulers stay off
# until --enable-schedulers, so a pre-cutover install never works on state the
# running monolith still owns. The shared internal token used by the network
# service's LancoAgent is created here when it does not exist yet.
set -euo pipefail
SELF=$(realpath -- "${BASH_SOURCE[0]}")
cd -- "$(dirname -- "$SELF")/.."

RUNTIME=${LANCOAI_WORKBENCH_ROOT:-/opt/lancoai/workbench}
ENV_FILE=${LANCOAI_WORKBENCH_ENV:-/etc/lancoai/workbench.env}
TOKEN_FILE=/etc/lancoai/internal-token
UNIT=lancoai-workbench.service
VENV_PYTHON=${LANCOAI_VENV_PYTHON:-/root/hysteria/.venv-web/bin/python}
STATE_DIR=/root/hysteria/state
GROUP=hy2-xray
LOCK_EXEC=/usr/local/sbin/hy2-lock-exec.py
enable_schedulers=0

for arg in "$@"; do
  case "$arg" in
    --enable-schedulers) enable_schedulers=1 ;;
    *) echo "usage: $0 [--enable-schedulers]" >&2; exit 64 ;;
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

# 1. Python modules (web_api/ai included) and the learning sandbox files: copy
#    the current set, then drop files that left the repo.
install -d -m 0750 -o root -g "$GROUP" "$RUNTIME" "$RUNTIME/panel"
while IFS= read -r -d '' source; do
  relative=${source#server/}
  install -d -m 0750 -o root -g "$GROUP" "$RUNTIME/$(dirname -- "$relative")"
  install -m 0640 -o root -g "$GROUP" "$source" "$RUNTIME/$relative"
done < <(find server -type f \( -name '*.py' -o -path 'server/learning_sandbox/*' \) -print0)
while IFS= read -r -d '' installed; do
  relative=${installed#"$RUNTIME"/}
  [[ -e server/$relative ]] || rm -f -- "$installed"
done < <(find "$RUNTIME" -path "$RUNTIME/panel" -prune -o -type f \( -name '*.py' -o -path "$RUNTIME/learning_sandbox/*" \) -print0)
find "$RUNTIME" -name __pycache__ -type d -prune -exec rm -rf -- {} +

# 2. Frontend: a verified, content-addressed release, then the current pointer.
release_id=$(python3 scripts/hy2_panel_release.py install frontend/dist "$RUNTIME/panel" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["release_id"])')
python3 scripts/hy2_panel_release.py activate "$RUNTIME/panel" "$release_id" >/dev/null
chgrp -R "$GROUP" "$RUNTIME/panel"

# 3. Configuration: created once, never overwritten except the scheduler flag.
install -d -m 0750 -o root -g "$GROUP" "$(dirname -- "$ENV_FILE")" "$(dirname -- "$TOKEN_FILE")"
if [[ ! -e $ENV_FILE ]]; then
  install -m 0640 -o root -g "$GROUP" deploy/workbench.env.example "$ENV_FILE"
fi
if [[ $enable_schedulers -eq 1 ]]; then
  sed -i 's/^WORKBENCH_SCHEDULERS=.*/WORKBENCH_SCHEDULERS=1/' "$ENV_FILE"
  grep -q '^WORKBENCH_SCHEDULERS=1$' "$ENV_FILE" || echo 'WORKBENCH_SCHEDULERS=1' >>"$ENV_FILE"
fi
# The LancoAgent intent endpoint fails closed without this shared token.
if [[ ! -s $TOKEN_FILE ]]; then
  token_tmp=$(mktemp)
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))' >"$token_tmp"
  install -m 0640 -o root -g "$GROUP" "$token_tmp" "$TOKEN_FILE"
  rm -f -- "$token_tmp"
fi

# 4. Service: install the unit, restart, and wait for the loopback probe.
install -m 0644 deploy/$UNIT /etc/systemd/system/$UNIT
systemctl daemon-reload
systemctl enable $UNIT >/dev/null
systemctl restart $UNIT
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8085/healthz >/dev/null 2>&1; then
    echo "workbench service healthy; release $release_id; $(grep -o '^WORKBENCH_SCHEDULERS=.*' "$ENV_FILE")"
    exit 0
  fi
  sleep 1
done
echo 'workbench service did not become healthy; see journalctl -u lancoai-workbench' >&2
exit 1
