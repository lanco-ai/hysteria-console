"""Transport-free administrator and user password-change orchestration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping


@dataclass(frozen=True, slots=True)
class PasswordChangeResult:
    outcome: Literal['success', 'invalid', 'login_required', 'forbidden', 'disabled', 'expired']
    code: str = ''
    session_id: str = field(default='', repr=False)


@dataclass(frozen=True, slots=True)
class PasswordChangeService:
    PASSWORD_MAX_LENGTH: int
    PASSWORD_MIN_LENGTH: int
    SESSIONS_FILE: Path
    USERS_FILE: Path
    USER_SESSIONS_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    _change_admin_password: Callable[..., object]
    _credential_generation: Callable[..., object]
    _replace_sessions_with_new: Callable[..., object]
    hash_secret: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]
    user_panel_access_error: Callable[..., object]
    verify_secret: Callable[..., object]

    @staticmethod
    def _first(form: Mapping[str, list[str]], name: str) -> str:
        return (form.get(name) or [''])[0]

    def change_admin(self, *, form: Mapping[str, list[str]]) -> PasswordChangeResult:
        current = self._first(form, 'current')
        new = self._first(form, 'new')
        confirm = self._first(form, 'confirm')
        code, new_hash = self._change_admin_password(current, new, confirm)
        if code != 'ok':
            return PasswordChangeResult(outcome='invalid', code=str(code))
        sid = self._replace_sessions_with_new(
            self.SESSIONS_FILE,
            'admin',
            revoke_all=True,
            credential_generation=self._credential_generation(new_hash),
        )
        return PasswordChangeResult(outcome='success', session_id=str(sid))

    def change_user(
        self,
        *,
        username: str,
        session_kind: str,
        form: Mapping[str, list[str]],
    ) -> PasswordChangeResult:
        if not username or session_kind != self.USER_SESSION_PANEL_PASSWORD:
            return PasswordChangeResult(outcome='login_required')

        current_cfg = self.load_json(self.USERS_FILE, {}).get(username)
        access_error = self.user_panel_access_error(
            current_cfg,
            session_kind,
            today=self.local_now().date(),
        )
        if access_error in ('forbidden', 'disabled', 'expired'):
            return PasswordChangeResult(outcome=access_error)

        current = self._first(form, 'current')
        new = self._first(form, 'new')
        confirm = self._first(form, 'confirm')
        if len(new) < self.PASSWORD_MIN_LENGTH:
            return PasswordChangeResult(outcome='invalid', code='new password short')
        if len(new) > self.PASSWORD_MAX_LENGTH:
            return PasswordChangeResult(outcome='invalid', code='new password long')
        if new != confirm:
            return PasswordChangeResult(outcome='invalid', code='new password mismatch')

        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            cfg = users.get(username)
            locked_access_error = self.user_panel_access_error(
                cfg,
                session_kind,
                today=self.local_now().date(),
            )
            if locked_access_error in ('forbidden', 'disabled', 'expired'):
                return PasswordChangeResult(outcome=locked_access_error)
            stored_hash = str(cfg.get('panel_pass_hash') or '') if isinstance(cfg, dict) else ''
            if not (
                len(current) <= self.PASSWORD_MAX_LENGTH
                and stored_hash
                and self.verify_secret(current, stored_hash)
            ):
                return PasswordChangeResult(outcome='invalid', code='current password wrong')
            if self.verify_secret(new, stored_hash):
                return PasswordChangeResult(outcome='invalid', code='new password same')
            new_hash = self.hash_secret(new)
            cfg['panel_pass_hash'] = new_hash
            cfg.pop('panel_password_must_change', None)
            users[username] = cfg
            self.save_json(self.USERS_FILE, users)

        sid = self._replace_sessions_with_new(
            self.USER_SESSIONS_FILE,
            username,
            credential_generation=self._credential_generation(new_hash),
            credential_kind=self.USER_SESSION_PANEL_PASSWORD,
        )
        return PasswordChangeResult(outcome='success', session_id=str(sid))
