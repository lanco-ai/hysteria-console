"""Rule pack application HTTP endpoints."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    RULE_PACKS: dict
    TemplateConfigError: type[Exception]
    TemplateConflictError: type[Exception]
    apply_rule_pack_to_template: Callable[..., object]
    apply_rule_pack_to_user: Callable[..., object]
    configured_public_host: Callable[..., object]
    is_logged_in: Callable[..., object]
    render_rules: Callable[..., object]


def _apply(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    pack = (form.get('pack') or [''])[0]
    scope = (form.get('scope') or ['global'])[0]
    if pack not in ctx.RULE_PACKS:
        handler.redirect('/admin/rules?msg=err:invalid_rule_pack')
        return
    if scope == 'global':
        expected_revision = (form.get('template_revision') or [''])[0]
        try:
            applied = ctx.apply_rule_pack_to_template(
                pack,
                expected_revision=expected_revision,
            )
        except ctx.TemplateConflictError:
            host = ctx.configured_public_host(
                handler.headers.get('Host', '127.0.0.1'),
            )
            handler.send_response_body(
                409,
                ctx.render_rules(host, flash='err:conflict'),
                'text/html; charset=utf-8',
            )
            return
        except ctx.TemplateConfigError:
            handler.redirect('/admin/rules?msg=err:load_failed')
            return
        if not applied:
            handler.redirect('/admin/rules?msg=err:invalid_rule_pack')
            return
    elif scope == 'user':
        username = (form.get('user') or [''])[0].strip()
        if not username:
            handler.redirect('/admin/rules?msg=err:rule_pack_user_missing')
            return
        if not ctx.apply_rule_pack_to_user(username, pack):
            handler.redirect('/admin/rules?msg=err:rule_pack_user_missing')
            return
    else:
        handler.redirect('/admin/rules?msg=err:invalid_rule_pack_scope')
        return
    handler.redirect('/admin/rules?msg=rule_pack_applied')
    return


_ROUTES = {
    '/admin/rule-pack/apply': _apply,
}


def handle_write(
    handler,
    context: Context,
    *,
    path: str,
    form: Mapping[str, list[str]],
    query: Mapping[str, list[str]],
    request_user_revision: str,
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form, query, request_user_revision)
    return True
