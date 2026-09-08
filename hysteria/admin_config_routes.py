"""Template mutation HTTP endpoints; storage transactions remain in the service."""

import json
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import state_store


class ConfigHandler(Protocol):
    headers: Mapping[str, str]

    def redirect(self, to: str) -> None: ...

    def send_response_body(
        self, code: int, body: str, ctype: str = 'text/plain; charset=utf-8'
    ) -> None: ...


@dataclass(frozen=True)
class ConfigContext:
    is_logged_in: Callable[[ConfigHandler], bool]
    configured_public_host: Callable[[str], str]
    render_config_editor: Callable[..., str]
    validate_template_config: Callable[[object], bool]
    replace_template_config: Callable[..., None]
    render_rules: Callable[..., str]
    add_template_rule: Callable[..., None]
    delete_template_rule: Callable[..., bool]
    validate_clash_rule: Callable[[str], bool]
    replace_template_rules: Callable[..., None]
    TemplateConflictError: type[Exception]
    TemplateConfigError: type[Exception]


def _save_config(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    raw = (form.get('config_json') or [''])[0]
    expected_revision = (form.get('template_revision') or [''])[0]
    host = ctx.configured_public_host(
        handler.headers.get('Host', '127.0.0.1'),
    )
    if not raw.strip():
        handler.send_response_body(
            422,
            ctx.render_config_editor(
                host,
                flash='err:empty',
                draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        handler.send_response_body(
            422,
            ctx.render_config_editor(
                host,
                flash='err:invalid_json',
                draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    if not ctx.validate_template_config(data):
        handler.send_response_body(
            422,
            ctx.render_config_editor(
                host,
                flash='err:schema_invalid',
                draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    try:
        ctx.replace_template_config(
            data,
            expected_revision=expected_revision,
        )
    except ctx.TemplateConflictError:
        handler.send_response_body(
            409,
            ctx.render_config_editor(
                host,
                flash='err:conflict',
                draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    except (state_store.StateStoreError, OSError):
        handler.send_response_body(
            503,
            ctx.render_config_editor(
                host,
                flash='err:save_failed',
                draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    handler.redirect('/admin/config?msg=saved')
    return


def _add_rule(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    rule_type = (form.get('rule_type') or ['DOMAIN-SUFFIX'])[0]
    pattern = (form.get('pattern') or [''])[0].strip()
    action = (form.get('action') or ['DIRECT'])[0]
    extra = (form.get('extra') or [''])[0]
    if not pattern:
        handler.redirect('/admin/rules?msg=err:pattern_empty')
        return
    if rule_type not in ('DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'DOMAIN', 'IP-CIDR'):
        handler.redirect('/admin/rules?msg=err:invalid_rule_type')
        return
    if ',' in pattern or any(ord(ch) < 32 for ch in pattern) or len(pattern) > 512:
        handler.redirect('/admin/rules?msg=err:invalid_pattern')
        return
    if action not in ('DIRECT', 'REJECT', '🚀 节点选择'):
        handler.redirect('/admin/rules?msg=err:invalid_action')
        return
    if extra not in ('', 'no-resolve'):
        handler.redirect('/admin/rules?msg=err:invalid_extra')
        return
    rule_str = f'{rule_type},{pattern},{action}'
    if extra:
        rule_str += f',{extra}'
    expected_revision = (form.get('template_revision') or [''])[0]
    try:
        ctx.add_template_rule(
            rule_str,
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
    handler.redirect('/admin/rules?msg=rule_added')
    return


def _delete_rule(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    try:
        idx = int((form.get('index') or [''])[0])
    except (ValueError, IndexError):
        handler.redirect('/admin/rules?msg=err:invalid_index')
        return
    expected_revision = (form.get('template_revision') or [''])[0]
    expected_rule = (form.get('expected_rule') or [''])[0]
    try:
        deleted = ctx.delete_template_rule(
            idx,
            expected_revision=expected_revision,
            expected_rule=expected_rule,
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
    if not deleted:
        handler.redirect('/admin/rules?msg=err:index_out_of_range')
        return
    handler.redirect('/admin/rules?msg=rule_deleted')
    return


def _save_rules(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    raw = (form.get('rules_raw') or [''])[0]
    expected_revision = (form.get('template_revision') or [''])[0]
    rules = [line.strip() for line in raw.splitlines() if line.strip()]
    if not rules:
        handler.redirect('/admin/rules?msg=err:raw_empty')
        return
    if len(rules) > 5000 or any(not ctx.validate_clash_rule(rule) for rule in rules):
        host = ctx.configured_public_host(
            handler.headers.get('Host', '127.0.0.1'),
        )
        handler.send_response_body(
            422,
            ctx.render_rules(
                host,
                flash='err:invalid_rule_schema',
                raw_draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    try:
        ctx.replace_template_rules(
            rules,
            expected_revision=expected_revision,
        )
    except ctx.TemplateConflictError:
        host = ctx.configured_public_host(
            handler.headers.get('Host', '127.0.0.1'),
        )
        handler.send_response_body(
            409,
            ctx.render_rules(
                host,
                flash='err:conflict',
                raw_draft=raw,
                expected_revision=expected_revision,
            ),
            'text/html; charset=utf-8',
        )
        return
    except ctx.TemplateConfigError:
        handler.redirect('/admin/rules?msg=err:load_failed')
        return
    handler.redirect('/admin/rules?msg=raw_saved')
    return


_ROUTES = {
    '/admin/config/save': _save_config,
    '/admin/rules/add': _add_rule,
    '/admin/rules/delete': _delete_rule,
    '/admin/rules/raw': _save_rules,
}


def handle_write(
    handler: ConfigHandler, context: ConfigContext, *, path: str, form: Mapping[str, list[str]]
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form)
    return True
