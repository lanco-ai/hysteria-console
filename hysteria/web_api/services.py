"""Adapters from the HTTP boundary to the legacy panel services."""

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
