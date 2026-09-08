"""Strict static-access authorization and reconciliation of generated configs."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, ContextManager

import cycle as cycle_util
import display as display_config
import landing_egress
import state_store
import static_access
import tuic_config
import user_compat
import xray_config


@dataclass(frozen=True)
class AuthorizationService:
    CYCLE_LENGTH_MAX: int
    CYCLE_LENGTH_MIN: int
    DISPLAY_MULTIPLIER_STATE_FILE: Path
    USAGE_DAILY_FILE: Path
    USAGE_FILE: Path
    _CYCLE_USAGE_CACHE_HARD_MAX: int
    _CYCLE_USAGE_CACHE_MIN: int
    _cycle_usage_cache: OrderedDict
    _cycle_usage_cache_lock: ContextManager[object]
    _using_live_core_state: Callable[[], bool]
    is_valid_username: Callable[[str], bool]
    load_json: Callable[..., object]
    load_meta: Callable[[], dict]
    local_now: Callable[[], datetime]

    def _critical_authorization_state(self, detail):
        raise state_store.CriticalStateUnavailable(
            f'authorization state is invalid: {detail}',
        )

    def _strict_usage_entry_total(self, entry, *, field):
        """Read one canonical daily entry without allowing quota fail-open values."""
        if isinstance(entry, dict):
            unknown = set(entry) - {'tx', 'rx', 'total'}
            if unknown:
                self._critical_authorization_state(
                    f'{field} has unsupported fields',
                )
            values = {}
            for direction in ('tx', 'rx', 'total'):
                value = entry.get(direction, 0)
                if isinstance(value, bool):
                    self._critical_authorization_state(
                        f'{field}.{direction} must be a non-negative integer',
                    )
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    self._critical_authorization_state(
                        f'{field}.{direction} must be a non-negative integer',
                    )
                if parsed < 0 or (isinstance(value, str) and str(parsed) != value.strip()):
                    self._critical_authorization_state(
                        f'{field}.{direction} must be a non-negative integer',
                    )
                values[direction] = parsed
            if values['total'] != values['tx'] + values['rx']:
                self._critical_authorization_state(
                    f'{field}.total must equal tx + rx',
                )
            return values['total']
        if isinstance(entry, bool):
            self._critical_authorization_state(
                f'{field} must be a non-negative integer',
            )
        try:
            total = int(entry or 0)
        except (TypeError, ValueError):
            self._critical_authorization_state(
                f'{field} must be a non-negative integer',
            )
        if total < 0 or (isinstance(entry, str) and str(total) != entry.strip()):
            self._critical_authorization_state(
                f'{field} must be a non-negative integer',
            )
        return total

    def _cycle_usage_sum_strict(self, daily, cycle_days, username):
        """Raw cycle bytes for one user, with the exact original validation."""
        used = 0
        for day_key in cycle_days:
            bucket = daily.get(day_key, {})
            if not isinstance(bucket, dict):
                self._critical_authorization_state(
                    f'usage day {day_key!r} must be an object',
                )
            used += self._strict_usage_entry_total(
                bucket.get(username, 0),
                field=f'{day_key}.{username}',
            )
        return used

    def _cycle_usage_cache_bound(self, user_count):
        """Effective capacity for one plan build: scales with users, never
        below the floor, never above the hard cap."""
        return min(
            self._CYCLE_USAGE_CACHE_HARD_MAX,
            max(self._CYCLE_USAGE_CACHE_MIN, 4 * user_count),
        )

    def _prune_cycle_usage_cache(self, max_entries):
        """Shrink to the effective bound. Caller holds _cycle_usage_cache_lock.
        Runs on hits too: after a user-count shrink, a hit-only sequence must
        not keep the cache above the current bound indefinitely."""
        while len(self._cycle_usage_cache) > max_entries:
            self._cycle_usage_cache.popitem(last=False)

    def _usage_daily_file_version(self):
        """(mtime_ns, size) version of the live usage_daily file, or None."""
        try:
            st = self.USAGE_DAILY_FILE.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _cached_cycle_usage_sum(self, daily, cycle_days, username, *, version, max_entries):
        """Cycle usage sum with a bounded cache keyed on the exact inputs.

        Cache hit requires: same cycle days, same usage_daily file version
        (mtime_ns + size), same username, and live core state. Anything else —
        including tests and alternate roots — bypasses the cache entirely."""
        if version is None:
            return self._cycle_usage_sum_strict(daily, cycle_days, username)
        key = (tuple(cycle_days), version, username)
        with self._cycle_usage_cache_lock:
            hit = self._cycle_usage_cache.get(key)
            if hit is not None:
                self._cycle_usage_cache.move_to_end(key)
                self._prune_cycle_usage_cache(max_entries)
                return hit
        # Compute outside the lock; strict validation raises before any caching.
        used = self._cycle_usage_sum_strict(daily, cycle_days, username)
        with self._cycle_usage_cache_lock:
            self._cycle_usage_cache[key] = used
            self._cycle_usage_cache.move_to_end(key)
            self._prune_cycle_usage_cache(max_entries)
        return used

    def _validate_authorization_meta(self, meta):
        if not isinstance(meta, dict):
            self._critical_authorization_state('subscription metadata must be an object')
        for field, minimum, maximum in (
            ('settlement_day', 1, 28),
            ('cycle_length_days', self.CYCLE_LENGTH_MIN, self.CYCLE_LENGTH_MAX),
        ):
            if field not in meta:
                continue
            value = meta[field]
            if isinstance(value, bool):
                self._critical_authorization_state(f'{field} is invalid')
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                self._critical_authorization_state(f'{field} is invalid')
            if not minimum <= parsed <= maximum or (
                isinstance(value, str) and str(parsed) != value.strip()
            ):
                self._critical_authorization_state(f'{field} is invalid')
        anchor = meta.get('cycle_anchor_date')
        if anchor not in (None, ''):
            if not isinstance(anchor, str):
                self._critical_authorization_state('cycle_anchor_date is invalid')
            try:
                datetime.strptime(anchor, '%Y-%m-%d')
            except ValueError:
                self._critical_authorization_state('cycle_anchor_date is invalid')

    def _build_static_access_plan(self, users, daily, meta, *, now=None, usage_version=None):
        """Derive the exact generated-proxy authorization set from core state.

        usage_version: (mtime_ns, size) of the usage_daily file the `daily` dict
        was loaded from, taken under usage_lock. When provided, the raw per-user
        cycle aggregation may be served from a small LRU cache. The cache never
        stores allow/deny decisions — disabled/expires/quota/uuid/multiplier are
        re-evaluated on every call."""
        current = now or self.local_now()
        if not self._using_live_core_state():
            # Tests and alternate roots must never share the live aggregation
            # cache — one test's usage data must not leak into another.
            usage_version = None
        self._validate_authorization_meta(meta)
        multiplier_path = self.DISPLAY_MULTIPLIER_STATE_FILE
        if not self._using_live_core_state():
            multiplier_path = (
                Path(self.USAGE_FILE).parent / Path(self.DISPLAY_MULTIPLIER_STATE_FILE).name
            )
        try:
            multiplier = display_config.effective_display_multiplier_strict(
                path=multiplier_path,
            )
        except ValueError as exc:
            raise state_store.CriticalStateUnavailable(
                'display multiplier policy is invalid',
            ) from exc
        cycle_days = cycle_util.cycle_days(current, meta=meta)
        plan = {}
        claimed_vless_uuids = {}
        for username, cfg in users.items():
            if not self.is_valid_username(username):
                self._critical_authorization_state(
                    f'invalid user key {username!r}',
                )
            config_error = user_compat.authorization_config_error(cfg)
            if config_error:
                self._critical_authorization_state(
                    f'user {username!r}: {config_error}',
                )
            if user_compat.is_inactive(cfg, today=current.date()):
                plan[username] = None
                continue
            quota = user_compat.total_quota_bytes(cfg)
            if user_compat.is_metered(cfg) and quota > 0:
                used = self._cached_cycle_usage_sum(
                    daily,
                    cycle_days,
                    username,
                    version=usage_version,
                    max_entries=self._cycle_usage_cache_bound(len(users)),
                )
                if used * multiplier >= quota:
                    plan[username] = None
                    continue
            vless_uuid = str(cfg.get('vless_uuid') or '').strip()
            if vless_uuid:
                try:
                    uuid_key = uuid.UUID(vless_uuid).hex
                except (ValueError, AttributeError, TypeError):
                    self._critical_authorization_state(
                        f'user {username!r}: vless_uuid is invalid',
                    )
                previous = claimed_vless_uuids.get(uuid_key)
                if previous is not None:
                    self._critical_authorization_state(
                        f'users {previous!r} and {username!r} share vless_uuid',
                    )
                claimed_vless_uuids[uuid_key] = username
                plan[username] = vless_uuid
        return plan

    def _build_landing_access_plan(self, users, direct_plan, egress_nodes):
        """Derive fail-closed residential VLESS identities from active users."""
        result = {}
        claimed = {}
        for direct_user, value in (direct_plan or {}).items():
            if not value:
                continue
            try:
                key = uuid.UUID(str(value).strip()).hex
            except (ValueError, AttributeError, TypeError):
                self._critical_authorization_state(
                    f'user {direct_user!r}: active vless_uuid is invalid',
                )
            claimed[key] = f'direct:{direct_user}'
        nodes = egress_nodes if isinstance(egress_nodes, dict) else {}
        for username, cfg in sorted((users or {}).items()):
            if not direct_plan.get(username) or not isinstance(cfg, dict):
                continue
            allowed = cfg.get('landing_allowed_egress_ids')
            if allowed is None or allowed == []:
                continue
            if not isinstance(allowed, list):
                self._critical_authorization_state(
                    f'user {username!r}: landing authorization is invalid',
                )
            raw_uuid = str(cfg.get('landing_vless_uuid') or '').strip()
            try:
                parsed = uuid.UUID(raw_uuid)
            except (ValueError, AttributeError, TypeError):
                self._critical_authorization_state(
                    f'user {username!r}: landing_vless_uuid is invalid',
                )
            uuid_key = parsed.hex
            previous = claimed.get(uuid_key)
            if previous is not None:
                self._critical_authorization_state(
                    f'landing identity for {username!r} conflicts with {previous}',
                )
            allowed_ids = {value for value in allowed if isinstance(value, str) and value in nodes}
            if not allowed_ids:
                continue
            selected = cfg.get('landing_selected_egress_id')
            if selected not in allowed_ids:
                selected = None
            parsed_uuid = str(parsed)
            claimed[uuid_key] = f'landing:{username}'
            result[str(username)] = {
                'uuid': parsed_uuid,
                'selected_id': selected,
            }
        return result

    def _sync_static_access_from_users(self, users, *, now=None):
        """Exact-reconcile both generated proxy configs. Caller holds usage_lock."""
        live = self._using_live_core_state()
        if not live:
            # Alternate roots are validation/test artifacts. They must never read
            # host billing state or create generated credentials beside it.
            return False, False
        daily = self.load_json(self.USAGE_DAILY_FILE, {})
        # usage_lock is held, so the file cannot change between the load and this
        # stat — the version provably describes the dict we just read. It keys
        # the raw cycle-usage aggregation cache inside the plan builder.
        usage_version = self._usage_daily_file_version()
        meta = self.load_meta()
        plan = self._build_static_access_plan(
            users,
            daily,
            meta,
            now=now,
            usage_version=usage_version,
        )
        try:
            egress_registry = landing_egress.load_registry()
        except state_store.InvalidJsonState:
            egress_registry = landing_egress.empty_registry()
        egress_nodes = egress_registry['nodes']
        landing_plan = self._build_landing_access_plan(users, plan, egress_nodes)
        xray_kwargs = {'prune_unknown': True}
        tuic_kwargs = {}
        try:
            xray_changed = xray_config.apply_user_plan(
                plan,
                landing_plan=landing_plan,
                egress_nodes=egress_nodes,
                **xray_kwargs,
            )
            static_access.recover_if_pending(
                xray_config.RELOAD_SERVICE,
                live=live,
            )
            tuic_changed = tuic_config.sync_user_plan(
                users,
                plan,
                **tuic_kwargs,
            )
            static_access.recover_if_pending(
                tuic_config.RELOAD_SERVICE,
                live=live,
            )
        except state_store.CriticalStateUnavailable:
            raise
        except Exception as exc:
            raise state_store.CriticalStateUnavailable(
                'generated static authorization could not be reconciled',
            ) from exc
        return xray_changed, tuic_changed
