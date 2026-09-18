"""Durable, secret-safe state for the administrator video workflow."""

import math
from pathlib import Path
from urllib.parse import urlsplit

import state_store

from .video_models import VideoSettings

DEFAULT_VIDEO_SETTINGS_PATH = Path('/root/hysteria/state/video/settings.json')
_MISSING = object()


class VideoSettingsError(ValueError):
    pass


def mask_video_key(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '•' * 8
    return f'{value[:3]}…{value[-4:]}'


def _validate(base_url: str, api_key: str, provider: str) -> None:
    if len(base_url) > 2048:
        raise VideoSettingsError('base_url is too long')
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise VideoSettingsError('base_url must be an http(s) URL')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VideoSettingsError('base_url contains unsupported components')
    if provider != 'grok':
        raise VideoSettingsError('unsupported provider')
    if not api_key or len(api_key) > 2048:
        raise VideoSettingsError('api_key is invalid')


def _coerce(raw: object) -> VideoSettings:
    if raw is None:
        return VideoSettings('', '')
    if not isinstance(raw, dict):
        raise VideoSettingsError('invalid settings shape')
    base_url = str(raw.get('base_url') or '').strip()
    api_key = str(raw.get('api_key') or '')
    provider = str(raw.get('provider') or 'grok').strip().lower()
    if not base_url or not api_key:
        return VideoSettings(base_url, api_key, provider)
    _validate(base_url, api_key, provider)
    return VideoSettings(base_url, api_key, provider)


class VideoSettingsStore:
    def __init__(self, path: str | Path = DEFAULT_VIDEO_SETTINGS_PATH):
        self.path = Path(path)

    def read(self) -> VideoSettings:
        try:
            raw = state_store.load_json_strict(self.path, {})
        except state_store.StateStoreError as exc:
            raise VideoSettingsError('settings unavailable') from exc
        return _coerce(raw)

    def public(self) -> dict[str, object]:
        settings = self.read()
        return {
            'provider': settings.provider,
            'base_url': settings.base_url,
            'api_key_configured': bool(settings.api_key),
            'api_key_masked': mask_video_key(settings.api_key),
        }

    def update(self, *, base_url: object = _MISSING, api_key: object = _MISSING, provider: object = _MISSING):
        lock_path = self.path.with_name(self.path.name + '.lock')
        try:
            with state_store.file_lock(lock_path):
                current = self.read()
                next_base = current.base_url if base_url is _MISSING else str(base_url or '').strip()
                next_key = current.api_key if api_key is _MISSING else str(api_key or '')
                next_provider = current.provider if provider is _MISSING else str(provider or '').strip().lower()
                _validate(next_base, next_key, next_provider)
                state_store.save_json(self.path, {
                    'provider': next_provider,
                    'base_url': next_base,
                    'api_key': next_key,
                })
                self.path.chmod(0o600)
        except (state_store.StateStoreError, OSError) as exc:
            raise VideoSettingsError('settings unavailable') from exc
        return self.public()

