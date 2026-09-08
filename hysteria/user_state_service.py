"""User auxiliary-state cleanup and residential-egress identity helpers."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import alerts
import landing_egress
import state_store
import subscription_profiles as profile_defs


@dataclass(frozen=True)
class UserStateService:
    USAGE_FILE: Path
    USAGE_DAILY_FILE: Path
    USAGE_HOURLY_FILE: Path
    USAGE_PRESERVED_FILE: Path
    ONLINE_FILE: Path
    DEVICE_ADMISSIONS_FILE: Path
    USER_SESSIONS_FILE: Path
    USERS_FILE: Path
    _using_live_core_state: Callable[..., object]
    load_json: Callable[..., object]
    save_json: Callable[..., object]
    _delete_sessions_for: Callable[..., object]
    usage_lock: Callable[..., object]

    def _scoped_state_path(self, path):
        """Keep alternate/test state roots isolated from the host runtime."""
        target = Path(path)
        if self._using_live_core_state():
            return target
        return Path(self.USAGE_FILE).parent / target.name

    def _clear_alert_dedup_for_users(self, usernames, *, quota_only):
        """Best-effort alert-state transaction for reset/delete operations.

        Alert state is auxiliary. A corrupt file must remain untouched for
        operator repair, but it must not roll back an otherwise valid accounting
        reset or user deletion.
        """
        alert_path = self._scoped_state_path(alerts.STATE_FILE)
        try:
            if quota_only:
                alerts.clear_quota_dedup_transaction(usernames, alert_path)
            else:
                alerts.clear_user_dedup_transaction(usernames, alert_path)
            return True
        except (state_store.StateStoreError, OSError) as exc:
            print(
                f'alert dedup update skipped: auxiliary state unavailable: {exc}',
                file=sys.stderr,
            )
            return False

    def _purge_user_history_locked(self, username):
        """Remove user-keyed state before hard deletion. Caller holds usage_lock."""
        for configured_path in (
            self.USAGE_FILE,
            self.USAGE_DAILY_FILE,
            self.USAGE_HOURLY_FILE,
            self.USAGE_PRESERVED_FILE,
        ):
            path = self._scoped_state_path(configured_path)
            data = self.load_json(path, {})
            changed = False
            for bucket in data.values():
                if isinstance(bucket, dict) and username in bucket:
                    bucket.pop(username, None)
                    changed = True
            if changed:
                self.save_json(path, data)

        online_path = self._scoped_state_path(self.ONLINE_FILE)
        online = self.load_json(online_path, {})
        if username in online:
            online.pop(username, None)
            self.save_json(online_path, online)

        admission_path = self._scoped_state_path(self.DEVICE_ADMISSIONS_FILE)
        with state_store.file_lock(admission_path.with_name(admission_path.name + '.lock')):
            admissions = self.load_json(admission_path, {})
            if username in admissions:
                admissions.pop(username, None)
                self.save_json(admission_path, admissions)

        self._clear_alert_dedup_for_users([username], quota_only=False)

        self._delete_sessions_for(
            self._scoped_state_path(self.USER_SESSIONS_FILE),
            username,
        )

    def _landing_registry_or_empty(self):
        try:
            return landing_egress.load_registry()
        except (state_store.InvalidJsonState, OSError):
            return landing_egress.empty_registry()

    def _authorized_landing_nodes(self, cfg, registry=None):
        registry = registry or self._landing_registry_or_empty()
        nodes = registry.get('nodes', {}) if isinstance(registry, dict) else {}
        allowed = cfg.get('landing_allowed_egress_ids', []) if isinstance(cfg, dict) else []
        if not isinstance(allowed, list):
            return []
        return [
            nodes[node_id]
            for node_id in allowed
            if isinstance(node_id, str)
            and isinstance(nodes.get(node_id), dict)
            and nodes[node_id].get('enabled') is True
        ]

    def _enabled_landing_public_nodes(self, registry=None):
        registry = registry or self._landing_registry_or_empty()
        nodes = registry.get('nodes', {}) if isinstance(registry, dict) else {}
        return [
            landing_egress.public_node(node)
            for _node_id, node in sorted(nodes.items())
            if isinstance(node, dict) and node.get('enabled') is True
        ]

    def _ensure_landing_vless_uuid(self, cfg, users):
        current = str(cfg.get('landing_vless_uuid') or '').strip()
        if current:
            return current

        def uuid_identity(value):
            raw = str(value or '').strip()
            if not raw:
                return ''
            try:
                return uuid.UUID(raw).hex
            except (ValueError, AttributeError):
                # Invalid persisted values remain occupied as raw identities here;
                # their schema validation still fails closed in config generation.
                return 'raw:' + raw.lower()

        occupied = {
            uuid_identity(item.get(field))
            for item in users.values()
            if isinstance(item, dict)
            for field in ('vless_uuid', 'landing_vless_uuid')
        }
        occupied.add(uuid_identity(cfg.get('vless_uuid')))
        occupied.discard('')
        while True:
            candidate = str(uuid.uuid4())
            if uuid_identity(candidate) not in occupied:
                cfg['landing_vless_uuid'] = candidate
                return candidate

    def apply_rule_pack_to_user(self, username, pack_key):
        username = str(username or '').strip()
        if not username:
            return False
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            cfg = users.get(username)
            if not isinstance(cfg, dict):
                return False
            if not profile_defs.apply_rule_pack_to_user_config(cfg, pack_key):
                return False
            users[username] = cfg
            self.save_json(self.USERS_FILE, users)
        return True
