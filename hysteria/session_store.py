"""Locked session storage independent of HTTP authentication policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _session_lock_file(path):
    return Path(str(path) + '.lock')


def _credential_generation(stored_hash):
    """Stable, non-secret marker used to invalidate sessions after a rekey."""
    value = str(stored_hash or '').encode('utf-8')
    return hashlib.sha256(value).hexdigest() if value else ''


@dataclass(frozen=True)
class SessionStore:
    load_json: Callable[..., object]
    save_json: Callable[..., object]
    file_lock: Callable[..., object]
    clock: Callable[[], float]
    token_urlsafe: Callable[[int], str]
    lock_timeout: float
    ttl: int
    max_per_identity: int
    max_global: int

    def _alive_sessions(self, path):
        sessions = self.load_json(path, {})
        if not isinstance(sessions, dict):
            sessions = {}
        now = int(self.clock())
        alive = {}
        for sid, info in sessions.items():
            if not isinstance(info, dict):
                continue
            try:
                expires_at = int(info.get('exp', 0))
            except (TypeError, ValueError):
                continue
            if expires_at > now:
                alive[sid] = info
        return sessions, alive

    def _get_sessions(self, path):
        with self.file_lock(
            _session_lock_file(path),
            timeout=self.lock_timeout,
        ):
            sessions, alive = self._alive_sessions(path)
            if alive != sessions:
                self.save_json(path, alive)
        return alive

    def _create_session(
        self,
        path,
        username,
        credential_generation='',
        credential_kind='',
    ):
        with self.file_lock(
            _session_lock_file(path),
            timeout=self.lock_timeout,
        ):
            _sessions, alive = self._alive_sessions(path)
            # Successful logins are attacker-controlled for any valid account.
            # Bound the file so repeated logins cannot grow every request's JSON
            # parse/write cost without limit. `exp` is monotonic with creation time.
            own = sorted(
                ((sid, info) for sid, info in alive.items() if info.get('user') == username),
                key=lambda item: int(item[1].get('exp', 0)),
                reverse=True,
            )
            keep_own = {sid for sid, _info in own[: self.max_per_identity - 1]}
            alive = {
                sid: info
                for sid, info in alive.items()
                if info.get('user') != username or sid in keep_own
            }
            if len(alive) >= self.max_global:
                newest = sorted(
                    alive.items(),
                    key=lambda item: int(item[1].get('exp', 0)),
                    reverse=True,
                )
                alive = dict(newest[: self.max_global - 1])
            sid = self.token_urlsafe(24)
            info = {'user': username, 'exp': int(self.clock()) + self.ttl}
            if credential_generation:
                info['credential_generation'] = credential_generation
            if credential_kind:
                info['credential_kind'] = credential_kind
            alive[sid] = info
            self.save_json(path, alive)
            return sid

    def _delete_session(self, path, sid):
        if not sid:
            return
        with self.file_lock(
            _session_lock_file(path),
            timeout=self.lock_timeout,
        ):
            sessions, alive = self._alive_sessions(path)
            if sid in alive:
                del alive[sid]
            if alive != sessions:
                self.save_json(path, alive)

    def _delete_sessions_for(self, path, username):
        with self.file_lock(
            _session_lock_file(path),
            timeout=self.lock_timeout,
        ):
            sessions, alive = self._alive_sessions(path)
            kept = {sid: info for sid, info in alive.items() if info.get('user') != username}
            if kept != sessions:
                self.save_json(path, kept)

    def _replace_sessions_with_new(
        self,
        path,
        username,
        *,
        revoke_all=False,
        credential_generation='',
        credential_kind='',
    ):
        """Revoke matching sessions and mint the replacement in one transaction."""
        with self.file_lock(
            _session_lock_file(path),
            timeout=self.lock_timeout,
        ):
            _sessions, alive = self._alive_sessions(path)
            if revoke_all:
                alive = {}
            else:
                alive = {sid: info for sid, info in alive.items() if info.get('user') != username}
            sid = self.token_urlsafe(24)
            info = {'user': username, 'exp': int(self.clock()) + self.ttl}
            if credential_generation:
                info['credential_generation'] = credential_generation
            if credential_kind:
                info['credential_kind'] = credential_kind
            alive[sid] = info
            self.save_json(path, alive)
            return sid
