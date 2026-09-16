#!/usr/bin/env bash
set -euo pipefail

runner=/usr/local/sbin/hy2-codex-safe-run

working_directory="$($runner /bin/pwd)"
test "$working_directory" = "$PWD"

output="$($runner /bin/sh -eu -c '
  cgroup_path=$(cut -d: -f3 /proc/self/cgroup)
  cgroup_root=/sys/fs/cgroup"$cgroup_path"
  printf "memory=%s\n" "$(cat "$cgroup_root/memory.max")"
  printf "swap=%s\n" "$(cat "$cgroup_root/memory.swap.max")"
  printf "pids=%s\n" "$(cat "$cgroup_root/pids.max")"
  printf "cpu=%s\n" "$(cat "$cgroup_root/cpu.max")"
')"

grep -qx 'memory=268435456' <<<"$output"
grep -qx 'swap=268435456' <<<"$output"
grep -qx 'pids=32' <<<"$output"
grep -Eq '^cpu=50000 100000$' <<<"$output"

set +e
disk_output="$(HY2_SAFE_RUN_MIN_DISK_PERCENT=100 "$runner" /bin/true 2>&1)"
disk_status=$?
set -e

test "$disk_status" -eq 75
grep -q 'root disk safety guard' <<<"$disk_output"

set +e
memory_output="$(HY2_SAFE_RUN_MIN_AVAILABLE_KIB=999999999 "$runner" /bin/true 2>&1)"
memory_status=$?
set -e

test "$memory_status" -eq 75
grep -q 'available memory safety guard' <<<"$memory_output"

set +e
pytest_output="$("$runner" /usr/local/bin/pytest tests/first.py tests/second.py 2>&1)"
pytest_status=$?
set -e

test "$pytest_status" -eq 64
grep -q 'exactly one pytest test target' <<<"$pytest_output"

set +e
shell_pytest_output="$("$runner" /bin/sh -c 'pytest tests/first.py tests/second.py' 2>&1)"
shell_pytest_status=$?
set -e

test "$shell_pytest_status" -eq 64
grep -q 'invoke pytest directly' <<<"$shell_pytest_output"

timeout_started="$(date +%s)"
set +e
HY2_SAFE_RUN_TIMEOUT_SECONDS=1 "$runner" /bin/sleep 3 >/dev/null 2>&1
timeout_status=$?
set -e
timeout_elapsed=$(( $(date +%s) - timeout_started ))

test "$timeout_status" -ne 0
test "$timeout_elapsed" -lt 3

HY2_SAFE_RUN_TIMEOUT_SECONDS=2 "$runner" /bin/sleep 2 >/dev/null 2>&1 &
first_runner_pid=$!
/bin/sleep 0.25
set +e
concurrent_output="$("$runner" /bin/true 2>&1)"
concurrent_status=$?
set -e
wait "$first_runner_pid" || true

test "$concurrent_status" -eq 75
grep -q 'another guarded command is already running' <<<"$concurrent_output"
