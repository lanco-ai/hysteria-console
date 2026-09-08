"""Account creation and plan editing HTTP endpoints."""

import json
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import landing_egress
import state_store
import tuic_config
import user_compat
import xray_config


@dataclass(frozen=True)
class Context:
    PASSWORD_MAX_LENGTH: int
    USERS_FILE: Path
    _ensure_landing_vless_uuid: Callable[..., object]
    _landing_registry_or_empty: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    configured_public_host: Callable[..., object]
    delete_user_sessions_for: Callable[..., object]
    hash_secret: Callable[..., object]
    is_logged_in: Callable[..., object]
    is_valid_username: Callable[..., object]
    load_json: Callable[..., object]
    parse_bounded_int_field: Callable[..., object]
    parse_date_field: Callable[..., object]
    parse_note_field: Callable[..., object]
    render_admin: Callable[..., object]
    revision_matches: Callable[..., object]
    safe_base_url: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]


def _update(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    username = (form.get('user') or [''])[0].strip()
    expected_revision = request_user_revision
    panel_password = (form.get('panel_password') or [''])[0]
    new_password = (form.get('password') or [''])[0].strip()
    max_devices = ctx.parse_bounded_int_field(
        (form.get('max_devices') or [''])[0],
        0,
        100,
    )
    quota_gb = ctx.parse_bounded_int_field(
        (form.get('quota_gb') or [''])[0],
        0,
        10240,
    )
    quota_extra_gb = ctx.parse_bounded_int_field(
        (form.get('quota_extra_gb') or [''])[0],
        0,
        10240,
    )
    expires_raw = (form.get('expires_at') or [''])[0]
    note_raw = (form.get('note') or [''])[0]
    expires_at = ctx.parse_date_field(expires_raw)
    note = ctx.parse_note_field(note_raw)
    landing_values = {}
    landing_error = None
    for landing_name in user_compat.LANDING_FIELDS:
        parser = (
            user_compat.parse_landing_ip_write
            if landing_name == 'landing_ip'
            else user_compat.parse_landing_write
        )
        value, err = parser(
            (form.get(landing_name) or [''])[0],
        )
        if err:
            landing_error = err
            break
        landing_values[landing_name] = value
    guest = 'guest' in form
    tuic_enabled = 'tuic_enabled' in form

    def respond_update_error(message):
        host = ctx.configured_public_host(
            handler.headers.get('Host', '127.0.0.1'),
        )
        handler.send_response_body(
            422,
            ctx.render_admin(
                host,
                ctx.safe_base_url(
                    host,
                    handler.headers.get('X-Forwarded-Proto', 'http'),
                    handler.headers.get('X-Forwarded-Port', ''),
                ),
                flash=message,
            ),
            'text/html; charset=utf-8',
        )

    if max_devices is None:
        respond_update_error('err:max_devices_invalid')
        return
    if quota_gb is None:
        respond_update_error('err:quota_invalid')
        return
    if quota_extra_gb is None:
        respond_update_error('err:quota_extra_invalid')
        return
    if str(expires_raw).strip() and not expires_at:
        respond_update_error('err:expiry_invalid')
        return
    if len(str(note_raw).strip()) > 200:
        respond_update_error('err:note_too_long')
        return
    if landing_error:
        respond_update_error('err:' + landing_error)
        return
    if panel_password and len(panel_password) < 8:
        handler.redirect('/admin?msg=err:panel_password_short')
        return
    if len(panel_password) > ctx.PASSWORD_MAX_LENGTH:
        handler.redirect('/admin?msg=err:panel_password_long')
        return
    if len(new_password) > ctx.PASSWORD_MAX_LENGTH:
        handler.redirect('/admin?msg=err:proxy_password_long')
        return
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler.redirect('/admin?msg=user+not+found')
            return
        cfg = users[username]
        if not isinstance(cfg, dict) or not ctx.revision_matches(cfg, expected_revision):
            handler.send_user_state_conflict(
                '/admin',
                draft={
                    'user': username,
                    'max_devices': max_devices,
                    'quota_gb': quota_gb,
                    'quota_extra_gb': quota_extra_gb,
                    'expires_at': expires_at,
                    'note': note,
                    'guest': '是' if guest else '否',
                    'tuic_enabled': ('是' if tuic_enabled else '否'),
                },
            )
            return
        if panel_password:
            cfg['panel_pass_hash'] = ctx.hash_secret(panel_password)
            cfg['panel_password_must_change'] = True
        if new_password:
            cfg['password_hash'] = ctx.hash_secret(new_password)
        cfg.pop('password', None)
        cfg['max_devices'] = max_devices
        cfg['monthly_quota_bytes'] = quota_gb * 1024 * 1024 * 1024
        cfg['quota_extra_bytes'] = quota_extra_gb * 1024 * 1024 * 1024
        if expires_at:
            cfg['expires_at'] = expires_at
        else:
            cfg.pop('expires_at', None)
        if note:
            cfg['note'] = note
        else:
            cfg.pop('note', None)
        for landing_name, landing_value in landing_values.items():
            if landing_value:
                cfg[landing_name] = landing_value
            else:
                cfg.pop(landing_name, None)
        cfg['metered'] = guest
        cfg['guest'] = guest
        cfg['tuic_enabled'] = tuic_enabled
        if not cfg.get('sub_token'):
            cfg['sub_token'] = secrets.token_urlsafe(18)
        if not str(cfg.get('vless_uuid') or '').strip():
            cfg['vless_uuid'] = str(uuid.uuid4())
        users[username] = cfg
        ctx.save_json(ctx.USERS_FILE, users)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    if panel_password:
        ctx.delete_user_sessions_for(username)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect('/admin?msg=updated+' + username)
    return


def _add(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    username = (form.get('user') or [''])[0].strip()
    panel_password = (form.get('panel_password') or [''])[0]
    password = (form.get('password') or [''])[0].strip()
    quota_gb_raw = (form.get('quota_gb') or [''])[0]
    quota_extra_gb_raw = (form.get('quota_extra_gb') or [''])[0]
    quota_gb = ctx.parse_bounded_int_field(
        quota_gb_raw,
        0,
        10240,
    )
    quota_extra_gb = ctx.parse_bounded_int_field(
        quota_extra_gb_raw,
        0,
        10240,
    )
    expires_raw = (form.get('expires_at') or [''])[0]
    note_raw = (form.get('note') or [''])[0]
    landing_initial_egress_id = (form.get('landing_initial_egress_id') or [''])[0].strip()
    expires_at = ctx.parse_date_field(expires_raw)
    note = ctx.parse_note_field(note_raw)
    guest = 'guest' in form
    tuic_enabled = 'tuic_enabled' in form
    create_draft = {
        'user': username,
        'quota_gb': quota_gb_raw,
        'quota_extra_gb': quota_extra_gb_raw,
        'expires_at': expires_raw,
        'note': note_raw,
        'landing_initial_egress_id': landing_initial_egress_id,
        'guest': guest,
        'tuic_enabled': tuic_enabled,
    }

    def respond_create_error(message, field_id):
        host = ctx.configured_public_host(
            handler.headers.get('Host', '127.0.0.1'),
        )
        base_url = ctx.safe_base_url(
            host,
            handler.headers.get('X-Forwarded-Proto', 'http'),
            handler.headers.get('X-Forwarded-Port', ''),
        )
        handler.send_response_body(
            422,
            ctx.render_admin(
                host,
                base_url,
                flash=message,
                create_draft=create_draft,
                create_error_field=field_id,
            ),
            'text/html; charset=utf-8',
        )

    if not username:
        respond_create_error('user empty', 'create-user')
        return
    if not ctx.is_valid_username(username):
        respond_create_error('err:username_invalid', 'create-user')
        return
    if quota_gb is None:
        respond_create_error(
            'err:quota_invalid',
            'create-quota-gb',
        )
        return
    if quota_extra_gb is None:
        respond_create_error(
            'err:quota_extra_invalid',
            'create-quota-extra-gb',
        )
        return
    if str(expires_raw).strip() and not expires_at:
        respond_create_error(
            'err:expiry_invalid',
            'create-expires-at',
        )
        return
    if len(str(note_raw).strip()) > 200:
        respond_create_error(
            'err:note_too_long',
            'create-note',
        )
        return
    if panel_password and len(panel_password) < 8:
        respond_create_error(
            'err:panel_password_short',
            'create-panel-password',
        )
        return
    if len(panel_password) > ctx.PASSWORD_MAX_LENGTH:
        respond_create_error(
            'err:panel_password_long',
            'create-panel-password',
        )
        return
    if len(password) > ctx.PASSWORD_MAX_LENGTH:
        respond_create_error(
            'err:proxy_password_long',
            'create-proxy-password',
        )
        return
    if landing_initial_egress_id:
        registry = ctx._landing_registry_or_empty()
        initial_node = registry.get('nodes', {}).get(
            landing_initial_egress_id,
        )
        if not isinstance(initial_node, dict) or initial_node.get('enabled') is not True:
            respond_create_error(
                '家宽出口已不可用，请重新选择',
                'create-landing-initial-egress',
            )
            return
    user_exists = False
    landing_became_unavailable = False
    with ctx.usage_lock():
        original_users_text = Path(ctx.USERS_FILE).read_text(
            encoding='utf-8',
        )
        users = ctx.load_json(ctx.USERS_FILE, {})
        if landing_initial_egress_id:
            registry = landing_egress.load_registry()
            initial_node = registry.get('nodes', {}).get(
                landing_initial_egress_id,
            )
            landing_became_unavailable = (
                not isinstance(initial_node, dict) or initial_node.get('enabled') is not True
            )
        if landing_became_unavailable:
            pass
        elif username in users:
            user_exists = True
        else:
            entry = {
                'metered': guest,
                'guest': guest,
                'tuic_enabled': tuic_enabled,
                'monthly_quota_bytes': quota_gb * 1024 * 1024 * 1024,
                'quota_extra_bytes': quota_extra_gb * 1024 * 1024 * 1024,
                'sub_token': secrets.token_urlsafe(18),
                'vless_uuid': str(uuid.uuid4()),
                'disabled': False,
                'max_devices': 2,
            }
            if expires_at:
                entry['expires_at'] = expires_at
            if note:
                entry['note'] = note
            if password:
                entry['password_hash'] = ctx.hash_secret(password)
            if panel_password:
                entry['panel_pass_hash'] = ctx.hash_secret(panel_password)
                entry['panel_password_must_change'] = True
            if landing_initial_egress_id:
                entry['landing_allowed_egress_ids'] = [
                    landing_initial_egress_id,
                ]
                entry['landing_selected_egress_id'] = landing_initial_egress_id
                ctx._ensure_landing_vless_uuid(entry, users)
            users[username] = entry
            ctx.save_json(ctx.USERS_FILE, users)
            try:
                xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
            except Exception:
                state_store.save_text_atomic(
                    ctx.USERS_FILE,
                    original_users_text,
                )
                try:
                    ctx._sync_static_access_from_users(
                        json.loads(original_users_text),
                    )
                except Exception:
                    pass
                raise
    if landing_became_unavailable:
        respond_create_error(
            '家宽出口已不可用，请重新选择',
            'create-landing-initial-egress',
        )
        return
    if user_exists:
        respond_create_error(
            'user_exists_use_reset_token',
            'create-user',
        )
        return
    if panel_password:
        ctx.delete_user_sessions_for(username)
    # Re-syncing a suspended user back into xray would undo the suspend.
    # Config writes are committed with the user snapshot above; service
    # reloads stay outside the lock because they can perform process I/O.
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect('/admin?msg=created+' + username)
    return


_ROUTES = {
    '/admin/update': _update,
    '/admin/add': _add,
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
