"""Transport-free account creation and plan-edit orchestration."""

import json
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping

import landing_egress
import state_store
import tuic_config
import user_compat
import xray_config


@dataclass(frozen=True, slots=True)
class AccountMutationResult:
    outcome: Literal['created', 'updated', 'invalid', 'conflict', 'not_found']
    username: str = ''
    code: str = ''
    field_id: str = ''
    draft: dict | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AccountMutationService:
    PASSWORD_MAX_LENGTH: int
    USERS_FILE: Path
    _ensure_landing_vless_uuid: Callable[..., object]
    _landing_registry_or_empty: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    delete_user_sessions_for: Callable[..., object]
    hash_secret: Callable[..., object]
    is_valid_username: Callable[..., object]
    load_json: Callable[..., object]
    parse_bounded_int_field: Callable[..., object]
    parse_date_field: Callable[..., object]
    parse_note_field: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]

    @staticmethod
    def _first(form: Mapping[str, list[str]], name: str) -> str:
        return (form.get(name) or [''])[0]

    @staticmethod
    def _invalid_create(code: str, field_id: str, draft: dict) -> AccountMutationResult:
        return AccountMutationResult(
            outcome='invalid',
            code=code,
            field_id=field_id,
            draft=draft,
        )

    def create(self, *, form: Mapping[str, list[str]]) -> AccountMutationResult:
        username = self._first(form, 'user').strip()
        panel_password = self._first(form, 'panel_password')
        password = self._first(form, 'password').strip()
        quota_gb_raw = self._first(form, 'quota_gb')
        quota_extra_gb_raw = self._first(form, 'quota_extra_gb')
        quota_gb = self.parse_bounded_int_field(quota_gb_raw, 0, 10240)
        quota_extra_gb = self.parse_bounded_int_field(quota_extra_gb_raw, 0, 10240)
        expires_raw = self._first(form, 'expires_at')
        note_raw = self._first(form, 'note')
        landing_initial_egress_id = self._first(form, 'landing_initial_egress_id').strip()
        expires_at = self.parse_date_field(expires_raw)
        note = self.parse_note_field(note_raw)
        guest = 'guest' in form
        tuic_enabled = 'tuic_enabled' in form
        create_draft = {
            'user': username,
            'quota_gb': quota_gb_raw,
            'quota_extra_gb': quota_extra_gb_raw,
            'expires_at': expires_raw,
            'note': note_raw,
            'landing_initial_egress_id': landing_initial_egress_id,
            'guest': guest,
            'tuic_enabled': tuic_enabled,
        }

        if not username:
            return self._invalid_create('user empty', 'create-user', create_draft)
        if not self.is_valid_username(username):
            return self._invalid_create('err:username_invalid', 'create-user', create_draft)
        if quota_gb is None:
            return self._invalid_create('err:quota_invalid', 'create-quota-gb', create_draft)
        if quota_extra_gb is None:
            return self._invalid_create(
                'err:quota_extra_invalid', 'create-quota-extra-gb', create_draft
            )
        if str(expires_raw).strip() and not expires_at:
            return self._invalid_create('err:expiry_invalid', 'create-expires-at', create_draft)
        if len(str(note_raw).strip()) > 200:
            return self._invalid_create('err:note_too_long', 'create-note', create_draft)
        if panel_password and len(panel_password) < 8:
            return self._invalid_create(
                'err:panel_password_short', 'create-panel-password', create_draft
            )
        if len(panel_password) > self.PASSWORD_MAX_LENGTH:
            return self._invalid_create(
                'err:panel_password_long', 'create-panel-password', create_draft
            )
        if len(password) > self.PASSWORD_MAX_LENGTH:
            return self._invalid_create(
                'err:proxy_password_long', 'create-proxy-password', create_draft
            )
        if landing_initial_egress_id:
            registry = self._landing_registry_or_empty()
            initial_node = registry.get('nodes', {}).get(landing_initial_egress_id)
            if not isinstance(initial_node, dict) or initial_node.get('enabled') is not True:
                return self._invalid_create(
                    '家宽出口已不可用，请重新选择',
                    'create-landing-initial-egress',
                    create_draft,
                )

        user_exists = False
        landing_became_unavailable = False
        with self.usage_lock():
            original_users_text = Path(self.USERS_FILE).read_text(encoding='utf-8')
            users = self.load_json(self.USERS_FILE, {})
            if landing_initial_egress_id:
                registry = landing_egress.load_registry()
                initial_node = registry.get('nodes', {}).get(landing_initial_egress_id)
                landing_became_unavailable = (
                    not isinstance(initial_node, dict) or initial_node.get('enabled') is not True
                )
            if landing_became_unavailable:
                pass
            elif username in users:
                user_exists = True
            else:
                entry = {
                    'metered': guest,
                    'guest': guest,
                    'tuic_enabled': tuic_enabled,
                    'monthly_quota_bytes': quota_gb * 1024 * 1024 * 1024,
                    'quota_extra_bytes': quota_extra_gb * 1024 * 1024 * 1024,
                    'sub_token': secrets.token_urlsafe(18),
                    'vless_uuid': str(uuid.uuid4()),
                    'disabled': False,
                    'max_devices': 2,
                }
                if expires_at:
                    entry['expires_at'] = expires_at
                if note:
                    entry['note'] = note
                if password:
                    entry['password_hash'] = self.hash_secret(password)
                if panel_password:
                    entry['panel_pass_hash'] = self.hash_secret(panel_password)
                    entry['panel_password_must_change'] = True
                if landing_initial_egress_id:
                    entry['landing_allowed_egress_ids'] = [landing_initial_egress_id]
                    entry['landing_selected_egress_id'] = landing_initial_egress_id
                    self._ensure_landing_vless_uuid(entry, users)
                users[username] = entry
                self.save_json(self.USERS_FILE, users)
                try:
                    xray_changed, tuic_changed = self._sync_static_access_from_users(users)
                except Exception:
                    state_store.save_text_atomic(self.USERS_FILE, original_users_text)
                    try:
                        self._sync_static_access_from_users(json.loads(original_users_text))
                    except Exception:
                        pass
                    raise

        if landing_became_unavailable:
            return self._invalid_create(
                '家宽出口已不可用，请重新选择',
                'create-landing-initial-egress',
                create_draft,
            )
        if user_exists:
            return self._invalid_create('user_exists_use_reset_token', 'create-user', create_draft)
        if panel_password:
            self.delete_user_sessions_for(username)
        if xray_changed:
            xray_config.reload_async()
        if tuic_changed:
            tuic_config.reload_async()
        return AccountMutationResult(outcome='created', username=username)

    def update(
        self,
        *,
        form: Mapping[str, list[str]],
        expected_revision: str,
    ) -> AccountMutationResult:
        username = self._first(form, 'user').strip()
        panel_password = self._first(form, 'panel_password')
        new_password = self._first(form, 'password').strip()
        max_devices = self.parse_bounded_int_field(self._first(form, 'max_devices'), 0, 100)
        quota_gb = self.parse_bounded_int_field(self._first(form, 'quota_gb'), 0, 10240)
        quota_extra_gb = self.parse_bounded_int_field(self._first(form, 'quota_extra_gb'), 0, 10240)
        expires_raw = self._first(form, 'expires_at')
        note_raw = self._first(form, 'note')
        expires_at = self.parse_date_field(expires_raw)
        note = self.parse_note_field(note_raw)
        landing_values = {}
        landing_error = None
        for landing_name in user_compat.LANDING_FIELDS:
            parser = (
                user_compat.parse_landing_ip_write
                if landing_name == 'landing_ip'
                else user_compat.parse_landing_write
            )
            value, error = parser(self._first(form, landing_name))
            if error:
                landing_error = error
                break
            landing_values[landing_name] = value
        guest = 'guest' in form
        tuic_enabled = 'tuic_enabled' in form

        if max_devices is None:
            return AccountMutationResult(outcome='invalid', code='err:max_devices_invalid')
        if quota_gb is None:
            return AccountMutationResult(outcome='invalid', code='err:quota_invalid')
        if quota_extra_gb is None:
            return AccountMutationResult(outcome='invalid', code='err:quota_extra_invalid')
        if str(expires_raw).strip() and not expires_at:
            return AccountMutationResult(outcome='invalid', code='err:expiry_invalid')
        if len(str(note_raw).strip()) > 200:
            return AccountMutationResult(outcome='invalid', code='err:note_too_long')
        if landing_error:
            return AccountMutationResult(outcome='invalid', code='err:' + landing_error)
        if panel_password and len(panel_password) < 8:
            return AccountMutationResult(outcome='invalid', code='err:panel_password_short')
        if len(panel_password) > self.PASSWORD_MAX_LENGTH:
            return AccountMutationResult(outcome='invalid', code='err:panel_password_long')
        if len(new_password) > self.PASSWORD_MAX_LENGTH:
            return AccountMutationResult(outcome='invalid', code='err:proxy_password_long')

        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users:
                return AccountMutationResult(outcome='not_found', username=username)
            cfg = users[username]
            if not isinstance(cfg, dict) or not self.revision_matches(cfg, expected_revision):
                return AccountMutationResult(
                    outcome='conflict',
                    username=username,
                    draft={
                        'user': username,
                        'max_devices': max_devices,
                        'quota_gb': quota_gb,
                        'quota_extra_gb': quota_extra_gb,
                        'expires_at': expires_at,
                        'note': note,
                        'guest': '是' if guest else '否',
                        'tuic_enabled': '是' if tuic_enabled else '否',
                    },
                )
            if panel_password:
                cfg['panel_pass_hash'] = self.hash_secret(panel_password)
                cfg['panel_password_must_change'] = True
            if new_password:
                cfg['password_hash'] = self.hash_secret(new_password)
            cfg.pop('password', None)
            cfg['max_devices'] = max_devices
            cfg['monthly_quota_bytes'] = quota_gb * 1024 * 1024 * 1024
            cfg['quota_extra_bytes'] = quota_extra_gb * 1024 * 1024 * 1024
            if expires_at:
                cfg['expires_at'] = expires_at
            else:
                cfg.pop('expires_at', None)
            if note:
                cfg['note'] = note
            else:
                cfg.pop('note', None)
            for landing_name, landing_value in landing_values.items():
                if landing_value:
                    cfg[landing_name] = landing_value
                else:
                    cfg.pop(landing_name, None)
            cfg['metered'] = guest
            cfg['guest'] = guest
            cfg['tuic_enabled'] = tuic_enabled
            if not cfg.get('sub_token'):
                cfg['sub_token'] = secrets.token_urlsafe(18)
            if not str(cfg.get('vless_uuid') or '').strip():
                cfg['vless_uuid'] = str(uuid.uuid4())
            users[username] = cfg
            self.save_json(self.USERS_FILE, users)
            xray_changed, tuic_changed = self._sync_static_access_from_users(users)
        if panel_password:
            self.delete_user_sessions_for(username)
        if xray_changed:
            xray_config.reload_async()
        if tuic_changed:
            tuic_config.reload_async()
        return AccountMutationResult(outcome='updated', username=username)
