"""User deletion with durable revocation recovery HTTP endpoints."""

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import revocation_queue
import state_store
import static_access


@dataclass(frozen=True)
class Context:
    USERS_FILE: Path
    _DELETE_TARGET_GENERATION: str
    _attempt_revocation_side_effects: Callable[..., object]
    _delete_previous_generation: Callable[..., object]
    _json_request: Callable[..., object]
    _purge_user_history_locked: Callable[..., object]
    _revocation_queue_path: Callable[..., object]
    _static_reload_status: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    is_logged_in: Callable[..., object]
    load_json: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]
    with_flash: Callable[..., object]


def _delete(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    username = (form.get('user') or [''])[0].strip()
    task_id = ''
    delete_error = None
    sync_error = None
    cleanup_error = None
    xray_changed = False
    tuic_changed = False
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._mutation_user_not_found(username, '/admin')
            return
        cfg = users.get(username)
        if not isinstance(cfg, dict):
            handler._mutation_user_not_found(username, '/admin')
            return
        if not ctx.revision_matches(
            cfg,
            request_user_revision,
        ):
            handler._mutation_conflict(username, '/admin')
            return
        task_id = revocation_queue.task_id_for(
            username,
            secrets.token_urlsafe(24),
        )
        revocation_queue.prepare(
            ctx._revocation_queue_path(),
            task_id=task_id,
            user=username,
            previous_generation=ctx._delete_previous_generation(cfg),
            target_generation=ctx._DELETE_TARGET_GENERATION,
            static_services=static_access.SERVICES,
        )
        del users[username]
        try:
            ctx.save_json(ctx.USERS_FILE, users)
        except (state_store.StateStoreError, OSError) as exc:
            # The WAL was durable first, so the worker may safely redo
            # this exact account revision even when replace never ran.
            # A post-replace durability uncertainty is likewise left
            # for the worker to re-save before deleting history.
            delete_error = exc
        if delete_error is None:
            try:
                xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
            except (state_store.StateStoreError, OSError) as exc:
                sync_error = exc
            try:
                ctx._purge_user_history_locked(username)
            except (state_store.StateStoreError, OSError) as exc:
                cleanup_error = exc

    # Config commits share the state lock. Process and network
    # side-effects remain outside it; the WAL retains every
    # unconfirmed reload/stop and the required second kick.
    outcome = ctx._attempt_revocation_side_effects(
        task_id,
        username,
        xray_changed=xray_changed,
        tuic_changed=tuic_changed,
        sync_error=delete_error or sync_error,
    )
    retry_pending = bool(delete_error or sync_error or cleanup_error or outcome['uncertain'])
    flash = 'err:deleted_retry ' + username if retry_pending else 'deleted ' + username
    if ctx._json_request(handler):
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'deleted': True,
                'flash': flash.split(' ', 1)[0],
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect(ctx.with_flash('/admin', flash))
    return


_ROUTES = {
    '/admin/delete': _delete,
}


def handle_write(
    handler,
    context: Context,
    *,
    path: str,
    form: Mapping[str, list[str]],
    query: Mapping[str, list[str]],
    request_user_revision: str,
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form, query, request_user_revision)
    return True
