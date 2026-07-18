import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/hy2-preflight.sh'


def _write_command(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(
        '#!/bin/sh\n'
        'set -eu\n'
        f'{body}\n',
        encoding='utf-8',
    )
    path.chmod(0o755)


def _stubbed_environment(tmp_path, *, bash_status=0, git_diff_status=0):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    command_log = tmp_path / 'commands.log'

    _write_command(
        bin_dir,
        'python3',
        r'''
printf 'python3' >>"$COMMAND_LOG"
for arg in "$@"; do printf ' <%s>' "$arg" >>"$COMMAND_LOG"; done
printf '\n' >>"$COMMAND_LOG"
if [ "${1-}" = "-" ]; then
  cat >/dev/null
fi
exit 0
'''.strip(),
    )
    _write_command(
        bin_dir,
        'bash',
        f'''
printf 'bash' >>"$COMMAND_LOG"
for arg in "$@"; do printf ' <%s>' "$arg" >>"$COMMAND_LOG"; done
printf '\\n' >>"$COMMAND_LOG"
exit {bash_status}
'''.strip(),
    )
    _write_command(
        bin_dir,
        'node',
        r'''
printf 'node' >>"$COMMAND_LOG"
for arg in "$@"; do printf ' <%s>' "$arg" >>"$COMMAND_LOG"; done
printf '\n' >>"$COMMAND_LOG"
exit 0
'''.strip(),
    )
    _write_command(
        bin_dir,
        'git',
        f'''
printf 'git' >>"$COMMAND_LOG"
for arg in "$@"; do printf ' <%s>' "$arg" >>"$COMMAND_LOG"; done
printf '\\n' >>"$COMMAND_LOG"
case " $* " in
  *" rev-parse --is-inside-work-tree "*)
    printf 'true\\n'
    exit 0
    ;;
  *" diff --check "*)
    if [ "{git_diff_status}" -ne 0 ]; then
      printf 'TOP_SECRET_FROM_DIFF\\n'
      printf 'TOP_SECRET_FROM_DIFF_STDERR\\n' >&2
    fi
    exit {git_diff_status}
    ;;
esac
exit 0
'''.strip(),
    )

    env = os.environ.copy()
    env['PATH'] = f'{bin_dir}:/usr/bin:/bin'
    env['COMMAND_LOG'] = str(command_log)
    return env, command_log


def test_preflight_is_local_and_read_only_by_construction():
    content = SCRIPT.read_text(encoding='utf-8')

    assert '/root/hysteria' not in content
    assert '/etc/' not in content
    assert 'server.key' not in content
    assert 'api_secret' not in content
    assert 'systemctl' not in content
    assert 'install -' not in content
    assert 'PYTHONDONTWRITEBYTECODE=1' in content
    assert 'compile(source' in content
    assert 'bash -n -- "$file"' in content
    assert 'node --check "$file"' in content
    assert 'python3 -m pytest -q -p no:cacheprovider' in content
    assert 'git -C "$REPO_ROOT" diff --check' in content


def test_preflight_runs_all_stages_with_stubbed_commands(tmp_path):
    env, command_log = _stubbed_environment(tmp_path)

    result = subprocess.run(
        ['/bin/bash', str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '[1/5] Python syntax' in result.stdout
    assert '[2/5] Shell syntax' in result.stdout
    assert '[3/5] JavaScript syntax' in result.stdout
    assert '[4/5] Full pytest suite' in result.stdout
    assert '[5/5] Git whitespace errors' in result.stdout
    assert '[PASS] Local preflight completed' in result.stdout

    calls = command_log.read_text(encoding='utf-8')
    assert 'python3 <->' in calls
    assert 'python3 <-m> <pytest> <-q> <-p> <no:cacheprovider>' in calls
    assert 'bash <-n> <-->' in calls
    assert 'node <--check>' in calls
    assert 'git <-C>' in calls
    assert '<diff> <--check>' in calls


def test_preflight_stops_at_first_failed_stage(tmp_path):
    env, command_log = _stubbed_environment(tmp_path, bash_status=12)

    result = subprocess.run(
        ['/bin/bash', str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert '[FAIL] Shell syntax failed' in result.stderr
    calls = command_log.read_text(encoding='utf-8')
    assert 'bash <-n> <-->' in calls
    assert '<pytest>' not in calls
    assert 'node <--check>' not in calls


def test_preflight_redacts_git_diff_output_on_failure(tmp_path):
    env, _ = _stubbed_environment(tmp_path, git_diff_status=3)

    result = subprocess.run(
        ['/bin/bash', str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert '[FAIL] git diff --check found whitespace errors' in result.stderr
    assert 'TOP_SECRET_FROM_DIFF' not in result.stdout
    assert 'TOP_SECRET_FROM_DIFF' not in result.stderr
