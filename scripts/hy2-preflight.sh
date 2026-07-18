#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

# Keep validation from leaving interpreter or pytest caches in the worktree.
export PYTHONDONTWRITEBYTECODE=1
export GIT_OPTIONAL_LOCKS=0

stage() {
  printf '\n[%s/5] %s\n' "$1" "$2"
}

ok() {
  printf '[OK] %s\n' "$1"
}

skip() {
  printf '[SKIP] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3 is required"

stage 1 "Python syntax"
if ! python3 - "$REPO_ROOT" <<'PY'
import os
import sys
import tokenize
from pathlib import Path

root = Path(sys.argv[1])
ignored_dirs = {
    '.git',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    '.tox',
    '.venv',
    '__pycache__',
    'node_modules',
    'venv',
}
checked = 0

for directory, dirnames, filenames in os.walk(root, followlinks=False):
    current = Path(directory)
    dirnames[:] = [
        name
        for name in dirnames
        if name not in ignored_dirs and not (current / name).is_symlink()
    ]
    for name in sorted(filenames):
        if not name.endswith('.py'):
            continue
        path = current / name
        if path.is_symlink():
            continue
        relative = path.relative_to(root)
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            compile(source, str(relative), 'exec', dont_inherit=True)
        except Exception as exc:
            print(
                f'Python syntax failed ({relative}): '
                f'{exc.__class__.__name__}: {exc}',
                file=sys.stderr,
            )
            raise SystemExit(1)
        checked += 1

if checked == 0:
    print('Python syntax failed: no Python files found', file=sys.stderr)
    raise SystemExit(1)

print(f'[OK] Python syntax: {checked} file(s)')
PY
then
  fail "Python syntax checks failed"
fi

stage 2 "Shell syntax"
mapfile -d '' -t shell_files < <(
  find "$REPO_ROOT" \
    \( \
      -path "$REPO_ROOT/.git" -o \
      -path "$REPO_ROOT/.venv" -o \
      -path "$REPO_ROOT/venv" -o \
      -path "$REPO_ROOT/node_modules" \
    \) -prune -o \
    -type f -name '*.sh' -print0
)
if ((${#shell_files[@]} == 0)); then
  fail "Shell syntax checks found no .sh files"
fi
for file in "${shell_files[@]}"; do
  if ! bash -n -- "$file" >/dev/null 2>&1; then
    fail "Shell syntax failed (${file#"$REPO_ROOT/"}); run bash -n locally for details"
  fi
done
ok "Shell syntax: ${#shell_files[@]} file(s)"

stage 3 "JavaScript syntax"
if command -v node >/dev/null 2>&1; then
  mapfile -d '' -t javascript_files < <(
    find "$REPO_ROOT" \
      \( \
        -path "$REPO_ROOT/.git" -o \
        -path "$REPO_ROOT/.venv" -o \
        -path "$REPO_ROOT/venv" -o \
        -path "$REPO_ROOT/node_modules" \
      \) -prune -o \
      -type f -name '*.js' -print0
  )
  if ((${#javascript_files[@]} == 0)); then
    skip "Node.js is available, but no .js files were found"
  else
    for file in "${javascript_files[@]}"; do
      if ! node --check "$file" >/dev/null 2>&1; then
        fail "JavaScript syntax failed (${file#"$REPO_ROOT/"}); run node --check locally for details"
      fi
    done
    ok "JavaScript syntax: ${#javascript_files[@]} file(s)"
  fi
else
  skip "Node.js is unavailable; JavaScript syntax check was not run"
fi

stage 4 "Full pytest suite"
if ! (
  cd -- "$REPO_ROOT"
  python3 -m pytest -q -p no:cacheprovider
); then
  fail "pytest suite failed"
fi
ok "pytest suite passed"

stage 5 "Git whitespace errors"
if ! command -v git >/dev/null 2>&1; then
  skip "git is unavailable; git diff --check was not run"
elif git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Suppress the diff body: a locally edited line may contain confidential data.
  if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
    fail "git diff --check found whitespace errors"
  fi
  ok "git diff --check passed"
else
  skip "Repository root is not a Git worktree"
fi

printf '\n[PASS] Local preflight completed without touching deployed runtime state.\n'
