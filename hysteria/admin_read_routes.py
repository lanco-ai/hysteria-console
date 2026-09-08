"""Read-only admin endpoints; session exchange and mutations stay in the service.

Dependencies are supplied per request. Importing this module never reads state,
starts probes, or imports the main HTTP service.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Optional, Protocol


class ReadHandler(Protocol):
    def redirect(self, to: str) -> None: ...

    def send_response_body(
        self,
        code: int,
        body: str,
        ctype: str = 'text/plain; charset=utf-8',
        send_body: bool = True,
        extra_headers: Optional[dict] = None,
    ) -> None: ...


@dataclass(frozen=True)
class ReadContext:
    is_logged_in: Callable[[ReadHandler], bool]
    local_now: Callable[[], datetime]
    render_usage_page: Callable[[str], str]
    render_health: Callable[..., str]
    render_daily_table: Callable[[str], str]
    build_analytics: Callable[..., dict]
    build_usage: Callable[..., dict]
    build_usage_csv: Callable[..., str]
    render_health_fragment: Callable[[], str]
    build_health_snapshot: Callable[[], dict]


def _usage_page(handler, ctx, query, host, send_payload):
    handler.send_response_body(
        200,
        ctx.render_usage_page(host),
        'text/html; charset=utf-8',
        send_payload,
    )


def _analytics(handler, ctx, query, host, send_payload):
    summary_only = (query.get('summary') or ['0'])[0].lower() in ('1', 'true', 'yes')
    payload = ctx.build_analytics(now=ctx.local_now(), include_charts=not summary_only)
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        'application/json; charset=utf-8',
        send_payload,
    )


def _history(handler, ctx, query, host, send_payload):
    handler.send_response_body(
        200,
        ctx.render_daily_table(host),
        'text/html; charset=utf-8',
        send_payload,
    )


def _usage_json(handler, ctx, query, host, send_payload):
    payload = ctx.build_usage(now=ctx.local_now())
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        'application/json; charset=utf-8',
        send_payload,
    )


def _usage_csv(handler, ctx, query, host, send_payload):
    window = (query.get('window') or ['cycle'])[0]
    if window not in ('cycle', '30d'):
        handler.send_response_body(400, '无效的导出时间范围', send_body=send_payload)
        return
    now = ctx.local_now()
    body = ctx.build_usage_csv(now=now, window=window)
    filename = f'usage-{window}-{now.strftime("%Y%m%d")}.csv'
    handler.send_response_body(
        200,
        body,
        'text/csv; charset=utf-8',
        send_payload,
        extra_headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _health_page(handler, ctx, query, host, send_payload):
    flash = (query.get('msg') or [''])[0]
    handler.send_response_body(
        200,
        ctx.render_health(host, flash=flash),
        'text/html; charset=utf-8',
        send_payload,
    )


def _health_fragment(handler, ctx, query, host, send_payload):
    if query.get('snapshot') == ['1']:
        handler.send_response_body(
            200,
            json.dumps(ctx.build_health_snapshot()),
            'application/json; charset=utf-8',
            send_payload,
        )
        return
    handler.send_response_body(
        200,
        ctx.render_health_fragment(),
        'text/html; charset=utf-8',
        send_payload,
    )


_ROUTES = {
    '/admin/usage': _usage_page,
    '/admin/analytics.json': _analytics,
    '/admin/usage-history': _history,
    '/admin/usage.json': _usage_json,
    '/admin/usage.csv': _usage_csv,
    '/admin/health': _health_page,
    '/admin/health.fragment': _health_fragment,
}
_LOGIN_REDIRECTS = frozenset(('/admin/usage', '/admin/usage.csv', '/admin/health'))
_FRAGMENT_ERRORS = {
    '/admin/usage-history': '<div class="err" role="alert">登录已失效，请重新登录</div>',
    '/admin/health.fragment': '<div class="err" role="alert">登录已失效</div>',
}


def handle_read(
    handler: ReadHandler,
    ctx: ReadContext,
    *,
    path: str,
    query: Mapping[str, list[str]],
    host: str,
    send_payload: bool,
) -> bool:
    """Return False without side effects for routes owned by other handlers."""
    route = _ROUTES.get(path)
    if route is None:
        return False
    if not ctx.is_logged_in(handler):
        if path in _LOGIN_REDIRECTS:
            handler.redirect('/login')
        elif path in _FRAGMENT_ERRORS:
            handler.send_response_body(
                401,
                _FRAGMENT_ERRORS[path],
                'text/html; charset=utf-8',
                send_payload,
            )
        else:
            handler.send_response_body(
                401,
                '{"error":"login_required"}',
                'application/json; charset=utf-8',
                send_payload,
            )
        return True
    route(handler, ctx, query, host, send_payload)
    return True
