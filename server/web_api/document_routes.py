"""Exact React document and immutable-asset routes for the workbench + AI service.

Only this service's own pages are served here.  An unknown path is deliberately
left as a 404 instead of becoming an SPA page; nginx sends every other page to
the service that owns it.
"""

import html
import os
import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .services import LoginRequired, StateUnavailable, UserAccessDenied

_BODY_RE = re.compile(r'<body(?:\s+class="[^"]*")?>')
_TITLE_RE = re.compile(r'<title>[^<]*</title>')
_ROOT_RE = re.compile(r'<div id="root" data-public-host="[^"]*"')
_HOST_RE = re.compile(r'[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?')
_SAFE_LOGIN_QUERY_KEYS = frozenset(
    {'msg', 'tab', 'range', 'window', 'page', 'filter', 'conversation'}
)
# Mirrors the network service's PASSWORD_MAX_LENGTH; its API stays authoritative.
LOGIN_PASSWORD_MAX_LENGTH = 256
ASSET_PREFIX = '/static/workbench/assets'

REACT_DOCUMENTS = {
    # 开源发现 is a public portal page; its login button needs the password limit.
    '/trending': ('GitHub 热榜 · LancoAI', 'page-portal page-workbench', None),
    '/admin/plans': ('今日计划', 'has-shell', 'admin'),
    '/admin/chat': ('AI 对话', 'has-shell page-workbench', 'admin'),
    '/admin/video': ('AI 视频', 'has-shell', 'admin'),
    '/admin/services': ('服务中心', 'has-shell', 'admin'),
}
LOGIN_DOCUMENTS = frozenset({'/trending'})


def _public_host(request: Request) -> str:
    configured = os.environ.get('LANCOAI_PUBLIC_HOST', '').strip()
    raw = configured or request.headers.get('host', '127.0.0.1')
    return raw if _HOST_RE.fullmatch(raw or '') else '127.0.0.1'


def _login_location(request: Request) -> str:
    query = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in _SAFE_LOGIN_QUERY_KEYS
    ]
    target = request.url.path
    encoded = urlencode(query, doseq=True)
    if encoded:
        target += f'?{encoded}'
    return '/login?' + urlencode({'next': target})


async def _guard(request: Request, services, dispatch, guard):
    if guard is None:
        return None
    if guard != 'admin':
        raise ValueError('invalid React document guard')
    try:
        payload = await dispatch(services.read_session, request)
        if not isinstance(payload, dict) or payload.get('role') != 'admin':
            return RedirectResponse(_login_location(request), status_code=303)
    except (LoginRequired, UserAccessDenied):
        return RedirectResponse(_login_location(request), status_code=303)
    except StateUnavailable:
        return HTMLResponse('服务状态暂不可用，请稍后重试。', status_code=503)
    return None


def _render_document(
    template: str,
    *,
    title: str,
    body_class: str,
    public_host: str,
    password_max: int | None,
):
    title_match = _TITLE_RE.search(template)
    body_match = _BODY_RE.search(template)
    root_match = _ROOT_RE.search(template)
    if not title_match or not body_match or not root_match:
        raise RuntimeError('React document bootstrap markers are missing')
    if (
        len(_TITLE_RE.findall(template)) != 1
        or len(_BODY_RE.findall(template)) != 1
        or len(_ROOT_RE.findall(template)) != 1
    ):
        raise RuntimeError('React document bootstrap markers are ambiguous')
    payload = (
        template[: title_match.start()]
        + f'<title>{html.escape(title)}</title>'
        + template[title_match.end() :]
    )
    payload = _BODY_RE.sub(
        f'<body class="{html.escape(body_class, quote=True)}">', payload, count=1
    )
    escaped_host = html.escape(public_host, quote=True)
    payload = _ROOT_RE.sub(f'<div id="root" data-public-host="{escaped_host}"', payload, count=1)
    if password_max is not None:
        marker = f'data-public-host="{escaped_host}"'
        if payload.count(marker) != 1:
            raise RuntimeError('React login bootstrap marker is missing or ambiguous')
        payload = payload.replace(
            marker,
            marker + f' data-password-max-length="{int(password_max)}"',
            1,
        )
    return payload


def register_react_document_routes(app, services, dispatch, react_dist):
    """Register this service's exact React documents and assets from ``react_dist``."""

    dist = Path(react_dist).resolve()
    template_path = dist / 'index.html'
    if not dist.is_dir() or not template_path.is_file():
        raise ValueError('react_dist must contain index.html')
    template = template_path.read_text(encoding='utf-8')
    router = APIRouter()

    async def document(request: Request, *, path: str):
        title, body_class, guard = REACT_DOCUMENTS[path]
        denied = await _guard(request, services, dispatch, guard)
        if denied is not None:
            return denied
        body = _render_document(
            template,
            title=title,
            body_class=body_class,
            public_host=_public_host(request),
            password_max=LOGIN_PASSWORD_MAX_LENGTH if path in LOGIN_DOCUMENTS else None,
        )
        return HTMLResponse(body)

    def endpoint_for(path):
        async def endpoint(request: Request):
            return await document(request, path=path)

        return endpoint

    for path in REACT_DOCUMENTS:
        router.add_api_route(
            path,
            endpoint_for(path),
            methods=['GET', 'HEAD'],
            include_in_schema=False,
        )

    app.include_router(router)
    assets = dist / 'assets'
    if not assets.is_dir():
        raise ValueError('react_dist must contain an assets directory')
    app.mount(
        ASSET_PREFIX,
        StaticFiles(directory=str(assets), html=False),
        name='workbench-assets',
    )
