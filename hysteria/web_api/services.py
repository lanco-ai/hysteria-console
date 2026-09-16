"""Adapters from the HTTP boundary to the legacy panel services."""

import json
from dataclasses import dataclass, field
from typing import Literal, Mapping

import admin_overview_data
import http_utils
from login_service import LoginResult
from password_change_service import PasswordChangeResult
from reset_log_data import read_reset_logs

from .requests import RequestHeaders


class LoginRequired(Exception):
    """No existing cookie session authorizes the requested read."""


class StateUnavailable(Exception):
    """Legacy state could not be read safely."""


class UserAccessDenied(Exception):
    """An authenticated user's current lifecycle state blocks panel access."""

    _PUBLIC_CODES = frozenset(('disabled', 'expired', 'forbidden', 'password_change_required'))

    def __init__(self, code, *, cookie=None):
        self.code = code if code in self._PUBLIC_CODES else 'forbidden'
        self.cookie = cookie
        super().__init__()


@dataclass(frozen=True, slots=True)
class LegacyRequestBridge:
    """The complete legacy-shaped request surface exposed to identity code."""

    headers: Mapping[str, str]
    path: str
    client_address: tuple = ('', 0)


@dataclass(slots=True)
class _CapturedLegacyHandler:
    """Capture a legacy write result without sending an HTML response."""

    headers: Mapping[str, str]
    path: str
    redirect_to: str | None = None
    redirect_status: int | None = None
    response_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)

    def redirect(self, to, *, status=302):
        self.redirect_to = str(to)
        self.redirect_status = int(status)

    def send_response_body(
        self,
        code,
        _body='',
        _ctype='text/plain; charset=utf-8',
        *,
        extra_headers=None,
    ):
        self.response_status = int(code)
        if isinstance(extra_headers, Mapping):
            self.response_headers.update(
                {str(name).lower(): str(value) for name, value in extra_headers.items()}
            )


@dataclass(frozen=True, slots=True)
class LoginReply:
    result: LoginResult
    cookie: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AdminTokenExchangeReply:
    """One-time result for exchanging the admin bearer URL token."""

    cookie: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LogoutReply:
    cookie: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PasswordChangeReply:
    result: PasswordChangeResult
    cookie: str | None = field(default=None, repr=False)


class LegacyPanelServices:
    """Call authoritative synchronous services through narrow HTTP adapters."""

    def __init__(self, service_module):
        self.service_module = service_module

    def _bridge(self, *, headers, path, client_address=('', 0)):
        return LegacyRequestBridge(
            headers=RequestHeaders(headers),
            path=str(path).split('?', 1)[0],
            client_address=tuple(client_address),
        )

    def _run_operation(self, operation, *, post_path=None):
        service = self.service_module

        @service.request_multiplier_snapshot
        def snapshotted_operation():
            try:
                return operation()
            except (service.state_store.StateStoreError, OSError) as exc:
                try:
                    if post_path is None:
                        requires_stop = service._state_failure_requires_static_stop(exc)
                    else:
                        requires_stop = service._state_failure_requires_static_stop(
                            exc,
                            post_path=post_path,
                        )
                    if requires_stop:
                        service._fail_closed_static_access(exc)
                finally:
                    raise StateUnavailable from None

        return snapshotted_operation()

    def _run_read(self, operation):
        return self._run_operation(operation)

    def submit_login(self, *, headers, path, form, client_address):
        request = self._bridge(
            headers=headers,
            path=path,
            client_address=client_address,
        )
        service = self.service_module

        def login():
            result = service._login_service().authenticate(
                form=form,
                meta=service.load_meta(),
                client_ip=http_utils.request_client_ip(request),
            )
            cookie = None
            if result.outcome == 'success':
                cookie_helper = (
                    service.user_session_cookie
                    if result.realm == 'user'
                    else service.session_cookie
                )
                cookie = cookie_helper(
                    result.session_id,
                    secure=http_utils.is_secure_request(request),
                )
            return LoginReply(result=result, cookie=cookie)

        return self._run_operation(login, post_path='/login')

    def exchange_admin_token(self, *, headers, path, token):
        """Mint the same admin session as the legacy bearer URL exchange.

        The token is supplied by the document boundary and is never included
        in the returned value or any loggable request path.  Invalid tokens
        return ``None`` so the caller can continue through the normal session
        guard and redirect anonymously.
        """
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def exchange():
            meta = service.load_meta()
            expected = str(meta.get('admin_token') or '')
            if not service._safe_secret_equal(str(token or ''), expected):
                return None
            generation = service._credential_generation(meta.get('admin_pass_hash'))
            if not generation:
                raise StateUnavailable
            try:
                sid = service.create_session('admin', generation)
            except (service.state_store.StateStoreError, OSError) as exc:
                raise StateUnavailable from exc
            return AdminTokenExchangeReply(
                cookie=service.session_cookie(
                    sid,
                    secure=http_utils.is_secure_request(request),
                )
            )

        return self._run_operation(exchange, post_path='/admin')

    def submit_logout(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        realm: Literal['admin', 'user'],
    ):
        if realm not in ('admin', 'user'):
            raise ValueError('invalid logout realm')
        del form
        request = self._bridge(
            headers=headers,
            path=path,
            client_address=client_address,
        )
        service = self.service_module

        def logout():
            service.load_meta()
            cookies = service.parse_cookies(request)
            if realm == 'admin':
                service.delete_session(cookies.get('sid', ''))
                cookie_helper = service.clear_session_cookie
            else:
                service.delete_user_session(cookies.get('usid', ''))
                cookie_helper = service.clear_user_session_cookie
            return LogoutReply(
                cookie=cookie_helper(
                    secure=http_utils.is_secure_request(request),
                )
            )

        post_path = '/logout' if realm == 'admin' else '/user/logout'
        return self._run_operation(logout, post_path=post_path)

    def submit_password_change(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        realm: Literal['admin', 'user'],
    ):
        if realm not in ('admin', 'user'):
            raise ValueError('invalid password-change realm')
        del client_address
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def change_password():
            service.load_meta()
            if realm == 'admin':
                if not service.is_logged_in(request):
                    result = PasswordChangeResult(outcome='login_required')
                else:
                    result = service._password_change_service().change_admin(form=form)
            else:
                username, session_kind = service.get_logged_in_user_context(request)
                result = service._password_change_service().change_user(
                    username=username,
                    session_kind=session_kind,
                    form=form,
                )

            cookie = None
            if result.outcome == 'success':
                cookie_helper = (
                    service.session_cookie if realm == 'admin' else service.user_session_cookie
                )
                cookie = cookie_helper(
                    result.session_id,
                    secure=http_utils.is_secure_request(request),
                )
            elif realm == 'user' and result.outcome == 'forbidden':
                cookie = service.clear_user_session_cookie(
                    secure=http_utils.is_secure_request(request),
                )
            return PasswordChangeReply(result=result, cookie=cookie)

        post_path = '/admin/change-password' if realm == 'admin' else '/user/change-password'
        return self._run_operation(change_password, post_path=post_path)

    def submit_account_mutation(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        action: Literal['create', 'update'],
    ):
        if action not in ('create', 'update'):
            raise ValueError('invalid account mutation action')
        request = self._bridge(
            headers=headers,
            path=path,
            client_address=client_address,
        )
        service = self.service_module

        def mutate_account():
            service.load_meta()
            if not service.is_logged_in(request):
                raise LoginRequired
            account_service = service._account_mutation_service()
            if action == 'create':
                return account_service.create(form=form)
            expected_revision = (form.get('user_revision') or [''])[0]
            return account_service.update(
                form=form,
                expected_revision=expected_revision,
            )

        post_path = '/admin/add' if action == 'create' else '/admin/update'
        return self._run_operation(mutate_account, post_path=post_path)

    def submit_overview_operation(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        action: Literal[
            'cycle',
            'reset-usage',
            'refresh-usage',
            'reset-usage-all',
            'pause-user',
            'toggle-user',
            'rotate-token',
            'delete',
        ],
    ):
        post_paths = {
            'cycle': '/admin/cycle-config',
            'reset-usage': '/admin/reset-usage',
            'refresh-usage': '/admin/refresh-usage',
            'reset-usage-all': '/admin/reset-usage-all',
            'pause-user': '/admin/pause-user',
            'toggle-user': '/admin/toggle-user',
            'rotate-token': '/admin/rotate-token',
            'delete': '/admin/delete',
        }
        if action not in post_paths:
            raise ValueError('invalid overview operation action')
        request = self._bridge(
            headers=headers,
            path=path,
            client_address=client_address,
        )
        service = self.service_module

        def audit(log_action, target, before, after):
            actor = service._admin_actor(request)
            service._write_reset_log(
                request,
                actor,
                log_action,
                target,
                before,
                after,
            )

        def mutate():
            service.load_meta()
            if not service.is_logged_in(request):
                raise LoginRequired
            expected_revision = (form.get('user_revision') or [''])[0]
            if action == 'rotate-token':
                # Capture before committing credentials: an actor lookup failure
                # afterward must not misclassify the already completed rotation.
                actor = service._admin_actor(request)

                def rotation_audit(log_action, target, before, after):
                    service._write_reset_log(
                        request,
                        actor,
                        log_action,
                        target,
                        before,
                        after,
                    )

                return service._admin_credential_service(rotation_audit).rotate(
                    form=form,
                    expected_revision=expected_revision,
                )
            if action == 'delete':
                return service._user_deletion_service().delete(
                    form=form,
                    expected_revision=expected_revision,
                )
            if action == 'cycle':
                return service._traffic_mutation_service(audit).configure_cycle(form=form)
            if action == 'reset-usage':
                return service._traffic_mutation_service(audit).reset_user(
                    form=form,
                    expected_revision=expected_revision,
                )
            if action == 'refresh-usage':
                return service._traffic_mutation_service(audit).refresh_user(
                    form=form,
                    expected_revision=expected_revision,
                )
            if action == 'reset-usage-all':
                return service._traffic_mutation_service(audit).reset_all()
            if action == 'pause-user':
                return service._user_status_service(audit).pause(
                    form=form,
                    expected_revision=expected_revision,
                )
            desired = (form.get('desired') or [''])[0]
            return service._user_status_service(audit).toggle(
                form=form,
                desired=desired,
                expected_revision=expected_revision,
            )

        return self._run_operation(mutate, post_path=post_paths[action])

    def read_session(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if service.is_logged_in(request):
                return {'role': 'admin'}
            username, credential_kind = service.get_logged_in_user_context(request)
            if not username:
                raise LoginRequired
            users = service.load_json(service.USERS_FILE, {})
            error = service.user_panel_access_error(
                users.get(username),
                credential_kind,
            )
            if error:
                raise UserAccessDenied(error)
            return {'role': 'user', 'username': username}

        return self._run_read(read)

    def read_user_identity(self, *, headers, path):
        """Return only the authenticated user identity for document guards."""
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            username, credential_kind = service.get_logged_in_user_context(request)
            if not username:
                raise LoginRequired
            return {
                'role': 'user',
                'username': str(username),
                'credential_kind': str(credential_kind),
            }

        return self._run_read(read)

    def read_admin_settings(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            meta = service.load_meta()
            return {
                'username': str(meta.get('admin_user', 'admin')),
                'password_min_length': service.PASSWORD_MIN_LENGTH,
                'password_max_length': service.PASSWORD_MAX_LENGTH,
            }

        return self._run_read(read)

    def read_user_password(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            username, credential_kind = service.get_logged_in_user_context(request)
            if not username or credential_kind != service.USER_SESSION_PANEL_PASSWORD:
                raise LoginRequired
            users = service.load_json(service.USERS_FILE, {})
            config = users.get(username)
            if not isinstance(config, dict):
                raise UserAccessDenied(
                    'forbidden',
                    cookie=service.clear_user_session_cookie(
                        secure=http_utils.is_secure_request(request),
                    ),
                )
            error = service.user_panel_access_error(config, credential_kind)
            if error and error != 'password_change_required':
                raise UserAccessDenied(error)
            return {
                'username': username,
                'password_min_length': service.PASSWORD_MIN_LENGTH,
                'password_max_length': service.PASSWORD_MAX_LENGTH,
            }

        return self._run_read(read)

    def read_admin_overview(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_overview_json_payload(now=service.local_now())

        return self._run_read(read)

    def read_admin_overview_page(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            host = service.configured_public_host(request.headers.get('Host', '127.0.0.1'))
            base_url = service.safe_base_url(
                host,
                request.headers.get('X-Forwarded-Proto', 'http'),
                request.headers.get('X-Forwarded-Port', ''),
            )
            return admin_overview_data.build_page(service._admin_views_context(), base_url)

        return self._run_read(read)

    def read_admin_usage(self, *, headers, path, include_charts):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_analytics_json_payload(
                now=service.local_now(),
                include_charts=bool(include_charts),
            )

        return self._run_read(read)

    def read_admin_usage_csv(self, *, headers, path, window):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            if window not in ('cycle', '30d'):
                raise ValueError('invalid usage export window')
            now = service.local_now()
            return {
                'body': service._build_usage_csv(now=now, window=window),
                'filename': f'usage-{window}-{now.strftime("%Y%m%d")}.csv',
            }

        return self._run_read(read)

    def read_admin_usage_history(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_daily_history_json_payload(now=service.local_now())

        return self._run_read(read)

    def read_admin_user_detail(self, *, headers, path, uid):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module
        if not isinstance(uid, str) or not uid or '/' in uid:
            raise ValueError('invalid user id')

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_user_json_payload(
                uid,
                now=service.local_now(),
                include_charts=True,
            )

        return self._run_read(read)

    def read_admin_health(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_health_json_payload(now=service.local_now())

        return self._run_read(read)

    def read_admin_incidents(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service.build_incident_payload(now=service.local_now())

        return self._run_read(read)

    def submit_health_operation(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        action: Literal[
            'update-check',
            'update-apply',
            'test-alert',
            'multiplier-apply',
            'multiplier-auto',
        ],
    ):
        del client_address
        request = self._bridge(headers=headers, path=path)
        service = self.service_module
        valid_actions = {
            'update-check',
            'update-apply',
            'test-alert',
            'multiplier-apply',
            'multiplier-auto',
        }
        if action not in valid_actions:
            raise ValueError('invalid health operation action')

        def mutate():
            if not service.is_logged_in(request):
                raise LoginRequired
            if action == 'update-check':
                try:
                    info = service.hysteria_update.check_and_record()
                except service.state_store.LockTimeout:
                    return {'ok': False, 'reason': 'update_busy'}
                except Exception:
                    return {'ok': False, 'reason': 'update_check_failed'}
                return {
                    'ok': True,
                    'status': 'checked',
                    'current': str(info.get('current') or ''),
                    'latest': str(info.get('latest') or ''),
                    'update_available': bool(info.get('update_available')),
                    'pending': False,
                }
            if action == 'update-apply':
                try:
                    state = service.hysteria_update.schedule_apply_async()
                except service.state_store.LockTimeout:
                    return {'ok': False, 'reason': 'update_busy'}
                except Exception:
                    return {'ok': False, 'reason': 'update_schedule_failed'}
                return service.hysteria_update.public_status(state)
            if action == 'test-alert':
                config = service.alerts.load_config()
                if not isinstance(config, dict) or not (
                    config.get('telegram') or config.get('webhook')
                ):
                    return {'ok': False, 'reason': 'alert_no_channels'}
                service._fire_test_alert(config, service._admin_actor(request))
                return {'ok': True, 'status': 'alert_dispatched'}
            if action == 'multiplier-apply':
                code = service.apply_suggested_display_multiplier(
                    actor=service._admin_actor(request),
                )
                return {
                    'ok': code == 'multiplier_applied',
                    'status': code,
                    'reason': '' if code == 'multiplier_applied' else code,
                }
            service.save_multiplier_auto_policy_from_form(form)
            return {'ok': True, 'status': 'multiplier_auto_saved'}

        return self._run_operation(mutate, post_path='/admin/health')

    def read_admin_landing(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            registry = service._landing_registry_or_empty()
            raw_nodes = registry.get('nodes', {}) if isinstance(registry, dict) else {}
            nodes = []
            for node_id, node in sorted(raw_nodes.items()):
                if not isinstance(node, dict):
                    continue
                public = service.landing_egress.public_node(node)
                if public.get('id') != node_id:
                    continue
                nodes.append(public)
            users = []
            for username, cfg in sorted(service.load_json(service.USERS_FILE, {}).items()):
                if not isinstance(cfg, dict):
                    continue
                allowed = cfg.get('landing_allowed_egress_ids', [])
                if not isinstance(allowed, list):
                    allowed = []
                users.append(
                    {
                        'user': str(username),
                        'revision': service.user_config_revision(cfg),
                        'allowed_ids': [str(item) for item in allowed if isinstance(item, str)],
                    }
                )
            return {
                'ts': service.local_now().isoformat(timespec='seconds'),
                'revision': service.content_revision(registry),
                'nodes': nodes,
                'users': users,
            }

        return self._run_read(read)

    def submit_landing_operation(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        action: Literal['save', 'delete', 'check', 'access', 'select'],
        request_user_revision='',
    ):
        if action not in ('save', 'delete', 'check', 'access', 'select'):
            raise ValueError('invalid landing operation action')
        del client_address
        request = self._bridge(headers=headers, path=path)
        service = self.service_module
        post_paths = {
            'save': '/admin/landing-egress/save',
            'delete': '/admin/landing-egress/delete',
            'check': '/admin/landing-egress/check',
            'access': '/admin/user-landing-access',
            'select': '/user/landing-egress/select',
        }

        def value(name, default=''):
            values = form.get(name) or [default]
            return str(values[0] if values else default)

        def execute():
            handler = _CapturedLegacyHandler(headers=request.headers, path=request.path)
            user_revision = str(request_user_revision or value('user_revision'))
            handled = service.landing_write_routes.handle_write(
                handler,
                service._landing_write_routes_context(),
                path=post_paths[action],
                form=form,
                query={},
                request_user_revision=user_revision,
            )
            if not handled:
                raise ValueError('landing operation route is not registered')
            if handler.redirect_to == '/login':
                raise LoginRequired
            if handler.response_status is not None:
                status = handler.response_status
                if status == 403:
                    error = 'forbidden'
                elif status == 404:
                    error = 'not_found'
                elif status == 409:
                    error = 'revision_conflict'
                elif status == 429:
                    error = 'rate_limited'
                else:
                    error = 'validation_error'
                retry_after = None
                raw_retry_after = handler.response_headers.get('retry-after')
                if raw_retry_after:
                    try:
                        retry_after = int(raw_retry_after)
                    except (TypeError, ValueError):
                        retry_after = None
                result = {'ok': False, 'action': action, 'error': error}
                if retry_after and retry_after > 0:
                    result['retry_after'] = retry_after
                return result
            if not handler.redirect_to:
                return {
                    'ok': False,
                    'action': action,
                    'error': 'validation_error',
                    'code': 'missing_result',
                }
            registry = service._landing_registry_or_empty()
            revision = service.content_revision(registry)
            return {'ok': True, 'action': action, 'revision': revision}

        return self._run_operation(execute, post_path=post_paths[action])

    def read_user_panel(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            username, session_kind = service.get_logged_in_user_context(request)
            if not username:
                raise LoginRequired
            users = service.load_json(service.USERS_FILE, {})
            cfg = users.get(username)
            if not isinstance(cfg, dict):
                raise UserAccessDenied(
                    'forbidden',
                    cookie=service.clear_user_session_cookie(
                        secure=service.is_secure_request(request),
                    ),
                )
            access_error = service.user_panel_access_error(
                cfg,
                session_kind,
                today=service.local_now().date(),
            )
            if access_error:
                raise UserAccessDenied(access_error)
            now = service.local_now()
            stats = service._build_panel_json_payload(username, cfg, now=now)
            reset_date, days_left, cycle_len = service._cycle_reset_info(now)
            expiry = service.user_expiry_state(cfg, today=now.date())
            inactive = bool(cfg.get('disabled')) or bool(expiry.get('expired'))
            profiles = []
            if not inactive:
                token = str(cfg.get('sub_token') or '')
                host = service.configured_public_host(request.headers.get('Host', '127.0.0.1'))
                base_url = service.safe_base_url(
                    host,
                    request.headers.get('X-Forwarded-Proto', 'http'),
                    request.headers.get('X-Forwarded-Port', ''),
                )
                for key in service.SUBSCRIPTION_PROFILE_ORDER:
                    meta = service.SUBSCRIPTION_PROFILES[key]
                    profiles.append(
                        {
                            'key': key,
                            'label': str(meta.get('label', key)),
                            'description': str(meta.get('desc', '')),
                            'url': service.subscription_profile_url(base_url, username, token, key),
                            'qr_path': service.subscription_profile_qr_path(username, token, key),
                        }
                    )
            landing_nodes = []
            selected = str(cfg.get('landing_selected_egress_id') or '')
            for node in service._authorized_landing_nodes(cfg):
                public = service.landing_egress.public_node(node)
                health = public.get('health') or {}
                landing_nodes.append(
                    {
                        **public,
                        'selected': public['id'] == selected,
                        'health_status': str(health.get('status') or '未探测'),
                    }
                )
            return {
                **stats,
                'username': str(username),
                'revision': service.user_config_revision(cfg),
                'cycle_reset_date': reset_date,
                'cycle_days_left': days_left,
                'cycle_length_days': cycle_len,
                'disabled': bool(cfg.get('disabled')),
                'expired': bool(expiry.get('expired')),
                'expiry_label': str(expiry.get('label') or ''),
                'can_change_password': session_kind == service.USER_SESSION_PANEL_PASSWORD
                and not inactive,
                'can_select_egress': session_kind == service.USER_SESSION_PANEL_PASSWORD
                and not inactive
                and bool(landing_nodes),
                'subscription_profiles': profiles,
                'landing_nodes': landing_nodes,
            }

        return self._run_read(read)

    def read_admin_config(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            try:
                config, revision = service.load_template_config_snapshot()
            except (service.TemplateConfigError, OSError, UnicodeError) as exc:
                raise StateUnavailable from exc
            return {'config': config, 'revision': revision}

        return self._run_read(read)

    def read_admin_rules(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            try:
                rules, revision = service.load_template_rules_snapshot()
            except (service.TemplateConfigError, OSError, UnicodeError) as exc:
                raise StateUnavailable from exc
            packs = []
            pack_map = getattr(service, 'RULE_PACKS', {})
            pack_order = getattr(service, 'RULE_PACK_ORDER', tuple(pack_map))
            if isinstance(pack_map, Mapping):
                for key in pack_order:
                    pack = pack_map.get(key)
                    if not isinstance(pack, Mapping):
                        continue
                    label = str(pack.get('label') or '').strip()
                    description = str(pack.get('desc') or '').strip()
                    if label and description:
                        packs.append(
                            {
                                'key': str(key),
                                'label': label,
                                'description': description,
                            }
                        )
            users_data = service.load_json(getattr(service, 'USERS_FILE', ''), {})
            users = sorted(
                str(username)
                for username, config in (
                    users_data.items() if isinstance(users_data, Mapping) else ()
                )
                if isinstance(config, Mapping) and str(username).strip()
            )
            return {'rules': rules, 'revision': revision, 'packs': packs, 'users': users}

        return self._run_read(read)

    def submit_rules_operation(
        self,
        *,
        headers,
        path,
        form,
        client_address,
        action: Literal['add', 'delete', 'pack'],
    ):
        if action not in ('add', 'delete', 'pack'):
            raise ValueError('invalid rules operation action')
        del client_address
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def value(name, default=''):
            values = form.get(name) or [default]
            return str(values[0] if values else default)

        def snapshot_revision():
            try:
                _rules, revision = service.load_template_rules_snapshot()
            except (service.TemplateConfigError, OSError, UnicodeError) as exc:
                raise StateUnavailable from exc
            return revision

        def validation(code):
            return {'ok': False, 'action': action, 'error': 'validation_error', 'code': code}

        def conflict():
            return {'ok': False, 'action': action, 'error': 'revision_conflict'}

        def mutate():
            if not service.is_logged_in(request):
                raise LoginRequired

            if action == 'add':
                rule_type = value('rule_type', 'DOMAIN-SUFFIX')
                pattern = value('pattern').strip()
                rule_action = value('action', 'DIRECT')
                extra = value('extra')
                if not pattern:
                    return validation('pattern_empty')
                if rule_type not in ('DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'DOMAIN', 'IP-CIDR'):
                    return validation('invalid_rule_type')
                if ',' in pattern or any(ord(char) < 32 for char in pattern) or len(pattern) > 512:
                    return validation('invalid_pattern')
                if rule_action not in ('DIRECT', 'REJECT', '🚀 节点选择'):
                    return validation('invalid_action')
                if extra not in ('', 'no-resolve'):
                    return validation('invalid_extra')
                rule = f'{rule_type},{pattern},{rule_action}'
                if extra:
                    rule += f',{extra}'
                if not service.validate_clash_rule(rule):
                    return validation('invalid_rule_schema')
                try:
                    service.add_template_rule(
                        rule,
                        expected_revision=value('template_revision'),
                    )
                except service.TemplateConflictError:
                    return conflict()
                except service.TemplateConfigError:
                    return validation('load_failed')
                return {'ok': True, 'action': action, 'revision': snapshot_revision()}

            if action == 'delete':
                try:
                    index = int(value('index'))
                except (TypeError, ValueError):
                    return validation('invalid_index')
                try:
                    deleted = service.delete_template_rule(
                        index,
                        expected_revision=value('template_revision'),
                        expected_rule=value('expected_rule'),
                    )
                except service.TemplateConflictError:
                    return conflict()
                except service.TemplateConfigError:
                    return validation('load_failed')
                if not deleted:
                    return validation('index_out_of_range')
                return {'ok': True, 'action': action, 'revision': snapshot_revision()}

            pack = value('pack')
            scope = value('scope', 'global')
            pack_map = getattr(service, 'RULE_PACKS', {})
            if not isinstance(pack_map, Mapping) or pack not in pack_map:
                return validation('invalid_rule_pack')
            if scope == 'global':
                try:
                    applied = service.apply_rule_pack_to_template(
                        pack,
                        expected_revision=value('template_revision'),
                    )
                except service.TemplateConflictError:
                    return conflict()
                except service.TemplateConfigError:
                    return validation('load_failed')
                if not applied:
                    return validation('invalid_rule_pack')
                return {'ok': True, 'action': action, 'revision': snapshot_revision()}
            if scope == 'user':
                username = value('user').strip()
                if not username or not service.apply_rule_pack_to_user(username, pack):
                    return {
                        'ok': False,
                        'action': action,
                        'error': 'user_not_found',
                    }
                return {'ok': True, 'action': action, 'user': username}
            return validation('invalid_rule_pack_scope')

        post_paths = {
            'add': '/admin/rules/add',
            'delete': '/admin/rules/delete',
            'pack': '/admin/rule-pack/apply',
        }
        return self._run_operation(mutate, post_path=post_paths[action])

    def submit_template_config(self, *, headers, path, form):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def mutate():
            if not service.is_logged_in(request):
                raise LoginRequired
            raw = (form.get('config_json') or [''])[0]
            expected_revision = (form.get('template_revision') or [''])[0]
            if not raw.strip():
                return {'ok': False, 'error': 'validation_error', 'code': 'empty'}
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {'ok': False, 'error': 'validation_error', 'code': 'invalid_json'}
            if not service.validate_template_config(data):
                return {'ok': False, 'error': 'validation_error', 'code': 'schema_invalid'}
            try:
                service.replace_template_config(data, expected_revision=expected_revision)
                _data, revision = service.load_template_config_snapshot()
            except service.TemplateConflictError:
                return {'ok': False, 'error': 'revision_conflict'}
            except (service.TemplateConfigError, OSError, UnicodeError) as exc:
                raise StateUnavailable from exc
            return {'ok': True, 'revision': revision}

        return self._run_operation(mutate, post_path='/admin/config/save')

    def submit_template_rules(self, *, headers, path, form):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def mutate():
            if not service.is_logged_in(request):
                raise LoginRequired
            raw = (form.get('rules_raw') or [''])[0]
            expected_revision = (form.get('template_revision') or [''])[0]
            rules = [line.strip() for line in raw.splitlines() if line.strip()]
            if not rules:
                return {'ok': False, 'error': 'validation_error', 'code': 'raw_empty'}
            if len(rules) > 5000 or any(not service.validate_clash_rule(rule) for rule in rules):
                return {'ok': False, 'error': 'validation_error', 'code': 'invalid_rule_schema'}
            try:
                service.replace_template_rules(rules, expected_revision=expected_revision)
                _rules, revision = service.load_template_rules_snapshot()
            except service.TemplateConflictError:
                return {'ok': False, 'error': 'revision_conflict'}
            except (service.TemplateConfigError, OSError, UnicodeError) as exc:
                raise StateUnavailable from exc
            return {'ok': True, 'revision': revision}

        return self._run_operation(mutate, post_path='/admin/rules/raw')

    def read_admin_logs(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return read_reset_logs(
                service.RESET_LOG_FILE,
                limit=300,
                action_label=service._action_label,
                fmt_bytes=service.fmt_bytes,
            )

        return self._run_read(read)

    def read_admin_reload_status(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._static_reload_status()

        return self._run_read(read)
