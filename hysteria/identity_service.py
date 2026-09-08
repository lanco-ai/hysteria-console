"""Password initialization, migration and authenticated identity checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import state_store
import user_compat


@dataclass(frozen=True)
class IdentityService:
    META_FILE: Path
    USERS_FILE: Path
    PASSWORD_HASH_MAX_LENGTH: int
    PASSWORD_MAX_LENGTH: int
    PASSWORD_MIN_LENGTH: int
    PBKDF2_DIGEST_BYTES: int
    PBKDF2_ROUNDS_MAX: int
    PBKDF2_ROUNDS_MIN: int
    PBKDF2_SALT_BYTES: int
    USER_SESSION_CREDENTIAL_KINDS: object
    USER_SESSION_PANEL_PASSWORD: str
    USER_SESSION_SUBSCRIPTION_TOKEN: str
    _credential_generation: Callable[..., object]
    delete_session: Callable[..., object]
    delete_user_session: Callable[..., object]
    get_sessions: Callable[..., object]
    get_user_sessions: Callable[..., object]
    is_valid_username: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    meta_lock: Callable[..., object]
    parse_cookies: Callable[..., object]
    parse_query_params: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]

    def _b64url_nopad(self, data):
        return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

    def hash_secret(self, secret):
        salt = secrets.token_bytes(16)
        rounds = 200000
        digest = hashlib.pbkdf2_hmac('sha256', secret.encode('utf-8'), salt, rounds)
        return f'pbkdf2_sha256${rounds}${self._b64url_nopad(salt)}${self._b64url_nopad(digest)}'

    def migrate_plaintext_passwords(self):
        with self.usage_lock():
            users = self.load_json(self.USERS_FILE, {})
            changed = False
            for _, cfg in users.items():
                plain = str(cfg.get('password') or '')
                if plain:
                    cfg['password_hash'] = self.hash_secret(plain)
                    cfg.pop('password', None)
                    changed = True
                if cfg.get('password') is not None:
                    cfg.pop('password', None)
                    changed = True
            if changed:
                self.save_json(self.USERS_FILE, users)

    def _write_initial_admin_password(self, user, password):
        """Persist an auto-generated initial admin password to a root-only file so
        the operator can retrieve it on a fresh deploy, log in, then rotate it via
        /admin/settings. Meta initialization fails if this credential cannot be
        persisted; silently creating an inaccessible admin hash would lock out the
        operator.
        Path follows META_FILE so tests (which repoint META_FILE) stay isolated."""
        try:
            path = Path(self.META_FILE).parent / 'admin_initial_password.txt'
            state_store.save_text_atomic(
                path,
                (
                    f'# hy2 auto-generated initial admin password.\n'
                    f'# Log in at /admin (user: {user}), rotate it at /admin/settings, then delete this file.\n'
                    f'{user}:{password}\n'
                ),
            )
            os.chmod(str(path), 0o600)
            return True
        except OSError:
            return False

    def load_meta(self):
        """Load runtime admin state fail-closed after deployment initialization."""
        return self.load_json(self.META_FILE, {}, required=True)

    def ensure_meta(self):
        """Explicit first-deploy initializer for subscription_meta.json.

        Runtime request paths use ``load_meta`` instead, so deleting live admin
        state cannot silently generate a new password/token and lock out the
        operator. deploy.sh is the sole production caller allowed to initialize a
        missing file.
        """
        with self.meta_lock():
            meta = self.load_json(self.META_FILE, {}, required=False)
            changed = False
            if not meta.get('admin_token'):
                meta['admin_token'] = secrets.token_urlsafe(24)
                changed = True
            if not meta.get('admin_user'):
                meta['admin_user'] = 'admin'
                changed = True
            if not meta.get('admin_pass') and not meta.get('admin_pass_hash'):
                initial = secrets.token_urlsafe(12)
                meta['admin_pass_hash'] = self.hash_secret(initial)
                if not self._write_initial_admin_password(
                    meta.get('admin_user', 'admin'),
                    initial,
                ):
                    raise state_store.StateStoreError(
                        'cannot persist initial admin credential',
                    )
                changed = True
            if changed:
                self.save_json(self.META_FILE, meta)
            return meta

    def migrate_admin_password(self):
        with self.meta_lock():
            meta = self.load_meta()
            plain = str(meta.get('admin_pass') or '')
            if plain:
                meta['admin_pass_hash'] = self.hash_secret(plain)
                del meta['admin_pass']
                self.save_json(self.META_FILE, meta)

    def _change_admin_password(self, current, new, confirm):
        """Validate and persist an admin password change under the Meta lock."""
        with self.meta_lock():
            meta = self.load_meta()
            stored_hash = str(meta.get('admin_pass_hash') or '')
            if not (
                len(current) <= self.PASSWORD_MAX_LENGTH
                and stored_hash
                and self.verify_secret(current, stored_hash)
            ):
                return 'password_wrong', ''
            if len(new) < self.PASSWORD_MIN_LENGTH:
                return 'password_short', ''
            if len(new) > self.PASSWORD_MAX_LENGTH:
                return 'password_long', ''
            if new != confirm:
                return 'password_mismatch', ''
            new_hash = self.hash_secret(new)
            meta['admin_pass_hash'] = new_hash
            meta.pop('admin_pass', None)
            self.save_json(self.META_FILE, meta)
            return 'ok', new_hash

    def verify_secret(self, plain, stored_hash):
        """Verify a plaintext value against a pbkdf2 hash."""
        try:
            if (
                not isinstance(plain, str)
                or len(plain) > self.PASSWORD_MAX_LENGTH
                or not isinstance(stored_hash, str)
                or len(stored_hash) > self.PASSWORD_HASH_MAX_LENGTH
            ):
                return False
            algorithm, rounds_s, salt_b64, digest_b64 = stored_hash.split('$')
            if algorithm != 'pbkdf2_sha256' or not rounds_s.isascii() or not rounds_s.isdigit():
                return False
            rounds = int(rounds_s)
            if not self.PBKDF2_ROUNDS_MIN <= rounds <= self.PBKDF2_ROUNDS_MAX:
                return False
            salt = base64.b64decode(
                salt_b64 + ('=' * (-len(salt_b64) % 4)),
                altchars=b'-_',
                validate=True,
            )
            expected = base64.b64decode(
                digest_b64 + ('=' * (-len(digest_b64) % 4)),
                altchars=b'-_',
                validate=True,
            )
            if len(salt) != self.PBKDF2_SALT_BYTES or len(expected) != self.PBKDF2_DIGEST_BYTES:
                return False
            candidate = hashlib.pbkdf2_hmac('sha256', plain.encode('utf-8'), salt, rounds)
            return hmac.compare_digest(candidate, expected)
        except Exception:
            return False

    def check_user_token(self, user, token):
        users = self.load_json(self.USERS_FILE, {})
        cfg = users.get(user)
        if not cfg:
            return None
        expected = str(cfg.get('sub_token') or '')
        supplied = str(token or '')
        if not self._safe_secret_equal(supplied, expected):
            return None
        return cfg

    def _safe_secret_equal(self, supplied, expected):
        left = str(supplied or '')
        right = str(expected or '')
        if (
            not left
            or not right
            or len(left) > self.PASSWORD_MAX_LENGTH
            or len(right) > self.PASSWORD_MAX_LENGTH
        ):
            return False
        return hmac.compare_digest(
            left.encode('utf-8'),
            right.encode('utf-8'),
        )

    def get_logged_in_user_context(self, handler):
        """Return the authenticated user and the credential that minted the session."""
        sid = self.parse_cookies(handler).get('usid', '')
        info = self.get_user_sessions().get(sid)
        if not isinstance(info, dict):
            return '', ''
        username = str(info.get('user') or '')
        if not self.is_valid_username(username):
            self.delete_user_session(sid)
            return '', ''
        kind = str(info.get('credential_kind') or self.USER_SESSION_PANEL_PASSWORD)
        if kind not in self.USER_SESSION_CREDENTIAL_KINDS:
            self.delete_user_session(sid)
            return '', ''
        generation = str(info.get('credential_generation') or '')
        if kind == self.USER_SESSION_SUBSCRIPTION_TOKEN and not generation:
            self.delete_user_session(sid)
            return '', ''
        if generation:
            cfg = self.load_json(self.USERS_FILE, {}).get(username)
            credential = (
                cfg.get('sub_token')
                if (isinstance(cfg, dict) and kind == self.USER_SESSION_SUBSCRIPTION_TOKEN)
                else (cfg.get('panel_pass_hash') if isinstance(cfg, dict) else '')
            )
            current = self._credential_generation(
                credential,
            )
            if not current or not hmac.compare_digest(generation, current):
                self.delete_user_session(sid)
                return '', ''
        return username, kind

    def get_logged_in_user(self, handler):
        return self.get_logged_in_user_context(handler)[0]

    def user_panel_access_error(
        self,
        cfg,
        session_kind,
        *,
        today=None,
    ):
        """Return the first authorization state blocking an active user panel.

        Authentication and account lifecycle are deliberately separate: an
        already-issued session may remain cryptographically valid after an
        administrator disables the account or its expiry date passes.  Every
        protected panel route uses this helper so password- and token-minted
        sessions apply the same lifecycle policy, while only password sessions are
        subject to the initial-password-change gate.
        """
        if not isinstance(cfg, dict):
            return 'forbidden'
        if session_kind == self.USER_SESSION_PANEL_PASSWORD and cfg.get(
            'panel_password_must_change'
        ):
            return 'password_change_required'
        if cfg.get('disabled'):
            return 'disabled'
        effective_today = today or self.local_now().date()
        if user_compat.is_expired(cfg, today=effective_today):
            return 'expired'
        return ''

    def is_logged_in(self, handler):
        q = self.parse_query_params(handler.path)
        token = (q.get('token') or [''])[0]
        meta = self.load_meta()
        admin_token = str(meta.get('admin_token') or '')
        if self._safe_secret_equal(token, admin_token):
            return True
        sid = self.parse_cookies(handler).get('sid', '')
        sessions = self.get_sessions()
        info = sessions.get(sid)
        if not isinstance(info, dict):
            return False
        generation = str(info.get('credential_generation') or '')
        if generation:
            current = self._credential_generation(meta.get('admin_pass_hash'))
            if not current or not hmac.compare_digest(generation, current):
                self.delete_session(sid)
                return False
        return True
