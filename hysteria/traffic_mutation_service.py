"""Traffic and billing-cycle mutations independent of HTTP presentation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from overview_mutation_result import OverviewMutationResult


@dataclass(frozen=True, slots=True)
class TrafficMutationService:
    CYCLE_LENGTH_MAX: int
    CYCLE_LENGTH_MIN: int
    USAGE_FILE: Path
    USERS_FILE: Path
    _clear_alert_dedup_for_users: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    _update_cycle_meta: Callable[..., object]
    _zero_cycle_daily_hourly_for: Callable[..., object]
    add_preserved_for_user: Callable[..., object]
    audit: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    month_key: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    tuic_reload_async: Callable[..., object]
    usage_for_user: Callable[..., object]
    usage_lock: Callable[..., object]
    xray_reload_async: Callable[..., object]

    @staticmethod
    def _first(form: Mapping[str, list[str]], name: str) -> str:
        return (form.get(name) or [''])[0]

    def _reload_changed(self, xray_changed, tuic_changed):
        if xray_changed:
            self.xray_reload_async()
        if tuic_changed:
            self.tuic_reload_async()

    def configure_cycle(self, *, form: Mapping[str, list[str]]) -> OverviewMutationResult:
        try:
            day = int(self._first(form, 'day'))
        except (ValueError, TypeError):
            return OverviewMutationResult(outcome='invalid', code='err:settlement_invalid')
        if day < 1 or day > 28:
            return OverviewMutationResult(outcome='invalid', code='err:settlement_invalid')

        raw_length = self._first(form, 'length').strip()
        length = None
        if raw_length:
            try:
                length = int(raw_length)
            except (ValueError, TypeError):
                return OverviewMutationResult(outcome='invalid', code='err:cycle_length_invalid')
            if length < self.CYCLE_LENGTH_MIN or length > self.CYCLE_LENGTH_MAX:
                return OverviewMutationResult(outcome='invalid', code='err:cycle_length_invalid')

        self._update_cycle_meta(day, length)
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            xray_changed, tuic_changed = self._sync_static_access_from_users(users)
        self._reload_changed(xray_changed, tuic_changed)
        return OverviewMutationResult(outcome='success', day=day)

    def _mutate_user_usage(
        self,
        *,
        form: Mapping[str, list[str]],
        expected_revision: str,
        preserve: bool,
    ) -> OverviewMutationResult:
        username = self._first(form, 'user').strip()
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users:
                return OverviewMutationResult(outcome='not_found', username=username)
            if not self.revision_matches(users.get(username), expected_revision):
                return OverviewMutationResult(outcome='conflict', username=username)
            now = self.local_now()
            usage = self.load_json(self.USAGE_FILE, {})
            month = self.month_key(now)
            usage.setdefault(month, {})
            tx, rx, total = self.usage_for_user(username, now=now)
            before = {'tx': tx, 'rx': rx, 'total': total}
            if preserve:
                self.add_preserved_for_user(username, tx, rx, total, now=now)
            usage[month][username] = {'tx': 0, 'rx': 0, 'total': 0}
            after = {'tx': 0, 'rx': 0, 'total': 0}
            self.save_json(self.USAGE_FILE, usage)
            self._zero_cycle_daily_hourly_for([username], now=now)
            self._clear_alert_dedup_for_users([username], quota_only=True)
            xray_changed, tuic_changed = self._sync_static_access_from_users(users, now=now)
        self._reload_changed(xray_changed, tuic_changed)
        action = 'refresh_usage_user' if preserve else 'reset_usage_user'
        self.audit(action, username, before, after)
        return OverviewMutationResult(outcome='success', username=username)

    def reset_user(
        self,
        *,
        form: Mapping[str, list[str]],
        expected_revision: str,
    ) -> OverviewMutationResult:
        return self._mutate_user_usage(
            form=form,
            expected_revision=expected_revision,
            preserve=False,
        )

    def refresh_user(
        self,
        *,
        form: Mapping[str, list[str]],
        expected_revision: str,
    ) -> OverviewMutationResult:
        return self._mutate_user_usage(
            form=form,
            expected_revision=expected_revision,
            preserve=True,
        )

    def reset_all(self) -> OverviewMutationResult:
        with self.usage_lock():
            now = self.local_now()
            usage = self.load_json(self.USAGE_FILE, {})
            month = self.month_key(now)
            usage.setdefault(month, {})
            before_all = {}
            users = self.load_json(self.USERS_FILE, {})
            for username in users.keys():
                tx, rx, total = self.usage_for_user(username, now=now)
                before_all[username] = {'tx': tx, 'rx': rx, 'total': total}
                usage[month][username] = {'tx': 0, 'rx': 0, 'total': 0}
            self.save_json(self.USAGE_FILE, usage)
            usernames = list(users.keys())
            self._zero_cycle_daily_hourly_for(usernames, now=now)
            self._clear_alert_dedup_for_users(usernames, quota_only=True)
            xray_changed, tuic_changed = self._sync_static_access_from_users(users, now=now)
        self._reload_changed(xray_changed, tuic_changed)
        after_all = {username: {'tx': 0, 'rx': 0, 'total': 0} for username in users.keys()}
        self.audit('reset_usage_all', 'all_users', before_all, after_all)
        return OverviewMutationResult(outcome='success')
