"""Private registry for AI service credentials and feature bindings.

The public representation is deliberately separate from the private state:
API keys never leave this module's server-side profile objects.
"""

from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

import state_store


DEFAULT_AI_SERVICES_PATH = Path('/root/hysteria/state/ai/services.json')
DEFAULT_CHAT_SETTINGS_PATH = Path('/root/hysteria/state/chat/settings.json')
DEFAULT_VIDEO_SETTINGS_PATH = Path('/root/hysteria/state/video/settings.json')
DEFAULT_MIGRATION_BACKUP_DIR = Path('/root/hysteria/state/ai/migration-backup')
GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
PROTOCOLS = {'gemini_native', 'openai_compatible', 'grok_media'}
PROFILE_IDS = {'gemini-primary', 'chat-primary', 'media-primary'}
FEATURE_PROTOCOLS = {
    'chat': {'openai_compatible', 'gemini_native'},
    'plan_assistant': {'openai_compatible', 'gemini_native'},
    'video_assistant': {'openai_compatible', 'gemini_native'},
    'image_generation': {'grok_media'},
    'video_generation': {'grok_media'},
}


class AIServiceError(ValueError):
    """Invalid or unavailable AI service configuration."""


def _mask_key(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '•' * 8
    return f'{value[:3]}…{value[-4:]}'


def _default_profile(profile_id: str) -> dict:
    defaults = {
        'gemini-primary': {
            'id': 'gemini-primary', 'name': 'Gemini',
            'protocol': 'gemini_native', 'base_url': GEMINI_BASE_URL,
            'api_key': '', 'temperature': 0.7, 'provider': '',
            'models': [], 'last_verified_at': '', 'verified_capabilities': [],
        },
        'chat-primary': {
            'id': 'chat-primary', 'name': 'Chat API',
            'protocol': 'openai_compatible', 'base_url': '',
            'api_key': '', 'temperature': 0.7, 'provider': '',
            'models': [], 'last_verified_at': '', 'verified_capabilities': [],
        },
        'media-primary': {
            'id': 'media-primary', 'name': '图像 / 视频生成',
            'protocol': 'grok_media', 'base_url': '',
            'api_key': '', 'temperature': 0.7, 'provider': 'grok',
            'models': [], 'last_verified_at': '', 'verified_capabilities': [],
        },
    }
    return deepcopy(defaults[profile_id])


def _default_state() -> dict:
    return {
        'schema_version': 1,
        'revision': 0,
        'migrated_legacy': False,
        'profiles': {profile_id: _default_profile(profile_id) for profile_id in sorted(PROFILE_IDS)},
        'bindings': {
            'chat': 'chat-primary',
            'plan_assistant': 'gemini-primary',
            'video_assistant': 'gemini-primary',
            'image_generation': 'media-primary',
            'video_generation': 'media-primary',
        },
        'model_bindings': {'plan_assistant': '', 'video_assistant': ''},
    }


def _validate_base_url(value: object, protocol: str) -> str:
    url = str(value or '').strip()
    if len(url) > 2048:
        raise AIServiceError('base_url is too long')
    if not url:
        if protocol == 'gemini_native':
            raise AIServiceError('Gemini base_url is required')
        return ''
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ('http', 'https') or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or '\\' in url
        or any(ord(char) < 32 for char in url)
    ):
        raise AIServiceError('base_url is invalid')
    try:
        parsed.port
    except ValueError:
        raise AIServiceError('base_url has an invalid port') from None
    if protocol in ('gemini_native', 'grok_media') and parsed.scheme == 'http':
        hostname = parsed.hostname or ''
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.lower().rstrip('.') == 'localhost'
        if not loopback:
            label = 'Gemini API' if protocol == 'gemini_native' else 'media API'
            raise AIServiceError(f'{label} must use HTTPS or loopback HTTP')
    return url.rstrip('/')


def _validate_models(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 1000:
        raise AIServiceError('invalid model catalog')
    models = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise AIServiceError('invalid model catalog')
        model_id = raw.get('id')
        name = raw.get('name', model_id)
        if (
            not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256
            or not isinstance(name, str) or len(name) > 256
            or any(ord(char) < 32 for char in model_id + name)
        ):
            raise AIServiceError('invalid model catalog')
        model_id = model_id.strip()
        if model_id in seen:
            continue
        seen.add(model_id)
        result = {'id': model_id, 'name': name.strip() or model_id}
        for key in ('context_window', 'input_token_limit', 'output_token_limit'):
            candidate = raw.get(key)
            if candidate is not None:
                if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate <= 0:
                    raise AIServiceError('invalid model metadata')
                result[key] = candidate
        models.append(result)
    return models


class AIServiceStore:
    """Atomic private registry; first access backs up and imports legacy settings."""

    def __init__(
        self,
        path: str | Path = DEFAULT_AI_SERVICES_PATH,
        *,
        chat_legacy_path: str | Path = DEFAULT_CHAT_SETTINGS_PATH,
        video_legacy_path: str | Path = DEFAULT_VIDEO_SETTINGS_PATH,
        backup_dir: str | Path = DEFAULT_MIGRATION_BACKUP_DIR,
    ):
        self.path = Path(path)
        self.chat_legacy_path = Path(chat_legacy_path)
        self.video_legacy_path = Path(video_legacy_path)
        self.backup_dir = Path(backup_dir)

    def _prepare_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)

    def _backup_legacy(self):
        sources = (
            ('chat-settings.json', self.chat_legacy_path),
            ('video-settings.json', self.video_legacy_path),
        )
        present = [(name, source) for name, source in sources if source.exists() or source.is_symlink()]
        if not present:
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backup_dir.chmod(0o700)
        for name, source in present:
            if source.is_symlink() or not source.is_file():
                raise AIServiceError('legacy settings migration failed')
            destination = self.backup_dir / name
            if destination.exists():
                continue
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with source.open('rb') as source_file, os.fdopen(fd, 'wb') as target_file:
                    shutil.copyfileobj(source_file, target_file)
                    target_file.flush()
                    os.fsync(target_file.fileno())
                    fd = -1
            finally:
                if fd >= 0:
                    os.close(fd)
            destination.chmod(0o600)

    @staticmethod
    def _legacy_json(path: Path) -> dict:
        if not path.exists():
            return {}
        raw = state_store.load_json_strict(path, {})
        if not isinstance(raw, dict):
            raise AIServiceError('legacy settings migration failed')
        return raw

    def _import_legacy(self, state: dict) -> dict:
        chat = self._legacy_json(self.chat_legacy_path)
        video = self._legacy_json(self.video_legacy_path)
        if set(chat) - {'base_url', 'api_key', 'temperature'}:
            raise AIServiceError('legacy settings migration failed')
        if set(video) - {'base_url', 'api_key', 'provider'}:
            raise AIServiceError('legacy settings migration failed')

        chat_profile = state['profiles']['chat-primary']
        chat_profile['base_url'] = _validate_base_url(chat.get('base_url'), 'openai_compatible')
        chat_profile['api_key'] = str(chat.get('api_key') or '')
        chat_profile['temperature'] = float(chat.get('temperature', 0.7))
        if len(chat_profile['api_key']) > 1024 or not 0 <= chat_profile['temperature'] <= 2:
            raise AIServiceError('legacy settings migration failed')

        video_profile = state['profiles']['media-primary']
        video_profile['base_url'] = _validate_base_url(video.get('base_url'), 'grok_media')
        video_profile['api_key'] = str(video.get('api_key') or '')
        video_profile['provider'] = str(video.get('provider') or 'grok').strip().lower()
        if len(video_profile['api_key']) > 2048 or video_profile['provider'] != 'grok':
            raise AIServiceError('legacy settings migration failed')

        state['migrated_legacy'] = True
        return state

    @staticmethod
    def _validate_state(raw: object) -> dict:
        if not isinstance(raw, dict) or raw.get('schema_version') != 1:
            raise AIServiceError('AI service registry is invalid')
        if isinstance(raw.get('revision'), bool) or not isinstance(raw.get('revision'), int) or raw['revision'] < 0:
            raise AIServiceError('AI service registry is invalid')
        profiles = raw.get('profiles')
        bindings = raw.get('bindings')
        if not isinstance(profiles, dict) or set(profiles) != PROFILE_IDS or not isinstance(bindings, dict):
            raise AIServiceError('AI service registry is invalid')
        state = deepcopy(raw)
        model_bindings = state.get('model_bindings', {})
        if not isinstance(model_bindings, dict) or set(model_bindings) - {'plan_assistant', 'video_assistant'}:
            raise AIServiceError('AI service registry is invalid')
        state['model_bindings'] = {
            feature: model_bindings.get(feature, '')
            for feature in ('plan_assistant', 'video_assistant')
        }
        if any(not isinstance(value, str) or len(value) > 256 for value in state['model_bindings'].values()):
            raise AIServiceError('AI service registry is invalid')
        for profile_id in PROFILE_IDS:
            profile = state['profiles'].get(profile_id)
            default = _default_profile(profile_id)
            if not isinstance(profile, dict) or profile.get('id') != profile_id or profile.get('protocol') != default['protocol']:
                raise AIServiceError('AI service registry is invalid')
            profile['name'] = str(profile.get('name') or '')
            if not profile['name'] or len(profile['name']) > 80:
                raise AIServiceError('AI service registry is invalid')
            profile['base_url'] = _validate_base_url(profile.get('base_url'), profile['protocol'])
            profile['api_key'] = str(profile.get('api_key') or '')
            if len(profile['api_key']) > 2048:
                raise AIServiceError('AI service registry is invalid')
            temperature = profile.get('temperature', 0.7)
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 2:
                raise AIServiceError('AI service registry is invalid')
            profile['temperature'] = float(temperature)
            profile['provider'] = str(profile.get('provider') or '')
            if profile['protocol'] == 'grok_media' and profile['provider'] != 'grok':
                raise AIServiceError('AI service registry is invalid')
            profile['models'] = _validate_models(profile.get('models', []))
            caps = profile.get('verified_capabilities', [])
            if not isinstance(caps, list) or any(not isinstance(item, str) or len(item) > 80 for item in caps):
                raise AIServiceError('AI service registry is invalid')
            profile['verified_capabilities'] = sorted(set(caps))
            if not isinstance(profile.get('last_verified_at', ''), str) or len(profile.get('last_verified_at', '')) > 64:
                raise AIServiceError('AI service registry is invalid')
        if set(bindings) != set(FEATURE_PROTOCOLS):
            raise AIServiceError('AI service registry is invalid')
        for feature, profile_id in bindings.items():
            profile = state['profiles'].get(profile_id)
            if not profile or profile['protocol'] not in FEATURE_PROTOCOLS[feature]:
                raise AIServiceError('AI service registry is invalid')
        state['migrated_legacy'] = bool(state.get('migrated_legacy'))
        return state

    def _read_file(self) -> dict:
        try:
            raw = state_store.load_json_strict(self.path, None, required=True)
            return self._validate_state(raw)
        except (state_store.StateStoreError, AIServiceError) as exc:
            raise AIServiceError('AI service registry unavailable') from exc

    def _save(self, state: dict) -> dict:
        self._prepare_directory()
        validated = self._validate_state(state)
        state_store.save_json(self.path, validated)
        self.path.chmod(0o600)
        return validated

    def _ensure_initialized(self) -> dict:
        if self.path.exists():
            return self._read_file()
        self._prepare_directory()
        lock_path = self.path.with_name(self.path.name + '.lock')
        try:
            with state_store.file_lock(lock_path, timeout=3):
                if self.path.exists():
                    return self._read_file()
                self._backup_legacy()
                state = _default_state()
                try:
                    state = self._import_legacy(state)
                except (OSError, ValueError, TypeError, state_store.StateStoreError) as exc:
                    raise AIServiceError('legacy settings migration failed') from exc
                state['revision'] = 1
                return self._save(state)
        except (OSError, RuntimeError, state_store.StateStoreError) as exc:
            if isinstance(exc, AIServiceError):
                raise
            raise AIServiceError('AI service registry unavailable') from exc

    @staticmethod
    def _public_profile(profile: dict) -> dict:
        return {
            'id': profile['id'],
            'name': profile['name'],
            'protocol': profile['protocol'],
            'base_url': profile['base_url'],
            'api_key_configured': bool(profile['api_key']),
            'api_key_masked': _mask_key(profile['api_key']),
            'temperature': profile['temperature'],
            'provider': profile['provider'],
            'models': deepcopy(profile['models']),
            'last_verified_at': profile['last_verified_at'],
            'verified_capabilities': list(profile['verified_capabilities']),
        }

    def public(self) -> dict:
        state = self._ensure_initialized()
        return {
            'revision': str(state['revision']),
            'profiles': [self._public_profile(state['profiles'][key]) for key in sorted(state['profiles'])],
            'bindings': deepcopy(state['bindings']),
            'model_bindings': deepcopy(state['model_bindings']),
        }

    def profile(self, profile_id: str) -> dict:
        if profile_id not in PROFILE_IDS:
            raise AIServiceError('unknown AI service')
        return deepcopy(self._ensure_initialized()['profiles'][profile_id])

    def profile_snapshot(self, profile_id: str) -> dict:
        if profile_id not in PROFILE_IDS:
            raise AIServiceError('unknown AI service')
        state = self._ensure_initialized()
        return {
            'profile': deepcopy(state['profiles'][profile_id]),
            'revision': str(state['revision']),
        }

    def bound_profile(self, feature: str) -> dict:
        if feature not in FEATURE_PROTOCOLS:
            raise AIServiceError('unknown AI feature')
        state = self._ensure_initialized()
        profile_id = state['bindings'][feature]
        return deepcopy(state['profiles'][profile_id])

    def bound_assistant(self, feature: str) -> dict:
        if feature not in {'plan_assistant', 'video_assistant'}:
            raise AIServiceError('unknown AI assistant')
        state = self._ensure_initialized()
        profile_id = state['bindings'][feature]
        return {
            'profile': deepcopy(state['profiles'][profile_id]),
            'model_id': state['model_bindings'][feature],
            'revision': str(state['revision']),
        }

    @staticmethod
    def _check_revision(state: dict, revision: object):
        if not isinstance(revision, str) or revision != str(state['revision']):
            raise AIServiceError('revision conflict')

    def _mutate(self, revision: object, update):
        self._ensure_initialized()
        lock_path = self.path.with_name(self.path.name + '.lock')
        try:
            with state_store.file_lock(lock_path, timeout=3):
                state = self._read_file()
                self._check_revision(state, revision)
                update(state)
                state['revision'] += 1
                return self.public_from_state(self._save(state))
        except (OSError, RuntimeError, state_store.StateStoreError) as exc:
            if isinstance(exc, AIServiceError):
                raise
            raise AIServiceError('AI service registry unavailable') from exc

    @classmethod
    def public_from_state(cls, state: dict) -> dict:
        return {
            'revision': str(state['revision']),
            'profiles': [cls._public_profile(state['profiles'][key]) for key in sorted(state['profiles'])],
            'bindings': deepcopy(state['bindings']),
            'model_bindings': deepcopy(state['model_bindings']),
        }

    def update_profile(self, profile_id: str, *, revision: object, **values) -> dict:
        if profile_id not in PROFILE_IDS:
            raise AIServiceError('unknown AI service')
        allowed = {'name', 'base_url', 'api_key', 'clear_api_key', 'temperature'}
        if set(values) - allowed:
            raise AIServiceError('invalid service fields')

        def update(state):
            profile = state['profiles'][profile_id]
            previous_base_url = profile['base_url']
            previous_api_key = profile['api_key']
            if 'name' in values:
                name = str(values['name'] or '').strip()
                if not name or len(name) > 80:
                    raise AIServiceError('service name is invalid')
                profile['name'] = name
            if 'base_url' in values:
                profile['base_url'] = _validate_base_url(values['base_url'], profile['protocol'])
            if 'temperature' in values:
                try:
                    temperature = float(values['temperature'])
                except (TypeError, ValueError):
                    raise AIServiceError('temperature is invalid') from None
                if isinstance(values['temperature'], bool) or not 0 <= temperature <= 2:
                    raise AIServiceError('temperature is invalid')
                profile['temperature'] = temperature
            if values.get('clear_api_key') is True:
                profile['api_key'] = ''
            elif 'api_key' in values and values['api_key']:
                api_key = str(values['api_key'])
                if len(api_key) > 2048 or any(ord(char) < 32 for char in api_key):
                    raise AIServiceError('API key is invalid')
                profile['api_key'] = api_key
            if values.get('clear_api_key') not in (None, True, False):
                raise AIServiceError('clear_api_key is invalid')
            if (
                profile['protocol'] in {'gemini_native', 'openai_compatible'}
                and (profile['base_url'] != previous_base_url or profile['api_key'] != previous_api_key)
            ):
                profile['models'] = []
                profile['last_verified_at'] = ''
                profile['verified_capabilities'] = []

        return self._mutate(revision, update)

    def set_binding(self, feature: str, profile_id: str, *, revision: object, model_id: object = None) -> dict:
        if feature not in FEATURE_PROTOCOLS or profile_id not in PROFILE_IDS:
            raise AIServiceError('invalid feature binding')
        if feature not in {'plan_assistant', 'video_assistant'} and model_id is not None:
            raise AIServiceError('invalid feature binding')
        if model_id is not None and (
            not isinstance(model_id, str) or len(model_id) > 256
            or any(ord(char) < 32 for char in model_id)
        ):
            raise AIServiceError('invalid model binding')

        def update(state):
            profile = state['profiles'][profile_id]
            if profile['protocol'] not in FEATURE_PROTOCOLS[feature]:
                raise AIServiceError('incompatible AI service')
            previous_profile_id = state['bindings'][feature]
            state['bindings'][feature] = profile_id
            if feature in {'plan_assistant', 'video_assistant'}:
                if model_id is not None:
                    model_ids = {item['id'] for item in profile['models']}
                    if model_id and model_id not in model_ids:
                        raise AIServiceError('invalid model binding')
                    state['model_bindings'][feature] = model_id
                elif previous_profile_id != profile_id:
                    state['model_bindings'][feature] = ''

        return self._mutate(revision, update)

    def update_catalog(self, profile_id: str, models: object, *, capabilities: object, checked_at: object, revision: object) -> dict:
        if profile_id not in PROFILE_IDS:
            raise AIServiceError('unknown AI service')
        normalized_models = _validate_models(models)
        if not isinstance(capabilities, list) or any(not isinstance(value, str) or len(value) > 80 for value in capabilities):
            raise AIServiceError('invalid service capabilities')
        if not isinstance(checked_at, str) or len(checked_at) > 64:
            raise AIServiceError('invalid service verification time')

        def update(state):
            profile = state['profiles'][profile_id]
            profile['models'] = normalized_models
            profile['verified_capabilities'] = sorted(set(capabilities))
            profile['last_verified_at'] = checked_at

        return self._mutate(revision, update)
