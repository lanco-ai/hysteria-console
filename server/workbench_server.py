"""Workbench + AI service entrypoint on loopback :8085.

今日计划/行程/日记, AI 对话与视频, 服务中心 and 开源发现.  Admin sessions are
checked by the network service (``RemoteSessionServices``).  The AI video and
GitHub trending schedulers only run with ``WORKBENCH_SCHEDULERS=1`` and
LANCOAI_STANDBY=0. Standby serves health/assets without constructing business routes.
"""

import os
import re
import stat
from pathlib import Path

from standby import create_standby_app, standby_enabled
from web_api import create_app
from web_api.ai.compat import ChatSettingsAdapter, VideoSettingsAdapter
from web_api.ai.service_store import AIServiceStore
from web_api.services import NETWORK_SESSION_URL, RemoteSessionServices

RUNTIME_ROOT = Path(__file__).resolve().parent
_REACT_DIST_CANDIDATES = (
    RUNTIME_ROOT / 'frontend' / 'dist',
    RUNTIME_ROOT.parent / 'frontend' / 'dist',
)
_MANAGED_TARGET = re.compile(r'releases/([0-9a-f]{24})\Z')


def _validated_dist(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f'React dist is missing: {path}') from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f'React dist is not a regular directory: {path}')
    for required in ('index.html', 'manifest.json', 'assets'):
        item = path / required
        try:
            item_metadata = item.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(f'React dist is missing {required}: {path}') from exc
        if (
            item.is_symlink()
            or (
                required in ('index.html', 'manifest.json')
                and not stat.S_ISREG(item_metadata.st_mode)
            )
            or (required == 'assets' and not stat.S_ISDIR(item_metadata.st_mode))
        ):
            raise RuntimeError(f'React dist has an unsafe {required} entry: {path}')
    return path


def resolve_react_dist(runtime_root: Path | str | None = None) -> Path:
    """Select the managed release, or a validated source dist for preview."""

    root = Path(runtime_root) if runtime_root is not None else RUNTIME_ROOT
    pointer = root / 'panel' / 'current'
    if pointer.exists() or pointer.is_symlink():
        try:
            metadata = pointer.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError('React release pointer disappeared') from exc
        if not pointer.is_symlink() or not stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError('React release pointer is not a symlink')
        target = os.readlink(pointer)
        match = _MANAGED_TARGET.fullmatch(target)
        if match is None:
            raise RuntimeError('React release pointer is outside the managed release root')
        release_root = (pointer.parent / 'releases').resolve()
        release = (pointer.parent / target).resolve()
        if release.parent != release_root or release.name != match.group(1):
            raise RuntimeError('React release pointer is outside the managed release root')
        return _validated_dist(release)

    for candidate in (
        _REACT_DIST_CANDIDATES
        if runtime_root is None
        else (
            root / 'frontend' / 'dist',
            root.parent / 'frontend' / 'dist',
        )
    ):
        try:
            return _validated_dist(candidate)
        except RuntimeError:
            continue
    raise RuntimeError('No validated React dist is available')


REACT_DIST = resolve_react_dist()


def schedulers_enabled(environ=None) -> bool:
    return (environ if environ is not None else os.environ).get('WORKBENCH_SCHEDULERS', '0') == '1'


def build_app(environ=None):
    environ = environ if environ is not None else os.environ
    if standby_enabled(environ, 'WORKBENCH_SCHEDULERS'):
        return create_standby_app(REACT_DIST, '/static/workbench')
    enabled = schedulers_enabled(environ)
    ai_services = AIServiceStore()
    return create_app(
        RemoteSessionServices(environ.get('LANCOAI_SESSION_URL', NETWORK_SESSION_URL)),
        react_dist=REACT_DIST,
        video_scheduler_enabled=enabled,
        github_trending_scheduler_enabled=enabled,
        chat_settings_store=ChatSettingsAdapter(ai_services),
        video_settings_store=VideoSettingsAdapter(ai_services),
        ai_services_store=ai_services,
    )


app = build_app()
