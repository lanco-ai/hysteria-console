#!/usr/bin/bash -p
# One-shot installer for hy2 — Hysteria2 + Xray + subscription panel.
# Run as root on a fresh Debian/Ubuntu VPS.
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset CDPATH

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HY_DIR=/root/hysteria
XRAY_ETC=/usr/local/etc/xray
SYSTEMD_DIR=/etc/systemd/system

log()  { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Load .env through the strict, non-executable parser before locks, service
# inspection, package installation, or any other mutable operation.
ENV_FILE="$REPO_DIR/.env"
[[ -f "$ENV_FILE" ]] ||
  die "$ENV_FILE not found. Copy .env.example → .env and fill it in."
if [[ "${HY2_DEPLOY_ENV_LOADED:-0}" != "1" ]]; then
  exec /usr/bin/python3 -I "$REPO_DIR/scripts/hy2-render-template.py" \
    --exec-env-file "$ENV_FILE" /usr/bin/bash -p "$0"
fi
/usr/bin/python3 -I "$REPO_DIR/scripts/hy2-render-template.py" \
  --verify-exec-env-file "$ENV_FILE"

DEPLOY_SUCCEEDED=0
SERVICE_STATE_CAPTURED=0
XRAY_CANDIDATE=""
XRAY_ARCHIVE=""
XRAY_EXTRACT_DIR=""
XRAY_GEOIP_CANDIDATE=""
XRAY_GEOSITE_CANDIDATE=""
XRAY_GEOIP_COMMIT_CANDIDATE=""
XRAY_GEOSITE_COMMIT_CANDIDATE=""
XRAY_INSTALL_REQUIRED=0
HYSTERIA_CANDIDATE=""
HYSTERIA_INSTALL_REQUIRED=0
TUIC_CANDIDATE=""
TUIC_DOWNLOAD=""
TUIC_INSTALL_REQUIRED=0
CERT_CANDIDATE=""
KEY_CANDIDATE=""
OUTER_RECOVERY_HELPER="$REPO_DIR/scripts/hy2-deploy-recovery.py"
DURABLE_RECOVERY_PREPARED=0
DURABLE_RECOVERY_ACTIVE=0
XRAY_LOG_DIR_STATE_CAPTURED=0
XRAY_LOG_DIR_EXISTED=0
XRAY_LOG_DIR_PREVIOUS_UID=""
XRAY_LOG_DIR_PREVIOUS_GID=""
XRAY_LOG_DIR_PREVIOUS_MODE=""
XRAY_DATA_DIR_CREATED=0
XRAY_CONFIG_DIR_CREATED=0
ROLLBACK_ACTIVE=0
ROLLBACK_DIR=""
ROLLBACK_TOTAL_BYTES=0
ROLLBACK_MAX_FILE_BYTES=$((64 * 1024 * 1024))
ROLLBACK_MAX_TOTAL_BYTES=$((256 * 1024 * 1024))
readonly HYSTERIA_PINNED_VERSION=v2.9.3
readonly HYSTERIA_AMD64_SHA256=66dbdb0608f25f3057b433afe975a9fc1af2ca8e512479e294988b3ef363d6c1
readonly HYSTERIA_ARM64_SHA256=938df06c5a8ed001dbc38718b5385b5fcbd721669f1163518ea8e738866865f2
readonly XRAY_PINNED_VERSION=v26.6.27
readonly XRAY_AMD64_BINARY_SHA256=8ef87ac07f95617e094b8e9302ea3e0c2d0edaa7045d57b455fdee28b3c9e41e
readonly XRAY_ARM64_BINARY_SHA256=53ad04b1ddcba6f4ff8834b3db2e9a596456441259cea3e4f03f86cd39e22884
readonly XRAY_AMD64_ARCHIVE_SHA256=b3e5902d06d6282fe53cfa2fc426058b9aeaa429b2c812e20887cd47f26d08bf
readonly XRAY_ARM64_ARCHIVE_SHA256=13a251379bea366c2cf10363ad71e75734193d401f26f518bf0c25e5c8f8c931
readonly XRAY_GEOIP_SHA256=e551b66e9300a98ecc94a5dc8c86a3973bf7033138b0fa61eb0638419ce50057
readonly XRAY_GEOSITE_SHA256=1417d29aa40e07fa3cd92e730e8d81921a78b8e573849ca2a4b8199c7c1d3b2b
readonly TUIC_VERSION=1.0.0
readonly TUIC_AMD64_SHA256=7cd85d8857cef7990ce067d8b48595e6532f0440522529d796d3a8b2f29e7b9f
readonly TUIC_ARM64_SHA256=0403ba2a5f3e463f000b5db897baad9f5d077ef304e0f8d537334b6e4c324f4a
declare -a DEPLOY_MANAGED_UNITS=(
  hy2-deploy-recovery.service
  hy2-https-recovery.service
  nginx.service
  hysteria-porthop.service
  hysteria-tcp-mss.service
  hysteria-auth.service
  hysteria-server.service
  hysteria-subscription.service
  hysteria-traffic-limiter.timer
  hysteria-traffic-limiter.service
  codex-quota-collector.timer
  codex-quota-collector.service
  hy2-backup.timer
  hy2-backup.service
  hy2-health-check.timer
  hy2-health-check.service
  xray.service
  tuic-server.service
  snap.certbot.renew.timer
  fail2ban.service
  systemd-journald.service
)
declare -a CRITICAL_UNITS=(
  hysteria-traffic-limiter.timer
  hysteria-traffic-limiter.service
  hysteria-subscription.service
  hysteria-auth.service
  hysteria-server.service
  xray.service
  tuic-server.service
)
declare -a PREVIOUSLY_ACTIVE_UNITS=()
declare -A PREVIOUS_ENABLE_STATE=()
declare -A PREVIOUS_SYSCTL_VALUES=()
SYSCTL_STATE_CAPTURED=0
declare -a HY2_SYSCTL_KEYS=(
  net.core.rmem_max
  net.core.wmem_max
  net.core.rmem_default
  net.core.wmem_default
  net.core.netdev_max_backlog
  net.ipv4.udp_rmem_min
  net.ipv4.udp_wmem_min
  net.ipv4.tcp_mtu_probing
  net.core.default_qdisc
  net.ipv4.tcp_congestion_control
)
declare -a ROLLBACK_PATHS=()
declare -a ROLLBACK_EXISTED=()
declare -a DURABLE_ARTIFACT_PATHS=()
declare -A DURABLE_ARTIFACT_SET=()

outer_recovery() {
  /usr/bin/python3 -I "$OUTER_RECOVERY_HELPER" "$@"
}

bootstrap_install_atomic() {
  # Recovery infrastructure must exist before prepare so a first deployment
  # is recoverable after a power loss. These four files intentionally form a
  # small, idempotent safety bootstrap outside the application transaction.
  local mode="$1" src="$2" dst="$3" parent staged metadata
  [[ -f "$src" && ! -L "$src" ]] ||
    die "Recovery bootstrap source is missing or unsafe: $src"
  metadata="$(stat -c '%u:%a:%h' "$src")"
  [[ "$metadata" =~ ^0:[0-7]+:1$ ]] ||
    die "Recovery bootstrap source metadata is unsafe: $src"
  (( (8#$(cut -d: -f2 <<<"$metadata") & 8#022) == 0 )) ||
    die "Recovery bootstrap source is writable by an untrusted account: $src"
  parent="$(dirname "$dst")"
  [[ -d "$parent" && ! -L "$parent" ]] ||
    die "Recovery bootstrap parent is missing or unsafe: $parent"
  metadata="$(stat -c '%u:%g:%a' "$parent")"
  [[ "$metadata" =~ ^0:[0-9]+:[0-7]+$ ]] ||
    die "Recovery bootstrap parent is not root-owned: $parent"
  (( (8#${metadata##*:} & 8#022) == 0 )) ||
    die "Recovery bootstrap parent is writable by an untrusted account: $parent"
  staged="$(mktemp "$parent/.hy2-bootstrap.XXXXXX")"
  if ! install -o root -g root -m "$mode" "$src" "$staged"; then
    rm -f -- "$staged" 2>/dev/null || true
    return 1
  fi
  sync -f "$staged"
  if ! mv -Tf -- "$staged" "$dst"; then
    rm -f -- "$staged" 2>/dev/null || true
    return 1
  fi
  sync -f "$parent"
}

add_durable_artifact() {
  local path="$1"
  [[ "$path" == /* && "$path" != */../* && "$path" != */./* ]] ||
    die "Refusing a non-canonical durable artifact path: $path"
  case "$path" in
    *.lock|*.reload.pending|/var/log/*)
      die "Dynamic runtime state cannot enter the durable artifact set: $path"
      ;;
  esac
  if [[ -n "${DURABLE_ARTIFACT_SET[$path]:-}" ]]; then
    die "Durable artifact was registered twice: $path"
  fi
  DURABLE_ARTIFACT_PATHS+=("$path")
  DURABLE_ARTIFACT_SET["$path"]=1
}

is_durable_artifact() {
  [[ -n "${DURABLE_ARTIFACT_SET[$1]:-}" ]]
}

durable_replace_candidate() {
  local candidate="$1" destination="$2"
  [[ "$DURABLE_RECOVERY_ACTIVE" == "1" ]] ||
    die "Durable artifact replacement attempted outside an active transaction"
  is_durable_artifact "$destination" ||
    die "Artifact replacement is outside the frozen durable set: $destination"
  outer_recovery replace --path "$destination" --candidate "$candidate"
}

durable_remove_artifact() {
  local destination="$1"
  [[ "$DURABLE_RECOVERY_ACTIVE" == "1" ]] ||
    die "Durable artifact removal attempted outside an active transaction"
  is_durable_artifact "$destination" ||
    die "Artifact removal is outside the frozen durable set: $destination"
  outer_recovery remove --path "$destination"
}

build_durable_artifact_set() {
  local name path
  (( ${#DURABLE_ARTIFACT_PATHS[@]} == 0 )) ||
    die "Durable artifact set was already built"

  if [[ "$HYSTERIA_INSTALL_REQUIRED" == "1" ]]; then
    add_durable_artifact /usr/local/bin/hysteria
  fi
  if [[ "$XRAY_INSTALL_REQUIRED" == "1" ]]; then
    add_durable_artifact /usr/local/bin/xray
    add_durable_artifact /usr/local/share/xray/geoip.dat
    add_durable_artifact /usr/local/share/xray/geosite.dat
  fi
  if [[ "$TUIC_INSTALL_REQUIRED" == "1" ]]; then
    add_durable_artifact /usr/local/bin/tuic-server
  fi

  for name in \
    api_secret \
    config.yaml \
    auth_backend.py \
    auth_service.py \
    subscription_service.py \
    traffic_limiter.py \
    alerts.py \
    anomaly.py \
    charts.py \
    codex_dashboard.py \
    codex_quota.py \
    cost_calibrator.py \
    cycle.py \
    health.py \
    health_widgets.py \
    http_utils.py \
    incident_console.py \
    online_snapshot.py \
    revocation_queue.py \
    rotation_recovery.py \
    static_access.py \
    state_store.py \
    subscription_profiles.py \
    xray_config.py \
    tuic_config.py \
    tuic_meter.py \
    usage_dashboard.py \
    user_compat.py \
    display.py \
    timeutil.py \
    admin.css \
    admin_poll.js \
    codex_quota.js \
    usage.js; do
    add_durable_artifact "$HY_DIR/$name"
  done
  add_durable_artifact "$HY_DIR/state/https_required"
  if [[ ! -f "$HY_DIR/template.yaml" ]]; then
    add_durable_artifact "$HY_DIR/template.yaml"
  fi
  if [[ ! -f "$HY_DIR/server.crt" || ! -f "$HY_DIR/server.key" ]]; then
    add_durable_artifact "$HY_DIR/server.crt"
    add_durable_artifact "$HY_DIR/server.key"
  fi

  for path in \
    /usr/local/sbin/hysteria-porthop.sh \
    /usr/local/sbin/hysteria-tcp-mss.sh \
    /usr/local/sbin/hysteria-auth-recover.sh \
    /usr/local/sbin/hy2-backup.sh \
    /usr/local/sbin/hy2-backup-git.sh \
    /usr/local/sbin/hy2-restore-check.sh \
    /usr/local/sbin/hy2-lock-exec.py \
    /usr/local/sbin/hy2-enable-https.sh \
    /usr/local/sbin/hy2-deploy-recovery.py \
    /usr/local/sbin/hy2-cert-renew-hook.sh \
    /usr/local/sbin/hy2-health-check.sh \
    /usr/local/share/hy2/hysteria-panel-log.conf \
    /usr/local/share/hy2/hysteria-panel-https.conf \
    /usr/local/share/hy2/hysteria-panel-redirect.conf \
    /usr/local/share/hy2/hy2-cert-renew-hook.sh \
    /etc/logrotate.d/xray \
    /etc/sysctl.d/99-hysteria-udp.conf \
    /etc/modules-load.d/tcp-bbr.conf \
    /etc/nginx/conf.d/hysteria-panel-log.conf \
    /etc/nginx/sites-enabled/hysteria-panel.conf \
    /etc/nginx/sites-enabled/default \
    "$SYSTEMD_DIR/xray.service" \
    "$SYSTEMD_DIR/xray@.service" \
    "$SYSTEMD_DIR/xray.service.d/10-donot_touch_single_conf.conf" \
    "$SYSTEMD_DIR/xray.service.d/10-donot_touch_multi_conf.conf" \
    "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_single_conf.conf" \
    "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_multi_conf.conf" \
    "$SYSTEMD_DIR/xray.service.d/20-hy2-hardening.conf" \
    "$SYSTEMD_DIR/hysteria-server.service" \
    "$SYSTEMD_DIR/hysteria-auth.service" \
    "$SYSTEMD_DIR/hysteria-subscription.service" \
    "$SYSTEMD_DIR/hysteria-traffic-limiter.service" \
    "$SYSTEMD_DIR/hysteria-traffic-limiter.timer" \
    "$SYSTEMD_DIR/codex-quota-collector.service" \
    "$SYSTEMD_DIR/codex-quota-collector.timer" \
    "$SYSTEMD_DIR/hy2-backup.service" \
    "$SYSTEMD_DIR/hy2-backup.timer" \
    "$SYSTEMD_DIR/hysteria-porthop.service" \
    "$SYSTEMD_DIR/hysteria-tcp-mss.service" \
    "$SYSTEMD_DIR/tuic-server.service" \
    "$SYSTEMD_DIR/hy2-health-check.service" \
    "$SYSTEMD_DIR/hy2-health-check.timer" \
    "$SYSTEMD_DIR/hy2-https-recovery.service" \
    "$SYSTEMD_DIR/hy2-deploy-recovery.service" \
    "$SYSTEMD_DIR/hy2-deploy-watchdog.service" \
    /etc/fail2ban/filter.d/tuic-auth.conf \
    /etc/fail2ban/jail.d/tuic-auth.conf \
    /etc/systemd/journald.conf.d/60-hy2-limits.conf; do
    add_durable_artifact "$path"
  done
  if [[ "$HY_ENABLE_HTTPS" != "1" ||
        ! -e /etc/nginx/sites-enabled/hysteria-panel-https.conf ||
        ! -f /etc/nginx/sites-available/hysteria-panel.conf ]]; then
    add_durable_artifact \
      /etc/nginx/sites-available/hysteria-panel.conf
  fi
  if [[ "$HY_ENABLE_HTTPS" == "0" ]]; then
    add_durable_artifact \
      /etc/nginx/sites-enabled/hysteria-panel-https.conf
  fi
}

begin_durable_artifact_snapshot() {
  local path
  local -a snapshot_args=(snapshot)
  build_durable_artifact_set
  for path in "${DURABLE_ARTIFACT_PATHS[@]}"; do
    snapshot_args+=(--path "$path")
  done
  outer_recovery "${snapshot_args[@]}" ||
    die "Could not freeze the durable deployment artifact snapshot."
  DURABLE_RECOVERY_ACTIVE=1
}

was_previously_active() {
  local wanted="$1" active
  for active in "${PREVIOUSLY_ACTIVE_UNITS[@]}"; do
    [[ "$active" == "$wanted" ]] && return 0
  done
  return 1
}

is_deferred_rollback_unit() {
  [[ "$1" == "systemd-journald.service" ]]
}

capture_service_state() {
  local unit state
  for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do
    if systemctl is-active --quiet "$unit"; then
      PREVIOUSLY_ACTIVE_UNITS+=("$unit")
    fi
    state="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    PREVIOUS_ENABLE_STATE["$unit"]="${state:-not-found}"
  done
  SERVICE_STATE_CAPTURED=1
}

capture_sysctl_state() {
  local key value
  for key in "${HY2_SYSCTL_KEYS[@]}"; do
    value="$(sysctl -n "$key")" ||
      die "Could not capture live sysctl state for $key"
    PREVIOUS_SYSCTL_VALUES["$key"]="$value"
  done
  SYSCTL_STATE_CAPTURED=1
}

restore_sysctl_state() {
  local key
  [[ "$SYSCTL_STATE_CAPTURED" == "1" ]] || return 0
  for key in "${HY2_SYSCTL_KEYS[@]}"; do
    sysctl -q -w "$key=${PREVIOUS_SYSCTL_VALUES[$key]}" >/dev/null ||
      warn "Could not restore live sysctl value for $key"
  done
}

begin_rollback_snapshot() {
  local old_umask
  if [[ "${DURABLE_RECOVERY_PREPARED:-0}" == "1" ]]; then
    return 0
  fi
  [[ -z "$ROLLBACK_DIR" ]] ||
    die "Deployment rollback snapshot was initialized twice"
  old_umask="$(umask)"
  umask 077
  ROLLBACK_DIR="$(mktemp -d /root/.hy2-deploy-rollback.XXXXXX)"
  chmod 700 "$ROLLBACK_DIR"
  umask "$old_umask"
}

capture_artifact_snapshot() {
  local path="$1" known index size backup
  # The persistent Python journal is authoritative during real deployments.
  # Keep the legacy in-memory helper available for its isolated regression
  # harnesses, but never duplicate secret-bearing snapshots once prepare has
  # durably recorded the outer transaction.
  if [[ "${DURABLE_RECOVERY_PREPARED:-0}" == "1" ]]; then
    return 0
  fi
  [[ -n "$ROLLBACK_DIR" && -d "$ROLLBACK_DIR" ]] ||
    die "Deployment rollback snapshot is unavailable"
  for known in "${ROLLBACK_PATHS[@]}"; do
    [[ "$known" == "$path" ]] && return 0
  done

  index="${#ROLLBACK_PATHS[@]}"
  if [[ -e "$path" || -L "$path" ]]; then
    if [[ ! -f "$path" && ! -L "$path" ]]; then
      die "Refusing to snapshot unsupported deployment artifact: $path"
    fi
    size="$(stat -c %s -- "$path")" ||
      die "Could not size deployment artifact: $path"
    [[ "$size" =~ ^[0-9]+$ ]] ||
      die "Invalid deployment artifact size for $path"
    (( size <= ROLLBACK_MAX_FILE_BYTES )) ||
      die "Deployment artifact exceeds the 64 MiB rollback limit: $path"
    (( ROLLBACK_TOTAL_BYTES + size <= ROLLBACK_MAX_TOTAL_BYTES )) ||
      die "Deployment rollback snapshot exceeds the 256 MiB total limit"
    backup="$ROLLBACK_DIR/$index"
    cp -a --no-target-directory -- "$path" "$backup" ||
      die "Could not snapshot deployment artifact: $path"
    ROLLBACK_TOTAL_BYTES=$((ROLLBACK_TOTAL_BYTES + size))
    ROLLBACK_PATHS+=("$path")
    ROLLBACK_EXISTED+=("1")
  else
    ROLLBACK_PATHS+=("$path")
    ROLLBACK_EXISTED+=("0")
  fi
}

track_created_artifact_for_rollback() {
  local path="$1" known
  if [[ "${DURABLE_RECOVERY_PREPARED:-0}" == "1" ]]; then
    return 0
  fi
  [[ -n "$ROLLBACK_DIR" && -d "$ROLLBACK_DIR" ]] ||
    die "Deployment rollback snapshot is unavailable"
  if [[ -e "$path" || -L "$path" ]]; then
    return 0
  fi
  for known in "${ROLLBACK_PATHS[@]}"; do
    [[ "$known" == "$path" ]] && return 0
  done
  ROLLBACK_PATHS+=("$path")
  ROLLBACK_EXISTED+=("0")
}

capture_xray_log_directory_state() {
  local uid gid mode hy2_uid hy2_gid
  hy2_uid="$(id -u hy2-xray)"
  hy2_gid="$(getent group hy2-xray | awk -F: '{print $3}')"
  [[ "$hy2_uid" =~ ^[0-9]+$ && "$hy2_gid" =~ ^[0-9]+$ ]] ||
    die "Could not resolve the dedicated Xray account"
  if [[ -e /var/log/xray || -L /var/log/xray ]]; then
    [[ -d /var/log/xray && ! -L /var/log/xray ]] ||
      die "Refusing unsafe Xray log directory"
    IFS=: read -r uid gid mode < <(
      stat -c '%u:%g:%a' /var/log/xray
    )
    if ! {
      [[ "$uid" == "0" && "$gid" == "0" && "$mode" == "755" ]] ||
        [[ ( "$uid" == "0" || "$uid" == "$hy2_uid" ) &&
           "$gid" == "$hy2_gid" && "$mode" == "750" ]]
    }; then
      die "Existing Xray log directory metadata is unsafe"
    fi
    XRAY_LOG_DIR_EXISTED=1
    XRAY_LOG_DIR_PREVIOUS_UID="$uid"
    XRAY_LOG_DIR_PREVIOUS_GID="$gid"
    XRAY_LOG_DIR_PREVIOUS_MODE="$mode"
  else
    XRAY_LOG_DIR_EXISTED=0
  fi
  XRAY_LOG_DIR_STATE_CAPTURED=1
}

restore_xray_log_directory_state() {
  local failed=0
  [[ "$XRAY_LOG_DIR_STATE_CAPTURED" == "1" ]] || return 0
  if [[ "$XRAY_LOG_DIR_EXISTED" == "1" ]]; then
    if [[ -d /var/log/xray && ! -L /var/log/xray ]]; then
      chown "$XRAY_LOG_DIR_PREVIOUS_UID:$XRAY_LOG_DIR_PREVIOUS_GID" \
        /var/log/xray ||
        { warn "Could not restore Xray log directory ownership"; failed=1; }
      chmod "$XRAY_LOG_DIR_PREVIOUS_MODE" /var/log/xray ||
        { warn "Could not restore Xray log directory mode"; failed=1; }
    else
      warn "Could not restore missing or unsafe Xray log directory"
      failed=1
    fi
  elif [[ -d /var/log/xray && ! -L /var/log/xray ]]; then
    rmdir /var/log/xray 2>/dev/null ||
      { warn "Could not remove the newly created Xray log directory"; failed=1; }
  fi
  return "$failed"
}

restore_created_xray_directories() {
  local failed=0
  if [[ "$XRAY_CONFIG_DIR_CREATED" == "1" &&
        -d "$XRAY_ETC" && ! -L "$XRAY_ETC" ]]; then
    rmdir "$XRAY_ETC" 2>/dev/null ||
      { warn "Could not remove the newly created Xray config directory"; failed=1; }
  fi
  if [[ "$XRAY_DATA_DIR_CREATED" == "1" &&
        -d /usr/local/share/xray && ! -L /usr/local/share/xray ]]; then
    rmdir /usr/local/share/xray 2>/dev/null ||
      { warn "Could not remove the newly created Xray data directory"; failed=1; }
  fi
  return "$failed"
}

restore_artifacts_on_failure() {
  local i path backup parent staged failed=0
  for ((i = ${#ROLLBACK_PATHS[@]} - 1; i >= 0; i--)); do
    path="${ROLLBACK_PATHS[$i]}"
    if [[ "${ROLLBACK_EXISTED[$i]}" == "1" ]]; then
      backup="$ROLLBACK_DIR/$i"
      if [[ ! -e "$backup" && ! -L "$backup" ]]; then
        warn "Rollback copy is missing; cannot restore $path"
        failed=1
        continue
      fi
      parent="$(dirname "$path")"
      if ! mkdir -p -- "$parent"; then
        warn "Could not recreate rollback parent for $path"
        failed=1
        continue
      fi
      if ! staged="$(mktemp "$parent/.hy2-rollback.XXXXXX")"; then
        warn "Could not stage rollback for $path"
        failed=1
        continue
      fi
      rm -f -- "$staged"
      if ! cp -a --no-target-directory -- "$backup" "$staged"; then
        warn "Could not copy rollback data for $path"
        rm -f -- "$staged" 2>/dev/null || true
        failed=1
        continue
      fi
      if ! mv -Tf -- "$staged" "$path"; then
        warn "Could not atomically restore $path"
        rm -f -- "$staged" 2>/dev/null || true
        failed=1
      fi
    elif [[ -d "$path" && ! -L "$path" ]]; then
      warn "Refusing to remove unexpected directory created at $path"
      failed=1
    elif ! rm -f -- "$path"; then
      warn "Could not remove newly created deployment artifact: $path"
      failed=1
    fi
  done
  return "$failed"
}

cleanup_rollback_snapshot() {
  if [[ -n "${ROLLBACK_DIR:-}" &&
        "$ROLLBACK_DIR" == /root/.hy2-deploy-rollback.* ]]; then
    rm -rf -- "$ROLLBACK_DIR" 2>/dev/null || true
  fi
  ROLLBACK_DIR=""
}

restore_unit_enable_state() {
  local unit="$1" state="${PREVIOUS_ENABLE_STATE[$1]:-not-found}"
  case "$state" in
    enabled)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      systemctl unmask "$unit" >/dev/null 2>&1 || true
      systemctl enable "$unit" >/dev/null 2>&1 ||
        warn "Could not restore enabled state for $unit"
      ;;
    enabled-runtime)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      systemctl unmask "$unit" >/dev/null 2>&1 || true
      systemctl enable --runtime "$unit" >/dev/null 2>&1 ||
        warn "Could not restore runtime-enabled state for $unit"
      ;;
    masked)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      systemctl unmask "$unit" >/dev/null 2>&1 || true
      systemctl mask "$unit" >/dev/null 2>&1 ||
        warn "Could not restore masked state for $unit"
      ;;
    masked-runtime)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      systemctl unmask "$unit" >/dev/null 2>&1 || true
      systemctl mask --runtime "$unit" >/dev/null 2>&1 ||
        warn "Could not restore runtime-masked state for $unit"
      ;;
    disabled|not-found)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      systemctl unmask "$unit" >/dev/null 2>&1 || true
      ;;
    static|indirect|generated|transient|linked|linked-runtime|alias)
      # The restored unit artifact carries these non-enableable relationships.
      # Running disable here could delete operator-managed dependency links.
      ;;
    *)
      warn "Unknown previous enable state for $unit: $state"
      ;;
  esac
}

restore_services_on_failure() {
  local rc=$? unit rollback_failed=0
  # EXIT traps inherit errexit. Cleanup must remain best-effort all the way
  # through service restoration, even when the filesystem itself is degraded.
  trap - HUP INT TERM
  set +e
  if [[ -n "${XRAY_CANDIDATE:-}" ]]; then
    rm -f -- "$XRAY_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_ARCHIVE:-}" ]]; then
    rm -f -- "$XRAY_ARCHIVE" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_EXTRACT_DIR:-}" ]]; then
    rm -rf -- "$XRAY_EXTRACT_DIR" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_GEOIP_CANDIDATE:-}" ]]; then
    rm -f -- "$XRAY_GEOIP_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_GEOSITE_CANDIDATE:-}" ]]; then
    rm -f -- "$XRAY_GEOSITE_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_GEOIP_COMMIT_CANDIDATE:-}" ]]; then
    rm -f -- "$XRAY_GEOIP_COMMIT_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${XRAY_GEOSITE_COMMIT_CANDIDATE:-}" ]]; then
    rm -f -- "$XRAY_GEOSITE_COMMIT_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${TUIC_CANDIDATE:-}" ]]; then
    rm -f -- "$TUIC_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${HYSTERIA_CANDIDATE:-}" ]]; then
    rm -f -- "$HYSTERIA_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${TUIC_DOWNLOAD:-}" ]]; then
    rm -f -- "$TUIC_DOWNLOAD" 2>/dev/null || true
  fi
  if [[ -n "${CERT_CANDIDATE:-}" ]]; then
    rm -f -- "$CERT_CANDIDATE" 2>/dev/null || true
  fi
  if [[ -n "${KEY_CANDIDATE:-}" ]]; then
    rm -f -- "$KEY_CANDIDATE" 2>/dev/null || true
  fi
  if [[ "$DEPLOY_SUCCEEDED" != "1" &&
        "${DURABLE_RECOVERY_PREPARED:-0}" == "1" ]]; then
    warn "Deployment failed; invoking the durable outer recovery journal."
    if ! outer_recovery recover; then
      rollback_failed=1
      warn "Durable recovery failed closed; the root-only journal remains at /var/lib/hysteria/deploy-recovery/pending"
    fi
  elif [[ "$DEPLOY_SUCCEEDED" != "1" && "$ROLLBACK_ACTIVE" == "1" ]]; then
    warn "Deployment failed; restoring the previous runtime artifacts and service state."
    if [[ "$SERVICE_STATE_CAPTURED" == "1" ]]; then
      for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do
        is_deferred_rollback_unit "$unit" && continue
        systemctl stop "$unit" >/dev/null 2>&1 || true
      done
    fi
    restore_artifacts_on_failure || rollback_failed=1
    restore_created_xray_directories || rollback_failed=1
    restore_xray_log_directory_state || rollback_failed=1
    restore_sysctl_state
    if [[ "$SERVICE_STATE_CAPTURED" == "1" ]]; then
      systemctl daemon-reload >/dev/null 2>&1 || true
      for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do
        restore_unit_enable_state "$unit"
      done
      for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do
        if is_deferred_rollback_unit "$unit" &&
          ! was_previously_active "$unit"; then
          systemctl stop "$unit" >/dev/null 2>&1 || true
        fi
      done
      for unit in "${PREVIOUSLY_ACTIVE_UNITS[@]}"; do
        if is_deferred_rollback_unit "$unit"; then
          systemctl restart "$unit" >/dev/null 2>&1 ||
            warn "Could not reload restored configuration for $unit"
        else
          systemctl start "$unit" >/dev/null 2>&1 ||
            warn "Could not restart previously active $unit"
        fi
      done
    fi
  fi
  if [[ "$rollback_failed" == "1" ]]; then
    if [[ "${DURABLE_RECOVERY_PREPARED:-0}" == "1" ]]; then
      warn "Rollback was incomplete; preserving the durable recovery journal."
    else
      warn "Rollback was incomplete; preserving the root-only recovery snapshot at $ROLLBACK_DIR"
    fi
  else
    cleanup_rollback_snapshot
  fi
  return "$rc"
}
trap restore_services_on_failure EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $EUID -eq 0 ]] || die "Must run as root."
LOCK_EXEC="$REPO_DIR/scripts/hy2-lock-exec.py"
DEPLOY_LOCK=/run/hy2-locks/deploy.lock
HTTPS_ACTIVATION_LOCK=/run/hy2-locks/https-activation.lock
[[ -f "$LOCK_EXEC" && ! -L "$LOCK_EXEC" ]] ||
  die "Hardened lock executor is missing or unsafe."

if [[ -n "${HY2_DEPLOY_LOCK_MARKER:-}" ]]; then
  /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$DEPLOY_LOCK" \
    --marker-env HY2_DEPLOY_LOCK_MARKER \
    --verify ||
    die "Could not verify the inherited deployment lock."
else
  exec /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$DEPLOY_LOCK" \
    --timeout 0 \
    --marker-env HY2_DEPLOY_LOCK_MARKER \
    -- /usr/bin/bash -p "$0"
fi

# Hold HTTPS activation for the whole core deployment. The post-commit HTTPS
# helper verifies and reuses this exact descriptor instead of deadlocking.
if [[ -n "${HY2_HTTPS_LOCK_MARKER:-}" ]]; then
  /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$HTTPS_ACTIVATION_LOCK" \
    --marker-env HY2_HTTPS_LOCK_MARKER \
    --verify ||
    die "Could not verify the inherited HTTPS activation lock."
else
  exec /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$HTTPS_ACTIVATION_LOCK" \
    --timeout 0 \
    --marker-env HY2_HTTPS_LOCK_MARKER \
    -- /usr/bin/bash -p "$0"
fi

# Recover the outer transaction at the first point where both global locks are
# proven to be held. No validation, package operation, or shared-file write is
# allowed to precede this same-boot recovery gate.
outer_recovery recover ||
  die "Pending outer deployment could not be recovered; stopped before mutation."

# A previous HTTPS activation may have been interrupted after changing nginx
# but before committing its durable journal. Recover that transaction while
# both deployment locks are held and before this deploy snapshots or mutates
# any shared artifact. The boot recovery unit covers restarts; this gate also
# covers a same-boot retry after SIGKILL or process failure.
set +e
/usr/bin/bash -p "$REPO_DIR/scripts/hy2-enable-https.sh" --recover-only
https_recovery_status=$?
set -e
case "$https_recovery_status" in
  0) ;;
  2)
    warn "HTTPS files are recovered, but certificate renewal remains degraded."
    ;;
  *)
    die "Pending HTTPS activation could not be recovered; deployment stopped before mutation."
    ;;
esac
unset HY2_DEPLOY_ENV_LOADED

# ---------- 1. Validate parsed deployment environment ----------
REQUIRED=(
  HY_SERVER_HOST HY_API_SECRET HY_OBFS_PASSWORD
  XRAY_REALITY_PRIVATE_KEY XRAY_REALITY_PUBLIC_KEY
  XRAY_REALITY_SHORT_ID
)
for v in "${REQUIRED[@]}"; do
  val="${!v:-}"
  [[ -n "$val" && "$val" != replace_me* && "$val" != your.server* ]] || die "$v is not set in .env"
done
HY_DISPLAY_MULTIPLIER="${HY_DISPLAY_MULTIPLIER:-2.28}"
HY_HYSTERIA_VERSION="${HY_HYSTERIA_VERSION:-$HYSTERIA_PINNED_VERSION}"
HY_XRAY_VERSION="${HY_XRAY_VERSION:-$XRAY_PINNED_VERSION}"
HY_ENABLE_HTTPS="${HY_ENABLE_HTTPS:-1}"
HY_HTTPS_PORT="${HY_HTTPS_PORT:-9444}"

validate_template_value() {
  local name="$1" value="${!1-}"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "$name must be a single-line template value"
  fi
}
for v in \
  HY_API_SECRET \
  HY_OBFS_PASSWORD \
  HY_SERVER_HOST \
  HY_DISPLAY_MULTIPLIER \
  XRAY_REALITY_PRIVATE_KEY \
  XRAY_REALITY_PUBLIC_KEY \
  XRAY_REALITY_SHORT_ID; do
  validate_template_value "$v"
  export "$v"
done
export HY_HYSTERIA_VERSION HY_XRAY_VERSION HY_ENABLE_HTTPS HY_HTTPS_PORT
export HYSTERIA_PINNED_VERSION
export XRAY_PINNED_VERSION

python3 - <<'PY'
import ipaddress
import math
import os
import re


def reject(message):
    raise SystemExit(message)


if os.environ["HY_ENABLE_HTTPS"] not in {"0", "1"}:
    reject("HY_ENABLE_HTTPS must be 0 or 1")

port_text = os.environ["HY_HTTPS_PORT"]
if not port_text.isascii() or not port_text.isdigit():
    reject("HY_HTTPS_PORT must be an integer between 1024 and 65535")
port = int(port_text)
if str(port) != port_text or not 1024 <= port <= 65535:
    reject("HY_HTTPS_PORT must be an integer between 1024 and 65535")
if port in {443, 8081, 8082, 8443, 9443, 10085, 25413}:
    reject("HY_HTTPS_PORT conflicts with a reserved proxy port")

if (
    os.environ["HY_HYSTERIA_VERSION"]
    != os.environ["HYSTERIA_PINNED_VERSION"]
):
    reject("HY_HYSTERIA_VERSION is not in the checksum allowlist")
if os.environ["HY_XRAY_VERSION"] != os.environ["XRAY_PINNED_VERSION"]:
    reject("HY_XRAY_VERSION is not in the checksum allowlist")

host = os.environ["HY_SERVER_HOST"]
if len(host) > 253:
    reject("HY_SERVER_HOST must be a canonical IPv4 address or DNS name")
try:
    address = ipaddress.IPv4Address(host)
except ipaddress.AddressValueError:
    if re.fullmatch(r"[0-9.]+", host):
        reject("HY_SERVER_HOST must be a canonical IPv4 address or DNS name")
    if host != host.lower():
        reject("HY_SERVER_HOST DNS names must use lowercase canonical form")
    labels = host.split(".")
    label_re = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
    if not labels or any(not label_re.fullmatch(label) for label in labels):
        reject("HY_SERVER_HOST must be a canonical IPv4 address or DNS name")
    if os.environ["HY_ENABLE_HTTPS"] == "1" and len(labels) < 2:
        reject("HY_SERVER_HOST must contain at least two DNS labels for HTTPS")
else:
    if str(address) != host:
        reject("HY_SERVER_HOST must use canonical IPv4 notation")

try:
    multiplier = float(os.environ["HY_DISPLAY_MULTIPLIER"])
except ValueError:
    reject("HY_DISPLAY_MULTIPLIER must be a number")
if not math.isfinite(multiplier) or not 0.1 <= multiplier <= 20.0:
    reject("HY_DISPLAY_MULTIPLIER must be between 0.1 and 20.0")

token_re = re.compile(r"[A-Za-z0-9._~-]+")
for name, minimum, maximum in (
    ("HY_API_SECRET", 24, 128),
    ("HY_OBFS_PASSWORD", 16, 128),
):
    value = os.environ[name]
    if not minimum <= len(value) <= maximum or not token_re.fullmatch(value):
        reject(
            f"{name} must be a {minimum}-{maximum} character safe token"
        )

reality_re = re.compile(r"[A-Za-z0-9_-]{43}")
for name in (
    "XRAY_REALITY_PRIVATE_KEY",
    "XRAY_REALITY_PUBLIC_KEY",
):
    if not reality_re.fullmatch(os.environ[name]):
        reject(f"{name} must be a 43-character base64url key")

short_id = os.environ["XRAY_REALITY_SHORT_ID"]
if (
    not re.fullmatch(r"[0-9A-Fa-f]{2,16}", short_id)
    or len(short_id) % 2
):
    reject(
        "XRAY_REALITY_SHORT_ID must be 2-16 hexadecimal characters "
        "with even length"
    )
PY

case "$(uname -m)" in
  x86_64|amd64)
    hysteria_asset=hysteria-linux-amd64
    hysteria_sha256="$HYSTERIA_AMD64_SHA256"
    xray_asset=Xray-linux-64.zip
    xray_archive_sha256="$XRAY_AMD64_ARCHIVE_SHA256"
    xray_binary_sha256="$XRAY_AMD64_BINARY_SHA256"
    tuic_target=x86_64-unknown-linux-gnu
    tuic_sha256="$TUIC_AMD64_SHA256"
    ;;
  aarch64|arm64)
    hysteria_asset=hysteria-linux-arm64
    hysteria_sha256="$HYSTERIA_ARM64_SHA256"
    xray_asset=Xray-linux-arm64-v8a.zip
    xray_archive_sha256="$XRAY_ARM64_ARCHIVE_SHA256"
    xray_binary_sha256="$XRAY_ARM64_BINARY_SHA256"
    tuic_target=aarch64-unknown-linux-gnu
    tuic_sha256="$TUIC_ARM64_SHA256"
    ;;
  *)
    die "Unsupported architecture: $(uname -m)"
    ;;
esac
tuic_asset="tuic-server-${TUIC_VERSION}-${tuic_target}"

CURL_DOWNLOAD=(
  --fail
  --location
  --silent
  --show-error
  --connect-timeout 10
  --max-time 300
  --retry 4
  --retry-delay 2
  --retry-connrefused
)

# This product owns one Xray runtime and does not support operator-created
# xray@ instances. Refuse before mutation instead of silently leaving an
# instance running against binaries or geodata being replaced underneath it.
mapfile -t unsupported_xray_instances < <(
  {
    systemctl list-unit-files --no-legend --plain 'xray@*.service' \
      2>/dev/null || true
    systemctl list-units --all --type=service --no-legend --plain \
      'xray@*.service' 2>/dev/null || true
  } |
    awk '$1 ~ /^xray@.+\.service$/ {print $1}' |
    sort -u
)
if (( ${#unsupported_xray_instances[@]} > 0 )); then
  printf '[x] Unsupported Xray instance units are configured:\n' >&2
  printf '    %s\n' "${unsupported_xray_instances[@]}" >&2
  die "Disable and remove Xray instance units before deploying this single-instance product."
fi

# Install the boot-time recovery path only after all deployment inputs and
# unsupported-instance checks pass, but before prepare creates any durable
# transaction state. These files are also included in the later frozen
# artifact set so every in-transaction rewrite is generation-checked.
bootstrap_install_atomic 755 \
  "$REPO_DIR/scripts/hy2-lock-exec.py" \
  /usr/local/sbin/hy2-lock-exec.py
bootstrap_install_atomic 755 \
  "$REPO_DIR/scripts/hy2-deploy-recovery.py" \
  /usr/local/sbin/hy2-deploy-recovery.py
bootstrap_install_atomic 644 \
  "$REPO_DIR/systemd/hy2-deploy-recovery.service" \
  "$SYSTEMD_DIR/hy2-deploy-recovery.service"
bootstrap_install_atomic 644 \
  "$REPO_DIR/systemd/hy2-deploy-watchdog.service" \
  "$SYSTEMD_DIR/hy2-deploy-watchdog.service"
# systemd requires every non-optional ReadWritePaths= parent to exist before
# it can construct the watchdog's mount namespace on a fresh host.
install -d -o root -g root -m 700 /var/lib/hysteria
# Treat the dedicated log directory as bootstrap infrastructure. Xray's test
# mode opens its configured log files, so a later rollback must restore this
# directory's metadata instead of trying to remove a newly non-empty path.
if [[ ! -e /var/log/xray && ! -L /var/log/xray ]]; then
  install -d -o root -g root -m 755 /var/log/xray
fi
systemctl daemon-reload
systemctl enable hy2-deploy-recovery.service

# Arm the same-boot watchdog before creating the manifest. It cannot execute
# recovery while this process owns the deploy lock, and pre-arming closes the
# otherwise uncatchable SIGKILL window between prepare and watchdog dispatch.
systemctl --no-block start hy2-deploy-watchdog.service ||
  die "Could not dispatch the same-boot deployment recovery watchdog."
watchdog_ready=0
for _watchdog_attempt in {1..50}; do
  if ! watchdog_state="$(
    systemctl show --property=ActiveState --value \
      hy2-deploy-watchdog.service
  )"; then
    die "Could not inspect the same-boot deployment recovery watchdog."
  fi
  case "$watchdog_state" in
    activating|active)
      watchdog_ready=1
      break
      ;;
    inactive)
      # --no-block may return before the queued start job enters activating.
      sleep 0.1
      ;;
    failed)
      die "Same-boot deployment recovery watchdog failed to arm."
      ;;
    *)
      die "Same-boot deployment recovery watchdog entered an unsafe state: $watchdog_state"
      ;;
  esac
done
[[ "$watchdog_ready" == "1" ]] ||
  die "Same-boot deployment recovery watchdog did not enter its waiting state."

# Persist runtime metadata before apt can start, enable, or otherwise alter a
# managed service. Setting the shell guard first closes the signal window
# between prepare and the EXIT trap's durable recovery branch.
prepare_args=(prepare)
for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do
  prepare_args+=(--unit "$unit")
done
for key in "${HY2_SYSCTL_KEYS[@]}"; do
  prepare_args+=(--sysctl-key "$key")
done
prepare_args+=(--log-dir /var/log/xray)
DURABLE_RECOVERY_PREPARED=1
outer_recovery "${prepare_args[@]}" ||
  die "Could not prepare the durable outer deployment transaction."

# Capture the pre-deploy state before package installation or artifact
# replacement can affect a service. The EXIT trap restores both activity and
# enable/mask state if a later step fails.
capture_service_state
begin_rollback_snapshot
# These binary-owned artifacts are captured before staging. Mutable users,
# ledgers, and proxy configs are captured later under quiescence.
for artifact in \
  /usr/local/bin/hysteria \
  /usr/local/bin/xray \
  /usr/local/bin/tuic-server \
  /usr/local/share/xray/geoip.dat \
  /usr/local/share/xray/geosite.dat \
  "$SYSTEMD_DIR/xray.service" \
  "$SYSTEMD_DIR/xray@.service" \
  "$SYSTEMD_DIR/xray.service.d/10-donot_touch_single_conf.conf" \
  "$SYSTEMD_DIR/xray.service.d/10-donot_touch_multi_conf.conf" \
  "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_single_conf.conf" \
  "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_multi_conf.conf"; do
  capture_artifact_snapshot "$artifact"
done
ROLLBACK_ACTIVE=1

# ---------- 2. OS packages ----------
log "Installing OS packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y curl openssl iptables nftables ca-certificates python3 python3-yaml nginx qrencode logrotate fail2ban >/dev/null

# ---------- 3. Install/upgrade hysteria binary ----------
installed_hysteria_sha256=""
installed_hysteria_metadata=""
if [[ -f /usr/local/bin/hysteria && ! -L /usr/local/bin/hysteria ]]; then
  installed_hysteria_metadata="$(
    stat -c '%u:%g:%a:%h' /usr/local/bin/hysteria 2>/dev/null || true
  )"
fi
if [[ "$installed_hysteria_metadata" == "0:0:755:1" ]]; then
  installed_hysteria_sha256="$(
    sha256sum /usr/local/bin/hysteria | awk '{print $1}'
  )"
fi
if [[ "$installed_hysteria_sha256" != "$hysteria_sha256" ]]; then
  log "Staging checksum-pinned Hysteria $HY_HYSTERIA_VERSION..."
  tmpdir="$(mktemp -d)"
  hysteria_base="https://github.com/apernet/hysteria/releases/download/app%2F${HY_HYSTERIA_VERSION}"
  curl "${CURL_DOWNLOAD[@]}" \
    "$hysteria_base/$hysteria_asset" -o "$tmpdir/$hysteria_asset"
  printf '%s  %s\n' "$hysteria_sha256" \
    "$tmpdir/$hysteria_asset" | sha256sum -c -
  HYSTERIA_CANDIDATE="$(mktemp /usr/local/bin/.hysteria.deploy.XXXXXX)"
  install -m 755 "$tmpdir/$hysteria_asset" "$HYSTERIA_CANDIDATE"
  chown root:root "$HYSTERIA_CANDIDATE"
  [[ "$(stat -c '%u:%g:%a:%h' "$HYSTERIA_CANDIDATE")" == "0:0:755:1" ]] ||
    die "Staged Hysteria binary metadata is invalid"
  printf '%s  %s\n' "$hysteria_sha256" "$HYSTERIA_CANDIDATE" |
    sha256sum -c -
  sync -f "$HYSTERIA_CANDIDATE"
  HYSTERIA_INSTALL_REQUIRED=1
  rm -rf "$tmpdir"
else
  log "Hysteria already at verified $HY_HYSTERIA_VERSION"
fi

# ---------- 4. Install/upgrade xray binary ----------
# Xray is installed directly from a repository-pinned release archive. No
# upstream installer is executed as root, and no package step may start Xray
# before this deploy has rendered and validated its complete configuration.
installed_xray_sha256=""
installed_xray_metadata=""
installed_geoip_sha256=""
installed_geoip_metadata=""
installed_geosite_sha256=""
installed_geosite_metadata=""
installed_xray_verified=0
if [[ -f /usr/local/bin/xray && ! -L /usr/local/bin/xray ]]; then
  installed_xray_metadata="$(
    stat -c '%u:%g:%a:%h' /usr/local/bin/xray 2>/dev/null || true
  )"
fi
if [[ "$installed_xray_metadata" == "0:0:755:1" ]]; then
  installed_xray_sha256="$(
    sha256sum /usr/local/bin/xray | awk '{print $1}'
  )"
fi
if [[ -f /usr/local/share/xray/geoip.dat &&
      ! -L /usr/local/share/xray/geoip.dat ]]; then
  installed_geoip_metadata="$(
    stat -c '%u:%g:%a:%h' /usr/local/share/xray/geoip.dat \
      2>/dev/null || true
  )"
fi
if [[ "$installed_geoip_metadata" == "0:0:644:1" ]]; then
  installed_geoip_sha256="$(
    sha256sum /usr/local/share/xray/geoip.dat | awk '{print $1}'
  )"
fi
if [[ -f /usr/local/share/xray/geosite.dat &&
      ! -L /usr/local/share/xray/geosite.dat ]]; then
  installed_geosite_metadata="$(
    stat -c '%u:%g:%a:%h' /usr/local/share/xray/geosite.dat \
      2>/dev/null || true
  )"
fi
if [[ "$installed_geosite_metadata" == "0:0:644:1" ]]; then
  installed_geosite_sha256="$(
    sha256sum /usr/local/share/xray/geosite.dat | awk '{print $1}'
  )"
fi
if [[ "$installed_xray_sha256" == "$xray_binary_sha256" &&
      "$installed_geoip_sha256" == "$XRAY_GEOIP_SHA256" &&
      "$installed_geosite_sha256" == "$XRAY_GEOSITE_SHA256" ]]; then
  installed_xray_verified=1
fi
if [[ "$installed_xray_verified" != "1" ]]; then
  log "Staging checksum-pinned Xray $HY_XRAY_VERSION..."
  XRAY_ARCHIVE="$(mktemp --suffix=.zip)"
  XRAY_EXTRACT_DIR="$(mktemp -d)"
  curl "${CURL_DOWNLOAD[@]}" \
    "https://github.com/XTLS/Xray-core/releases/download/${HY_XRAY_VERSION}/${xray_asset}" \
    -o "$XRAY_ARCHIVE"
  printf '%s  %s\n' "$xray_archive_sha256" "$XRAY_ARCHIVE" |
    sha256sum -c -
  /usr/bin/python3 -I "$REPO_DIR/scripts/hy2-extract-xray.py" \
    --archive "$XRAY_ARCHIVE" \
    --output-dir "$XRAY_EXTRACT_DIR" \
    --archive-sha256 "$xray_archive_sha256" \
    --xray-sha256 "$xray_binary_sha256" \
    --geoip-sha256 "$XRAY_GEOIP_SHA256" \
    --geosite-sha256 "$XRAY_GEOSITE_SHA256"
  printf '%s  %s\n' "$xray_binary_sha256" "$XRAY_EXTRACT_DIR/xray" |
    sha256sum -c -
  printf '%s  %s\n' "$XRAY_GEOIP_SHA256" "$XRAY_EXTRACT_DIR/geoip.dat" |
    sha256sum -c -
  printf '%s  %s\n' "$XRAY_GEOSITE_SHA256" "$XRAY_EXTRACT_DIR/geosite.dat" |
    sha256sum -c -

  XRAY_CANDIDATE="$(mktemp /usr/local/bin/.xray.deploy.XXXXXX)"
  install -m 755 "$XRAY_EXTRACT_DIR/xray" "$XRAY_CANDIDATE"
  chown root:root "$XRAY_CANDIDATE"
  [[ "$(stat -c '%u:%g:%a:%h' "$XRAY_CANDIDATE")" == "0:0:755:1" ]] ||
    die "Staged Xray binary metadata is invalid"
  printf '%s  %s\n' "$xray_binary_sha256" "$XRAY_CANDIDATE" |
    sha256sum -c -
  sync -f "$XRAY_CANDIDATE"

  XRAY_GEOIP_CANDIDATE="$(
    mktemp /usr/local/share/.xray-geoip.dat.deploy.XXXXXX
  )"
  install -m 644 "$XRAY_EXTRACT_DIR/geoip.dat" "$XRAY_GEOIP_CANDIDATE"
  chown root:root "$XRAY_GEOIP_CANDIDATE"
  [[ "$(stat -c '%u:%g:%a:%h' "$XRAY_GEOIP_CANDIDATE")" == "0:0:644:1" ]] ||
    die "Staged Xray geoip.dat metadata is invalid"
  printf '%s  %s\n' "$XRAY_GEOIP_SHA256" "$XRAY_GEOIP_CANDIDATE" |
    sha256sum -c -
  sync -f "$XRAY_GEOIP_CANDIDATE"

  XRAY_GEOSITE_CANDIDATE="$(
    mktemp /usr/local/share/.xray-geosite.dat.deploy.XXXXXX
  )"
  install -m 644 "$XRAY_EXTRACT_DIR/geosite.dat" \
    "$XRAY_GEOSITE_CANDIDATE"
  chown root:root "$XRAY_GEOSITE_CANDIDATE"
  [[ "$(stat -c '%u:%g:%a:%h' "$XRAY_GEOSITE_CANDIDATE")" == "0:0:644:1" ]] ||
    die "Staged Xray geosite.dat metadata is invalid"
  printf '%s  %s\n' "$XRAY_GEOSITE_SHA256" "$XRAY_GEOSITE_CANDIDATE" |
    sha256sum -c -
  sync -f "$XRAY_GEOSITE_CANDIDATE"

  rm -rf -- "$XRAY_EXTRACT_DIR"
  XRAY_EXTRACT_DIR=""
  rm -f -- "$XRAY_ARCHIVE"
  XRAY_ARCHIVE=""
  XRAY_INSTALL_REQUIRED=1
else
  log "Xray binary and geodata already at verified $HY_XRAY_VERSION"
fi

# ---------- 4b. Install TUIC server binary ----------
installed_tuic_sha256=""
installed_tuic_metadata=""
if [[ -f /usr/local/bin/tuic-server && ! -L /usr/local/bin/tuic-server ]]; then
  installed_tuic_metadata="$(
    stat -c '%u:%g:%a:%h' /usr/local/bin/tuic-server 2>/dev/null || true
  )"
fi
if [[ "$installed_tuic_metadata" == "0:0:755:1" ]]; then
  installed_tuic_sha256="$(
    sha256sum /usr/local/bin/tuic-server | awk '{print $1}'
  )"
fi
if [[ "$installed_tuic_sha256" != "$tuic_sha256" ]]; then
  log "Staging checksum-pinned TUIC server $TUIC_VERSION for $tuic_target..."
  TUIC_DOWNLOAD="$(mktemp)"
  curl "${CURL_DOWNLOAD[@]}" \
    "https://github.com/tuic-protocol/tuic/releases/download/tuic-server-${TUIC_VERSION}/${tuic_asset}" \
    -o "$TUIC_DOWNLOAD"
  printf '%s  %s\n' "$tuic_sha256" "$TUIC_DOWNLOAD" | sha256sum -c -
  TUIC_CANDIDATE="$(mktemp /usr/local/bin/.tuic-server.deploy.XXXXXX)"
  install -m 755 "$TUIC_DOWNLOAD" "$TUIC_CANDIDATE"
  chown root:root "$TUIC_CANDIDATE"
  [[ "$(stat -c '%u:%g:%a:%h' "$TUIC_CANDIDATE")" == "0:0:755:1" ]] ||
    die "Staged TUIC binary metadata is invalid"
  printf '%s  %s\n' "$tuic_sha256" "$TUIC_CANDIDATE" |
    sha256sum -c -
  sync -f "$TUIC_CANDIDATE"
  TUIC_INSTALL_REQUIRED=1
  rm -f -- "$TUIC_DOWNLOAD"
  TUIC_DOWNLOAD=""
else
  log "TUIC server $TUIC_VERSION checksum already matches for $tuic_target"
fi

# ---------- 5. Render templates ----------
render() {
  # render <src_template> <dest>
  local src="$1" dst="$2" parent staged mode
  if [[ "$DURABLE_RECOVERY_ACTIVE" == "1" ]] &&
    is_durable_artifact "$dst"; then
    parent="$(dirname "$dst")"
    mkdir -p -- "$parent"
    staged="$(mktemp "$parent/.hy2-render.XXXXXX")"
    case "$dst" in
      "$HY_DIR"/*.py) mode=700 ;;
      /etc/nginx/*) mode=644 ;;
      *) mode=600 ;;
    esac
    if ! python3 "$REPO_DIR/scripts/hy2-render-template.py" \
      "$src" "$staged"; then
      rm -f -- "$staged" 2>/dev/null || true
      return 1
    fi
    chmod "$mode" "$staged"
    chown root:root "$staged"
    durable_replace_candidate "$staged" "$dst"
    return
  fi
  python3 "$REPO_DIR/scripts/hy2-render-template.py" "$src" "$dst"
}

install_atomic() {
  # install_atomic <mode> <source> <destination>
  local mode="$1" src="$2" dst="$3" parent staged
  parent="$(dirname "$dst")"
  mkdir -p -- "$parent"
  staged="$(mktemp "$parent/.hy2-install.XXXXXX")"
  if ! install -m "$mode" "$src" "$staged"; then
    rm -f -- "$staged" || true
    return 1
  fi
  if ! durable_replace_candidate "$staged" "$dst"; then
    rm -f -- "$staged" || true
    return 1
  fi
}

write_atomic_from_stdin() {
  # write_atomic_from_stdin <mode> <destination>
  local mode="$1" dst="$2" parent staged
  parent="$(dirname "$dst")"
  mkdir -p -- "$parent"
  staged="$(mktemp "$parent/.hy2-write.XXXXXX")"
  if ! cat > "$staged"; then
    rm -f -- "$staged" || true
    return 1
  fi
  chmod "$mode" "$staged"
  if ! durable_replace_candidate "$staged" "$dst"; then
    rm -f -- "$staged" || true
    return 1
  fi
}

symlink_atomic() {
  # symlink_atomic <target> <link>
  local target="$1" link="$2" parent staged
  parent="$(dirname "$link")"
  mkdir -p -- "$parent"
  staged="$(mktemp "$parent/.hy2-link.XXXXXX")"
  rm -f -- "$staged"
  if ! ln -s -- "$target" "$staged"; then
    rm -f -- "$staged" || true
    return 1
  fi
  if ! durable_replace_candidate "$staged" "$link"; then
    rm -f -- "$staged" || true
    return 1
  fi
}

wait_for_stable_readiness() {
  # wait_for_stable_readiness [required_streak] [max_attempts] [delay_seconds]
  # A full observation includes every required unit, auth's deep dependency
  # probe, and the panel's state-aware health endpoint. Any failure resets the
  # streak.
  local required_streak="${1:-3}"
  local max_attempts="${2:-15}"
  local delay_seconds="${3:-1}"
  local streak=0 attempt unit all_ready
  [[ "$required_streak" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$delay_seconds" =~ ^[0-9]+$ ]] || return 2

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    all_ready=1
    for unit in "${required_active_units[@]}"; do
      if ! systemctl is-active --quiet "$unit"; then
        all_ready=0
        break
      fi
    done
    if (( all_ready )) &&
      ! curl -fsS --noproxy '*' --max-time 3 \
        http://127.0.0.1:8082/readyz >/dev/null; then
      all_ready=0
    fi
    if (( all_ready )) &&
      ! curl -fsS --noproxy '*' --max-time 3 \
        http://127.0.0.1:8081/healthz >/dev/null; then
      all_ready=0
    fi

    if (( all_ready )); then
      streak=$((streak + 1))
      if (( streak >= required_streak )); then
        return 0
      fi
    else
      streak=0
    fi
    if (( attempt < max_attempts && delay_seconds > 0 )); then
      sleep "$delay_seconds"
    fi
  done
  return 1
}

require_unit_quiescent() {
  # systemctl may fail with no stdout when D-Bus is unavailable.  Treat only
  # the documented, non-active state/exit-status combinations as authoritative;
  # an empty or otherwise ambiguous observation must stop the deployment.
  local unit="$1" unit_state unit_state_rc
  if unit_state="$(LC_ALL=C systemctl is-active "$unit" 2>/dev/null)"; then
    unit_state_rc=0
  else
    unit_state_rc=$?
  fi
  case "$unit_state_rc:$unit_state" in
    3:inactive|3:failed|4:unknown)
      return 0
      ;;
    *)
      die "Could not authoritatively quiesce $unit (systemctl rc: $unit_state_rc, state: ${unit_state:-<empty>})"
      ;;
  esac
}

# Quiesce every critical reader/writer only after package and binary
# installation has succeeded. Stopping the timer alone is insufficient when
# its oneshot is already active. Never overwrite runtime code while a writer
# remains active or is still transitioning.
for unit in "${CRITICAL_UNITS[@]}"; do
  systemctl stop "$unit" 2>/dev/null || true
done
for unit in "${CRITICAL_UNITS[@]}"; do
  require_unit_quiescent "$unit"
done

# Freeze the one complete static allowlist only after package installation has
# finished and every dynamic writer is stopped. This is the last operation
# before the first application artifact commit.
begin_durable_artifact_snapshot

# Commit only fully verified candidates after every critical runtime is
# quiescent. Each rename is same-filesystem and atomic; the outer rollback
# snapshot covers the complete binary/geodata set if any later step fails.
if [[ "$HYSTERIA_INSTALL_REQUIRED" == "1" ]]; then
  durable_replace_candidate \
    "$HYSTERIA_CANDIDATE" /usr/local/bin/hysteria
  HYSTERIA_CANDIDATE=""
  sync -f /usr/local/bin
fi
if [[ "$XRAY_INSTALL_REQUIRED" == "1" ]]; then
  if [[ -e /usr/local/share/xray || -L /usr/local/share/xray ]]; then
    [[ -d /usr/local/share/xray && ! -L /usr/local/share/xray &&
       "$(stat -c '%u:%g:%a' /usr/local/share/xray)" == "0:0:755" ]] ||
      die "Existing Xray data directory metadata is unsafe"
  else
    install -d -o root -g root -m 755 /usr/local/share/xray
    XRAY_DATA_DIR_CREATED=1
  fi
  XRAY_GEOIP_COMMIT_CANDIDATE="$(
    mktemp /usr/local/share/xray/.geoip.dat.deploy.XXXXXX
  )"
  XRAY_GEOSITE_COMMIT_CANDIDATE="$(
    mktemp /usr/local/share/xray/.geosite.dat.deploy.XXXXXX
  )"
  install -o root -g root -m 644 \
    "$XRAY_GEOIP_CANDIDATE" "$XRAY_GEOIP_COMMIT_CANDIDATE"
  install -o root -g root -m 644 \
    "$XRAY_GEOSITE_CANDIDATE" "$XRAY_GEOSITE_COMMIT_CANDIDATE"
  rm -f -- "$XRAY_GEOIP_CANDIDATE" "$XRAY_GEOSITE_CANDIDATE"
  XRAY_GEOIP_CANDIDATE="$XRAY_GEOIP_COMMIT_CANDIDATE"
  XRAY_GEOSITE_CANDIDATE="$XRAY_GEOSITE_COMMIT_CANDIDATE"
  XRAY_GEOIP_COMMIT_CANDIDATE=""
  XRAY_GEOSITE_COMMIT_CANDIDATE=""
  durable_replace_candidate "$XRAY_CANDIDATE" /usr/local/bin/xray
  XRAY_CANDIDATE=""
  durable_replace_candidate \
    "$XRAY_GEOIP_CANDIDATE" /usr/local/share/xray/geoip.dat
  XRAY_GEOIP_CANDIDATE=""
  durable_replace_candidate "$XRAY_GEOSITE_CANDIDATE" \
    /usr/local/share/xray/geosite.dat
  XRAY_GEOSITE_CANDIDATE=""
  sync -f /usr/local/bin
  sync -f /usr/local/share/xray
fi
if [[ "$TUIC_INSTALL_REQUIRED" == "1" ]]; then
  durable_replace_candidate \
    "$TUIC_CANDIDATE" /usr/local/bin/tuic-server
  TUIC_CANDIDATE=""
  sync -f /usr/local/bin
fi

# Snapshot mutable application state only after every critical writer has
# stopped. This preserves the latest live user/config changes, while the fixed
# allowlist and byte caps keep secret-bearing rollback storage bounded.
for artifact in \
  "$HY_DIR/api_secret" \
  "$HY_DIR/config.yaml" \
  "$HY_DIR/auth_backend.py" \
  "$HY_DIR/auth_service.py" \
  "$HY_DIR/subscription_service.py" \
  "$HY_DIR/traffic_limiter.py" \
  "$HY_DIR/alerts.py" \
  "$HY_DIR/anomaly.py" \
  "$HY_DIR/charts.py" \
  "$HY_DIR/codex_dashboard.py" \
  "$HY_DIR/codex_quota.py" \
  "$HY_DIR/cost_calibrator.py" \
  "$HY_DIR/cycle.py" \
  "$HY_DIR/health.py" \
  "$HY_DIR/health_widgets.py" \
  "$HY_DIR/http_utils.py" \
  "$HY_DIR/incident_console.py" \
  "$HY_DIR/online_snapshot.py" \
  "$HY_DIR/revocation_queue.py" \
  "$HY_DIR/rotation_recovery.py" \
  "$HY_DIR/static_access.py" \
  "$HY_DIR/state_store.py" \
  "$HY_DIR/subscription_profiles.py" \
  "$HY_DIR/xray_config.py" \
  "$HY_DIR/tuic_config.py" \
  "$HY_DIR/tuic_meter.py" \
  "$HY_DIR/usage_dashboard.py" \
  "$HY_DIR/user_compat.py" \
  "$HY_DIR/display.py" \
  "$HY_DIR/timeutil.py" \
  "$HY_DIR/admin.css" \
  "$HY_DIR/admin_poll.js" \
  "$HY_DIR/codex_quota.js" \
  "$HY_DIR/usage.js" \
  "$HY_DIR/template.yaml" \
  "$HY_DIR/users.json" \
  "$HY_DIR/subscription_meta.json" \
  "$HY_DIR/subscription_meta.json.lock" \
  "$HY_DIR/admin_initial_password.txt" \
  "$HY_DIR/state/usage.json" \
  "$HY_DIR/state/usage_daily.json" \
  "$HY_DIR/state/auto_reset_state.json" \
  "$HY_DIR/state/https_required" \
  "$HY_DIR/state/usage.lock" \
  "$HY_DIR/state/tuic_locked_user.json" \
  "$HY_DIR/state/tuic_locked_user.json.lock" \
  "$HY_DIR/tuic.json" \
  "$HY_DIR/tuic.json.lock" \
  "$HY_DIR/tuic.json.reload.pending" \
  "$HY_DIR/server.crt" \
  "$HY_DIR/server.key" \
  "$XRAY_ETC/config.json" \
  "$XRAY_ETC/config.json.lock" \
  "$XRAY_ETC/config.json.reload.pending" \
  /usr/local/sbin/hysteria-porthop.sh \
  /usr/local/sbin/hysteria-tcp-mss.sh \
  /usr/local/sbin/hysteria-auth-recover.sh \
  /usr/local/sbin/hy2-backup.sh \
  /usr/local/sbin/hy2-backup-git.sh \
  /usr/local/sbin/hy2-restore-check.sh \
  /usr/local/sbin/hy2-lock-exec.py \
  /usr/local/sbin/hy2-enable-https.sh \
  /usr/local/sbin/hy2-cert-renew-hook.sh \
  /usr/local/sbin/hy2-health-check.sh \
  /usr/local/share/hy2/hysteria-panel-log.conf \
  /usr/local/share/hy2/hysteria-panel-https.conf \
  /usr/local/share/hy2/hysteria-panel-redirect.conf \
  /usr/local/share/hy2/hy2-cert-renew-hook.sh \
  /etc/logrotate.d/xray \
  /etc/sysctl.d/99-hysteria-udp.conf \
  /etc/modules-load.d/tcp-bbr.conf \
  /etc/nginx/conf.d/hysteria-panel-log.conf \
  /etc/nginx/sites-available/hysteria-panel.conf \
  /etc/nginx/sites-available/hysteria-panel-https.conf \
  /etc/nginx/sites-enabled/hysteria-panel.conf \
  /etc/nginx/sites-enabled/hysteria-panel-https.conf \
  /etc/nginx/sites-enabled/default \
  /etc/letsencrypt/renewal-hooks/deploy/hy2-cert-renew-hook.sh \
  "$SYSTEMD_DIR/hysteria-server.service" \
  "$SYSTEMD_DIR/hysteria-auth.service" \
  "$SYSTEMD_DIR/hysteria-subscription.service" \
  "$SYSTEMD_DIR/hysteria-traffic-limiter.service" \
  "$SYSTEMD_DIR/hysteria-traffic-limiter.timer" \
  "$SYSTEMD_DIR/codex-quota-collector.service" \
  "$SYSTEMD_DIR/codex-quota-collector.timer" \
  "$SYSTEMD_DIR/hy2-backup.service" \
  "$SYSTEMD_DIR/hy2-backup.timer" \
  "$SYSTEMD_DIR/hysteria-porthop.service" \
  "$SYSTEMD_DIR/hysteria-tcp-mss.service" \
  "$SYSTEMD_DIR/tuic-server.service" \
  "$SYSTEMD_DIR/hy2-health-check.service" \
  "$SYSTEMD_DIR/hy2-health-check.timer" \
  "$SYSTEMD_DIR/hy2-https-recovery.service" \
  "$SYSTEMD_DIR/xray.service.d/20-hy2-hardening.conf" \
  /etc/fail2ban/filter.d/tuic-auth.conf \
  /etc/fail2ban/jail.d/tuic-auth.conf \
  /etc/systemd/journald.conf.d/60-hy2-limits.conf; do
  capture_artifact_snapshot "$artifact"
done

install -d -m 755 "$HY_DIR" "$HY_DIR/state"
if ! getent group hy2-xray >/dev/null; then
  groupadd --system hy2-xray
fi
if ! id -u hy2-xray >/dev/null 2>&1; then
  useradd --system --gid hy2-xray --home-dir /nonexistent --shell /usr/sbin/nologin hy2-xray
fi
hy2_xray_gid="$(getent group hy2-xray | awk -F: '{print $3}')"
[[ "$hy2_xray_gid" =~ ^[0-9]+$ ]] ||
  die "Could not resolve the dedicated Xray group"
if [[ -e "$XRAY_ETC" || -L "$XRAY_ETC" ]]; then
  [[ -d "$XRAY_ETC" && ! -L "$XRAY_ETC" ]] ||
    die "Refusing unsafe Xray config directory"
  xray_config_dir_metadata="$(stat -c '%u:%g:%a' "$XRAY_ETC")"
  [[ "$xray_config_dir_metadata" == "0:0:755" ||
     "$xray_config_dir_metadata" == "0:${hy2_xray_gid}:750" ]] ||
    die "Existing Xray config directory metadata is unsafe"
else
  install -d -o root -g hy2-xray -m 750 "$XRAY_ETC"
  XRAY_CONFIG_DIR_CREATED=1
fi
capture_xray_log_directory_state
track_created_artifact_for_rollback /var/log/xray/hy2-access.log
track_created_artifact_for_rollback /var/log/xray/hy2-error.log
install -d -o root -g hy2-xray -m 750 /var/log/xray
chown root:hy2-xray /var/log/xray
for xray_log in hy2-access.log hy2-error.log; do
  xray_log_path="/var/log/xray/$xray_log"
  if [[ -e "$xray_log_path" || -L "$xray_log_path" ]]; then
    [[ -f "$xray_log_path" && ! -L "$xray_log_path" ]] ||
      die "Refusing unsafe Xray log path: $xray_log_path"
    xray_log_metadata="$(stat -c '%U:%G:%a:%h' "$xray_log_path")"
    [[ "$xray_log_metadata" == "hy2-xray:hy2-xray:600:1" ]] ||
      die "Existing Xray log metadata is unsafe: $xray_log_path"
  else
    install -o hy2-xray -g hy2-xray -m 600 /dev/null "$xray_log_path"
  fi
done

# Runtime secret file — read at module load by the three .py services. Means a
# later `git pull` of the source files can't accidentally overwrite a deployed
# secret with the literal placeholder string and break the API auth header.
log "Writing $HY_DIR/api_secret"
umask 077
api_secret_candidate="$(mktemp "$HY_DIR/.api_secret.deploy.XXXXXX")"
if ! printf '%s\n' "$HY_API_SECRET" > "$api_secret_candidate"; then
  rm -f -- "$api_secret_candidate" || true
  die "Could not stage runtime API secret"
fi
chmod 600 "$api_secret_candidate"
chown root:root "$api_secret_candidate"
durable_replace_candidate "$api_secret_candidate" "$HY_DIR/api_secret"
umask 022

log "Rendering hysteria config and sources..."
render "$REPO_DIR/hysteria/config.yaml.tpl"          "$HY_DIR/config.yaml"
render "$REPO_DIR/hysteria/auth_backend.py"          "$HY_DIR/auth_backend.py"
render "$REPO_DIR/hysteria/auth_service.py"          "$HY_DIR/auth_service.py"
render "$REPO_DIR/hysteria/subscription_service.py"  "$HY_DIR/subscription_service.py"
render "$REPO_DIR/hysteria/traffic_limiter.py"       "$HY_DIR/traffic_limiter.py"
render "$REPO_DIR/hysteria/alerts.py"                "$HY_DIR/alerts.py"
render "$REPO_DIR/hysteria/anomaly.py"               "$HY_DIR/anomaly.py"
render "$REPO_DIR/hysteria/charts.py"                "$HY_DIR/charts.py"
render "$REPO_DIR/hysteria/codex_dashboard.py"       "$HY_DIR/codex_dashboard.py"
render "$REPO_DIR/hysteria/codex_quota.py"           "$HY_DIR/codex_quota.py"
render "$REPO_DIR/hysteria/cost_calibrator.py"       "$HY_DIR/cost_calibrator.py"
render "$REPO_DIR/hysteria/cycle.py"                 "$HY_DIR/cycle.py"
render "$REPO_DIR/hysteria/health.py"                "$HY_DIR/health.py"
render "$REPO_DIR/hysteria/health_widgets.py"        "$HY_DIR/health_widgets.py"
render "$REPO_DIR/hysteria/http_utils.py"            "$HY_DIR/http_utils.py"
render "$REPO_DIR/hysteria/incident_console.py"      "$HY_DIR/incident_console.py"
render "$REPO_DIR/hysteria/online_snapshot.py"       "$HY_DIR/online_snapshot.py"
render "$REPO_DIR/hysteria/revocation_queue.py"      "$HY_DIR/revocation_queue.py"
render "$REPO_DIR/hysteria/rotation_recovery.py"     "$HY_DIR/rotation_recovery.py"
render "$REPO_DIR/hysteria/static_access.py"         "$HY_DIR/static_access.py"
render "$REPO_DIR/hysteria/state_store.py"           "$HY_DIR/state_store.py"
render "$REPO_DIR/hysteria/subscription_profiles.py" "$HY_DIR/subscription_profiles.py"
render "$REPO_DIR/hysteria/xray_config.py"           "$HY_DIR/xray_config.py"
render "$REPO_DIR/hysteria/tuic_config.py"           "$HY_DIR/tuic_config.py"
render "$REPO_DIR/hysteria/tuic_meter.py"            "$HY_DIR/tuic_meter.py"
render "$REPO_DIR/hysteria/usage_dashboard.py"       "$HY_DIR/usage_dashboard.py"
render "$REPO_DIR/hysteria/user_compat.py"           "$HY_DIR/user_compat.py"
render "$REPO_DIR/hysteria/display.py"               "$HY_DIR/display.py"
render "$REPO_DIR/hysteria/timeutil.py"              "$HY_DIR/timeutil.py"
install_atomic 644 "$REPO_DIR/hysteria/admin.css"      "$HY_DIR/admin.css"
install_atomic 644 "$REPO_DIR/hysteria/admin_poll.js"  "$HY_DIR/admin_poll.js"
install_atomic 644 "$REPO_DIR/hysteria/codex_quota.js" "$HY_DIR/codex_quota.js"
install_atomic 644 "$REPO_DIR/hysteria/usage.js"       "$HY_DIR/usage.js"
chmod 700 \
  "$HY_DIR/auth_backend.py" \
  "$HY_DIR/auth_service.py" \
  "$HY_DIR/subscription_service.py" \
  "$HY_DIR/traffic_limiter.py" \
  "$HY_DIR/alerts.py" \
  "$HY_DIR/anomaly.py" \
  "$HY_DIR/charts.py" \
  "$HY_DIR/codex_dashboard.py" \
  "$HY_DIR/codex_quota.py" \
  "$HY_DIR/cost_calibrator.py" \
  "$HY_DIR/cycle.py" \
  "$HY_DIR/health.py" \
  "$HY_DIR/health_widgets.py" \
  "$HY_DIR/http_utils.py" \
  "$HY_DIR/incident_console.py" \
  "$HY_DIR/online_snapshot.py" \
  "$HY_DIR/revocation_queue.py" \
  "$HY_DIR/rotation_recovery.py" \
  "$HY_DIR/static_access.py" \
  "$HY_DIR/state_store.py" \
  "$HY_DIR/subscription_profiles.py" \
  "$HY_DIR/xray_config.py" \
  "$HY_DIR/tuic_config.py" \
  "$HY_DIR/tuic_meter.py" \
  "$HY_DIR/usage_dashboard.py" \
  "$HY_DIR/user_compat.py" \
  "$HY_DIR/display.py" \
  "$HY_DIR/timeutil.py"
PYTHONPYCACHEPREFIX="$(mktemp -d "$HY_DIR/state/.deploy-pycache.XXXXXX")"
export PYTHONPYCACHEPREFIX
if ! python3 -m py_compile \
  "$HY_DIR/revocation_queue.py" \
  "$HY_DIR/rotation_recovery.py"; then
  rm -rf -- "$PYTHONPYCACHEPREFIX"
  unset PYTHONPYCACHEPREFIX
  die "Rendered credential-recovery modules failed Python validation"
fi
rm -rf -- "$PYTHONPYCACHEPREFIX"
unset PYTHONPYCACHEPREFIX
chmod 600 "$HY_DIR/config.yaml"

if [[ ! -f "$HY_DIR/template.yaml" ]]; then
  log "Creating initial clash subscription template → $HY_DIR/template.yaml"
  render "$REPO_DIR/hysteria/clash-default.yaml.tpl" "$HY_DIR/template.yaml"
else
  log "Preserving operator-managed clash subscription template: $HY_DIR/template.yaml"
fi

log "Preparing and validating the Xray template candidate..."
XRAY_CANDIDATE="$(mktemp "$XRAY_ETC/.config.json.candidate.XXXXXX")"
chmod 600 "$XRAY_CANDIDATE"
render "$REPO_DIR/xray/config.json.tpl" "$XRAY_CANDIDATE"
xray run -test -format json -c "$XRAY_CANDIDATE"

# ---------- 6. Runtime state initialization ----------
# One outer lock protects canonical users plus both derived proxy configs.
# Existing critical JSON is validated before any missing file is created, so a
# corrupt live state aborts deployment without partially "repairing" around it.
PYTHONPATH="$HY_DIR" python3 - \
  "$XRAY_CANDIDATE" "$HY_DIR" "$XRAY_ETC/config.json" <<'PY'
import os
import sys
from pathlib import Path

import state_store
import subscription_service
import traffic_limiter
import tuic_config
import xray_config

candidate = Path(sys.argv[1])
hy_dir = Path(sys.argv[2])
xray_target = Path(sys.argv[3])
usage_lock = hy_dir / 'state' / 'usage.lock'
core_defaults = (
    (hy_dir / 'users.json', {}),
    (hy_dir / 'state' / 'usage.json', {}),
    (hy_dir / 'state' / 'usage_daily.json', {}),
    (hy_dir / 'state' / 'auto_reset_state.json', {}),
)
meta_path = hy_dir / 'subscription_meta.json'

with state_store.file_lock(usage_lock):
    for path, default in core_defaults:
        if path.exists():
            state_store.load_json_strict(path, default, required=True)
    if meta_path.exists():
        state_store.load_json_strict(meta_path, {}, required=True)

    for path, default in core_defaults:
        if not path.exists():
            state_store.save_json(path, default)
        os.chmod(path, 0o600)

    subscription_service.ensure_meta()
    os.chmod(meta_path, 0o600)
    users = state_store.load_json_strict(
        hy_dir / 'users.json', {}, required=True,
    )
    usage = state_store.load_json_strict(
        hy_dir / 'state' / 'usage.json', {}, required=True,
    )
    daily = state_store.load_json_strict(
        hy_dir / 'state' / 'usage_daily.json', {}, required=True,
    )
    meta = state_store.load_json_strict(meta_path, {}, required=True)
    traffic_limiter._validate_users(users, path=hy_dir / 'users.json')
    traffic_limiter._validate_usage_ledger(
        usage, path=hy_dir / 'state' / 'usage.json',
    )
    traffic_limiter._validate_usage_ledger(
        daily,
        path=hy_dir / 'state' / 'usage_daily.json',
        daily=True,
    )
    traffic_limiter._validate_meta(meta, path=meta_path)
    now = traffic_limiter.local_now()
    traffic_limiter.preflight_persistent_state(now)
    access_plan, _ = traffic_limiter.build_static_access_plan(
        users,
        daily,
        now=now,
        meta=meta,
    )
    xray_initialized = xray_config.initialize_from_file(
        candidate, path=xray_target,
    )
    xray_changed = xray_config.apply_user_plan(
        access_plan, path=xray_target, prune_unknown=True,
    )
    tuic_changed = tuic_config.sync_user_plan(users, access_plan)

print(
    'Runtime state ready; Xray config '
    + ('initialized' if xray_initialized else 'preserved')
    + f'; exact access plan has {sum(bool(v) for v in access_plan.values())} '
    + 'active user(s); Xray config '
    + ('updated' if xray_changed else 'unchanged')
    + f'; TUIC config {"updated" if tuic_changed else "unchanged"}.'
)
PY

rm -f -- "$XRAY_CANDIDATE"
XRAY_CANDIDATE=""
xray run -test -c "$XRAY_ETC/config.json"

if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then
  printf 'required\n' |
    write_atomic_from_stdin 600 "$HY_DIR/state/https_required"
else
  durable_remove_artifact "$HY_DIR/state/https_required"
fi

# ---------- 7. Self-signed TLS cert ----------
if [[ ! -f "$HY_DIR/server.crt" || ! -f "$HY_DIR/server.key" ]]; then
  log "Generating self-signed TLS certificate..."
  CERT_CANDIDATE="$(mktemp "$HY_DIR/.server.crt.deploy.XXXXXX")"
  KEY_CANDIDATE="$(mktemp "$HY_DIR/.server.key.deploy.XXXXXX")"
  rm -f -- "$CERT_CANDIDATE" "$KEY_CANDIDATE"
  openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$KEY_CANDIDATE" -out "$CERT_CANDIDATE" \
    -subj "/CN=hysteria2" -days 3650 >/dev/null 2>&1
  chown root:root "$CERT_CANDIDATE" "$KEY_CANDIDATE"
  chmod 644 "$CERT_CANDIDATE"
  chmod 600 "$KEY_CANDIDATE"
  durable_replace_candidate "$CERT_CANDIDATE" "$HY_DIR/server.crt"
  CERT_CANDIDATE=""
  durable_replace_candidate "$KEY_CANDIDATE" "$HY_DIR/server.key"
  KEY_CANDIDATE=""
fi

# ---------- 8. Port hopping script ----------
install_atomic 755 "$REPO_DIR/scripts/hysteria-porthop.sh" /usr/local/sbin/hysteria-porthop.sh
install_atomic 755 "$REPO_DIR/scripts/hysteria-tcp-mss.sh" /usr/local/sbin/hysteria-tcp-mss.sh
install_atomic 755 "$REPO_DIR/scripts/hysteria-auth-recover.sh" /usr/local/sbin/hysteria-auth-recover.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-backup.sh" /usr/local/sbin/hy2-backup.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-backup-git.sh" /usr/local/sbin/hy2-backup-git.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-restore-check.sh" /usr/local/sbin/hy2-restore-check.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-lock-exec.py" /usr/local/sbin/hy2-lock-exec.py
install_atomic 755 "$REPO_DIR/scripts/hy2-enable-https.sh" /usr/local/sbin/hy2-enable-https.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-deploy-recovery.py" /usr/local/sbin/hy2-deploy-recovery.py
install_atomic 755 "$REPO_DIR/scripts/hy2-cert-renew-hook.sh" /usr/local/sbin/hy2-cert-renew-hook.sh
install_atomic 755 "$REPO_DIR/scripts/hy2-health-check.sh" /usr/local/sbin/hy2-health-check.sh
install_atomic 644 "$REPO_DIR/logrotate/xray" /etc/logrotate.d/xray
install -d -m 755 /usr/local/share/hy2
install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-log.conf" /usr/local/share/hy2/hysteria-panel-log.conf
install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-https.conf" /usr/local/share/hy2/hysteria-panel-https.conf
install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-redirect.conf" /usr/local/share/hy2/hysteria-panel-redirect.conf
install_atomic 644 "$REPO_DIR/scripts/hy2-cert-renew-hook.sh" /usr/local/share/hy2/hy2-cert-renew-hook.sh

# ---------- 8b. Network tuning ----------
log "Installing network tuning..."
write_atomic_from_stdin 644 /etc/sysctl.d/99-hysteria-udp.conf <<'SYSCTL'
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
capture_sysctl_state
modprobe tcp_bbr 2>/dev/null || true
printf 'tcp_bbr\n' |
  write_atomic_from_stdin 644 /etc/modules-load.d/tcp-bbr.conf
sysctl --system >/dev/null

# ---------- 9. nginx reverse proxy for the admin panel ----------
# The subscription service only listens on 127.0.0.1:8081; nginx on :80 fronts it.
log "Installing nginx site for hysteria-panel..."
install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-log.conf" /etc/nginx/conf.d/hysteria-panel-log.conf
if [[ "$HY_ENABLE_HTTPS" == "1" &&
      -e /etc/nginx/sites-enabled/hysteria-panel-https.conf &&
      -f /etc/nginx/sites-available/hysteria-panel.conf ]]; then
  log "Preserving the active HTTPS and redirect vhosts until certificate validation completes"
elif [[ "$HY_ENABLE_HTTPS" == "1" ]]; then
  log "Installing an ACME-only bootstrap vhost; the plaintext panel remains closed"
  render "$REPO_DIR/nginx/hysteria-panel-bootstrap.conf" /etc/nginx/sites-available/hysteria-panel.conf
else
  durable_remove_artifact \
    /etc/nginx/sites-enabled/hysteria-panel-https.conf
  render "$REPO_DIR/nginx/hysteria-panel.conf" /etc/nginx/sites-available/hysteria-panel.conf
fi
symlink_atomic \
  /etc/nginx/sites-available/hysteria-panel.conf \
  /etc/nginx/sites-enabled/hysteria-panel.conf
# Remove the default site if it's still there (it would clash on :80).
durable_remove_artifact /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx.service
systemctl reload nginx.service

# ---------- 10. Systemd units ----------
log "Installing systemd units..."
install_atomic 644 "$REPO_DIR/systemd/xray.service" \
  "$SYSTEMD_DIR/xray.service"
# Remove only the legacy installer's known generated ExecStart overrides and
# weak xray@ template. This product is deliberately single-instance, and all
# concrete instances were rejected before mutation above.
for legacy_xray_artifact in \
  "$SYSTEMD_DIR/xray.service.d/10-donot_touch_single_conf.conf" \
  "$SYSTEMD_DIR/xray.service.d/10-donot_touch_multi_conf.conf" \
  "$SYSTEMD_DIR/xray@.service" \
  "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_single_conf.conf" \
  "$SYSTEMD_DIR/xray@.service.d/10-donot_touch_multi_conf.conf"; do
  durable_remove_artifact "$legacy_xray_artifact"
done
install_atomic 644 "$REPO_DIR/systemd/hysteria-server.service" \
  "$SYSTEMD_DIR/hysteria-server.service"
install_atomic 644 "$REPO_DIR/systemd/hysteria-auth.service" \
  "$SYSTEMD_DIR/hysteria-auth.service"
install_atomic 644 "$REPO_DIR/systemd/hysteria-subscription.service" \
  "$SYSTEMD_DIR/hysteria-subscription.service"
install_atomic 644 "$REPO_DIR/systemd/hysteria-traffic-limiter.service" \
  "$SYSTEMD_DIR/hysteria-traffic-limiter.service"
install_atomic 644 "$REPO_DIR/systemd/hysteria-traffic-limiter.timer" \
  "$SYSTEMD_DIR/hysteria-traffic-limiter.timer"
install_atomic 644 "$REPO_DIR/systemd/codex-quota-collector.service" \
  "$SYSTEMD_DIR/codex-quota-collector.service"
install_atomic 644 "$REPO_DIR/systemd/codex-quota-collector.timer" \
  "$SYSTEMD_DIR/codex-quota-collector.timer"
install_atomic 644 "$REPO_DIR/systemd/hy2-backup.service" \
  "$SYSTEMD_DIR/hy2-backup.service"
install_atomic 644 "$REPO_DIR/systemd/hy2-backup.timer" \
  "$SYSTEMD_DIR/hy2-backup.timer"
install_atomic 644 "$REPO_DIR/systemd/hysteria-porthop.service" \
  "$SYSTEMD_DIR/hysteria-porthop.service"
install_atomic 644 "$REPO_DIR/systemd/hysteria-tcp-mss.service" \
  "$SYSTEMD_DIR/hysteria-tcp-mss.service"
install_atomic 644 "$REPO_DIR/systemd/tuic-server.service" \
  "$SYSTEMD_DIR/tuic-server.service"
install_atomic 644 "$REPO_DIR/systemd/hy2-health-check.service" \
  "$SYSTEMD_DIR/hy2-health-check.service"
install_atomic 644 "$REPO_DIR/systemd/hy2-health-check.timer" \
  "$SYSTEMD_DIR/hy2-health-check.timer"
install_atomic 644 "$REPO_DIR/systemd/hy2-https-recovery.service" \
  "$SYSTEMD_DIR/hy2-https-recovery.service"
install_atomic 644 "$REPO_DIR/systemd/hy2-deploy-recovery.service" \
  "$SYSTEMD_DIR/hy2-deploy-recovery.service"
install_atomic 644 "$REPO_DIR/systemd/hy2-deploy-watchdog.service" \
  "$SYSTEMD_DIR/hy2-deploy-watchdog.service"
install -d -m 755 "$SYSTEMD_DIR/xray.service.d"
install_atomic 644 "$REPO_DIR/systemd/xray.service.d/20-hy2-hardening.conf" \
  "$SYSTEMD_DIR/xray.service.d/20-hy2-hardening.conf"

install -d -m 755 /etc/fail2ban/filter.d /etc/fail2ban/jail.d
install_atomic 644 "$REPO_DIR/fail2ban/filter.d/tuic-auth.conf" /etc/fail2ban/filter.d/tuic-auth.conf
install_atomic 644 "$REPO_DIR/fail2ban/jail.d/tuic-auth.conf" /etc/fail2ban/jail.d/tuic-auth.conf

install -d -m 755 /etc/systemd/journald.conf.d
install_atomic 644 "$REPO_DIR/journald/60-hy2-limits.conf" /etc/systemd/journald.conf.d/60-hy2-limits.conf

systemctl daemon-reload
systemctl enable hy2-deploy-recovery.service
systemctl enable hy2-https-recovery.service
fail2ban-client -t >/dev/null
systemctl restart systemd-journald.service
systemctl restart fail2ban.service

# ---------- 11. Enable + start ----------
log "Enabling and starting services..."
systemctl enable --now hysteria-porthop.service
systemctl enable --now hysteria-tcp-mss.service
systemctl enable --now hysteria-auth.service
auth_live=0
for _attempt in {1..10}; do
  if curl -fsS --noproxy '*' --max-time 2 \
    http://127.0.0.1:8082/livez >/dev/null; then
    auth_live=1
    break
  fi
  sleep 1
done
[[ "$auth_live" == "1" ]] ||
  die "Hysteria authentication service failed its shallow liveness check"
systemctl enable --now hysteria-server.service
systemctl enable --now hysteria-subscription.service
systemctl enable --now hysteria-traffic-limiter.timer
systemctl enable --now codex-quota-collector.timer
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
if [[ -f "$HY_DIR/state/codex_quota.csv" ]]; then
  PYTHONPATH="$HY_DIR" python3 "$HY_DIR/codex_quota.py" migrate-legacy \
    --legacy-csv "$HY_DIR/state/codex_quota.csv" || \
    warn "Legacy Codex quota history migration failed; the CSV was left untouched."
fi
if command -v codex >/dev/null 2>&1 && codex login status >/dev/null 2>&1; then
  systemctl start codex-quota-collector.service || \
    warn "Initial Codex quota collection failed; the timer will retry in 3 minutes."
else
  warn "Codex is not logged in; quota collection will start after 'codex login'."
fi
# Operational health is timer-owned after this transaction releases the
# deployment flock. A fresh host has no backup yet, so synchronously running
# the operational check here would turn the expected bootstrap state into a
# deployment failure and can deadlock a first backup behind this same lock.

required_active_units=(
  nginx.service
  hysteria-porthop.service
  hysteria-tcp-mss.service
  hysteria-auth.service
  hysteria-server.service
  hysteria-subscription.service
  hysteria-traffic-limiter.timer
  codex-quota-collector.timer
  hy2-backup.timer
  hy2-health-check.timer
  xray.service
  tuic-server.service
)
wait_for_stable_readiness 3 15 1 ||
  die "Deployment did not sustain three consecutive healthy observations"

log "Status:"
for unit in "${required_active_units[@]}"; do
  unit_state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  printf '  %-40s %s\n' "$unit" "$unit_state"
  [[ "$unit_state" == "active" ]] ||
    die "Required service became unavailable during validation: $unit"
done

# Commit only after every sustained readiness and final activity check passes.
# The durable helper verifies every unmodified original and every exact
# replacement generation before making rollback residue disposable.
outer_recovery complete ||
  die "Could not durably commit the outer deployment transaction."
ROLLBACK_ACTIVE=0
DURABLE_RECOVERY_ACTIVE=0
DURABLE_RECOVERY_PREPARED=0
DEPLOY_SUCCEEDED=1
cleanup_rollback_snapshot

# HTTPS has its own durable transaction and recovery manifest. Activate it
# only after the core deployment is committed, so its recovery can never
# conflict with the outer deployment rollback.
if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then
  set +e
  /usr/local/sbin/hy2-enable-https.sh \
    "$HY_SERVER_HOST" "${HY_CERTBOT_EMAIL:-}" "$HY_HTTPS_PORT"
  https_activation_status=$?
  set -e
  case "$https_activation_status" in
    0) ;;
    2)
      warn "Core deployment and HTTPS are active, but automatic certificate renewal is degraded."
      exit 2
      ;;
    *)
      die "Core deployment is active, but HTTPS activation did not complete; the HTTPS recovery transaction remains authoritative."
      ;;
  esac
fi

cat <<EOF

Done. Open the admin panel at:
  $(if [[ "$HY_ENABLE_HTTPS" == "1" ]]; then printf 'https://%s:%s/admin' "$HY_SERVER_HOST" "$HY_HTTPS_PORT"; else printf 'http://%s/admin' "$HY_SERVER_HOST"; fi)

First-time setup:
  1. Log in. If no admin password was preconfigured, the first service start
     writes root-only credentials to $HY_DIR/admin_initial_password.txt.
  2. Create users. Each user gets a /sub/<name>?token=... URL to import into Clash.
  3. Hysteria uses the loopback-only persistent HTTP auth service on :8082.
     $HY_DIR/auth_backend.py remains available as a token-only emergency CLI.

Keep $HY_DIR/{users.json,subscription_meta.json,server.key} safe — they are NOT in git.
EOF
