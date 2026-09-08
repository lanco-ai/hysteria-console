"""Durable revocation side effects and bounded background retries."""

from __future__ import annotations

import hmac
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager

import revocation_queue
import rotation_recovery
import state_store
import static_access
import tuic_config
import xray_config


@dataclass(frozen=True)
class RevocationService:
    CredentialActionResult: type
    USERS_FILE: Path
    _DELETE_TARGET_GENERATION: str
    _WORKER_ERROR_LOG_INITIAL_SECONDS: float
    _WORKER_ERROR_LOG_LOCK: ContextManager[object]
    _WORKER_ERROR_LOG_MAX_SECONDS: float
    _WORKER_ERROR_LOG_STATE: dict
    _credential_generation: Callable[..., object]
    _delete_previous_generation: Callable[..., object]
    _fail_closed_static_access: Callable[..., object]
    _is_delete_revocation_task: Callable[..., object]
    _normalize_service_action: Callable[..., object]
    _purge_user_history_locked: Callable[..., object]
    _revocation_queue_path: Callable[..., object]
    _rotation_receipts_path: Callable[..., object]
    _state_failure_requires_static_stop: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    _using_live_core_state: Callable[..., object]
    hy_kick: Callable[..., object]
    load_json: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]

    def _action_succeeded(self, result):
        if isinstance(
            result,
            (self.CredentialActionResult, static_access.ServiceActionResult),
        ):
            return result.ok
        # Existing integrations historically returned None on accepted kick.
        return result is None or result is True

    def _schedule_static_reload(self, service, *, changed):
        if not self._using_live_core_state():
            return self.CredentialActionResult(
                action='static_reload',
                target=service,
                attempted=False,
                ok=True,
                code='not_live',
                retryable=False,
            )
        module = xray_config if service == static_access.XRAY_SERVICE else tuic_config
        loader = module.reload_async
        marker = module._reload_pending_path(module.CONFIG_FILE)
        if not changed and not marker.exists():
            return self.CredentialActionResult(
                action='static_reload',
                target=service,
                attempted=False,
                ok=True,
                code='already_applied',
                retryable=False,
            )
        try:
            scheduled = loader()
        except Exception as exc:
            return self.CredentialActionResult(
                action='static_reload',
                target=service,
                attempted=True,
                ok=False,
                code=type(exc).__name__,
                retryable=True,
            )
        # A false return with no marker means the generation was already ACKed
        # (or completed in the scheduling race). A retained marker is the durable
        # signal that this handoff still needs attention.
        marker_pending = marker.exists()
        ok = scheduled is True or (not changed and not marker_pending)
        return self.CredentialActionResult(
            action='static_reload',
            target=service,
            attempted=True,
            ok=ok,
            code=(
                'scheduled'
                if scheduled is True
                else ('already_applied' if not changed and not marker_pending else 'not_scheduled')
            ),
            retryable=not ok,
        )

    def _record_static_retry(self, task_id, services):
        if not task_id or not services:
            return True
        try:
            return revocation_queue.add_static_services(
                self._revocation_queue_path(),
                task_id,
                services,
            )
        except (state_store.StateStoreError, OSError):
            return False

    def _record_kick_attempt(
        self,
        task_id,
        result,
        *,
        completed_static_services=(),
    ):
        if not task_id:
            return False
        try:
            revocation_queue.complete_attempt(
                self._revocation_queue_path(),
                task_id,
                kick_ok=self._action_succeeded(result),
                stopped_services=completed_static_services,
            )
            return True
        except (state_store.StateStoreError, OSError):
            return False

    def _attempt_revocation_side_effects(
        self,
        task_id,
        username,
        *,
        xray_changed=False,
        tuic_changed=False,
        sync_error=None,
    ):
        """Attempt one durable static-auth and Hysteria revocation handoff."""
        uncertain = False
        static_outcomes = {}
        completed_static_services = []
        if sync_error is not None:
            static_outcomes = self._fail_closed_static_access(sync_error)
            completed_static_services.extend(
                service for service, outcome in static_outcomes.items() if outcome.ok
            )
        else:
            for service, changed in (
                (static_access.XRAY_SERVICE, xray_changed),
                (static_access.TUIC_SERVICE, tuic_changed),
            ):
                reload_result = self._schedule_static_reload(
                    service,
                    changed=bool(changed),
                )
                if reload_result.ok:
                    completed_static_services.append(service)
                    continue
                raw = static_access.stop_fail_closed(
                    service,
                    reason=RuntimeError(
                        'credential reload scheduling failed',
                    ),
                    live=self._using_live_core_state(),
                )
                outcome = self._normalize_service_action(service, raw)
                static_outcomes[service] = outcome
                if outcome.ok:
                    completed_static_services.append(service)

        retry_services = [service for service, outcome in static_outcomes.items() if not outcome.ok]
        if retry_services and not self._record_static_retry(
            task_id,
            retry_services,
        ):
            uncertain = True

        kick_result = self.hy_kick([username])
        kick_recorded = self._record_kick_attempt(
            task_id,
            kick_result,
            completed_static_services=completed_static_services,
        )
        if not self._action_succeeded(kick_result) or not kick_recorded:
            uncertain = True
        if any(not outcome.effect_confirmed for outcome in static_outcomes.values()):
            uncertain = True
        return {
            'uncertain': uncertain,
            'static_outcomes': static_outcomes,
            'kick_result': kick_result,
        }

    def _process_one_revocation_task(self):
        """Retry one durable task; all process/network I/O stays outside locks."""
        path = self._revocation_queue_path()
        task = revocation_queue.claim_due(path)
        if not task:
            return False
        task_id = task['task_id']
        static_pending = tuple(task.get('static_services', ()))
        is_delete = self._is_delete_revocation_task(task)
        sync_error = None
        xray_changed = False
        tuic_changed = False
        generation = ''
        try:
            with self.usage_lock():
                users = self.load_json(self.USERS_FILE, {})
                cfg = users.get(task['user'])
                if is_delete:
                    current_delete_generation = (
                        self._delete_previous_generation(cfg) if isinstance(cfg, dict) else ''
                    )
                    expected_delete_generation = str(
                        task.get('previous_generation') or '',
                    )
                    superseded = task['user'] in users and not (
                        current_delete_generation
                        and hmac.compare_digest(
                            current_delete_generation,
                            expected_delete_generation,
                        )
                    )
                    if not superseded:
                        if task['user'] in users:
                            users.pop(task['user'], None)
                        # Even an already-absent user is rewritten before any
                        # destructive history cleanup. This establishes a fresh,
                        # explicit durability point after a prior post-replace
                        # directory-fsync uncertainty.
                        self.save_json(self.USERS_FILE, users)
                        self._purge_user_history_locked(task['user'])
                    # A different complete revision is a same-name replacement,
                    # not the account incarnation named by this WAL record. Keep
                    # its data intact, but still reconcile current static auth and
                    # perform the username-wide kicks that retire old sessions.
                    generation = self._DELETE_TARGET_GENERATION
                else:
                    generation = self._credential_generation(
                        cfg.get('sub_token') if isinstance(cfg, dict) else '',
                    )
                if static_pending and (
                    is_delete
                    or hmac.compare_digest(
                        generation,
                        str(task.get('target_generation') or ''),
                    )
                ):
                    try:
                        xray_changed, tuic_changed = self._sync_static_access_from_users(users)
                    except (state_store.StateStoreError, OSError) as exc:
                        sync_error = exc
        except (state_store.StateStoreError, OSError) as exc:
            try:
                revocation_queue.release_claim(path, task_id)
            except (state_store.StateStoreError, OSError):
                pass
            if self._state_failure_requires_static_stop(exc):
                self._fail_closed_static_access(exc)
            return False
        if not hmac.compare_digest(
            generation,
            str(task.get('target_generation') or ''),
        ):
            if hmac.compare_digest(
                generation,
                str(task.get('previous_generation') or ''),
            ):
                if int(task.get('expires_at', 0)) <= int(time.time()):
                    # A generic rotation whose previous generation is still
                    # canonical after its replay window never committed. It is
                    # safe to retire this pre-commit intent so abandoned requests
                    # cannot consume bounded queue capacity forever. Deletions do
                    # not take this branch: their WAL is itself authorization to
                    # redo the bound account deletion.
                    revocation_queue.discard(path, task_id)
                else:
                    # Intent was durably prepared but canonical commit has not
                    # become visible yet. Leave it armed for the request replay.
                    revocation_queue.release_claim(path, task_id)
            else:
                # A newer generation superseded this task. Its own task covers the
                # username-wide kick, so the stale intent can be discarded.
                revocation_queue.discard(path, task_id)
            return False

        completed_static = []
        if sync_error is not None:
            outcomes = self._fail_closed_static_access(sync_error)
            completed_static.extend(
                service
                for service in static_pending
                if outcomes.get(service) is not None and outcomes[service].ok
            )
        else:
            changed_by_service = {
                static_access.XRAY_SERVICE: bool(xray_changed),
                static_access.TUIC_SERVICE: bool(tuic_changed),
            }
            for service in static_pending:
                reload_result = self._schedule_static_reload(
                    service,
                    changed=changed_by_service.get(service, False),
                )
                if reload_result.ok:
                    completed_static.append(service)
                    continue
                raw = static_access.stop_fail_closed(
                    service,
                    reason=RuntimeError(
                        'credential revocation reload retry failed',
                    ),
                    live=self._using_live_core_state(),
                )
                outcome = self._normalize_service_action(service, raw)
                if outcome.ok:
                    completed_static.append(service)
        kick_result = self.hy_kick([task['user']])
        try:
            revocation_queue.complete_attempt(
                path,
                task_id,
                kick_ok=self._action_succeeded(kick_result),
                stopped_services=completed_static,
            )
        except (state_store.StateStoreError, OSError):
            return False
        return True

    def _prune_expired_rotation_receipts(self):
        try:
            removed = rotation_recovery.prune_expired(
                self._rotation_receipts_path(),
            )
            self._reset_worker_error_log('rotation_receipt_cleanup')
            return removed
        except (state_store.StateStoreError, OSError) as exc:
            self._log_worker_error_throttled(
                'rotation_receipt_cleanup',
                exc,
                'credential rotation receipt cleanup deferred',
            )
            return 0

    def _reset_worker_error_log(self, category):
        with self._WORKER_ERROR_LOG_LOCK:
            self._WORKER_ERROR_LOG_STATE.pop(str(category), None)

    def _log_worker_error_throttled(self, category, exc, message):
        """Log only exception type with bounded exponential suppression."""
        key = str(category)
        now = time.monotonic()
        with self._WORKER_ERROR_LOG_LOCK:
            state = self._WORKER_ERROR_LOG_STATE.get(key, {})
            next_log_at = float(state.get('next_log_at', 0.0))
            delay = float(
                state.get(
                    'delay',
                    self._WORKER_ERROR_LOG_INITIAL_SECONDS,
                ),
            )
            if now < next_log_at:
                return False
            print(
                f'{message}: {type(exc).__name__}',
                file=sys.stderr,
            )
            self._WORKER_ERROR_LOG_STATE[key] = {
                'next_log_at': now + delay,
                'delay': min(delay * 2, self._WORKER_ERROR_LOG_MAX_SECONDS),
            }
            return True

    def _revocation_worker_loop(self, stop_event):
        while not stop_event.wait(1.0):
            self._prune_expired_rotation_receipts()
            # Bound work per wake so a damaged endpoint cannot monopolize the
            # subscription service.
            for _index in range(8):
                try:
                    progressed = self._process_one_revocation_task()
                except Exception as exc:
                    self._log_worker_error_throttled(
                        'credential_revocation_retry',
                        exc,
                        'credential revocation retry paused',
                    )
                    break
                self._reset_worker_error_log('credential_revocation_retry')
                if not progressed:
                    break
