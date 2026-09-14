"""Administrator credential rotation with durable revocation handoff."""

import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping

import revocation_queue
import state_store
import static_access


@dataclass(frozen=True, slots=True)
class CredentialMutationResult:
    outcome: Literal['success', 'not_found', 'conflict']
    username: str = ''
    code: str = ''
    subscription_token: str = field(default='', repr=False)


@dataclass(frozen=True, slots=True)
class AdminCredentialService:
    CredentialRotationCommitted: type[Exception]
    USERS_FILE: Path
    _action_succeeded: Callable[..., object]
    _credential_generation: Callable[..., object]
    _fail_closed_static_access: Callable[..., object]
    _normalize_service_action: Callable[..., object]
    _record_kick_attempt: Callable[..., object]
    _record_static_retry: Callable[..., object]
    _revocation_queue_path: Callable[..., object]
    _save_users_for_rotation: Callable[..., object]
    _schedule_static_reload: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    _using_live_core_state: Callable[..., object]
    audit: Callable[..., object]
    hy_kick: Callable[..., object]
    load_json: Callable[..., object]
    revision_matches: Callable[..., object]
    usage_lock: Callable[..., object]

    def rotate(
        self, *, form: Mapping[str, list[str]], expected_revision: str
    ) -> CredentialMutationResult:
        username = (form.get('user') or [''])[0].strip()
        sync_pending = False
        sync_error = None
        task_id = ''
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            if username not in users:
                return CredentialMutationResult(outcome='not_found', username=username)
            if not isinstance(users.get(username), dict):
                return CredentialMutationResult(outcome='not_found', username=username)
            if not self.revision_matches(
                users.get(username),
                expected_revision,
            ):
                return CredentialMutationResult(outcome='conflict', username=username)
            previous_generation = self._credential_generation(
                users[username].get('sub_token'),
            )
            new_token = secrets.token_urlsafe(18)
            new_uuid = str(uuid.uuid4())
            new_generation = self._credential_generation(new_token)
            task_id = revocation_queue.task_id_for(
                username,
                secrets.token_urlsafe(24),
            )
            revocation_queue.prepare(
                self._revocation_queue_path(),
                task_id=task_id,
                user=username,
                previous_generation=previous_generation,
                target_generation=new_generation,
                static_services=static_access.SERVICES,
            )
            users[username]['sub_token'] = new_token
            users[username]['vless_uuid'] = new_uuid
            durability_uncertain = self._save_users_for_rotation(
                users,
                user=username,
                new_token=new_token,
                new_uuid=new_uuid,
            )
            if durability_uncertain:
                sync_pending = True
                sync_error = self.CredentialRotationCommitted(
                    username,
                    new_token,
                    users[username],
                    durability_uncertain=True,
                )
                xray_changed = False
                tuic_changed = False
            else:
                try:
                    xray_changed, tuic_changed = self._sync_static_access_from_users(users)
                except state_store.CriticalStateUnavailable as exc:
                    sync_pending = True
                    sync_error = exc
                    xray_changed = False
                    tuic_changed = False

        revocation_uncertain = False
        static_outcomes = {}
        completed_static_services = []
        if sync_pending:
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
                    changed=changed,
                )
                if reload_result.ok:
                    completed_static_services.append(service)
                else:
                    raw = static_access.stop_fail_closed(
                        service,
                        reason=RuntimeError(
                            'credential reload scheduling failed',
                        ),
                        live=self._using_live_core_state(),
                    )
                    static_outcomes[service] = self._normalize_service_action(service, raw)
                    if static_outcomes[service].ok:
                        completed_static_services.append(service)
        retry_services = [service for service, outcome in static_outcomes.items() if not outcome.ok]
        if retry_services and not self._record_static_retry(
            task_id,
            retry_services,
        ):
            revocation_uncertain = True
        kick_result = self.hy_kick([username])
        kick_recorded = self._record_kick_attempt(
            task_id,
            kick_result,
            completed_static_services=completed_static_services,
        )
        if not self._action_succeeded(kick_result) or not kick_recorded:
            revocation_uncertain = True
        self.audit(
            'rotate_token',
            username,
            {},
            {},
        )
        confirmed_static_pause = (
            sync_pending
            and len(static_outcomes) == len(static_access.SERVICES)
            and all(outcome.effect_confirmed for outcome in static_outcomes.values())
        )
        if revocation_uncertain or any(
            not outcome.effect_confirmed for outcome in static_outcomes.values()
        ):
            code = 'err:rotated_retry'
        elif sync_pending and confirmed_static_pause:
            code = 'err:rotated_pending'
        elif static_outcomes:
            code = 'err:rotated_static_pending'
        else:
            code = 'rotated'
        return CredentialMutationResult(
            outcome='success',
            username=username,
            code=code,
            subscription_token=new_token,
        )
