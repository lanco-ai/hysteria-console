"""Credential rotation transactions and browser-bound retry receipts."""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import revocation_queue
import rotation_recovery
import state_store
import static_access
import user_compat


@dataclass(frozen=True)
class RecoverableRotationResult:
    status: str
    new_token: str = ''
    user_config: dict | None = None
    xray_changed: bool = False
    tuic_changed: bool = False
    sync_pending: bool = False
    durability_uncertain: bool = False
    sync_error: Exception | None = None
    task_id: str = ''
    replayed: bool = False


@dataclass(frozen=True)
class CredentialService:
    CredentialRotationCommitted: type[Exception]
    REVOCATION_QUEUE_FILE: Path
    ROTATION_RECEIPTS_FILE: Path
    USAGE_FILE: Path
    USERS_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    USER_SESSION_SUBSCRIPTION_TOKEN: str
    _credential_generation: Callable[..., str]
    _safe_secret_equal: Callable[..., bool]
    _sync_static_access_from_users: Callable[..., object]
    get_user_sessions: Callable[[], dict]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]

    def _visible_rotation_matches(self, user, new_token, new_uuid):
        """Check whether a post-replace durability error exposed our generation."""
        try:
            visible = state_store.load_json_strict(
                self.USERS_FILE,
                {},
                required=True,
            )
        except state_store.StateStoreError:
            return False
        cfg = visible.get(user)
        return (
            isinstance(cfg, dict)
            and self._safe_secret_equal(cfg.get('sub_token'), new_token)
            and hmac.compare_digest(
                str(cfg.get('vless_uuid') or ''),
                str(new_uuid or ''),
            )
        )

    def _save_users_for_rotation(
        self,
        users,
        *,
        user,
        new_token,
        new_uuid,
    ):
        """Return true when replace happened but directory durability is unknown."""
        try:
            self.save_json(self.USERS_FILE, users)
            return False
        except state_store.AtomicReplaceDurabilityUncertain:
            if self._visible_rotation_matches(user, new_token, new_uuid):
                return True
            raise

    def _rotate_user_token_if_current(
        self,
        user,
        posted,
        *,
        today=None,
        include_config=False,
        new_token=None,
        new_uuid=None,
    ):
        """Rotate every exported proxy credential after rechecking the old token."""

        def result(status, token='', xray=False, tuic=False, config=None):
            base = (status, token, xray, tuic)
            return (*base, config) if include_config else base

        with self.usage_lock():
            effective_today = today or self.local_now().date()
            users = self.load_json(self.USERS_FILE, {})
            cfg = users.get(user)
            if not isinstance(cfg, dict):
                return result('forbidden')
            expected = str(cfg.get('sub_token') or '')
            supplied = str(posted or '')
            if not self._safe_secret_equal(supplied, expected):
                return result('forbidden')
            if cfg.get('disabled'):
                return result('disabled')
            if user_compat.is_expired(cfg, today=effective_today):
                return result('expired')
            new_token = str(new_token or secrets.token_urlsafe(18))
            new_uuid = str(new_uuid or uuid.uuid4())
            cfg['sub_token'] = new_token
            cfg['vless_uuid'] = new_uuid
            users[user] = cfg
            durability_uncertain = self._save_users_for_rotation(
                users,
                user=user,
                new_token=new_token,
                new_uuid=new_uuid,
            )
            if durability_uncertain:
                raise self.CredentialRotationCommitted(
                    user,
                    new_token,
                    cfg,
                    durability_uncertain=True,
                )
            try:
                xray_changed, tuic_changed = self._sync_static_access_from_users(
                    users,
                )
            except state_store.CriticalStateUnavailable as exc:
                raise self.CredentialRotationCommitted(
                    user,
                    new_token,
                    cfg,
                ) from exc
            return result(
                'ok',
                new_token,
                xray_changed,
                tuic_changed,
                dict(cfg),
            )

    def _rotation_receipts_path(self):
        state_root = Path(self.USAGE_FILE).parent
        if state_root != Path('/root/hysteria/state'):
            return state_root / Path(self.ROTATION_RECEIPTS_FILE).name
        return Path(self.ROTATION_RECEIPTS_FILE)

    def _revocation_queue_path(self):
        state_root = Path(self.USAGE_FILE).parent
        if state_root != Path('/root/hysteria/state'):
            return state_root / Path(self.REVOCATION_QUEUE_FILE).name
        return Path(self.REVOCATION_QUEUE_FILE)

    def _rotation_session_allows(self, sid, user, posted, cfg):
        """Validate the browser session used to initiate a fresh rotation."""
        if not sid or not isinstance(cfg, dict):
            return False
        info = self.get_user_sessions().get(sid)
        if not isinstance(info, dict) or info.get('user') != user:
            return False
        kind = str(
            info.get('credential_kind') or self.USER_SESSION_PANEL_PASSWORD,
        )
        generation = str(info.get('credential_generation') or '')
        if kind == self.USER_SESSION_SUBSCRIPTION_TOKEN:
            return bool(generation) and hmac.compare_digest(
                generation,
                self._credential_generation(posted),
            )
        if kind == self.USER_SESSION_PANEL_PASSWORD:
            current = self._credential_generation(
                cfg.get('panel_pass_hash'),
            )
            # Preserve legacy password sessions that predate generation binding;
            # newly minted sessions always carry the generation.
            return not generation or (bool(current) and hmac.compare_digest(generation, current))
        return False

    def _recoverable_user_rotation(
        self,
        user,
        posted,
        *,
        request_id,
        session_id,
        today=None,
    ):
        """Commit or replay one browser-bound self-service token rotation."""
        if not rotation_recovery.valid_request_id(request_id):
            return RecoverableRotationResult('bad_request')
        receipt_path = self._rotation_receipts_path()
        queue_path = self._revocation_queue_path()
        receipt = rotation_recovery.lookup_bound(
            receipt_path,
            user=user,
            request_id=request_id,
            session_id=session_id,
        )
        replayed = receipt is not None
        with self.usage_lock():
            effective_today = today or self.local_now().date()
            users = self.load_json(self.USERS_FILE, {})
            cfg = users.get(user)
            if not isinstance(cfg, dict):
                return RecoverableRotationResult('forbidden')
            current_token = str(cfg.get('sub_token') or '')
            current_generation = self._credential_generation(current_token)
            if receipt is None:
                # A concurrent copy of the same HTTP request may have prepared the
                # receipt while this request waited for the canonical user lock.
                receipt = rotation_recovery.lookup_bound(
                    receipt_path,
                    user=user,
                    request_id=request_id,
                    session_id=session_id,
                )
                replayed = receipt is not None

            if receipt is None:
                if not self._rotation_session_allows(
                    session_id,
                    user,
                    posted,
                    cfg,
                ):
                    return RecoverableRotationResult('forbidden')
                if not self._safe_secret_equal(posted, current_token):
                    return RecoverableRotationResult('forbidden')
                if cfg.get('disabled'):
                    return RecoverableRotationResult('disabled')
                if user_compat.is_expired(cfg, today=effective_today):
                    return RecoverableRotationResult('expired')
                new_token = secrets.token_urlsafe(18)
                new_uuid = str(uuid.uuid4())
                new_generation = self._credential_generation(new_token)
                try:
                    receipt = rotation_recovery.prepare(
                        receipt_path,
                        user=user,
                        request_id=request_id,
                        session_id=session_id,
                        old_generation=current_generation,
                        new_generation=new_generation,
                        new_token=new_token,
                        new_uuid=new_uuid,
                    )
                except PermissionError:
                    return RecoverableRotationResult('forbidden')
            else:
                new_token = str(receipt.get('new_token') or '')
                new_uuid = str(receipt.get('new_uuid') or '')
                new_generation = str(
                    receipt.get('new_generation') or '',
                )
                try:
                    receipt = rotation_recovery.prepare(
                        receipt_path,
                        user=user,
                        request_id=request_id,
                        session_id=session_id,
                        old_generation=str(
                            receipt.get('old_generation') or '',
                        ),
                        new_generation=new_generation,
                        new_token=new_token,
                        new_uuid=new_uuid,
                    )
                except PermissionError:
                    return RecoverableRotationResult('forbidden')

            new_token = str(receipt.get('new_token') or '')
            new_uuid = str(receipt.get('new_uuid') or '')
            new_generation = str(receipt.get('new_generation') or '')
            previous_generation = str(
                receipt.get('old_generation') or '',
            )
            task_id = revocation_queue.task_id_for(user, request_id)
            try:
                revocation_queue.prepare(
                    queue_path,
                    task_id=task_id,
                    user=user,
                    previous_generation=previous_generation,
                    target_generation=new_generation,
                    static_services=static_access.SERVICES,
                )
            except PermissionError:
                return RecoverableRotationResult('conflict')

            current_token = str(cfg.get('sub_token') or '')
            current_generation = self._credential_generation(current_token)
            canonical_matches = hmac.compare_digest(
                current_generation, new_generation
            ) and hmac.compare_digest(
                str(cfg.get('vless_uuid') or ''),
                new_uuid,
            )
            if not canonical_matches:
                if not hmac.compare_digest(
                    current_generation,
                    previous_generation,
                ) or not self._safe_secret_equal(posted, current_token):
                    rotation_recovery.discard(
                        receipt_path,
                        user=user,
                        request_id=request_id,
                    )
                    revocation_queue.discard(queue_path, task_id)
                    return RecoverableRotationResult('conflict')
                cfg['sub_token'] = new_token
                cfg['vless_uuid'] = new_uuid
                users[user] = cfg

            # Rewriting a replayed matching generation establishes a fresh
            # directory-fsync point after a prior uncertain post-replace failure.
            durability_uncertain = self._save_users_for_rotation(
                users,
                user=user,
                new_token=new_token,
                new_uuid=new_uuid,
            )
            if durability_uncertain:
                return RecoverableRotationResult(
                    'ok',
                    new_token=new_token,
                    user_config=dict(cfg),
                    sync_pending=True,
                    durability_uncertain=True,
                    sync_error=self.CredentialRotationCommitted(
                        user,
                        new_token,
                        cfg,
                        durability_uncertain=True,
                    ),
                    task_id=task_id,
                    replayed=replayed,
                )
            try:
                xray_changed, tuic_changed = self._sync_static_access_from_users(
                    users,
                )
            except state_store.CriticalStateUnavailable:
                return RecoverableRotationResult(
                    'ok',
                    new_token=new_token,
                    user_config=dict(cfg),
                    sync_pending=True,
                    sync_error=self.CredentialRotationCommitted(
                        user,
                        new_token,
                        cfg,
                    ),
                    task_id=task_id,
                    replayed=replayed,
                )
            return RecoverableRotationResult(
                'ok',
                new_token=new_token,
                user_config=dict(cfg),
                xray_changed=bool(xray_changed),
                tuic_changed=bool(tuic_changed),
                task_id=task_id,
                replayed=replayed,
            )
