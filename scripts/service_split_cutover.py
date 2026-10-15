#!/usr/bin/python3 -I
"""lancoai.site service split: preinstall, cutover, rollback, verification.

Every command that changes production runs under the deploy lock:

  /usr/local/sbin/hy2-lock-exec.py --lock-file /run/hy2-locks/deploy.lock --timeout 30 -- \\
      /usr/bin/python3 -I /root/service-split-cutover-20261015/split_cutover.py <command>

Commands
  preinstall        install lancoai-shop (8084) and lancoai-workbench (8085) with their
                    schedulers off, pre-install the network frontend release (inactive),
                    verify both services on loopback and the routed vhost on a private
                    nginx instance (127.0.0.1:19444). Production nginx is untouched.
  cutover           maintenance window: stop the monolith, install the network code and
                    release on 8083, turn the new schedulers on, install the routed vhost
                    (nginx -t, reload), the network health check and nginx templates, then
                    verify. Any failure rolls everything back automatically.
  rollback          undo a completed cutover from its backups.
  rollback-check    read-only: prove the rollback inputs exist and are intact.
  verify            read-only public verification of the split (after cutover).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path('/root/service-split-cutover-20261015')
STAGE = Path('/root/hysteria/backups/service-split-cutover-20261015T075346Z')
WT = Path('/root/hy2/.worktrees')
NETWORK_WT, SHOP_WT, WORKBENCH_WT = WT / 'service-network', WT / 'service-shop', WT / 'service-workbench-ai'
# Exact branch heads are pinned in the receipt directory before deployment.
COMMITS = {WT / name: commit for name, commit in
           json.loads((HERE / 'versions.json').read_text()).items()}

RELEASES = {
    NETWORK_WT: '135e681be2ecfbd92af51fcd',
    SHOP_WT: 'd9b6e99e7a1cc42ac3d6d71d',
    WORKBENCH_WT: 'ee5c96499cd93f128337bc00',
}
MONOLITH_RELEASE = '12a1ea99ed87277140b05fad'
ROOT = Path('/root/hysteria')
PANEL = ROOT / 'panel'
SHOP_PANEL = Path('/opt/lancoai/shop/panel')
WORKBENCH_PANEL = Path('/opt/lancoai/workbench/panel')
sys.path.insert(0, str(NETWORK_WT / 'scripts'))
from service_rollback_guard import quiesce_services  # noqa: E402
RELEASE_TOOL = ['/usr/bin/python3', '-I', str(NETWORK_WT / 'scripts/hy2_panel_release.py')]
VHOST = Path('/etc/nginx/sites-available/hysteria-panel-https.conf')
VHOST_OLD_SHA = '5dfe9e4e9a1c22ce247ca8c9332942112a0bafb80830715e0db63287b19b2838'
VHOST_NEW_SHA = 'e80cffb50ed7e5f8fb1adc1e3d0d6f5a2e0e76b043cef3f82b134f88a0b0b87d'
SHARE = Path('/usr/local/share/hy2')
SHARE_FILES = ('hysteria-panel-https.conf', 'hysteria-panel-react-https.conf', 'hysteria-panel-react.conf')
SCRIPTS = {
    Path('/usr/local/sbin/hy2-health-check.sh'): 'hy2-health-check.sh',
    Path('/usr/local/sbin/hy2-react-cutover.sh'): 'hy2-react-cutover.sh',
}
ENV_FILES = {'shop': Path('/etc/lancoai/shop.env'), 'workbench': Path('/etc/lancoai/workbench.env')}
TOKEN_FILE = Path('/etc/lancoai/internal-token')
UNITS = ('lancoai-shop.service', 'lancoai-workbench.service')
HOST, ADDRESS = 'lancoai.site', '64.83.30.207'
# nginx sees every client behind the 443 stream proxy as 127.0.0.1, so all public
# checks share one 10 r/s bucket with real visitors: stay at about 2 r/s.
PUBLIC_PACE = 0.5
ASSET_PACE = 0.4
NGINX_LIKE = {'Host': HOST, 'X-Forwarded-Proto': 'https', 'X-Forwarded-Port': '443'}
TEST_NGINX_PORT = 19444
CHANGED = [line for line in (STAGE / 'changed.txt').read_text().split() if line]
# learning_sandbox/ was never deployed to the runtime directory.
DELETED = [line for line in (STAGE / 'deleted.txt').read_text().split() if line and not line.startswith('learning_sandbox/')]


class Failure(RuntimeError):
    pass


def log(message):
    stamp = dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{stamp}] {message}', flush=True)


def run(*command, check=True, env=None, cwd=None, timeout=300, pass_fds=()):
    result = subprocess.run(command, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout,
                            pass_fds=pass_fds)
    if check and result.returncode != 0:
        raise Failure(f'{" ".join(map(str, command))} failed ({result.returncode}): {result.stderr.strip()[-800:]}')
    return result


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_lock():
    marker = os.environ.get('HY2_LOCK_EXEC_MARKER', '')
    if not marker:
        raise Failure('run me through hy2-lock-exec.py with the deploy lock')
    run('/usr/local/sbin/hy2-lock-exec.py', '--lock-file', '/run/hy2-locks/deploy.lock', '--verify',
        pass_fds=(int(marker.split(':')[1]),))


def curl(url, *, method='GET', headers=None, data=None, resolve=None, timeout=15):
    """Return (status, location, headers, body) without following redirects."""
    with tempfile.TemporaryDirectory() as tmp:
        body_file, header_file = Path(tmp) / 'body', Path(tmp) / 'headers'
        command = ['curl', '-sS', '--noproxy', '*', '--max-time', str(timeout), '-X', method,
                   '-o', str(body_file), '-D', str(header_file), '-w', '%{http_code}']
        if resolve:
            command += ['--resolve', resolve]
        for key, value in (headers or {}).items():
            command += ['-H', f'{key}: {value}']
        if data is not None:
            command += ['--data-binary', '@-']
        result = subprocess.run(command + [url], input=data, capture_output=True, timeout=timeout + 5)
        if result.returncode != 0:
            raise Failure(f'curl {url} failed: {result.stderr.decode(errors="replace").strip()}')
        status = int(result.stdout.decode().strip() or 0)
        header_text = header_file.read_text(errors='replace') if header_file.exists() else ''
        location = ''
        for line in header_text.splitlines():
            if line.lower().startswith('location:'):
                location = line.split(':', 1)[1].strip()
        return status, location, header_text, body_file.read_bytes() if body_file.exists() else b''


def public(path, **kwargs):
    time.sleep(PUBLIC_PACE)
    return public_raw(path, **kwargs)


def public_raw(path, **kwargs):
    return curl(f'https://{HOST}{path}', resolve=f'{HOST}:443:{ADDRESS}', **kwargs)


def loopback(port, path, **kwargs):
    headers = dict(NGINX_LIKE)
    headers.update(kwargs.pop('headers', {}))
    return curl(f'http://127.0.0.1:{port}{path}', headers=headers, **kwargs)


def expect(label, actual, wanted):
    if actual != wanted:
        raise Failure(f'{label}: expected {wanted!r}, got {actual!r}')
    log(f'ok  {label}: {actual}')


def wait_http(url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, *_ = curl(url, timeout=3)
            if status == 200:
                return
        except Failure:
            pass
        time.sleep(1)
    raise Failure(f'{url} did not answer 200 within {timeout}s')


def is_active(unit):
    return run('systemctl', 'is-active', unit, check=False).stdout.strip() == 'active'


def current_release(panel):
    target = os.readlink(panel / 'current')
    return target.removeprefix('releases/')


def disk_headroom_mib():
    usage = shutil.disk_usage('/')
    usable = usage.used + usage.free
    return (int(usable * 0.85) - usage.used) / 1048576


def hysteria_online():
    try:
        return _hysteria_online()
    except Exception as error:  # informational only
        return f'unavailable: {type(error).__name__}'


def _hysteria_online():
    secret = re.search(r'^\s*secret:\s*(\S+)', (ROOT / 'config.yaml').read_text(), re.M).group(1)
    import urllib.request

    request = urllib.request.Request('http://127.0.0.1:25413/online', headers={'Authorization': secret})
    with urllib.request.urlopen(request, timeout=3) as response:
        return len(json.load(response))


def dist_assets(worktree):
    return sorted((worktree / 'frontend/dist/assets').iterdir())


def check_assets(fetch, worktree, prefix, pace):
    count = 0
    for asset in dist_assets(worktree):
        time.sleep(pace)
        status, _, _, body = fetch(f'{prefix}{asset.name}')
        if status != 200 or hashlib.sha256(body).digest() != hashlib.sha256(asset.read_bytes()).digest():
            raise Failure(f'asset {prefix}{asset.name}: status {status} or content mismatch')
        count += 1
    log(f'ok  {count} assets under {prefix} byte-identical to the release')
    return count


def document_assets(body, prefix):
    return sorted(set(re.findall(re.escape(prefix) + r'assets/[^"\']+', body.decode(errors='replace'))))


def strip_counters(value):
    """The storefront's view and sold counters may move between two reads."""
    if isinstance(value, dict):
        return {k: strip_counters(v) for k, v in value.items() if k not in ('views', 'sold')}
    if isinstance(value, list):
        return [strip_counters(v) for v in value]
    return value


def write_receipt(name, payload):
    path = HERE / f'receipt-{name}.json'
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')
    log(f'receipt written: {path}')


# ---------------------------------------------------------------- preinstall


def preinstall_preconditions():
    expect('monolith active', is_active('hysteria-react.service'), True)
    expect('monolith release', current_release(PANEL), MONOLITH_RELEASE)
    for worktree, commit in COMMITS.items():
        expect(f'{worktree.name} commit', run('git', '-C', str(worktree), 'rev-parse', 'HEAD').stdout.strip(), commit)
        expect(f'{worktree.name} clean', run('git', '-C', str(worktree), 'status', '--porcelain').stdout, '')
        manifest = json.loads(run(*RELEASE_TOOL, 'validate', str(worktree / 'frontend/dist')).stdout)
        expect(f'{worktree.name} dist release id', manifest['release_id'], RELEASES[worktree])
    for path in (Path('/opt/lancoai'), Path('/etc/lancoai'), *(Path('/etc/systemd/system') / unit for unit in UNITS)):
        expect(f'{path} absent before preinstall', path.exists(), False)
    listening = run('ss', '-ltnH').stdout
    for port in (8084, 8085, TEST_NGINX_PORT):
        expect(f'port {port} free', f'127.0.0.1:{port} ' in listening, False)
    if disk_headroom_mib() < 40:
        raise Failure(f'only {disk_headroom_mib():.0f} MiB left before the 15% free health threshold')


def install_service(worktree, flag=None):
    command = ['bash', 'deploy/install.sh'] + ([flag] if flag else [])
    env = dict(os.environ, LANCOAI_DEPLOY_LOCKED='1')
    result = run(*command, cwd=worktree, env=env, timeout=180)
    log(f'{worktree.name}: {result.stdout.strip().splitlines()[-1]}')
    return result.stdout


def same_as_monolith(label, port, path, attempts=3):
    """Both processes read the same state; a refresh may land between two reads."""
    for attempt in range(attempts):
        status, _, _, mine = loopback(port, path)
        expect(f'{label} on {port}', status, 200)
        status, _, _, theirs = loopback(8083, path)
        expect(f'{label} on the monolith', status, 200)
        mine, theirs = json.loads(mine), json.loads(theirs)
        # updated_at is the response time of the catalog, not data.
        if isinstance(mine, dict) and isinstance(theirs, dict):
            mine.pop('updated_at', None)
            theirs.pop('updated_at', None)
        if mine == theirs or strip_counters(mine) == strip_counters(theirs):
            log(f'ok  {label} on {port} equals the monolith')
            return mine
        time.sleep(3)
    raise Failure(f'{label} on {port} differs from the monolith after {attempts} reads')


def verify_standby(port, worktree, prefix):
    status, _, _, body = loopback(port, '/healthz')
    expect(f'{port} health', (status, json.loads(body).get('standby')), (200, True))
    check_assets(lambda p: loopback(port, p), worktree, prefix + 'assets/', 0)
    for path in ('/', '/api/v1/shop/catalog', '/api/v1/github-trending', '/api/video/runs/probe'):
        expect(f'{port} standby blocks {path}', loopback(port, path)[0], 503)
    for path in ('/api/chat/completions', '/internal/v1/agent-intent'):
        expect(f'{port} standby blocks writes {path}',
               loopback(port, path, method='POST', data=b'{}')[0], 503)


def verify_shop_loopback():
    verify_standby(8084, SHOP_WT, '/static/shop/')
    return {'standby': True}


def verify_workbench_loopback():
    verify_standby(8085, WORKBENCH_WT, '/static/workbench/')
    metadata = TOKEN_FILE.stat()
    expect('internal token permissions',
           (metadata.st_mode & 0o777, metadata.st_uid, metadata.st_gid), (0o640, 0, 992))



ROUTE_SAMPLES = {
    '127.0.0.1:8084': ['/', '/?view=sms', '/admin/shop', '/api/v1/shop/catalog', '/api/v1/shop/admin', '/api/v1/shop/refresh'],
    '127.0.0.1:8085': ['/trending', '/admin/plans', '/admin/chat', '/admin/video', '/admin/services', '/api/plans',
                       '/api/plans/reminders', '/api/journal', '/api/chat/settings', '/api/chat/completions',
                       '/api/chat/documents/upload', '/api/video/settings', '/api/ai/services', '/api/v1/admin/services',
                       '/api/v1/github-trending'],
    '127.0.0.1:8083': ['/login', '/user/login', '/user/panel', '/admin', '/admin/usage', '/admin/config', '/admin/health',
                       '/admin/settings', '/admin/github-trending', '/api/v1/session', '/api/v1/admin/rules', '/readyz',
                       '/sub/cutover-probe', '/panel/cutover-probe'],
    '-': ['/internal/v1/agent-intent'],
}


def rehearse_routing():
    """Serve the routed vhost from a private nginx on 127.0.0.1:19444 and check each upstream."""
    with tempfile.TemporaryDirectory(prefix='split-nginx-') as tmp:
        tmp = Path(tmp)
        tmp.chmod(0o755)
        vhost = (STAGE / 'nginx/new-https.conf').read_text()
        vhost = vhost.replace('listen 9444 ssl;', f'listen 127.0.0.1:{TEST_NGINX_PORT} ssl;').replace('    listen [::]:9444 ssl;\n', '')
        vhost = vhost.replace('access_log /var/log/nginx/hysteria-panel.access.log hy2_no_args;', f'access_log {tmp}/access.log split;')
        vhost = vhost.replace('error_log /var/log/nginx/hysteria-panel.error.log crit;', f'error_log {tmp}/error.log warn;')
        (tmp / 'vhost.conf').write_text(vhost)
        (tmp / 'nginx.conf').write_text(f'''user www-data;
worker_processes 1;
pid {tmp}/nginx.pid;
error_log {tmp}/main-error.log warn;
events {{ worker_connections 64; }}
http {{
    include /etc/nginx/mime.types;
    client_body_temp_path {tmp}/body;
    proxy_temp_path {tmp}/proxy;
    fastcgi_temp_path {tmp}/fastcgi;
    uwsgi_temp_path {tmp}/uwsgi;
    scgi_temp_path {tmp}/scgi;
    log_format split '$request_uri $status $upstream_addr';
    include /etc/nginx/conf.d/hysteria-panel-log.conf;
    include {tmp}/vhost.conf;
}}
''')
        run('nginx', '-t', '-c', str(tmp / 'nginx.conf'))
        run('nginx', '-c', str(tmp / 'nginx.conf'))
        try:
            time.sleep(1)
            expected = []
            for upstream, paths in ROUTE_SAMPLES.items():
                for path in paths:
                    curl(f'https://{HOST}:{TEST_NGINX_PORT}{path}', resolve=f'{HOST}:{TEST_NGINX_PORT}:127.0.0.1')
                    expected.append((path, upstream))
                    time.sleep(0.15)
            time.sleep(1)
        finally:
            run('nginx', '-c', str(tmp / 'nginx.conf'), '-s', 'quit', check=False)
            time.sleep(1)
        seen = [line.split() for line in (tmp / 'access.log').read_text().splitlines()]
        actual = [(uri, upstream) for uri, _status, upstream in seen]
        expect('private nginx routed every sample to its service', actual, expected)
        return len(actual)


def preinstall():
    verify_lock()
    preinstall_preconditions()
    receipt = {'command': 'preinstall', 'started': dt.datetime.now().isoformat(), 'disk_headroom_mib_before': round(disk_headroom_mib(), 1)}
    created_network_release = False
    try:
        receipt['shop_install'] = install_service(SHOP_WT).strip().splitlines()[-1]
        if f'release {RELEASES[SHOP_WT]}; scheduler SHOP_SCHEDULER=0' not in receipt['shop_install']:
            raise Failure('shop install did not report the expected release with the scheduler off')
        receipt['workbench_install'] = install_service(WORKBENCH_WT).strip().splitlines()[-1]
        if f'release {RELEASES[WORKBENCH_WT]}; WORKBENCH_SCHEDULERS=0' not in receipt['workbench_install']:
            raise Failure('workbench install did not report the expected release with the schedulers off')
        manifest = json.loads(run(*RELEASE_TOOL, 'install', str(NETWORK_WT / 'frontend/dist'), str(PANEL)).stdout)
        created_network_release = True
        expect('network release installed (inactive)', manifest['release_id'], RELEASES[NETWORK_WT])
        expect('monolith release still active', current_release(PANEL), MONOLITH_RELEASE)
        receipt['shop'] = verify_shop_loopback()
        verify_workbench_loopback()
        receipt['routing_samples'] = rehearse_routing()
        expect('monolith still active', is_active('hysteria-react.service'), True)
        receipt['memory_mib'] = {
            unit: int(run('systemctl', 'show', '-p', 'MemoryCurrent', '--value', unit).stdout.strip() or 0) // 1048576
            for unit in ('hysteria-react.service', *UNITS)
        }
        receipt['disk_headroom_mib_after'] = round(disk_headroom_mib(), 1)
        receipt['finished'] = dt.datetime.now().isoformat()
        write_receipt('preinstall', receipt)
        log('PREINSTALL_OK')
    except Exception as error:
        log(f'PREINSTALL_FAILED: {error}; removing what preinstall created')
        undo_preinstall(created_network_release)
        raise SystemExit(1)


def undo_preinstall(created_network_release):
    quiesce_services(UNITS, run)
    for unit in UNITS:
        (Path('/etc/systemd/system') / unit).unlink(missing_ok=True)
    run('systemctl', 'daemon-reload', check=False)
    shutil.rmtree('/opt/lancoai', ignore_errors=True)
    shutil.rmtree('/etc/lancoai', ignore_errors=True)
    release = PANEL / 'releases' / RELEASES[NETWORK_WT]
    if created_network_release and current_release(PANEL) != RELEASES[NETWORK_WT]:
        shutil.rmtree(release, ignore_errors=True)
    log('preinstall undone')


# ------------------------------------------------------------------- cutover


def cutover_preconditions():
    expect('monolith active', is_active('hysteria-react.service'), True)
    expect('monolith release active', current_release(PANEL), MONOLITH_RELEASE)
    manifest = json.loads(run(*RELEASE_TOOL, 'inspect', str(PANEL), RELEASES[NETWORK_WT]).stdout)
    expect('network release present and valid', manifest['release_id'], RELEASES[NETWORK_WT])
    for name in CHANGED + DELETED:
        if (ROOT / name).read_bytes() != (STAGE / 'base' / name).read_bytes():
            raise Failure(f'live {name} differs from rendered main dd6be5a')
    log(f'ok  {len(CHANGED)} changed and {len(DELETED)} removed modules equal rendered main')
    for name in CHANGED:
        if not (STAGE / 'new' / name).is_file():
            raise Failure(f'rendered network module missing: {name}')
    expect('live vhost is the pre-split vhost', sha256(VHOST), VHOST_OLD_SHA)
    expect('staged routed vhost', sha256(STAGE / 'nginx/new-https.conf'), VHOST_NEW_SHA)
    for live, name in SCRIPTS.items():
        expect(f'live {live.name} is main', live.read_bytes() == (STAGE / 'scripts-base' / name).read_bytes(), True)
    for unit, port in zip(UNITS, (8084, 8085)):
        expect(f'{unit} active', is_active(unit), True)
        expect(f'{unit} healthz', curl(f'http://127.0.0.1:{port}/healthz')[0], 200)
    expect('shop scheduler off', 'SHOP_SCHEDULER=0' in ENV_FILES['shop'].read_text().split(), True)
    expect('workbench schedulers off', 'WORKBENCH_SCHEDULERS=0' in ENV_FILES['workbench'].read_text().split(), True)
    for port, worktree, prefix in ((8084, SHOP_WT, '/static/shop/'), (8085, WORKBENCH_WT, '/static/workbench/')):
        expect(f'{port} still in standby', json.loads(loopback(port, '/healthz')[3]).get('standby'), True)
    expect('internal token present', len(TOKEN_FILE.read_text().strip()) >= 32, True)
    for worktree, commit in COMMITS.items():
        expect(f'{worktree.name} commit', run('git', '-C', str(worktree), 'rev-parse', 'HEAD').stdout.strip(), commit)
        expect(f'{worktree.name} clean', run('git', '-C', str(worktree), 'status', '--porcelain').stdout, '')
        manifest = json.loads(run(*RELEASE_TOOL, 'validate', str(worktree / 'frontend/dist')).stdout)
        expect(f'{worktree.name} dist release id', manifest['release_id'], RELEASES[worktree])
    for panel, worktree in ((SHOP_PANEL, SHOP_WT), (WORKBENCH_PANEL, WORKBENCH_WT)):
        expect(f'{panel} active release', current_release(panel), RELEASES[worktree])
    run('nginx', '-t')
    if disk_headroom_mib() < 25:
        raise Failure(f'only {disk_headroom_mib():.0f} MiB left before the 15% free health threshold')


def take_backups(backup):
    """Copy everything the cutover replaces, with owner, mode and mtime."""
    for name in CHANGED + DELETED:
        target = backup / 'modules' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        os.chown(target, (ROOT / name).stat().st_uid, (ROOT / name).stat().st_gid)
    (backup / 'nginx').mkdir(parents=True, exist_ok=True)
    shutil.copy2(VHOST, backup / 'nginx' / VHOST.name)
    (backup / 'scripts').mkdir(exist_ok=True)
    for live in SCRIPTS:
        shutil.copy2(live, backup / 'scripts' / live.name)
    (backup / 'share').mkdir(exist_ok=True)
    for name in SHARE_FILES:
        shutil.copy2(SHARE / name, backup / 'share' / name)
    (backup / 'env').mkdir(exist_ok=True)
    for name, path in ENV_FILES.items():
        shutil.copy2(path, backup / 'env' / path.name)
    manifest = {
        'monolith_release': MONOLITH_RELEASE,
        'modules': {name: sha256(backup / 'modules' / name) for name in CHANGED + DELETED},
        'vhost': sha256(backup / 'nginx' / VHOST.name),
    }
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rollback_check(backup)


def install_file(target, data, like):
    """Atomically replace target with data, keeping like's owner and mode."""
    staged = target.with_name(f'.{target.name}.split-tmp')
    staged.write_bytes(data)
    os.chown(staged, like.st_uid, like.st_gid)
    os.chmod(staged, like.st_mode & 0o7777)
    os.replace(staged, target)


def swap_network_code():
    for name in CHANGED:
        live = ROOT / name
        install_file(live, (STAGE / 'new' / name).read_bytes(), live.stat())
        if live.read_bytes() != (STAGE / 'new' / name).read_bytes():
            raise Failure(f'{name} did not install')
    for name in DELETED:
        (ROOT / name).unlink()
    # The ai/ package only held removed modules; its __pycache__ may remain.
    log(f'ok  network code installed: {len(CHANGED)} modules replaced, {len(DELETED)} removed')


def install_routed_nginx():
    install_file(VHOST, (STAGE / 'nginx/new-https.conf').read_bytes(), VHOST.stat())
    test = run('nginx', '-t', check=False)
    if test.returncode != 0:
        raise Failure(f'nginx -t rejected the routed vhost: {test.stderr.strip()[-600:]}')
    run('systemctl', 'reload', 'nginx.service')
    time.sleep(2)
    expect('nginx active', is_active('nginx.service'), True)
    expect('routed vhost installed', sha256(VHOST), VHOST_NEW_SHA)


def install_network_scripts():
    for live, name in SCRIPTS.items():
        install_file(live, (STAGE / 'scripts-new' / name).read_bytes(), live.stat())
        run('bash', '-n', str(live))
    for name in SHARE_FILES:
        install_file(SHARE / name, (STAGE / 'share-new' / name).read_bytes(), (SHARE / name).stat())
    log('ok  network health check, react-cutover helper and nginx templates installed')


def verify_public(receipt):
    """The public checks of the cutover checklist (read-only)."""
    status, _, _, body = public('/')
    expect('public / (shop storefront)', status, 200)
    shop_assets = document_assets(body, '/static/shop/')
    expect('home page uses shop assets', bool(shop_assets), True)
    expect('public /?view=sms', public('/?view=sms')[0], 200)
    status, location, *_ = public('/?view=trending')
    expect('old /?view=trending link', (status in (301, 302, 303, 307, 308), location.endswith('/trending')), (True, True))
    status, _, _, body = public('/api/v1/shop/catalog')
    expect('public shop catalog API', status, 200)
    receipt['catalog_products'] = len(json.loads(body).get('products', []))
    status, location, *_ = public('/admin/shop')
    expect('商品管理 page asks for login', (status, '/login?next=' in location), (303, True))
    expect('商品管理 API without session', public('/api/v1/shop/admin')[0], 401)
    status, _, _, body = public('/trending')
    expect('public /trending (开源发现)', status, 200)
    expect('/trending uses workbench assets', bool(document_assets(body, '/static/workbench/')), True)
    expect('trending API', public('/api/v1/github-trending')[0], 200)
    for page in ('/admin/plans', '/admin/chat', '/admin/video', '/admin/services'):
        status, location, *_ = public(page)
        expect(f'{page} asks for login', (status, '/login?next=' in location), (303, True))
    for api in ('/api/plans', '/api/journal', '/api/chat/settings', '/api/v1/admin/services'):
        expect(f'{api} without session', public(api)[0], 401)
    status, _, _, body = public('/login')
    expect('public /login (network)', status, 200)
    expect('/login uses network assets', bool(document_assets(body, '/static/react/')), True)
    for page in ('/admin', '/admin/usage', '/admin/config', '/user/panel'):
        status, location, *_ = public(page)
        expect(f'{page} asks for login', (status, '/login?next=' in location), (303, True))
    status, location, *_ = public('/admin/github-trending')
    expect('old /admin/github-trending link', (status in (301, 302, 303, 307, 308), location.endswith('/trending')), (True, True))
    expect('session API without session', public('/api/v1/session')[0], 401)
    expect('subscription route reaches the network service', public('/sub/cutover-probe')[0], 403)
    expect('/internal/ is not public', public('/internal/v1/agent-intent', method='POST', data=b'{}')[0], 404)
    status, _, _, body = curl('http://127.0.0.1:8083/auth', method='POST', headers={'Content-Type': 'application/json'},
                              data=b'{"addr":"127.0.0.1:1","auth":"cutover-probe-invalid","tx":0}')
    expect('Hysteria auth endpoint rejects an invalid credential', (status, json.loads(body)), (200, {'ok': False}))
    receipt['assets'] = {
        'network': check_assets(lambda p: public_raw(p, timeout=20), NETWORK_WT, '/static/react/assets/', ASSET_PACE),
        'shop': check_assets(lambda p: public_raw(p, timeout=20), SHOP_WT, '/static/shop/assets/', ASSET_PACE),
        'workbench': check_assets(lambda p: public_raw(p, timeout=20), WORKBENCH_WT, '/static/workbench/assets/', ASSET_PACE),
    }
    receipt['hysteria_online'] = hysteria_online()


def run_health_check(receipt):
    result = run('/usr/local/sbin/hy2-health-check.sh', check=False, timeout=120)
    receipt['health_check'] = result.stdout.strip().splitlines() + result.stderr.strip().splitlines()
    if result.returncode != 0:
        raise Failure('hy2-health-check failed: ' + ' | '.join(result.stderr.strip().splitlines()[-6:]))
    for unit in UNITS:
        if not any(unit in line and 'answering /healthz' in line for line in result.stdout.splitlines()):
            raise Failure(f'health check did not check {unit}')
    log('ok  hy2-health-check passed, including the lancoai-* checks')


def cutover():
    verify_lock()
    cutover_preconditions()
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = STAGE / f'backup-{stamp}'
    receipt = {'command': 'cutover', 'backup': str(backup), 'started': dt.datetime.now().isoformat(),
               'hysteria_online_before': hysteria_online()}
    take_backups(backup)
    (HERE / 'latest-backup').write_text(str(backup) + '\n')
    log(f'backups in {backup}')
    changed = False
    try:
        changed = True
        stopped = time.time()
        run('systemctl', 'stop', 'hysteria-react.service')
        log('monolith stopped (Hysteria auth and the panel are down until 8083 is back)')
        swap_network_code()
        run(*RELEASE_TOOL, 'activate', str(PANEL), RELEASES[NETWORK_WT])
        run('systemctl', 'start', 'hysteria-react.service')
        wait_http('http://127.0.0.1:8083/readyz', timeout=90)
        receipt['network_down_seconds'] = round(time.time() - stopped, 1)
        log(f'network service up on 8083 after {receipt["network_down_seconds"]}s')
        expect('network release active', current_release(PANEL), RELEASES[NETWORK_WT])
        expect('8083 no longer serves the storefront', loopback(8083, '/')[0], 404)
        receipt['shop_install'] = install_service(SHOP_WT, '--enable-scheduler').strip().splitlines()[-1]
        expect('shop scheduler on', 'SHOP_SCHEDULER=1' in receipt['shop_install'], True)
        receipt['workbench_install'] = install_service(WORKBENCH_WT, '--enable-schedulers').strip().splitlines()[-1]
        expect('workbench schedulers on', 'WORKBENCH_SCHEDULERS=1' in receipt['workbench_install'], True)
        install_routed_nginx()
        receipt['window_seconds'] = round(time.time() - stopped, 1)
        log(f'routed nginx live {receipt["window_seconds"]}s after the monolith stopped')
        install_network_scripts()
        verify_public(receipt)
        run_health_check(receipt)
        for unit in ('hysteria-react.service', *UNITS):
            expect(f'{unit} active', is_active(unit), True)
        receipt['finished'] = dt.datetime.now().isoformat()
        write_receipt('cutover', receipt)
        log('CUTOVER_OK')
    except Exception as error:
        log(f'CUTOVER_FAILED: {error}')
        receipt['error'] = str(error)
        if changed:
            log('rolling back automatically')
            restore(backup)
            receipt['rolled_back'] = True
        write_receipt('cutover-failed', receipt)
        raise SystemExit(1)


# ------------------------------------------------------------------ rollback


def rollback_check(backup):
    """Read-only proof that the rollback inputs are complete and intact."""
    manifest = json.loads((backup / 'manifest.json').read_text())
    for name, digest in manifest['modules'].items():
        if sha256(backup / 'modules' / name) != digest or (backup / 'modules' / name).read_bytes() != (STAGE / 'base' / name).read_bytes():
            raise Failure(f'backup of {name} is not rendered main')
    expect('backed-up vhost is the pre-split vhost', sha256(backup / 'nginx' / VHOST.name), VHOST_OLD_SHA)
    for live, name in SCRIPTS.items():
        if (backup / 'scripts' / live.name).read_bytes() != (STAGE / 'scripts-base' / name).read_bytes():
            raise Failure(f'backup of {live.name} is not main')
    for name in SHARE_FILES:
        if not (backup / 'share' / name).is_file():
            raise Failure(f'backup of {name} missing')
    manifest_release = json.loads(run(*RELEASE_TOOL, 'inspect', str(PANEL), MONOLITH_RELEASE).stdout)
    expect('monolith release still installed and valid', manifest_release['release_id'], MONOLITH_RELEASE)
    with tempfile.TemporaryDirectory(prefix='split-nginx-old-') as tmp:
        tmp = Path(tmp)
        sites = tmp / 'sites'
        sites.mkdir()
        for enabled in Path('/etc/nginx/sites-enabled').iterdir():
            if enabled.name.startswith('.'):
                continue
            source = backup / 'nginx' / VHOST.name if enabled.name == VHOST.name else enabled.resolve()
            shutil.copy(source, sites / enabled.name)
        config = Path('/etc/nginx/nginx.conf').read_text()
        config = config.replace('include /etc/nginx/sites-enabled/*;', f'include {sites}/*;')
        config = re.sub(r'(?m)^pid .*$', f'pid {tmp}/nginx.pid;', config)
        (tmp / 'nginx.conf').write_text(config)
        run('nginx', '-t', '-c', str(tmp / 'nginx.conf'))
    log(f'ok  rollback inputs intact: {len(manifest["modules"])} modules, vhost, scripts, templates, release')


def restore(backup):
    """Put the monolith back exactly as it was; stop the two new services first."""
    errors = []

    def step(label, function):
        try:
            function()
            log(f'rollback: {label}')
            return True
        except Exception as error:  # keep going: every later step still helps
            errors.append(f'{label}: {error}')
            log(f'rollback: {label} FAILED: {error}')
            return False

    def restore_environments():
        # Environment failures must never bypass process shutdown.
        failures = []
        for name, path in ENV_FILES.items():
            try:
                saved = backup / 'env' / path.name
                install_file(path, saved.read_bytes(), saved.stat())
            except Exception as error:
                failures.append(f'{name}: {type(error).__name__}')
        if failures:
            raise Failure('; '.join(failures))

    def restore_monolith():
        run('systemctl', 'stop', 'hysteria-react.service')
        for name in CHANGED + DELETED:
            saved = backup / 'modules' / name
            live = ROOT / name
            live.parent.mkdir(parents=True, exist_ok=True)
            staged = live.with_name(f'.{live.name}.split-restore')
            shutil.copy2(saved, staged)
            os.chown(staged, saved.stat().st_uid, saved.stat().st_gid)
            os.replace(staged, live)
            if live.read_bytes() != (STAGE / 'base' / name).read_bytes():
                raise Failure(f'{name} did not restore')
        run(*RELEASE_TOOL, 'activate', str(PANEL), MONOLITH_RELEASE)
        run('systemctl', 'start', 'hysteria-react.service')
        wait_http('http://127.0.0.1:8083/readyz', timeout=90)

    def restore_nginx():
        if sha256(VHOST) != VHOST_OLD_SHA:
            install_file(VHOST, (backup / 'nginx' / VHOST.name).read_bytes(), VHOST.stat())
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx.service')
            time.sleep(2)
        expect('pre-split vhost live', sha256(VHOST), VHOST_OLD_SHA)

    def restore_scripts():
        for live in SCRIPTS:
            install_file(live, (backup / 'scripts' / live.name).read_bytes(), live.stat())
        for name in SHARE_FILES:
            install_file(SHARE / name, (backup / 'share' / name).read_bytes(), (SHARE / name).stat())

    if not step('all new services stopped and disabled', lambda: quiesce_services(UNITS, run)):
        log('ROLLBACK_INCOMPLETE: new writers may remain; old monolith was NOT started')
        raise SystemExit(2)
    step('standby environments restored', restore_environments)
    step('monolith modules and release restored, 8083 ready', restore_monolith)
    step('pre-split nginx vhost restored and reloaded', restore_nginx)
    step('health check, helper and templates restored', restore_scripts)
    for label, check in (
        ('monolith serves the storefront again', lambda: expect('8083 /', loopback(8083, '/')[0], 200)),
        ('public / served by the monolith', lambda: expect('public /', document_assets(public('/')[3], '/static/react/') != [], True)),
        ('public catalog', lambda: expect('public catalog', public('/api/v1/shop/catalog')[0], 200)),
        ('public /admin/plans asks for login', lambda: expect('public /admin/plans', public('/admin/plans')[0], 303)),
    ):
        step(label, check)
    if errors:
        log('ROLLBACK_INCOMPLETE: ' + '; '.join(errors))
        raise SystemExit(2)
    log('ROLLBACK_OK')


def latest_backup():
    return Path((HERE / 'latest-backup').read_text().strip())


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if command == 'preinstall':
            preinstall()
        elif command == 'cutover':
            cutover()
        elif command == 'rollback':
            verify_lock()
            backup = latest_backup()
            rollback_check(backup)
            restore(backup)
            write_receipt('rollback', {'command': 'rollback', 'backup': str(backup), 'finished': dt.datetime.now().isoformat()})
        elif command == 'rollback-check':
            rollback_check(latest_backup())
        elif command == 'verify':
            receipt = {'command': 'verify', 'started': dt.datetime.now().isoformat()}
            verify_public(receipt)
            run_health_check(receipt)
            write_receipt('verify', receipt)
            log('VERIFY_OK')
        else:
            print(__doc__)
            raise SystemExit(64)
    except Failure as error:
        log(f'FAILED: {error}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
