"""Adapters from the HTTP boundary to the legacy panel services."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class LoginRequired(Exception):
    """No existing cookie session authorizes the requested read."""


class StateUnavailable(Exception):
    """Legacy state could not be read safely."""


class UserAccessDenied(Exception):
    """An authenticated user's current lifecycle state blocks panel access."""

    _PUBLIC_CODES = frozenset(('disabled', 'expired', 'forbidden', 'password_change_required'))

    def __init__(self, code):
        self.code = code if code in self._PUBLIC_CODES else 'forbidden'
        super().__init__()


@dataclass(frozen=True, slots=True)
class LegacyRequestBridge:
    """The complete legacy-shaped request surface exposed to identity code."""

    headers: Mapping[str, str]
    path: str


class _RequestHeaders(Mapping):
    def __init__(self, headers):
        self._headers = MappingProxyType(
            {str(name).lower(): str(value) for name, value in headers.items()}
        )

    def __getitem__(self, name):
        return self._headers[str(name).lower()]

    def __iter__(self):
        return iter(self._headers)

    def __len__(self):
        return len(self._headers)

    def get(self, name, default=None):
        return self._headers.get(str(name).lower(), default)


class LegacyPanelServices:
    """Call authoritative synchronous services through a narrow read adapter."""

    def __init__(self, service_module):
        self.service_module = service_module

    def _bridge(self, *, headers, path):
        return LegacyRequestBridge(
            headers=_RequestHeaders(headers),
            path=str(path).split('?', 1)[0],
        )

    def _run_read(self, operation):
        service = self.service_module

        @service.request_multiplier_snapshot
        def snapshotted_read():
            try:
                return operation()
            except (service.state_store.StateStoreError, OSError) as exc:
                try:
                    if service._state_failure_requires_static_stop(exc):
                        service._fail_closed_static_access(exc)
                finally:
                    raise StateUnavailable from None

        return snapshotted_read()

    def read_session(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if service.is_logged_in(request):
                return {'role': 'admin'}
            username, credential_kind = service.get_logged_in_user_context(request)
            if not username:
                raise LoginRequired
            users = service.load_json(service.USERS_FILE, {})
            error = service.user_panel_access_error(
                users.get(username),
                credential_kind,
            )
            if error:
                raise UserAccessDenied(error)
            return {'role': 'user', 'username': username}

        return self._run_read(read)

    def read_admin_overview(self, *, headers, path):
        request = self._bridge(headers=headers, path=path)
        service = self.service_module

        def read():
            if not service.is_logged_in(request):
                raise LoginRequired
            return service._build_overview_json_payload(now=service.local_now())

        return self._run_read(read)
