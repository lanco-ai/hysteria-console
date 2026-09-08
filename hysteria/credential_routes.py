"""Credential rotation HTTP flows; durable recovery and revocation remain explicit."""

import hmac
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import revocation_queue
import rotation_recovery
import state_store
import static_access


@dataclass(frozen=True)
class Context:
    CredentialRotationCommitted: type[Exception]
    USERS_FILE: Path
    USER_SESSION_SUBSCRIPTION_TOKEN: str
    _action_succeeded: Callable[..., object]
    _build_overview_user: Callable[..., object]
    _credential_generation: Callable[..., object]
    _fail_closed_static_access: Callable[..., object]
    _json_request: Callable[..., object]
    _normalize_service_action: Callable[..., object]
    _record_kick_attempt: Callable[..., object]
    _record_static_retry: Callable[..., object]
    _recoverable_user_rotation: Callable[..., object]
    _revocation_queue_path: Callable[..., object]
    _rotation_receipts_path: Callable[..., object]
    _save_users_for_rotation: Callable[..., object]
    _schedule_static_reload: Callable[..., object]
    _static_reload_status: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    _using_live_core_state: Callable[..., object]
    configured_public_host: Callable[..., object]
    create_user_session: Callable[..., object]
    html_page: Callable[..., object]
    hy_kick: Callable[..., object]
    is_logged_in: Callable[..., object]
    is_secure_request: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    parse_cookies: Callable[..., object]
    render_user_panel: Callable[..., object]
    revision_matches: Callable[..., object]
    safe_admin_next: Callable[..., object]
    safe_base_url: Callable[..., object]
    usage_lock: Callable[..., object]
    user_session_cookie: Callable[..., object]
    with_flash: Callable[..., object]


def _rotate_panel(handler, ctx, path, form, query, request_user_revision):
    user = path[len('/panel/') : -len('/rotate-token')]
    posted = (form.get('token') or [''])[0]
    request_id = (form.get('rotation_id') or [''])[0]
    original_sid = ctx.parse_cookies(handler).get('usid', '')
    rotation = ctx._recoverable_user_rotation(
        user,
        posted,
        request_id=request_id,
        session_id=original_sid,
    )
    if rotation.status == 'bad_request':
        handler.send_response_body(
            400,
            '重置请求已过期或缺少幂等标识，请返回用户面板重试。',
        )
        return
    if rotation.status == 'forbidden':
        handler.send_response_body(403, '无权限访问')
        return
    if rotation.status == 'disabled':
        handler.send_response_body(403, '账号已停用，请联系管理员')
        return
    if rotation.status == 'expired':
        handler.send_response_body(403, '账号已到期，请联系管理员续费')
        return
    if rotation.status == 'conflict':
        handler.send_response_body(
            409,
            ctx.html_page(
                'Token 已再次变更',
                '<div class="wrap"><div class="card">'
                '<h1>Token 已再次变更</h1>'
                '<div class="err" role="alert">'
                '管理员或另一个会话已完成更新，本次恢复凭据已作废。'
                '请使用管理员提供的最新链接，或用面板密码重新登录。'
                '</div><div class="row mt-md">'
                '<a class="btn" href="/login">返回登录</a>'
                '</div></div></div>',
            ),
            'text/html; charset=utf-8',
            True,
        )
        return

    revocation_uncertain = False
    static_outcomes = {}
    completed_static_services = []
    if rotation.sync_pending:
        static_outcomes = ctx._fail_closed_static_access(
            rotation.sync_error or RuntimeError('credential sync pending'),
        )
        completed_static_services.extend(
            service for service, outcome in static_outcomes.items() if outcome.ok
        )
    else:
        for service, changed in (
            (
                static_access.XRAY_SERVICE,
                rotation.xray_changed,
            ),
            (
                static_access.TUIC_SERVICE,
                rotation.tuic_changed,
            ),
        ):
            reload_result = ctx._schedule_static_reload(
                service,
                changed=changed,
            )
            if reload_result.ok:
                completed_static_services.append(service)
            else:
                raw = static_access.stop_fail_closed(
                    service,
                    reason=RuntimeError(
                        'credential reload scheduling failed',
                    ),
                    live=ctx._using_live_core_state(),
                )
                static_outcomes[service] = ctx._normalize_service_action(service, raw)
                if static_outcomes[service].ok:
                    completed_static_services.append(service)
    retry_services = [service for service, outcome in static_outcomes.items() if not outcome.ok]
    if retry_services and not ctx._record_static_retry(
        rotation.task_id,
        retry_services,
    ):
        revocation_uncertain = True

    kick_result = ctx.hy_kick([user])
    kick_recorded = ctx._record_kick_attempt(
        rotation.task_id,
        kick_result,
        completed_static_services=completed_static_services,
    )
    if not ctx._action_succeeded(kick_result) or not kick_recorded:
        revocation_uncertain = True

    confirmed_static_pause = (
        rotation.sync_pending
        and len(static_outcomes) == len(static_access.SERVICES)
        and all(outcome.effect_confirmed for outcome in static_outcomes.values())
    )
    if revocation_uncertain or any(
        not outcome.effect_confirmed for outcome in static_outcomes.values()
    ):
        notice = 'token_rotated_revocation_retry'
    elif rotation.sync_pending and confirmed_static_pause:
        notice = 'token_rotated_sync_pending'
    elif static_outcomes:
        notice = 'token_rotated_static_pending'
    else:
        notice = 'token_rotated'

    generation_conflict = False
    try:
        with ctx.usage_lock():
            latest = ctx.load_json(ctx.USERS_FILE, {}).get(user)
            current_generation = ctx._credential_generation(
                latest.get('sub_token') if isinstance(latest, dict) else '',
            )
            expected_generation = ctx._credential_generation(
                rotation.new_token,
            )
            if not current_generation or not hmac.compare_digest(
                current_generation,
                expected_generation,
            ):
                generation_conflict = True
                sid = ''
            else:
                sid = ctx.create_user_session(
                    user,
                    expected_generation,
                    ctx.USER_SESSION_SUBSCRIPTION_TOKEN,
                )
    except (state_store.StateStoreError, OSError):
        sid = ''
    if generation_conflict:
        handler.send_response_body(
            409,
            ctx.html_page(
                'Token 已被后续更新',
                '<div class="wrap"><div class="card">'
                '<h1>Token 已被后续更新</h1>'
                '<div class="err" role="alert">'
                '本次 Token 已提交，但在创建新会话前又被更新。'
                '为避免交付过期凭据，本页不显示旧一代 Token；'
                '请使用最新管理员链接或面板密码重新登录。'
                '</div><div class="row mt-md">'
                '<a class="btn" href="/login">返回登录</a>'
                '</div></div></div>',
            ),
            'text/html; charset=utf-8',
            True,
        )
        return
    if sid:
        try:
            receipt_bound = rotation_recovery.bind_replacement_session(
                ctx._rotation_receipts_path(),
                user=user,
                request_id=request_id,
                original_session_id=original_sid,
                replacement_session_id=sid,
            )
        except (state_store.StateStoreError, OSError):
            receipt_bound = False
        if receipt_bound:
            handler.redirect(
                f'/user/panel?msg={notice}',
                cookie=ctx.user_session_cookie(
                    sid,
                    secure=ctx.is_secure_request(handler),
                ),
                status=303,
            )
            return

    if not sid or not receipt_bound:
        recovery_notice = (
            'token_rotated_revocation_retry_recovery'
            if notice == 'token_rotated_revocation_retry'
            else (
                'token_rotated_sync_pending_recovery'
                if notice == 'token_rotated_sync_pending'
                else (
                    'token_rotated_static_pending_recovery'
                    if notice == 'token_rotated_static_pending'
                    else 'token_rotated_session_recovery'
                )
            )
        )
        cfg = rotation.user_config
        if not isinstance(cfg, dict):
            raise
        handler.send_response_body(
            200,
            ctx.render_user_panel(
                ctx.configured_public_host(
                    handler.headers.get('Host', '127.0.0.1'),
                ),
                ctx.safe_base_url(
                    ctx.configured_public_host(
                        handler.headers.get('Host', '127.0.0.1'),
                    ),
                    handler.headers.get(
                        'X-Forwarded-Proto',
                        'http',
                    ),
                    handler.headers.get('X-Forwarded-Port', ''),
                ),
                user,
                rotation.new_token,
                cfg,
                notice=recovery_notice,
            ),
            'text/html; charset=utf-8',
            True,
        )
        return


def _rotate_admin(handler, ctx, path, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    # Snapshot the actor before any mutation. A later session-lock
    # timeout must never turn a committed rotation into a false 503.
    actor = handler.get_admin_actor()
    username = (form.get('user') or [''])[0].strip()
    next_to = ctx.safe_admin_next((form.get('next') or [''])[0])
    sync_pending = False
    sync_error = None
    task_id = ''
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._mutation_user_not_found(username, next_to)
            return
        if not isinstance(users.get(username), dict):
            handler._mutation_user_not_found(username, next_to)
            return
        if not ctx.revision_matches(
            users.get(username),
            request_user_revision,
        ):
            handler._mutation_conflict(username, next_to)
            return
        previous_generation = ctx._credential_generation(
            users[username].get('sub_token'),
        )
        new_token = secrets.token_urlsafe(18)
        new_uuid = str(uuid.uuid4())
        new_generation = ctx._credential_generation(new_token)
        task_id = revocation_queue.task_id_for(
            username,
            secrets.token_urlsafe(24),
        )
        revocation_queue.prepare(
            ctx._revocation_queue_path(),
            task_id=task_id,
            user=username,
            previous_generation=previous_generation,
            target_generation=new_generation,
            static_services=static_access.SERVICES,
        )
        users[username]['sub_token'] = new_token
        users[username]['vless_uuid'] = new_uuid
        durability_uncertain = ctx._save_users_for_rotation(
            users,
            user=username,
            new_token=new_token,
            new_uuid=new_uuid,
        )
        if durability_uncertain:
            sync_pending = True
            sync_error = ctx.CredentialRotationCommitted(
                username,
                new_token,
                users[username],
                durability_uncertain=True,
            )
            xray_changed = False
            tuic_changed = False
        else:
            try:
                xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
            except state_store.CriticalStateUnavailable as exc:
                sync_pending = True
                sync_error = exc
                xray_changed = False
                tuic_changed = False

    revocation_uncertain = False
    static_outcomes = {}
    completed_static_services = []
    if sync_pending:
        static_outcomes = ctx._fail_closed_static_access(sync_error)
        completed_static_services.extend(
            service for service, outcome in static_outcomes.items() if outcome.ok
        )
    else:
        for service, changed in (
            (static_access.XRAY_SERVICE, xray_changed),
            (static_access.TUIC_SERVICE, tuic_changed),
        ):
            reload_result = ctx._schedule_static_reload(
                service,
                changed=changed,
            )
            if reload_result.ok:
                completed_static_services.append(service)
            else:
                raw = static_access.stop_fail_closed(
                    service,
                    reason=RuntimeError(
                        'credential reload scheduling failed',
                    ),
                    live=ctx._using_live_core_state(),
                )
                static_outcomes[service] = ctx._normalize_service_action(service, raw)
                if static_outcomes[service].ok:
                    completed_static_services.append(service)
    retry_services = [service for service, outcome in static_outcomes.items() if not outcome.ok]
    if retry_services and not ctx._record_static_retry(
        task_id,
        retry_services,
    ):
        revocation_uncertain = True
    kick_result = ctx.hy_kick([username])
    kick_recorded = ctx._record_kick_attempt(
        task_id,
        kick_result,
        completed_static_services=completed_static_services,
    )
    if not ctx._action_succeeded(kick_result) or not kick_recorded:
        revocation_uncertain = True
    handler.write_reset_log(
        actor,
        'rotate_token',
        username,
        {},
        {},
    )
    confirmed_static_pause = (
        sync_pending
        and len(static_outcomes) == len(static_access.SERVICES)
        and all(outcome.effect_confirmed for outcome in static_outcomes.values())
    )
    if revocation_uncertain or any(
        not outcome.effect_confirmed for outcome in static_outcomes.values()
    ):
        flash = 'err:rotated_retry ' + username
    elif sync_pending and confirmed_static_pause:
        flash = 'err:rotated_pending ' + username
    elif static_outcomes:
        flash = 'err:rotated_static_pending ' + username
    else:
        flash = 'rotated ' + username
    if ctx._json_request(handler):
        # The token changed, so the row's subscription/panel links
        # must be refreshed client-side along with the row itself.
        host = ctx.configured_public_host(
            handler.headers.get('Host', '127.0.0.1'),
        )
        base_url = ctx.safe_base_url(
            host,
            handler.headers.get('X-Forwarded-Proto', 'http'),
            handler.headers.get('X-Forwarded-Port', ''),
        )
        new_token = users.get(username, {}).get('sub_token', '')
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'flash': flash.split(' ', 1)[0],
                'user': ctx._build_overview_user(username, now=ctx.local_now()),
                'links': {
                    'panel': f'{base_url}/panel/{username}?token={new_token}',
                    'sub': f'{base_url}/sub/{username}?token={new_token}',
                },
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect(ctx.with_flash(next_to, flash))
    return


def handle_write(
    handler,
    context: Context,
    *,
    path: str,
    form: Mapping[str, list[str]],
    query: Mapping[str, list[str]],
    request_user_revision: str,
) -> bool:
    if path.startswith('/panel/') and path.endswith('/rotate-token'):
        route = _rotate_panel
    elif path == '/admin/rotate-token':
        route = _rotate_admin
    else:
        return False
    route(handler, context, path, form, query, request_user_revision)
    return True
