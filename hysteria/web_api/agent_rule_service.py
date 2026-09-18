"""Allowlisted, revision-checked rule changes for the administrator agent.

The model never receives a write primitive.  It can only produce a typed plan;
this service validates the target, computes a diff, and performs the actual
user-state transaction after the administrator confirms the plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
from typing import Mapping

import state_store
import subscription_profiles


RULE_KEYS = (
    subscription_profiles.USER_CLASH_RULES_KEY,
    subscription_profiles.USER_FAKE_IP_FILTER_KEY,
    subscription_profiles.USER_TUN_ROUTE_EXCLUDE_ADDRESS_KEY,
)
PENDING_TTL_SECONDS = 24 * 60 * 60
HISTORY_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_CHANGE_RECORDS = 100
ALLOWED_RULE_TYPES = frozenset({
    'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'DOMAIN', 'IP-CIDR', 'IP-CIDR6', 'PROCESS-NAME',
})
ALLOWED_RULE_ACTIONS = frozenset({'DIRECT', 'REJECT', '🚀 节点选择'})
ALLOWED_RULE_EXTRAS = frozenset({'', 'no-resolve'})


class AgentServiceError(RuntimeError):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(message or code)


def _revision(cfg: Mapping[str, object]) -> str:
    payload = json.dumps(cfg, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _context_for_users(service):
    lock = getattr(service, 'usage_lock', None)
    return lock() if callable(lock) else nullcontext()


def _merge_unique_rules(*groups):
    merged = []
    seen = set()
    for group in groups:
        for rule in group:
            value = str(rule).strip()
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
    return merged


class AgentRuleService:
    """Read, preview, apply and undo only the supported user rule fields."""

    def __init__(self, service, *, changes_path: str | Path | None = None):
        if service is None:
            raise AgentServiceError('state_unavailable')
        self.service = service
        users_path = getattr(service, 'USERS_FILE', None)
        if not isinstance(users_path, (str, Path)) or not str(users_path).strip():
            raise AgentServiceError('state_unavailable')
        self.users_path = Path(users_path)
        self.changes_path = Path(changes_path) if changes_path else (
            self.users_path.parent / 'state' / 'agent' / 'rule_changes.json'
        )

    def _load_users(self):
        try:
            users = self.service.load_json(self.users_path, {})
        except (OSError, ValueError, TypeError) as exc:
            raise AgentServiceError('state_unavailable') from exc
        if not isinstance(users, dict):
            raise AgentServiceError('state_unavailable')
        return users

    def _validate_username(self, username: str) -> str:
        value = str(username or '').strip()
        if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
            raise AgentServiceError('invalid_target')
        return value

    def _get_cfg(self, users, username):
        cfg = users.get(username)
        if not isinstance(cfg, dict):
            raise AgentServiceError('user_not_found')
        return cfg

    def _global_rules_snapshot(self):
        loader = getattr(self.service, 'load_template_rules_snapshot', None)
        if not callable(loader):
            return [], ''
        try:
            rules, revision = loader()
        except Exception as exc:
            raise AgentServiceError('state_unavailable') from exc
        if not isinstance(rules, (list, tuple)):
            raise AgentServiceError('state_unavailable')
        return [str(rule).strip() for rule in rules if str(rule).strip()], str(revision or '')

    @staticmethod
    def _rule_snapshot(cfg):
        return {
            key: {
                'present': key in cfg,
                'value': list(cfg.get(key) or []) if isinstance(cfg.get(key), list) else [],
            }
            for key in RULE_KEYS
        }

    @staticmethod
    def _restore_snapshot(cfg, snapshot):
        for key in RULE_KEYS:
            item = snapshot.get(key) if isinstance(snapshot, dict) else None
            if isinstance(item, dict) and item.get('present'):
                cfg[key] = list(item.get('value') or [])
            else:
                cfg.pop(key, None)

    def _validate_custom_rule(self, rule: str) -> str:
        value = str(rule or '').strip()
        if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
            raise AgentServiceError('invalid_rule')
        parts = [part.strip() for part in value.split(',')]
        if len(parts) not in (3, 4) or parts[0] not in ALLOWED_RULE_TYPES:
            raise AgentServiceError('invalid_rule')
        if not parts[1] or len(parts[1]) > 256 or parts[-1] == '':
            raise AgentServiceError('invalid_rule')
        if len(parts) >= 3 and parts[2] not in ALLOWED_RULE_ACTIONS:
            raise AgentServiceError('invalid_rule')
        if len(parts) == 4 and parts[3] not in ALLOWED_RULE_EXTRAS:
            raise AgentServiceError('invalid_rule')
        validator = getattr(self.service, 'validate_clash_rule', None)
        if callable(validator) and not validator(value):
            raise AgentServiceError('invalid_rule')
        return ','.join(parts)

    def _store_pending_plan(
        self,
        *,
        username,
        operation,
        pack,
        label,
        description,
        rule,
        additions,
        removals,
        before_revision,
        after_revision,
        before_global_revision='',
        before,
        after,
        operator,
        source_ip,
    ):
        change_id = secrets.token_urlsafe(24)
        with state_store.file_lock(self.changes_path.with_name(self.changes_path.name + '.lock')):
            changes = self._load_changes()
            changes[change_id] = {
                'status': 'pending',
                'created_at': _now(),
                'operator': str(operator or 'admin')[:128],
                'source_ip': str(source_ip or '')[:128],
                'username': username,
                'operation': operation,
                'pack': pack,
                'rule': rule,
                'before_revision': before_revision,
                'after_revision': after_revision,
                'before_global_revision': before_global_revision,
                'before': before,
                'after': after,
            }
            self._save_changes(changes)
        return {
            'change_id': change_id,
            'target_user': username,
            'operation': operation,
            'pack': pack,
            'rule': rule,
            'label': label,
            'description': description,
            'before_revision': before_revision,
            'after_revision': after_revision,
            'before_global_revision': before_global_revision,
            'additions': additions,
            'removals': removals,
            'requires_confirmation': True,
        }

    def get_user_rules(self, username: str):
        username = self._validate_username(username)
        with _context_for_users(self.service):
            cfg = self._get_cfg(self._load_users(), username)
            return self._snapshot_from_cfg(username, cfg)

    def _snapshot_from_cfg(self, username, cfg):
        revision_fn = getattr(self.service, 'user_config_revision', None)
        revision = revision_fn(cfg) if callable(revision_fn) else _revision(cfg)
        snapshot = self._rule_snapshot(cfg)
        global_rules, global_revision = self._global_rules_snapshot()
        user_rules = list(snapshot[subscription_profiles.USER_CLASH_RULES_KEY]['value'])
        return {
            'username': username,
            'revision': str(revision),
            'rules': user_rules,
            'fake_ip_filter': list(snapshot[subscription_profiles.USER_FAKE_IP_FILTER_KEY]['value']),
            'tun_route_exclude_address': list(snapshot[subscription_profiles.USER_TUN_ROUTE_EXCLUDE_ADDRESS_KEY]['value']),
            'global_rules': global_rules,
            'global_revision': global_revision,
            'merged_rules': _merge_unique_rules(user_rules, global_rules),
        }

    def _save_changes(self, data):
        try:
            self.changes_path.parent.mkdir(parents=True, exist_ok=True)
            state_store.save_json(self.changes_path, data)
            self.changes_path.chmod(0o600)
        except (OSError, state_store.StateStoreError) as exc:
            raise AgentServiceError('state_unavailable') from exc

    def _load_changes(self):
        try:
            data = state_store.load_json_strict(self.changes_path, {})
        except state_store.StateStoreError as exc:
            raise AgentServiceError('state_unavailable') from exc
        if not isinstance(data, dict):
            raise AgentServiceError('state_unavailable')
        now = datetime.now(timezone.utc).timestamp()
        kept = {}
        for change_id, record in data.items():
            if not isinstance(record, dict):
                continue
            age = now - _timestamp(record.get('created_at'))
            ttl = PENDING_TTL_SECONDS if record.get('status') in {'pending', 'applying', 'undoing'} else HISTORY_TTL_SECONDS
            if age <= ttl:
                kept[str(change_id)] = record
        if len(kept) > MAX_CHANGE_RECORDS:
            ordered = sorted(kept.items(), key=lambda item: _timestamp(item[1].get('created_at')), reverse=True)
            kept = dict(ordered[:MAX_CHANGE_RECORDS])
        return kept

    def preview_rule_pack(self, username: str, pack_key: str, *, expected_revision: str = '', expected_global_revision: str = '', operator: str = 'admin', source_ip: str = ''):
        username = self._validate_username(username)
        pack_key = str(pack_key or '').strip()
        packs = getattr(self.service, 'RULE_PACKS', subscription_profiles.RULE_PACKS)
        pack = packs.get(pack_key) if isinstance(packs, Mapping) else None
        if not isinstance(pack, Mapping):
            raise AgentServiceError('invalid_rule_pack')
        with _context_for_users(self.service):
            users = self._load_users()
            cfg = self._get_cfg(users, username)
            before_revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
            if expected_revision and expected_revision != before_revision:
                raise AgentServiceError('revision_conflict')
            _global_rules, before_global_revision = self._global_rules_snapshot()
            if expected_global_revision and expected_global_revision != before_global_revision:
                raise AgentServiceError('revision_conflict')
            before = self._rule_snapshot(cfg)
            changed = copy.deepcopy(cfg)
            if not subscription_profiles.apply_rule_pack_to_user_config(changed, pack_key):
                raise AgentServiceError('invalid_rule_pack')
            after = self._rule_snapshot(changed)
            after_revision = str(getattr(self.service, 'user_config_revision', _revision)(changed))
        additions = []
        for key in RULE_KEYS:
            old = before[key]['value']
            additions.extend(item for item in after[key]['value'] if item not in old)
        return self._store_pending_plan(
            username=username,
            operation='pack',
            pack=pack_key,
            label=str(pack.get('label') or pack_key),
            description=str(pack.get('desc') or ''),
            rule='',
            additions=additions,
            removals=[],
            before_revision=before_revision,
            after_revision=after_revision,
            before_global_revision=before_global_revision,
            before=before,
            after=after,
            operator=operator,
            source_ip=source_ip,
        )

    def preview_rule(
        self,
        username: str,
        operation: str,
        rule: str,
        *,
        expected_revision: str = '',
        operator: str = 'admin',
        source_ip: str = '',
        expected_global_revision: str = '',
    ):
        if operation not in ('add', 'delete'):
            raise AgentServiceError('invalid_operation')
        username = self._validate_username(username)
        rule = self._validate_custom_rule(rule)
        with _context_for_users(self.service):
            users = self._load_users()
            cfg = self._get_cfg(users, username)
            before_revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
            if expected_revision and expected_revision != before_revision:
                raise AgentServiceError('revision_conflict')
            _global_rules, before_global_revision = self._global_rules_snapshot()
            if expected_global_revision and expected_global_revision != before_global_revision:
                raise AgentServiceError('revision_conflict')
            before = self._rule_snapshot(cfg)
            changed = copy.deepcopy(cfg)
            rules = list(changed.get(subscription_profiles.USER_CLASH_RULES_KEY) or [])
            if operation == 'add':
                if rule not in rules:
                    changed[subscription_profiles.USER_CLASH_RULES_KEY] = [rule] + rules
                    additions = [rule]
                else:
                    additions = []
                removals = []
            else:
                if rule not in rules:
                    if rule in _global_rules:
                        raise AgentServiceError('global_rule_inherited')
                    raise AgentServiceError('rule_not_found')
                changed[subscription_profiles.USER_CLASH_RULES_KEY] = [item for item in rules if item != rule]
                additions = []
                removals = [rule]
            after = self._rule_snapshot(changed)
            after_revision = str(getattr(self.service, 'user_config_revision', _revision)(changed))
        return self._store_pending_plan(
            username=username,
            operation=operation,
            pack='',
            label='添加自定义规则' if operation == 'add' else '删除自定义规则',
            description='仅影响所选用户的个人 Clash 规则覆盖项',
            rule=rule,
            additions=additions,
            removals=removals,
            before_revision=before_revision,
            after_revision=after_revision,
            before_global_revision=before_global_revision,
            before=before,
            after=after,
            operator=operator,
            source_ip=source_ip,
        )

    def apply_change(self, change_id: str, *, operator: str = 'admin', source_ip: str = ''):
        change_id = str(change_id or '').strip()
        if not change_id or len(change_id) > 128:
            raise AgentServiceError('invalid_change')
        with state_store.file_lock(self.changes_path.with_name(self.changes_path.name + '.lock')):
            changes = self._load_changes()
            record = changes.get(change_id)
            if not isinstance(record, dict) or record.get('status') not in {'pending', 'applying'}:
                raise AgentServiceError('change_not_pending')
            username = self._validate_username(record.get('username'))
            with _context_for_users(self.service):
                users = self._load_users()
                cfg = self._get_cfg(users, username)
                current_revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
                before_global_revision = str(record.get('before_global_revision') or '')
                if before_global_revision:
                    _global_rules, current_global_revision = self._global_rules_snapshot()
                    if current_global_revision != before_global_revision:
                        raise AgentServiceError('revision_conflict')
                if record.get('status') == 'applying' and current_revision == str(record.get('after_revision') or ''):
                    record['status'] = 'applied'
                    record['applied_at'] = _now()
                    record['applied_revision'] = current_revision
                    changes[change_id] = record
                    self._save_changes(changes)
                    return {
                        'ok': True,
                        'change_id': change_id,
                        'username': username,
                        'revision': current_revision,
                        'snapshot': self._snapshot_from_cfg(username, cfg),
                    }
                if record.get('status') == 'applying' and current_revision != str(record.get('before_revision') or ''):
                    raise AgentServiceError('revision_conflict')
                if current_revision != str(record.get('before_revision') or ''):
                    raise AgentServiceError('revision_conflict')
                record['status'] = 'applying'
                record['operator'] = str(operator or record.get('operator') or 'admin')[:128]
                record['source_ip'] = str(source_ip or record.get('source_ip') or '')[:128]
                changes[change_id] = record
                self._save_changes(changes)
                self._restore_snapshot(cfg, record.get('after') or {})
                users[username] = cfg
                self.service.save_json(self.users_path, users)
                applied_revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
                applied_snapshot = self._snapshot_from_cfg(username, cfg)
            record['status'] = 'applied'
            record['applied_at'] = _now()
            record['applied_revision'] = applied_revision
            changes[change_id] = record
            self._save_changes(changes)
        return {
            'ok': True,
            'change_id': change_id,
            'username': username,
            'revision': applied_revision,
            'snapshot': applied_snapshot,
        }

    def undo_change(self, change_id: str, *, operator: str = 'admin', source_ip: str = ''):
        change_id = str(change_id or '').strip()
        with state_store.file_lock(self.changes_path.with_name(self.changes_path.name + '.lock')):
            changes = self._load_changes()
            record = changes.get(change_id)
            if not isinstance(record, dict) or record.get('status') not in {'applied', 'undoing'}:
                raise AgentServiceError('change_not_undoable')
            username = self._validate_username(record.get('username'))
            with _context_for_users(self.service):
                users = self._load_users()
                cfg = self._get_cfg(users, username)
                current_revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
                if record.get('status') == 'undoing' and current_revision == str(record.get('before_revision') or ''):
                    record['status'] = 'undone'
                    record['undone_at'] = _now()
                    record['undone_revision'] = current_revision
                    changes[change_id] = record
                    self._save_changes(changes)
                    return {
                        'ok': True,
                        'change_id': change_id,
                        'username': username,
                        'revision': current_revision,
                        'snapshot': self._snapshot_from_cfg(username, cfg),
                    }
                if record.get('status') == 'undoing' and current_revision != str(record.get('applied_revision') or ''):
                    raise AgentServiceError('revision_conflict')
                if current_revision != str(record.get('applied_revision') or ''):
                    raise AgentServiceError('revision_conflict')
                record['status'] = 'undoing'
                record['undo_operator'] = str(operator or 'admin')[:128]
                record['undo_source_ip'] = str(source_ip or '')[:128]
                record['undo_started_at'] = _now()
                changes[change_id] = record
                self._save_changes(changes)
                self._restore_snapshot(cfg, record.get('before') or {})
                users[username] = cfg
                self.service.save_json(self.users_path, users)
                revision = str(getattr(self.service, 'user_config_revision', _revision)(cfg))
                undone_snapshot = self._snapshot_from_cfg(username, cfg)
            record['status'] = 'undone'
            record['undone_at'] = _now()
            record['undone_revision'] = revision
            changes[change_id] = record
            self._save_changes(changes)
        return {
            'ok': True,
            'change_id': change_id,
            'username': username,
            'revision': revision,
            'snapshot': undone_snapshot,
        }
