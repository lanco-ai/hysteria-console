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


@dataclass(frozen=True, slots=True)
class LoginReply:
    result: LoginResult
    cookie: str | None = field(default=None, repr=False)


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

    def read_admin_usage_history(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_daily_history_json_payload(now=service.local_now())

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
            if access_error not in ('', None, 'disabled', 'expired', 'password_change_required'):
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
            return {'rules': rules, 'revision': revision}

        return self._run_read(read)

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
