#!/usr/bin/env python3
"""Build a hash-pinned, pip-free stats venv in a new task-owned staging directory.

This helper never installs into the deployed application. A reviewed release
must separately place the prepared venv at /root/hysteria/.venv-tuic-stats.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

WHEEL_HASH = '176d60a5168d7948539def20b2a3adcce67d72454d9ae05969a2e73f3a0feee7'
WHEEL_BYTES = 6180664
UNPACKED_BYTES = 15251027
MAX_STAGE_BYTES = 24 * 1024 * 1024
ROOT_RESERVE = 256 * 1024 * 1024
REQUIREMENTS = Path(__file__).resolve().parents[2] / 'requirements-tuic-stats.txt'


def command(argv):
    subprocess.run(argv, check=True, timeout=120, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def stage(args):
    output = Path(args.output)
    if (not output.is_absolute() or output.parent not in (Path('/tmp'), Path('/dev/shm'))
            or not output.name.startswith('tuic-stats-stage-') or output.exists()
            or output.is_symlink()):
        raise ValueError('require a new /tmp or /dev/shm/tuic-stats-stage-* directory')
    python, pip_python = Path(args.python), Path(args.pip_python)
    if not python.is_absolute() or not pip_python.is_absolute():
        raise ValueError('require absolute trusted Python paths')
    target = subprocess.run([str(python), '-I', '-B', '-c',
        "import json,platform,sys; print(json.dumps([sys.implementation.name,"
        "list(sys.version_info[:2]),sys.platform,platform.machine()]))"],
        check=True, capture_output=True, timeout=5, text=True)
    if json.loads(target.stdout) != ['cpython', [3, 12], 'linux', 'x86_64']:
        raise ValueError('the reviewed wheel requires CPython3.12 Linux amd64')
    free = shutil.disk_usage(output.parent).free
    reserve = 0 if output.parent == Path('/dev/shm') else ROOT_RESERVE
    if free < MAX_STAGE_BYTES + reserve:
        raise ValueError('insufficient storage reserve for dependency staging')
    output.mkdir(mode=0o700)
    wheels = output / 'wheels'
    wheels.mkdir(mode=0o700)
    pip = [str(pip_python), '-I', '-B', '-m', 'pip']
    download = pip + ['download', '--disable-pip-version-check', '--no-cache-dir',
        '--no-deps', '--only-binary=:all:', '--require-hashes', '--dest', str(wheels),
        '-r', str(REQUIREMENTS)]
    if args.wheel_dir:
        download.extend(['--no-index', '--find-links', args.wheel_dir])
    command(download)
    artifacts = list(wheels.iterdir())
    if len(artifacts) != 1 or artifacts[0].suffix != '.whl':
        raise ValueError('unexpected dependency artifacts')
    wheel = artifacts[0]
    if wheel.stat().st_size != WHEEL_BYTES:
        raise ValueError('unexpected wheel size')
    with wheel.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != WHEEL_HASH:
            raise ValueError('wheel checksum mismatch')
    with zipfile.ZipFile(wheel) as archive:
        if sum(info.file_size for info in archive.infolist()) != UNPACKED_BYTES:
            raise ValueError('unexpected unpacked wheel size')
    venv = output / 'venv'
    command([str(python), '-I', '-B', '-m', 'venv', '--without-pip', str(venv)])
    command(pip + ['--python', str(venv / 'bin/python'), 'install',
        '--disable-pip-version-check', '--no-cache-dir', '--no-deps',
        '--only-binary=:all:', '--require-hashes', '--no-compile', '--no-index',
        '--find-links', str(wheels), '-r', str(REQUIREMENTS)])
    command([str(venv / 'bin/python'), '-I', '-B', '-c',
             "import grpc; assert grpc.__version__ == '1.74.0'"])
    total = sum(path.lstat().st_size for path in output.rglob('*') if path.is_file()
                and not path.is_symlink())
    if total > MAX_STAGE_BYTES:
        raise ValueError('dependency staging exceeded its size cap')
    return {'status': 'PREPARED', 'venv': str(venv), 'wheel_sha256': WHEEL_HASH,
            'staging_bytes': total, 'max_staging_bytes': MAX_STAGE_BYTES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--python', default='/usr/bin/python3')
    parser.add_argument('--pip-python', default='/usr/bin/python3')
    parser.add_argument('--wheel-dir', help='optional trusted local wheel directory')
    args = parser.parse_args()
    try:
        result = stage(args)
    except Exception:
        print('TUIC stats dependency staging failed; retain task directory for diagnosis',
              file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
