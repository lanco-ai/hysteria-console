"""User pause and enable/disable mutations independent of HTTP."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable, Mapping

from overview_mutation_result import OverviewMutationResult


@dataclass(frozen=True, slots=True)
class UserStatusService:
    USERS_FILE: Path
    _sync_static_access_from_users: Callable[..., object]
    audit: Callable[..., object]
    delete_user_sessions_for: Callable[..., object]
    hy_kick: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    parse_int_field: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    tuic_reload_async: Callable[..., object]
    usage_lock: Callable[..., object]
    xray_reload_async: Callable[..., object]

    @staticmethod
    def _first(form: Mapping[str, list[str]], name: str, default: str = '') -> str:
        return (form.get(name) or [default])[0]

    def _reload_changed(self, xray_changed, tuic_changed):
        if xray_changed:
            self.xray_reload_async()
        if tuic_changed:
            self.tuic_reload_async()

    def pause(
        self,
        *,
        form: Mapping[str, list[str]],
        expected_revision: str,
    ) -> OverviewMutationResult:
        username = self._first(form, 'user').strip()
        minutes = self.parse_int_field(self._first(form, 'minutes', '60'), 60, 1, 1440)
        until = self.local_now() + timedelta(minutes=minutes)
        until_text = until.isoformat(timespec='seconds')
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users or not isinstance(users.get(username), dict):
                return OverviewMutationResult(outcome='not_found', username=username)
            if not self.revision_matches(users.get(username), expected_revision):
                return OverviewMutationResult(outcome='conflict', username=username)
            users[username]['disabled'] = True
            users[username]['disabled_until'] = until_text
            self.save_json(self.USERS_FILE, users)
            xray_changed, tuic_changed = self._sync_static_access_from_users(users)
        self.delete_user_sessions_for(username)
        self._reload_changed(xray_changed, tuic_changed)
        self.hy_kick([username])
        self.audit('pause_user', username, {}, {'disabled_until': until_text})
        return OverviewMutationResult(
            outcome='success',
            username=username,
            disabled_until=until_text,
        )

    def toggle(
        self,
        *,
        form: Mapping[str, list[str]],
        desired: str,
        expected_revision: str,
    ) -> OverviewMutationResult:
        if desired not in ('disabled', 'enabled'):
            return OverviewMutationResult(outcome='invalid', code='invalid_desired')
        username = self._first(form, 'user').strip()
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users or not isinstance(users.get(username), dict):
                return OverviewMutationResult(outcome='not_found', username=username)
            if not self.revision_matches(users.get(username), expected_revision):
                return OverviewMutationResult(outcome='conflict', username=username)
            disable = desired == 'disabled'
            users[username]['disabled'] = disable
            users[username].pop('disabled_until', None)
            self.save_json(self.USERS_FILE, users)
            xray_changed, tuic_changed = self._sync_static_access_from_users(users)
        if disable:
            self.delete_user_sessions_for(username)
        self._reload_changed(xray_changed, tuic_changed)
        if disable:
            self.hy_kick([username])
        self.audit('disable_user' if disable else 'enable_user', username, {}, {})
        return OverviewMutationResult(outcome='success', username=username)
