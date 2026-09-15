"""Exact React document and immutable-asset routes for the panel boundary.

The API routes remain usable without a frontend build.  Production can opt in
to this router only after installing a verified Vite ``dist`` directory; an
unknown path is deliberately left as a 404 instead of becoming an SPA page.
"""

import html
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .services import LoginRequired, StateUnavailable, UserAccessDenied

_BODY_RE = re.compile(r'<body(?:\s+class="[^"]*")?>')
_TITLE_RE = re.compile(r'<title>[^<]*</title>')
_ROOT_RE = re.compile(r'<div id="root" data-public-host="[^"]*"')

# Keep this list deliberately exact.  Compatibility documents such as
# ``/admin/daily`` and subscription/CSV/evidence downloads stay on their
# established handlers until their own cutover contracts are approved.
REACT_DOCUMENTS = {
    '/': ('Hysteria · 连接网络，掌控全局', 'page-home page-site', None),
    '/login': ('管理员登录 · Hysteria', 'page-auth page-admin-login', None),
    '/logout': ('确认退出', '', 'admin'),
    '/user/logout': ('确认退出', '', 'user'),
    '/user/change-password': ('修改面板密码', 'page-auth', 'user-password'),
    '/user/panel': ('用户面板 · Hysteria', '', 'user'),
    '/admin': ('总览', 'has-shell', 'admin'),
    '/admin/logs': ('清零日志', 'has-shell', 'admin'),
    '/admin/settings': ('设置', 'has-shell', 'admin'),
    '/admin/usage': ('流量分析', 'has-shell', 'admin'),
    '/admin/health': ('健康状态', 'has-shell', 'admin'),
    '/admin/incidents': ('事故处理', 'has-shell', 'admin'),
    '/admin/config': ('模板配置', 'has-shell', 'admin'),
    '/admin/rules': ('路由规则', 'has-shell', 'admin'),
    '/admin/landing-egresses': ('家宽出口', 'has-shell', 'admin'),
}


def _public_host(request: Request, services) -> str:
    raw = request.headers.get('host', '127.0.0.1')
    service = getattr(services, 'service_module', None)
    configured = getattr(service, 'configured_public_host', None)
    if callable(configured):
        try:
            raw = configured(raw)
        except Exception:
            # The host is only bootstrap metadata; a bad configuration must
            # not prevent the document from returning or reveal an exception.
            pass
    return str(raw or '127.0.0.1')


async def _guard(request: Request, services, dispatch, guard: str):
    if guard is None:
        return None
    try:
        if guard == 'admin':
            payload = await dispatch(services.read_session, request)
            if not isinstance(payload, dict) or payload.get('role') != 'admin':
                return RedirectResponse('/login', status_code=303)
        elif guard == 'user':
            payload = await dispatch(services.read_user_identity, request)
            if not isinstance(payload, dict) or payload.get('role') != 'user':
                return RedirectResponse('/login', status_code=303)
        elif guard == 'user-password':
            await dispatch(services.read_user_password, request)
        else:
            raise ValueError('invalid React document guard')
    except (LoginRequired, UserAccessDenied):
        return RedirectResponse('/login', status_code=303)
    except StateUnavailable:
        return HTMLResponse('服务状态暂不可用，请稍后重试。', status_code=503)
    return None


def _render_document(
    template: str, *, title: str, body_class: str, public_host: str, password_max: int | None
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
    """Register exact React documents and assets from ``react_dist``.

    ``react_dist`` is validated before routes are installed so a typo cannot
    silently produce a document endpoint which later fails open.
    """

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
        service = getattr(services, 'service_module', None)
        password_max = None
        if path == '/login':
            password_max = getattr(service, 'PASSWORD_MAX_LENGTH', 256)
            if (
                isinstance(password_max, bool)
                or not isinstance(password_max, int)
                or password_max <= 0
            ):
                raise RuntimeError('invalid login password limit')
        body = _render_document(
            template,
            title=title,
            body_class=body_class,
            public_host=_public_host(request, services),
            password_max=password_max,
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

    async def user_detail_document(request: Request, uid: str):
        denied = await _guard(request, services, dispatch, 'admin')
        if denied is not None:
            return denied
        body = _render_document(
            template,
            title=f'{uid} · 用量画像',
            body_class='has-shell',
            public_host=_public_host(request, services),
            password_max=None,
        )
        return HTMLResponse(body)

    router.add_api_route(
        '/admin/user/{uid}',
        user_detail_document,
        methods=['GET', 'HEAD'],
        include_in_schema=False,
    )

    app.include_router(router)
    assets = dist / 'assets'
    if not assets.is_dir():
        raise ValueError('react_dist must contain an assets directory')
    app.mount(
        '/static/react/assets',
        StaticFiles(directory=str(assets), html=False),
        name='react-assets',
    )
