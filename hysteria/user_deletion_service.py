"""User deletion with durable revocation recovery, independent of HTTP."""

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import revocation_queue
import state_store
import static_access
from admin_credential_service import CredentialMutationResult


@dataclass(frozen=True, slots=True)
class UserDeletionService:
    USERS_FILE: Path
    _DELETE_TARGET_GENERATION: str
    _attempt_revocation_side_effects: Callable[..., object]
    _delete_previous_generation: Callable[..., object]
    _purge_user_history_locked: Callable[..., object]
    _revocation_queue_path: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    load_json: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]

    def delete(
        self, *, form: Mapping[str, list[str]], expected_revision: str
    ) -> CredentialMutationResult:
        username = (form.get('user') or [''])[0].strip()
        task_id = ''
        delete_error = None
        sync_error = None
        cleanup_error = None
        xray_changed = False
        tuic_changed = False
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users:
                return CredentialMutationResult(outcome='not_found', username=username)
            cfg = users.get(username)
            if not isinstance(cfg, dict):
                return CredentialMutationResult(outcome='not_found', username=username)
            if not self.revision_matches(
                cfg,
                expected_revision,
            ):
                return CredentialMutationResult(outcome='conflict', username=username)
            task_id = revocation_queue.task_id_for(
                username,
                secrets.token_urlsafe(24),
            )
            revocation_queue.prepare(
                self._revocation_queue_path(),
                task_id=task_id,
                user=username,
                previous_generation=self._delete_previous_generation(cfg),
                target_generation=self._DELETE_TARGET_GENERATION,
                static_services=static_access.SERVICES,
            )
            del users[username]
            try:
                self.save_json(self.USERS_FILE, users)
            except (state_store.StateStoreError, OSError) as exc:
                # The WAL was durable first, so the worker may safely redo
                # this exact account revision even when replace never ran.
                # A post-replace durability uncertainty is likewise left
                # for the worker to re-save before deleting history.
                delete_error = exc
            if delete_error is None:
                try:
                    xray_changed, tuic_changed = self._sync_static_access_from_users(users)
                except (state_store.StateStoreError, OSError) as exc:
                    sync_error = exc
                try:
                    self._purge_user_history_locked(username)
                except (state_store.StateStoreError, OSError) as exc:
                    cleanup_error = exc

        # Config commits share the state lock. Process and network
        # side-effects remain outside it; the WAL retains every
        # unconfirmed reload/stop and the required second kick.
        outcome = self._attempt_revocation_side_effects(
            task_id,
            username,
            xray_changed=xray_changed,
            tuic_changed=tuic_changed,
            sync_error=delete_error or sync_error,
        )
        retry_pending = bool(delete_error or sync_error or cleanup_error or outcome['uncertain'])
        code = 'err:deleted_retry' if retry_pending else 'deleted'
        return CredentialMutationResult(outcome='success', username=username, code=code)
