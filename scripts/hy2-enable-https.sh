#!/bin/bash -p
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

target="${1:-${HY_SERVER_HOST:-}}"
email="${2:-${HY_CERTBOT_EMAIL:-}}"
https_port="${3:-${HY_HTTPS_PORT:-9444}}"
share_dir="${HY2_SHARE_DIR:-/usr/local/share/hy2}"
webroot="${HY2_CERTBOT_WEBROOT:-/var/www/certbot}"

# These overrides make every destructive boundary exercisable under a
# temporary test tree.  Production callers use the root-owned defaults.
nginx_root="${HY2_NGINX_ROOT:-/etc/nginx}"
letsencrypt_root="${HY2_LETSENCRYPT_ROOT:-/etc/letsencrypt}"
nginx_bin="${HY2_NGINX_BIN:-/usr/sbin/nginx}"
systemctl_bin="${HY2_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
openssl_bin="${HY2_OPENSSL_BIN:-/usr/bin/openssl}"
certbot_bin="${HY2_CERTBOT_BIN:-/snap/bin/certbot}"
tls_probe_bin="${HY2_HTTPS_TLS_PROBE_BIN:-}"
redirect_probe_bin="${HY2_HTTPS_REDIRECT_PROBE_BIN:-}"
lock_file="${HY2_HTTPS_LOCK_FILE:-/run/hy2-locks/https-activation.lock}"
lock_timeout="${HY2_HTTPS_LOCK_TIMEOUT_SECONDS:-5}"
recovery_root="${HY2_HTTPS_RECOVERY_DIR:-/var/lib/hysteria/https-activation-recovery}"
renewal_marker="$recovery_root/renewal-pending"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
lock_exec="${HY2_LOCK_EXEC_BIN:-$script_dir/hy2-lock-exec.py}"
lock_marker_env="HY2_HTTPS_LOCK_MARKER"

die() {
  printf '[x] %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Must run as root."
[[ -r "$lock_exec" ]] || die "Missing hardened lock helper."

# Path and command overrides are test-only.  This prevents an unrelated parent
# environment from replacing nginx/systemctl with a no-op during a production
# deployment.  Test overrides must themselves remain beneath one root-owned,
# root-only test tree.
test_mode="${HY2_HTTPS_TEST_MODE:-0}"
[[ "$test_mode" == 0 || "$test_mode" == 1 ]] ||
  die "HY2_HTTPS_TEST_MODE must be 0 or 1."
override_names=(
  HY2_SHARE_DIR
  HY2_CERTBOT_WEBROOT
  HY2_NGINX_ROOT
  HY2_LETSENCRYPT_ROOT
  HY2_NGINX_BIN
  HY2_SYSTEMCTL_BIN
  HY2_OPENSSL_BIN
  HY2_CERTBOT_BIN
  HY2_HTTPS_TLS_PROBE_BIN
  HY2_HTTPS_REDIRECT_PROBE_BIN
  HY2_HTTPS_LOCK_FILE
  HY2_HTTPS_RECOVERY_DIR
  HY2_LOCK_EXEC_BIN
)
declare -a supplied_override_pairs=()
for override_name in "${override_names[@]}"; do
  if [[ -v "$override_name" ]]; then
    [[ "$test_mode" == 1 ]] ||
      die "$override_name is accepted only by the isolated test harness."
    supplied_override_pairs+=("$override_name" "${!override_name}")
  fi
done
if [[ "$test_mode" == 1 ]]; then
  test_root="${HY2_HTTPS_TEST_ROOT:-}"
  [[ -n "$test_root" ]] ||
    die "Test overrides require HY2_HTTPS_TEST_ROOT."
  /usr/bin/python3 -I - "$test_root" "${supplied_override_pairs[@]}" <<'PY' ||
import os
import stat
import sys

root, *pairs = sys.argv[1:]
if not os.path.isabs(root) or os.path.normpath(root) != root:
    raise SystemExit("test root must be canonical and absolute")
metadata = os.lstat(root)
if (
    not stat.S_ISDIR(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or stat.S_IMODE(metadata.st_mode) & 0o077
):
    raise SystemExit("test root must be a root-owned, root-only directory")
real_root = os.path.realpath(root)
for index in range(0, len(pairs), 2):
    name, value = pairs[index : index + 2]
    if not value or not os.path.isabs(value):
        raise SystemExit(f"{name} must be absolute in test mode")
    real_value = os.path.realpath(value)
    try:
        contained = os.path.commonpath((real_root, real_value)) == real_root
    except ValueError:
        contained = False
    if not contained:
        raise SystemExit(f"{name} escapes the isolated test root")
PY
    die "HTTPS test overrides are outside the isolated test root."
fi

# The lock helper validates the descriptor and kernel lock on every nested
# entry.  An environment marker by itself is never accepted as proof.
if [[ -n "${HY2_HTTPS_LOCK_MARKER:-}" ]]; then
  /usr/bin/python3 -I "$lock_exec" \
    --lock-file "$lock_file" \
    --marker-env "$lock_marker_env" \
    --verify ||
    die "Could not verify the inherited HTTPS activation lock."
else
  exec /usr/bin/python3 -I "$lock_exec" \
    --lock-file "$lock_file" \
    --timeout "$lock_timeout" \
    --marker-env "$lock_marker_env" \
    -- /bin/bash -p "$0" "$@"
fi

remove_renewal_marker() {
  /usr/bin/python3 -I - "$recovery_root" "$renewal_marker" <<'PY'
import os
import stat
import sys

root, marker = sys.argv[1:]
try:
    metadata = os.lstat(marker)
except FileNotFoundError:
    raise SystemExit(0)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit("unsafe renewal marker")
os.unlink(marker)
root_fd = os.open(
    root,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(root_fd)
finally:
    os.close(root_fd)
PY
}

remove_renewal_marker_if_owned() {
  local owner_txid="$1"
  /usr/bin/python3 -I - \
    "$recovery_root" "$renewal_marker" "$owner_txid" <<'PY'
import os
import stat
import sys

root, marker, owner_txid = sys.argv[1:]
try:
    metadata = os.lstat(marker)
except FileNotFoundError:
    raise SystemExit(0)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit("unsafe renewal marker")
fd = os.open(
    marker,
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    raw = os.read(fd, 128)
    if os.read(fd, 1):
        raise SystemExit("renewal marker is too large")
finally:
    os.close(fd)
if raw.decode("ascii", "strict").strip() != owner_txid:
    raise SystemExit(0)
os.unlink(marker)
root_fd = os.open(
    root,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(root_fd)
finally:
    os.close(root_fd)
PY
}

reconcile_renewal_marker() {
  [[ -e "$renewal_marker" || -L "$renewal_marker" ]] || return 0
  if ! /usr/bin/timeout 30 "$systemctl_bin" \
    enable --now snap.certbot.renew.timer >/dev/null; then
    return 2
  fi
  if ! /usr/bin/timeout 10 "$systemctl_bin" \
    is-active --quiet snap.certbot.renew.timer; then
    return 2
  fi
  remove_renewal_marker || return 1
}

write_renewal_marker() {
  local marker_txid="$1"
  /usr/bin/python3 -I - \
    "$recovery_root" "$renewal_marker" "$marker_txid" <<'PY'
import os
import stat
import sys

root, marker, txid = sys.argv[1:]
if len(txid) != 32 or any(character not in "0123456789abcdef" for character in txid):
    raise SystemExit("invalid transaction id")
try:
    metadata = os.lstat(marker)
except FileNotFoundError:
    fd = os.open(
        marker,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        payload = (txid + "\n").encode("ascii")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    root_fd = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
else:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SystemExit("unsafe renewal marker")
PY
}

recover_only=0
if [[ "$target" == --recover-only ]]; then
  recover_only=1
  target=""
  email=""
  https_port=""
fi
if (( recover_only == 1 )); then
  recovery_presence="$(
    /usr/bin/python3 -I - "$recovery_root" <<'PY'
import os
import shutil
import stat
import sys

root = sys.argv[1]
if not os.path.isabs(root) or os.path.normpath(root) != root or root == "/":
    raise SystemExit("unsafe recovery root")
try:
    root_metadata = os.lstat(root)
except FileNotFoundError:
    print("clean")
    raise SystemExit(0)
if (
    not stat.S_ISDIR(root_metadata.st_mode)
    or stat.S_ISLNK(root_metadata.st_mode)
    or root_metadata.st_uid != 0
    or stat.S_IMODE(root_metadata.st_mode) & 0o077
):
    raise SystemExit("unsafe recovery root")
renewal_marker = os.path.join(root, "renewal-pending")
renewal_pending = False
try:
    renewal_metadata = os.lstat(renewal_marker)
except FileNotFoundError:
    pass
else:
    if (
        not stat.S_ISREG(renewal_metadata.st_mode)
        or stat.S_ISLNK(renewal_metadata.st_mode)
        or renewal_metadata.st_uid != 0
        or renewal_metadata.st_nlink != 1
        or stat.S_IMODE(renewal_metadata.st_mode) != 0o600
    ):
        raise SystemExit("unsafe renewal marker")
    renewal_pending = True
pending = os.path.join(root, "pending")
try:
    pending_metadata = os.lstat(pending)
except FileNotFoundError:
    print("renewal" if renewal_pending else "clean")
    raise SystemExit(0)
if (
    not stat.S_ISDIR(pending_metadata.st_mode)
    or stat.S_ISLNK(pending_metadata.st_mode)
    or pending_metadata.st_uid != 0
    or stat.S_IMODE(pending_metadata.st_mode) & 0o077
):
    raise SystemExit("unsafe pending recovery directory")
manifest = os.path.join(pending, "manifest.json")
if not os.path.lexists(manifest):
    # A mutation is forbidden until the initial manifest is durable, so this
    # is pre-transaction crash debris and is safe to discard even on a fresh
    # host where nginx directories do not exist yet.
    shutil.rmtree(pending)
    root_fd = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
    print("renewal" if renewal_pending else "clean")
else:
    print("pending")
PY
  )" || die "HTTPS recovery state is unsafe."
  if [[ "$recovery_presence" == clean ]]; then
    printf '[i] HTTPS activation recovery state is clean.\n'
    exit 0
  fi
  if [[ "$recovery_presence" == renewal ]]; then
    if reconcile_renewal_marker; then
      printf '[i] HTTPS renewal recovery state is clean.\n'
      exit 0
    else
      renewal_status="$?"
    fi
    printf '[!] HTTPS is active, but certificate renewal remains degraded.\n' >&2
    if [[ "$renewal_status" == 2 ]]; then
      exit 2
    fi
    exit 1
  fi
  [[ "$recovery_presence" == pending ||
     "$recovery_presence" == renewal ]] ||
    die "HTTPS recovery state could not be classified."
fi

# Ports and IPv4 octets must use canonical unsigned decimal notation.  This
# avoids shell arithmetic prefixes and makes certificate names deterministic.
is_ipv4=0
if (( recover_only == 0 )); then
  [[ -n "$target" ]] ||
    die "Usage: hy2-enable-https.sh <domain-or-ip> [email] [https-port]"
  [[ "$https_port" =~ ^(0|[1-9][0-9]{0,4})$ ]] ||
    die "HTTPS port must use canonical decimal notation."
  [[ "$https_port" != 443 &&
     "$https_port" != 8081 &&
     "$https_port" != 8082 &&
     "$https_port" != 8443 &&
     "$https_port" != 9443 &&
     "$https_port" != 10085 &&
     "$https_port" != 25413 ]] ||
    die "HTTPS port conflicts with an existing VPN listener."
  (( 10#$https_port >= 1024 && 10#$https_port <= 65535 )) ||
    die "HTTPS port must be between 1024 and 65535."

  if [[ "$target" =~ ^[0-9.]+$ ]]; then
    [[ "$target" =~ ^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$ ]] ||
      die "Invalid or non-canonical IPv4 address: $target"
    is_ipv4=1
    IFS=. read -r a b c d <<<"$target"
    for octet in "$a" "$b" "$c" "$d"; do
      (( 10#$octet <= 255 )) || die "Invalid IPv4 address: $target"
    done
  else
    target="${target,,}"
    (( ${#target} <= 253 )) || die "DNS hostname is too long."
    [[ "$target" == *.* ]] ||
      die "A DNS hostname must contain at least two labels."
    [[ "$target" != .* && "$target" != *. && "$target" != *..* ]] ||
      die "Invalid DNS hostname: $target"
    IFS=. read -r -a dns_labels <<<"$target"
    for label in "${dns_labels[@]}"; do
      (( ${#label} >= 1 && ${#label} <= 63 )) ||
        die "Invalid DNS label length in hostname: $target"
      [[ "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] ||
        die "Invalid DNS label in hostname: $target"
    done
  fi
fi

if (( recover_only == 0 )); then
  for template in \
    hysteria-panel-log.conf \
    hysteria-panel-https.conf \
    hysteria-panel-redirect.conf \
    hy2-cert-renew-hook.sh; do
    [[ -r "$share_dir/$template" ]] ||
      die "Missing required HTTPS asset: $template"
  done
fi

panel_conf="$nginx_root/sites-available/hysteria-panel.conf"
tls_conf="$nginx_root/sites-available/hysteria-panel-https.conf"
tls_link="$nginx_root/sites-enabled/hysteria-panel-https.conf"
log_conf="$nginx_root/conf.d/hysteria-panel-log.conf"
renew_hook="$letsencrypt_root/renewal-hooks/deploy/hy2-cert-renew-hook.sh"

for required_dir in \
  "$nginx_root/conf.d" \
  "$nginx_root/sites-available" \
  "$nginx_root/sites-enabled"; do
  [[ -d "$required_dir" && ! -L "$required_dir" ]] ||
    die "Missing or unsafe nginx directory: $required_dir"
done

if (( recover_only == 0 )); then
  # The hook directory and webroot are non-transactional containers.  Every
  # file we own inside them is included in the durable transaction.
  install -d -m 755 "$(dirname "$renew_hook")"
  install -d -m 755 "$webroot/.well-known/acme-challenge"
fi

fsync_path() {
  local path="$1"
  /usr/bin/python3 -I - "$path" <<'PY'
import os
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
if os.path.isdir(path):
    flags |= getattr(os, "O_DIRECTORY", 0)
fd = os.open(path, flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

fsync_parent() {
  fsync_path "$(dirname "$1")"
}

ensure_recovery_root() {
  /usr/bin/python3 -I - "$recovery_root" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
if not os.path.isabs(path) or os.path.normpath(path) != path or path == "/":
    raise SystemExit("recovery path must be canonical and absolute")
try:
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
except FileExistsError:
    pass
metadata = os.lstat(path)
if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit("recovery path must be a real directory")
if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("recovery path must be root-owned and root-only")
fd = os.open(
    path,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

ensure_recovery_root ||
  die "HTTPS recovery directory is not a root-only directory."
pending_dir="$recovery_root/pending"
manifest="$pending_dir/manifest.json"

declare -a artifact_names=(log panel tls link hook)
declare -a artifact_paths=(
  "$log_conf"
  "$panel_conf"
  "$tls_conf"
  "$tls_link"
  "$renew_hook"
)
declare -a transaction_candidates=()
declare -a committed_paths=()
transaction_active=0
transaction_complete=0
rollback_started=0
commit_count=0
txid=""

candidate_path_for() {
  local destination="$1" dir base
  dir="$(dirname "$destination")"
  base="$(basename "$destination")"
  printf '%s/.%s.hy2-%s.candidate' "$dir" "$base" "$txid"
}

manifest_initialize() {
  /usr/bin/python3 -I - \
    "$recovery_root" "$pending_dir" "$manifest" "$txid" \
    "${artifact_names[0]}" "${artifact_paths[0]}" \
    "${artifact_names[1]}" "${artifact_paths[1]}" \
    "${artifact_names[2]}" "${artifact_paths[2]}" \
    "${artifact_names[3]}" "${artifact_paths[3]}" \
    "${artifact_names[4]}" "${artifact_paths[4]}" <<'PY'
import hashlib
import json
import os
import secrets
import stat
import sys

recovery_root, pending_dir, manifest, txid, *pairs = sys.argv[1:]
if len(pairs) % 2:
    raise SystemExit("invalid artifact list")


def fsync_dir(path):
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_manifest(payload):
    temp = os.path.join(
        pending_dir,
        f".manifest-{os.getpid()}-{secrets.token_hex(4)}.tmp",
    )
    fd = os.open(
        temp,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        data = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, manifest)
    fsync_dir(pending_dir)


def generation_token(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


os.mkdir(pending_dir, 0o700)
os.chmod(pending_dir, 0o700)
fsync_dir(recovery_root)
artifacts = []
for index in range(0, len(pairs), 2):
    name, path = pairs[index : index + 2]
    destination_dir = os.path.dirname(path)
    candidate = os.path.join(
        destination_dir,
        f".{os.path.basename(path)}.hy2-{txid}.candidate",
    )
    entry = {
        "name": name,
        "path": path,
        "candidate": candidate,
        "existed": False,
        "kind": "absent",
        "original_generation": generation_token({"kind": "absent"}),
    }
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        artifacts.append(entry)
        continue
    if stat.S_ISLNK(metadata.st_mode):
        entry.update(
            {
                "existed": True,
                "kind": "symlink",
                "link_target": os.readlink(path),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
        entry["original_generation"] = generation_token(
            {
                "kind": "symlink",
                "target": entry["link_target"],
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
    elif stat.S_ISREG(metadata.st_mode):
        snapshot_name = f"snapshot-{name}"
        snapshot_path = os.path.join(pending_dir, snapshot_name)
        source_fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(source_fd)
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise SystemExit("artifact changed while snapshotting")
            destination_fd = os.open(
                snapshot_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                os.fchmod(destination_fd, 0o600)
                content_digest = hashlib.sha256()
                while True:
                    chunk = os.read(source_fd, 1024 * 1024)
                    if not chunk:
                        break
                    content_digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_fd, view)
                        view = view[written:]
                os.fsync(destination_fd)
            finally:
                os.close(destination_fd)
        finally:
            os.close(source_fd)
        fsync_dir(pending_dir)
        entry.update(
            {
                "existed": True,
                "kind": "file",
                "snapshot": snapshot_name,
                "mode": stat.S_IMODE(metadata.st_mode),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
        entry["original_generation"] = generation_token(
            {
                "kind": "file",
                "sha256": content_digest.hexdigest(),
                "mode": stat.S_IMODE(metadata.st_mode),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
    else:
        raise SystemExit(f"refusing non-file transaction artifact: {name}")
    artifacts.append(entry)

payload = {
    "version": 1,
    "txid": txid,
    "state": "active",
    "artifacts": artifacts,
    "committed": [],
    "pending_commit": None,
}
write_manifest(payload)
PY
}

manifest_action() {
  local action="$1"
  local value="${2:-}"
  /usr/bin/python3 -I - "$manifest" "$action" "$value" <<'PY'
import hashlib
import json
import os
import secrets
import stat
import sys

manifest, action, value = sys.argv[1:]
directory = os.path.dirname(manifest)


def generation_token(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def current_generation(path):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return generation_token({"kind": "absent"})
    if stat.S_ISLNK(metadata.st_mode):
        return generation_token(
            {
                "kind": "symlink",
                "target": os.readlink(path),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit("transaction artifact is no longer file-like")
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise SystemExit("transaction artifact changed while hashing")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return generation_token(
        {
            "kind": "file",
            "sha256": digest.hexdigest(),
            "mode": stat.S_IMODE(opened.st_mode),
            "uid": opened.st_uid,
            "gid": opened.st_gid,
        }
    )


metadata = os.lstat(manifest)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit("unsafe transaction manifest")
fd = os.open(
    manifest,
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    if os.fstat(fd).st_ino != metadata.st_ino:
        raise SystemExit("transaction manifest changed")
    raw = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        raw += chunk
        if len(raw) > 1024 * 1024:
            raise SystemExit("transaction manifest is too large")
finally:
    os.close(fd)
payload = json.loads(raw.decode("utf-8"))
entry_by_path = {entry["path"]: entry for entry in payload["artifacts"]}
known_paths = set(entry_by_path)
if action == "before":
    if value not in known_paths or value in payload["committed"]:
        raise SystemExit("invalid pending commit")
    if payload["pending_commit"] is not None:
        raise SystemExit("another commit is already pending")
    entry = entry_by_path[value]
    if current_generation(value) != entry.get("original_generation"):
        raise SystemExit("transaction artifact changed since snapshot")
    replacement_generation = current_generation(entry["candidate"])
    if replacement_generation == generation_token({"kind": "absent"}):
        raise SystemExit("transaction candidate is missing")
    entry["replacement_generation"] = replacement_generation
    payload["pending_commit"] = value
elif action == "after":
    if payload["pending_commit"] != value:
        raise SystemExit("commit journal mismatch")
    entry = entry_by_path[value]
    if current_generation(value) != entry.get("replacement_generation"):
        raise SystemExit("committed artifact generation mismatch")
    payload["committed"].append(value)
    payload["pending_commit"] = None
elif action == "verify":
    if payload["pending_commit"] is not None:
        raise SystemExit("cannot verify with a pending commit")
    for committed_path in payload["committed"]:
        entry = entry_by_path[committed_path]
        if current_generation(committed_path) != entry.get(
            "replacement_generation"
        ):
            raise SystemExit("committed artifact generation changed")
elif action == "state":
    if value not in {
        "active",
        "recovery-failed",
        "restored",
        "complete",
    }:
        raise SystemExit("invalid transaction state")
    payload["state"] = value
else:
    raise SystemExit("invalid manifest action")

temp = os.path.join(
    directory,
    f".manifest-{os.getpid()}-{secrets.token_hex(4)}.tmp",
)
out = os.open(
    temp,
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
    0o600,
)
try:
    data = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    os.fchmod(out, 0o600)
    view = memoryview(data)
    while view:
        written = os.write(out, view)
        view = view[written:]
    os.fsync(out)
finally:
    os.close(out)
os.replace(temp, manifest)
directory_fd = os.open(
    directory,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

read_manifest_state() {
  /usr/bin/python3 -I - "$manifest" <<'PY'
import json
import os
import stat
import sys

path = sys.argv[1]
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit(1)
fd = os.open(
    path,
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    payload = json.load(os.fdopen(fd, "r", encoding="utf-8"))
finally:
    # json.load's wrapper owns and closes fd.
    pass
print(payload.get("state", ""))
PY
}

read_manifest_txid() {
  /usr/bin/python3 -I - "$manifest" <<'PY'
import json
import os
import re
import stat
import sys

path = sys.argv[1]
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit(1)
fd = os.open(
    path,
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
with os.fdopen(fd, "r", encoding="utf-8") as handle:
    payload = json.load(handle)
txid = payload.get("txid", "")
if not re.fullmatch(r"[0-9a-f]{32}", txid):
    raise SystemExit(1)
print(txid)
PY
}

restore_manifest_artifacts() {
  HY2_HTTPS_EXPECTED_ARTIFACTS="$(
    printf '%s\n' "${artifact_paths[@]}"
  )" /usr/bin/python3 -I - "$manifest" <<'PY'
import hashlib
import json
import os
import secrets
import stat
import sys

manifest = sys.argv[1]
test_mode = os.environ.get("HY2_HTTPS_TEST_MODE") == "1"
fail_index_raw = os.environ.get("HY2_HTTPS_TEST_RESTORE_FAIL_INDEX", "")
fail_index = int(fail_index_raw) if test_mode and fail_index_raw else -1
expected = os.environ["HY2_HTTPS_EXPECTED_ARTIFACTS"].splitlines()
directory = os.path.dirname(manifest)


def fsync_dir_fd(fd):
    os.fsync(fd)


def generation_token(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def current_generation(path):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return generation_token({"kind": "absent"})
    if stat.S_ISLNK(metadata.st_mode):
        return generation_token(
            {
                "kind": "symlink",
                "target": os.readlink(path),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("transaction artifact is no longer file-like")
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise RuntimeError("transaction artifact changed while hashing")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return generation_token(
        {
            "kind": "file",
            "sha256": digest.hexdigest(),
            "mode": stat.S_IMODE(opened.st_mode),
            "uid": opened.st_uid,
            "gid": opened.st_gid,
        }
    )


def load():
    metadata = os.lstat(manifest)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RuntimeError("unsafe manifest")
    fd = os.open(
        manifest,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        raw = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 1024 * 1024:
                raise RuntimeError("manifest too large")
    finally:
        os.close(fd)
    return json.loads(raw.decode("utf-8"))


payload = load()
artifacts = payload.get("artifacts")
if (
    payload.get("version") != 1
    or not isinstance(artifacts, list)
    or [entry.get("path") for entry in artifacts] != expected
):
    raise SystemExit("transaction manifest does not match owned paths")
artifact_by_path = {entry["path"]: entry for entry in artifacts}
committed = payload.get("committed")
pending = payload.get("pending_commit")
if not isinstance(committed, list) or len(set(committed)) != len(committed):
    raise SystemExit("invalid committed path journal")
if any(path not in artifact_by_path for path in committed):
    raise SystemExit("unknown committed path")
if pending is not None and pending not in artifact_by_path:
    raise SystemExit("unknown pending path")
restore_paths = committed.copy()
if pending is not None and pending not in restore_paths:
    restore_paths.append(pending)

failures = []
for restore_index, path in enumerate(reversed(restore_paths), start=1):
    entry = artifact_by_path[path]
    try:
        if restore_index == fail_index:
            raise OSError("injected restore failure")
        current = current_generation(path)
        original = entry.get("original_generation")
        replacement = entry.get("replacement_generation")
        if current == original:
            # An outer transaction or operator already restored precisely the
            # snapshotted generation.  Do not rewrite it.
            continue
        if not replacement or current != replacement:
            # Never clobber a third generation created after our crash.
            raise RuntimeError("artifact generation conflicts with recovery")
        parent = os.path.dirname(path)
        basename = os.path.basename(path)
        parent_fd = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            kind = entry["kind"]
            if kind == "absent":
                try:
                    os.unlink(basename, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                fsync_dir_fd(parent_fd)
                if current_generation(path) != original:
                    raise RuntimeError("restored absence generation mismatch")
                continue

            temporary = (
                f".{basename}.hy2-restore-{payload['txid']}-"
                f"{restore_index}-{secrets.token_hex(3)}"
            )
            if kind == "file":
                snapshot_name = entry["snapshot"]
                if (
                    os.path.basename(snapshot_name) != snapshot_name
                    or "/" in snapshot_name
                ):
                    raise RuntimeError("unsafe snapshot name")
                snapshot_path = os.path.join(directory, snapshot_name)
                snapshot_metadata = os.lstat(snapshot_path)
                if (
                    not stat.S_ISREG(snapshot_metadata.st_mode)
                    or stat.S_ISLNK(snapshot_metadata.st_mode)
                    or snapshot_metadata.st_uid != 0
                    or snapshot_metadata.st_nlink != 1
                ):
                    raise RuntimeError("unsafe snapshot")
                source_fd = os.open(
                    snapshot_path,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                )
                destination_fd = os.open(
                    temporary,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    while True:
                        chunk = os.read(source_fd, 1024 * 1024)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_fd, view)
                            view = view[written:]
                    os.fchown(destination_fd, entry["uid"], entry["gid"])
                    os.fchmod(destination_fd, entry["mode"])
                    os.fsync(destination_fd)
                finally:
                    os.close(source_fd)
                    os.close(destination_fd)
            elif kind == "symlink":
                os.symlink(entry["link_target"], temporary, dir_fd=parent_fd)
                os.chown(
                    temporary,
                    entry["uid"],
                    entry["gid"],
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            else:
                raise RuntimeError("unknown snapshot kind")
            os.replace(
                temporary,
                basename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            fsync_dir_fd(parent_fd)
            if current_generation(path) != original:
                raise RuntimeError("restored artifact generation mismatch")
        finally:
            os.close(parent_fd)
    except Exception:
        failures.append(entry["name"])

if failures:
    print(
        "[x] Could not restore one or more HTTPS transaction artifacts.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

cleanup_candidates() {
  local candidate cleanup_failed=0
  for candidate in "${transaction_candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if [[ -e "$candidate" || -L "$candidate" ]]; then
      rm -f -- "$candidate" || cleanup_failed=1
      fsync_parent "$candidate" || cleanup_failed=1
    fi
  done
  (( cleanup_failed == 0 ))
}

cleanup_manifest_candidates() {
  HY2_HTTPS_EXPECTED_ARTIFACTS="$(
    printf '%s\n' "${artifact_paths[@]}"
  )" /usr/bin/python3 -I - "$manifest" <<'PY'
import json
import os
import re
import stat
import sys

manifest = sys.argv[1]
expected = os.environ["HY2_HTTPS_EXPECTED_ARTIFACTS"].splitlines()
with open(manifest, "r", encoding="utf-8") as handle:
    payload = json.load(handle)
txid = payload.get("txid", "")
artifacts = payload.get("artifacts", [])
if (
    not re.fullmatch(r"[0-9a-f]{32}", txid)
    or [entry.get("path") for entry in artifacts] != expected
):
    raise SystemExit("invalid transaction candidate journal")
for entry in artifacts:
    path = entry["path"]
    parent = os.path.dirname(path)
    basename = os.path.basename(path)
    expected_candidate = os.path.join(
        parent,
        f".{basename}.hy2-{txid}.candidate",
    )
    if entry.get("candidate") != expected_candidate:
        raise SystemExit("invalid transaction candidate path")
    parent_fd = os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        try:
            metadata = os.lstat(os.path.basename(expected_candidate), dir_fd=parent_fd)
        except FileNotFoundError:
            continue
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            raise SystemExit("unsafe transaction candidate")
        os.unlink(os.path.basename(expected_candidate), dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
PY
}

cleanup_pending_directory() {
  /usr/bin/python3 -I - "$recovery_root" "$pending_dir" <<'PY'
import os
import shutil
import stat
import sys

root, pending = sys.argv[1:]
if os.path.lexists(pending):
    metadata = os.lstat(pending)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise SystemExit("unsafe pending transaction directory")
    shutil.rmtree(pending)
root_fd = os.open(
    root,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(root_fd)
finally:
    os.close(root_fd)
PY
}

reload_restored_nginx() {
  local active_status
  "$nginx_bin" -t >/dev/null 2>&1 || return 1
  if "$systemctl_bin" is-active --quiet nginx.service >/dev/null 2>&1; then
    "$systemctl_bin" reload nginx.service >/dev/null 2>&1 || return 1
    return 0
  else
    active_status="$?"
  fi
  # systemctl documents 3 as inactive.  Any other failure leaves runtime
  # state unknown and must preserve the journal for operator recovery.
  [[ "$active_status" == 3 ]]
}

preserve_recovery_state() {
  manifest_action state recovery-failed >/dev/null 2>&1 || true
  printf '[x] HTTPS recovery is incomplete; durable recovery data remains at %s\n' \
    "$pending_dir" >&2
}

recover_pending_transaction() {
  [[ -e "$pending_dir" || -L "$pending_dir" ]] || return 0
  if [[ ! -d "$pending_dir" || -L "$pending_dir" ]]; then
    printf '[x] Unsafe HTTPS recovery entry at %s\n' "$pending_dir" >&2
    return 1
  fi
  if [[ ! -e "$manifest" ]]; then
    # No mutation may occur until the initial manifest is durable, so a
    # manifest-less directory can only be pre-transaction crash debris.
    cleanup_pending_directory
    return
  fi

  local state recovered_txid
  state="$(read_manifest_state)" || {
    preserve_recovery_state
    return 1
  }
  recovered_txid="$(read_manifest_txid)" || {
    preserve_recovery_state
    return 1
  }
  if [[ "$state" == complete ]]; then
    cleanup_manifest_candidates &&
      cleanup_pending_directory || {
      printf '[x] Completed HTTPS transaction residue remains at %s\n' \
        "$pending_dir" >&2
      return 1
    }
    return 0
  fi

  trap '' HUP INT TERM
  if ! restore_manifest_artifacts; then
    preserve_recovery_state
    return 1
  fi
  manifest_action state restored || {
    preserve_recovery_state
    return 1
  }
  if ! reload_restored_nginx; then
    preserve_recovery_state
    return 1
  fi
  cleanup_manifest_candidates &&
    remove_renewal_marker_if_owned "$recovered_txid" &&
    cleanup_pending_directory || {
    preserve_recovery_state
    return 1
  }
  trap - HUP INT TERM
  printf '[i] Recovered an interrupted HTTPS activation transaction.\n' >&2
}

recover_pending_transaction ||
  die "Resolve the preserved HTTPS recovery transaction before retrying."
preexisting_renewal_degraded=0
if [[ -e "$renewal_marker" || -L "$renewal_marker" ]]; then
  if reconcile_renewal_marker; then
    :
  else
    preexisting_renewal_degraded="$?"
    printf '[!] HTTPS certificate renewal remains degraded.\n' >&2
  fi
fi
if (( recover_only == 1 )); then
  if [[ "$preexisting_renewal_degraded" == 2 ]]; then
    exit 2
  elif [[ "$preexisting_renewal_degraded" != 0 ]]; then
    exit 1
  fi
  printf '[i] HTTPS activation recovery state is clean.\n'
  exit 0
fi

stage_regular() {
  local output_var="$1" source="$2" destination="$3" mode="$4"
  local candidate
  candidate="$(candidate_path_for "$destination")"
  /usr/bin/python3 -I - "$source" "$candidate" "$mode" <<'PY'
import os
import stat
import sys

source, destination, raw_mode = sys.argv[1:]
mode = int(raw_mode, 8)
source_metadata = os.lstat(source)
if not stat.S_ISREG(source_metadata.st_mode) or stat.S_ISLNK(
    source_metadata.st_mode
):
    raise SystemExit("source asset must be a regular file")
source_fd = os.open(
    source,
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
)
destination_fd = os.open(
    destination,
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
    mode,
)
try:
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            view = view[written:]
    os.fchmod(destination_fd, mode)
    os.fsync(destination_fd)
finally:
    os.close(source_fd)
    os.close(destination_fd)
PY
  fsync_parent "$candidate"
  transaction_candidates+=("$candidate")
  printf -v "$output_var" '%s' "$candidate"
}

stage_rendered() {
  local output_var="$1" source="$2" destination="$3" template_kind="$4"
  local candidate
  candidate="$(candidate_path_for "$destination")"
  /usr/bin/python3 -I - \
    "$source" "$candidate" "$template_kind" \
    "$target" "$https_port" "$cert" "$key" <<'PY'
import os
import re
import stat
import sys

source, destination, kind, host, port, certificate, key = sys.argv[1:]
placeholder = re.compile(r"__[A-Z][A-Z0-9_]*__")
mapping = {
    "__HY_SERVER_HOST__": host,
    "__HY_HTTPS_PORT__": port,
    "__HY_TLS_CERT__": certificate,
    "__HY_TLS_KEY__": key,
}
required_by_kind = {
    "redirect": {"__HY_SERVER_HOST__", "__HY_HTTPS_PORT__"},
    "tls": set(mapping),
}
if kind not in required_by_kind:
    raise SystemExit("unknown template kind")
for value in mapping.values():
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SystemExit("template value contains a control character")
metadata = os.lstat(source)
if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit("template must be a regular file")
with open(source, "r", encoding="utf-8", newline="") as handle:
    template = handle.read()
source_tokens = set(placeholder.findall(template))
if not required_by_kind[kind].issubset(source_tokens):
    raise SystemExit("template is missing a required placeholder")
unknown = source_tokens - set(mapping)
if unknown:
    raise SystemExit("template contains an unknown placeholder")
rendered = placeholder.sub(lambda match: mapping[match.group(0)], template)
if placeholder.search(rendered):
    raise SystemExit("rendered template still contains a placeholder")
fd = os.open(
    destination,
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0),
    0o644,
)
try:
    data = rendered.encode("utf-8")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fchmod(fd, 0o644)
    os.fsync(fd)
finally:
    os.close(fd)
PY
  fsync_parent "$candidate"
  transaction_candidates+=("$candidate")
  printf -v "$output_var" '%s' "$candidate"
}

stage_symlink() {
  local output_var="$1" link_target="$2" destination="$3"
  local candidate
  candidate="$(candidate_path_for "$destination")"
  ln -s -- "$link_target" "$candidate"
  fsync_parent "$candidate"
  transaction_candidates+=("$candidate")
  printf -v "$output_var" '%s' "$candidate"
}

# Fault injection is inert unless the explicit test guard is present.
test_fault="${HY2_HTTPS_TEST_FAULT:-}"
test_kill_after="${HY2_HTTPS_TEST_KILL_AFTER_COMMIT:-}"
if [[ -n "$test_fault$test_kill_after${HY2_HTTPS_TEST_RESTORE_FAIL_INDEX:-}" &&
      "${HY2_HTTPS_TEST_MODE:-0}" != 1 ]]; then
  die "HTTPS transaction fault injection is available only in test mode."
fi
if [[ -n "$test_kill_after" &&
      ! "$test_kill_after" =~ ^[1-9][0-9]*$ ]]; then
  die "Invalid test kill boundary."
fi

fault_requested() {
  [[ "${HY2_HTTPS_TEST_MODE:-0}" == 1 && "$test_fault" == "$1" ]]
}

commit_replacement() {
  local candidate="$1" destination="$2" label="$3"
  commit_count=$((commit_count + 1))
  manifest_action before "$destination" || return 1
  if fault_requested "replace:$commit_count" ||
    { [[ "$label" == link ]] && fault_requested link; }; then
    return 1
  fi
  if ! mv -Tf -- "$candidate" "$destination"; then
    return 1
  fi
  committed_paths+=("$destination")
  if ! fsync_parent "$destination"; then
    return 1
  fi
  if ! manifest_action after "$destination"; then
    return 1
  fi
  if [[ "${HY2_HTTPS_TEST_MODE:-0}" == 1 &&
        -n "$test_kill_after" &&
        "$test_kill_after" == "$commit_count" ]]; then
    kill -s KILL "$BASHPID"
  fi
}

rollback_nginx_config() {
  local rollback_failed=0 rollback_stage=""
  (( rollback_started == 0 )) || return 0
  rollback_started=1
  trap '' HUP INT TERM

  if ! restore_manifest_artifacts; then
    rollback_failed=1
    rollback_stage="artifact restore"
  fi
  if (( rollback_failed == 0 )); then
    if ! manifest_action state restored; then
      rollback_failed=1
      rollback_stage="restore journal"
    fi
  fi
  if (( rollback_failed == 0 )); then
    if ! reload_restored_nginx; then
      rollback_failed=1
      rollback_stage="nginx restore reload"
    fi
  fi
  if (( rollback_failed == 0 )); then
    if ! cleanup_candidates; then
      rollback_failed=1
      rollback_stage="candidate cleanup"
    fi
  fi
  if (( rollback_failed == 0 )); then
    if ! remove_renewal_marker_if_owned "$txid"; then
      rollback_failed=1
      rollback_stage="renewal journal cleanup"
    fi
  fi
  if (( rollback_failed == 0 )); then
    if ! cleanup_pending_directory; then
      rollback_failed=1
      rollback_stage="journal cleanup"
    fi
  fi
  if (( rollback_failed != 0 )); then
    printf '[x] HTTPS rollback stopped at: %s.\n' "$rollback_stage" >&2
    preserve_recovery_state
    return 1
  fi
  return 0
}

on_exit() {
  local status="$?"
  trap - EXIT
  trap '' HUP INT TERM PIPE
  if (( transaction_active == 1 && transaction_complete == 0 )); then
    if ! rollback_nginx_config; then
      status=1
    else
      printf '[i] Previous nginx configuration restored.\n' >&2
    fi
  fi
  exit "$status"
}

on_signal() {
  local signal="$1" status="$2"
  trap '' HUP INT TERM
  printf '[x] HTTPS activation interrupted by %s; restoring prior configuration.\n' \
    "$signal" >&2
  exit "$status"
}

trap on_exit EXIT
trap 'on_signal HUP 129' HUP
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM

txid="$(/usr/bin/python3 -I -c 'import secrets; print(secrets.token_hex(16))')"
manifest_initialize
for destination in "${artifact_paths[@]}"; do
  transaction_candidates+=("$(candidate_path_for "$destination")")
done
transaction_active=1

# The query-safe log format is needed for the ACME bootstrap request.  It is
# the first committed artifact and is journaled like every later mutation.
stage_regular log_candidate \
  "$share_dir/hysteria-panel-log.conf" "$log_conf" 644
commit_replacement "$log_candidate" "$log_conf" log ||
  die "Could not activate the query-safe nginx log configuration."
if ! "$nginx_bin" -t; then
  die "nginx rejected the query-safe log configuration."
fi
if ! "$systemctl_bin" reload nginx.service; then
  die "nginx reload failed while preparing the ACME challenge."
fi

export DEBIAN_FRONTEND=noninteractive
if [[ ! -x "$certbot_bin" ]]; then
  if [[ "$certbot_bin" != /snap/bin/certbot ]]; then
    die "Configured certbot executable is unavailable."
  fi
  command -v snap >/dev/null 2>&1 || {
    apt-get update -y >/dev/null
    apt-get install -y snapd >/dev/null
  }
  snap install certbot --classic
fi

if (( is_ipv4 )); then
  certbot_version="$("$certbot_bin" --version | awk '{print $2}')"
  dpkg --compare-versions "$certbot_version" ge 5.4 ||
    die "Certbot 5.4 or newer is required for IP certificates (found $certbot_version)."
fi

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
  --cert-name "$target" \
  --webroot \
  --webroot-path "$webroot" \
  "${account_args[@]}" \
  "${identifier_args[@]}"

cert_dir="$letsencrypt_root/live/$target"
cert="$cert_dir/fullchain.pem"
key="$cert_dir/privkey.pem"
[[ -r "$cert" && -r "$key" ]] ||
  die "Certificate files were not created under the expected certificate directory."
"$openssl_bin" x509 -checkend 86400 -noout -in "$cert" ||
  die "New certificate expires too soon."
if (( is_ipv4 )); then
  "$openssl_bin" x509 -noout -in "$cert" -checkip "$target" ||
    die "Issued certificate SAN does not cover the requested IP address."
else
  "$openssl_bin" x509 -noout -in "$cert" -checkhost "$target" ||
    die "Issued certificate SAN does not cover the requested DNS hostname."
fi

stage_rendered tls_candidate \
  "$share_dir/hysteria-panel-https.conf" "$tls_conf" tls
stage_regular hook_candidate \
  "$share_dir/hy2-cert-renew-hook.sh" "$renew_hook" 755
stage_symlink link_candidate "$tls_conf" "$tls_link"
stage_rendered panel_candidate \
  "$share_dir/hysteria-panel-redirect.conf" "$panel_conf" redirect

# Expand first: make the TLS vhost and renewal hook valid and live while the
# existing HTTP panel behavior remains untouched.
commit_replacement "$tls_candidate" "$tls_conf" tls ||
  die "Could not activate the HTTPS virtual-host configuration."
commit_replacement "$hook_candidate" "$renew_hook" hook ||
  die "Could not activate the certificate renewal hook."
commit_replacement "$link_candidate" "$tls_link" link ||
  die "Could not activate the HTTPS virtual-host link."
manifest_action verify ||
  die "A committed HTTPS artifact changed before nginx validation."
if ! "$nginx_bin" -t; then
  die "Expanded nginx TLS configuration failed validation."
fi
if fault_requested reload ||
  ! "$systemctl_bin" reload nginx.service; then
  die "nginx reload failed while activating the TLS listener."
fi

# Contract last: plaintext redirects are installed only after a valid TLS
# configuration and enabled link have survived validation and reload.
commit_replacement "$panel_candidate" "$panel_conf" panel ||
  die "Could not activate the HTTPS redirect configuration."
manifest_action verify ||
  die "A committed HTTPS artifact changed before final nginx validation."

if fault_requested signal:HUP; then
  kill -s HUP "$BASHPID"
elif fault_requested signal:INT; then
  kill -s INT "$BASHPID"
elif fault_requested signal:TERM; then
  kill -s TERM "$BASHPID"
fi

if ! "$nginx_bin" -t; then
  die "Final nginx HTTPS configuration failed validation."
fi
if ! "$systemctl_bin" reload nginx.service; then
  die "nginx reload failed while activating the HTTPS redirect."
fi

# systemctl considers the reload job complete once nginx has accepted the
# signal, while an immediately following loopback request can still reach an
# old worker serving the bootstrap 503 configuration.  Let the worker
# generation switch settle before enforcing three consecutive fail-closed
# readiness observations.  Isolated tests use command doubles and need no
# real-worker grace period.
if [[ "$test_mode" == 0 ]]; then
  /usr/bin/sleep 1
fi

probe_redirect_once() {
  local probe_path="/__hy2_https_activation_probe__"
  local expected_location="https://$target:$https_port$probe_path"
  if [[ -n "$redirect_probe_bin" ]]; then
    "$redirect_probe_bin" \
      "$target" "$https_port" "$probe_path" "$expected_location"
    return
  fi
  /usr/bin/python3 -I - \
    "$target" "$probe_path" "$expected_location" <<'PY'
import http.client
import sys

host, path, expected_location = sys.argv[1:]
connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=3)
try:
    connection.request(
        "GET",
        path,
        headers={
            "Host": host,
            "Connection": "close",
            "User-Agent": "hy2-local-readiness/1",
        },
    )
    response = connection.getresponse()
    if response.status != 308:
        raise SystemExit(f"unexpected redirect status: {response.status}")
    if response.getheader("Location") != expected_location:
        raise SystemExit("unexpected redirect location")
finally:
    connection.close()
PY
}

probe_tls_once() {
  if [[ -n "$tls_probe_bin" ]]; then
    "$tls_probe_bin" "$target" "$https_port" "$cert"
    return
  fi
  local -a verify_args=(-verify_hostname "$target" -servername "$target")
  if (( is_ipv4 )); then
    verify_args=(-verify_ip "$target")
  fi
  /usr/bin/timeout 6 "$openssl_bin" s_client \
    -connect "127.0.0.1:$https_port" \
    -brief \
    -verify_return_error \
    "${verify_args[@]}" \
    </dev/null >/dev/null 2>&1
}

if fault_requested redirect-probe; then
  die "Local HTTP-to-HTTPS redirect probe failed."
fi
for redirect_probe_index in 1 2 3; do
  probe_redirect_once ||
    die "Local HTTP-to-HTTPS redirect probe failed at attempt $redirect_probe_index."
done

if fault_requested probe; then
  die "Local TLS stability probe failed."
fi
for probe_index in 1 2 3; do
  probe_tls_once ||
    die "Local TLS stability probe failed at attempt $probe_index."
done

manifest_action verify ||
  die "A committed HTTPS artifact changed during readiness probing."
write_renewal_marker "$txid" ||
  die "Could not durably journal certificate renewal activation."
if fault_requested renewal-journal; then
  die "Injected failure after certificate renewal journal creation."
fi
manifest_action state complete
transaction_complete=1
transaction_active=0
trap - EXIT HUP INT TERM
if ! cleanup_candidates || ! cleanup_pending_directory; then
  # The manifest is already durably marked complete; startup will only remove
  # residue and will never roll a successful activation back.
  printf '[!] HTTPS is active, but transaction residue could not be removed from %s\n' \
    "$pending_dir" >&2
fi

# Enabling the system timer mutates state outside the nginx artifact
# transaction.  A root-only, fsynced marker is therefore written before the
# TLS transaction becomes complete and removed only after the timer is active.
# Boot recovery retries a marker left by SIGKILL or power loss.  A live failure
# returns an explicit degraded status while truthfully leaving the probed HTTPS
# service active.  Callers must treat exit 2 as committed-but-degraded.
timer_degraded=0
if fault_requested kill-before-timer; then
  kill -s KILL "$BASHPID"
fi
if fault_requested timer || ! reconcile_renewal_marker; then
  timer_degraded=1
fi

# Everything below is post-commit.  Broken stdout or an unavailable expiry
# formatter must never turn a successful TLS transaction into an apparent
# rollback.
trap '' PIPE
set +e
printf 'HTTPS enabled for https://%s:%s/admin\n' "$target" "$https_port"
if (( timer_degraded == 1 )); then
  printf '[!] HTTPS is active, but certificate renewal is degraded; the timer is not active.\n' >&2
fi
expiry_line="$("$openssl_bin" x509 -enddate -noout -in "$cert" 2>/dev/null)"
if [[ "$expiry_line" == notAfter=* ]]; then
  printf 'Certificate expires: %s\n' "${expiry_line#notAfter=}"
else
  printf '[!] HTTPS is active, but certificate expiry could not be displayed.\n' >&2
fi
if (( timer_degraded == 1 )); then
  exit 2
fi
exit 0
