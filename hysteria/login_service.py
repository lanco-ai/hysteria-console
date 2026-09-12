"""Transport-free login decisions with bounded verification reservations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Mapping

import user_compat

LoginOutcome = Literal['success', 'invalid', 'missing', 'throttled', 'disabled', 'expired']
LoginRealm = Literal['admin', 'user']


@dataclass(frozen=True, slots=True)
class LoginResult:
    outcome: LoginOutcome
    realm: LoginRealm = 'admin'
    username: str = ''
    session_id: str = field(default='', repr=False)
    redirect_to: str = ''
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class LoginService:
    PASSWORD_MAX_LENGTH: int
    USERS_FILE: Path
    _LOGIN_WINDOW: int
    _begin_login_attempt: Callable[..., bool]
    _finish_login_attempt: Callable[..., object]
    _user_login_failures: dict
    _credential_generation: Callable[[str], str]
    create_session: Callable[[str, str], str]
    create_user_session: Callable[[str, str], str]
    is_valid_username: Callable[[str], bool]
    load_json: Callable[[Path, object], object]
    local_now: Callable[[], datetime]
    verify_secret: Callable[[str, str], bool]

    def authenticate(
        self,
        *,
        form: Mapping[str, list[str]],
        meta: Mapping[str, object],
        client_ip: str,
    ) -> LoginResult:
        admin_username = (form.get('admin_username') or [''])[0].strip()
        admin_password = (form.get('admin_password') or [''])[0]
        user_username = (form.get('user_username') or [''])[0].strip()
        user_password = (form.get('user_password') or [''])[0]

        if admin_username:
            return self._authenticate_admin(
                username=admin_username,
                password=admin_password,
                meta=meta,
                client_ip=client_ip,
            )
        if user_username:
            return self._authenticate_user(
                username=user_username,
                password=user_password,
                client_ip=client_ip,
            )
        return LoginResult(outcome='missing')

    def _authenticate_admin(
        self,
        *,
        username: str,
        password: str,
        meta: Mapping[str, object],
        client_ip: str,
    ) -> LoginResult:
        if not self._begin_login_attempt(client_ip):
            return LoginResult(
                outcome='throttled',
                realm='admin',
                username=username,
                retry_after=self._LOGIN_WINDOW,
            )

        reservation_outcome: bool | None = None
        try:
            stored_hash = str(meta.get('admin_pass_hash') or '')
            accepted = bool(
                username == meta.get('admin_user')
                and len(password) <= self.PASSWORD_MAX_LENGTH
                and stored_hash
                and self.verify_secret(password, stored_hash)
            )
            reservation_outcome = accepted
        finally:
            self._finish_login_attempt(client_ip, reservation_outcome)

        if not accepted:
            return LoginResult(outcome='invalid', realm='admin', username=username)
        session_id = self.create_session('admin', self._credential_generation(stored_hash))
        return LoginResult(
            outcome='success',
            realm='admin',
            username=username,
            session_id=session_id,
            redirect_to='/admin?msg=login+success',
        )

    def _authenticate_user(self, *, username: str, password: str, client_ip: str) -> LoginResult:
        failures = self._user_login_failures
        if not self._begin_login_attempt(client_ip, failures):
            return LoginResult(
                outcome='throttled',
                realm='user',
                username=username,
                retry_after=self._LOGIN_WINDOW,
            )

        reservation_outcome: bool | None = None
        try:
            users = self.load_json(self.USERS_FILE, {})
            config = users.get(username) if isinstance(users, dict) else None
            stored_hash = (
                str(config.get('panel_pass_hash') or '') if isinstance(config, dict) else ''
            )
            accepted = bool(
                self.is_valid_username(username)
                and len(password) <= self.PASSWORD_MAX_LENGTH
                and stored_hash
                and self.verify_secret(password, stored_hash)
            )
            if not accepted:
                reservation_outcome = False
                return LoginResult(outcome='invalid', realm='user', username=username)
            if config.get('disabled'):
                return LoginResult(outcome='disabled', realm='user', username=username)
            if user_compat.is_expired(config, today=self.local_now().date()):
                return LoginResult(outcome='expired', realm='user', username=username)
            reservation_outcome = True
        finally:
            self._finish_login_attempt(client_ip, reservation_outcome, failures)

        session_id = self.create_user_session(username, self._credential_generation(stored_hash))
        redirect_to = (
            '/user/change-password' if config.get('panel_password_must_change') else '/user/panel'
        )
        return LoginResult(
            outcome='success',
            realm='user',
            username=username,
            session_id=session_id,
            redirect_to=redirect_to,
        )
