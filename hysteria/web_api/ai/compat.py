"""Compatibility facades keeping the existing Chat and Video route contracts."""

from datetime import datetime, timezone

from ..chat_service import (
    ChatSettings, ChatSettingsError, ChatUpstreamError, forward_chat,
    forward_chat_stream, list_chat_models, mask_api_key,
)
from ..video_models import VideoSettings
from ..video_service import VideoSettingsError, mask_video_key
from .gemini import GeminiAdapter, GeminiUpstreamError
from .service_store import AIServiceError, AIServiceStore


class ChatSettingsAdapter:
    """Expose the selected shared Chat service through the legacy Chat API."""

    def __init__(self, store: AIServiceStore, gemini: GeminiAdapter | None = None):
        self.store = store
        self.gemini = gemini or GeminiAdapter()

    def _profile(self):
        try:
            profile = self.store.bound_profile('chat')
        except AIServiceError as exc:
            raise ChatSettingsError('settings unavailable') from exc
        if profile['protocol'] not in ('openai_compatible', 'gemini_native'):
            raise ChatSettingsError('settings unavailable')
        return profile

    def read(self) -> ChatSettings:
        profile = self._profile()
        return ChatSettings(profile['base_url'], profile['api_key'], profile['temperature'])

    def public(self) -> dict[str, object]:
        profile = self._profile()
        return {
            'base_url': profile['base_url'],
            'temperature': profile['temperature'],
            'api_key_configured': bool(profile['api_key']),
            'api_key_masked': mask_api_key(profile['api_key']),
            'service_name': profile['name'],
            'protocol': profile['protocol'],
        }

    def update(self, *, base_url=None, api_key=None, temperature=None) -> dict[str, object]:
        if base_url is not None or api_key is not None:
            raise ChatSettingsError('service configuration is managed in Service Center')
        profile = self._profile()
        try:
            result = self.store.update_profile(
                profile['id'], revision=self.store.public()['revision'],
                **({'temperature': temperature} if temperature is not None else {}),
            )
        except AIServiceError as exc:
            raise ChatSettingsError('settings unavailable') from exc
        item = next(value for value in result['profiles'] if value['id'] == profile['id'])
        return {
            'base_url': item['base_url'],
            'temperature': item['temperature'],
            'api_key_configured': item['api_key_configured'],
            'api_key_masked': item['api_key_masked'],
            'service_name': item['name'],
            'protocol': item['protocol'],
        }

    @staticmethod
    def _as_chat_settings(profile: dict) -> ChatSettings:
        return ChatSettings(profile['base_url'], profile['api_key'], profile['temperature'])

    @staticmethod
    def _translate_gemini_error(error: GeminiUpstreamError):
        raise ChatUpstreamError(
            error.status, retry_after=error.retry_after, code=error.code,
        ) from None

    def complete(self, messages, *, model, reasoning_effort='auto'):
        profile = self._profile()
        if not profile['api_key']:
            raise ChatSettingsError('api key is not configured')
        if profile['protocol'] == 'gemini_native':
            try:
                return self.gemini.generate_chat(
                    profile, model, messages, temperature=profile['temperature'],
                    reasoning_effort=reasoning_effort,
                )
            except GeminiUpstreamError as exc:
                self._translate_gemini_error(exc)
        return forward_chat(
            self._as_chat_settings(profile), messages, model=model,
            reasoning_effort=reasoning_effort,
        )

    def stream(self, messages, *, model, reasoning_effort='auto'):
        profile = self._profile()
        if not profile['api_key']:
            raise ChatSettingsError('api key is not configured')
        if profile['protocol'] == 'gemini_native':
            try:
                return self.gemini.stream_chat(
                    profile, model, messages, temperature=profile['temperature'],
                    reasoning_effort=reasoning_effort,
                )
            except GeminiUpstreamError as exc:
                self._translate_gemini_error(exc)
        return forward_chat_stream(
            self._as_chat_settings(profile), messages, model=model,
            reasoning_effort=reasoning_effort,
        )

    def models(self):
        profile = self._profile()
        if not profile['api_key']:
            raise ChatSettingsError('api key is not configured')
        if profile['models']:
            return profile['models']
        if profile['protocol'] == 'gemini_native':
            try:
                models = self.gemini.list_models(profile)
            except GeminiUpstreamError as exc:
                self._translate_gemini_error(exc)
        else:
            models = list_chat_models(self._as_chat_settings(profile))
        try:
            result = self.store.update_catalog(
                profile['id'], models, capabilities=['chat'],
                checked_at=datetime.now(timezone.utc).isoformat(),
                revision=self.store.public()['revision'],
            )
        except AIServiceError as exc:
            raise ChatSettingsError('settings unavailable') from exc
        return next(item for item in result['profiles'] if item['id'] == profile['id'])['models']


class VideoSettingsAdapter:
    """Expose the shared media profile through the legacy Video API contract."""

    def __init__(self, store: AIServiceStore):
        self.store = store

    def _profile(self):
        try:
            profile = self.store.bound_profile('video_generation')
        except AIServiceError as exc:
            raise VideoSettingsError('settings unavailable') from exc
        if profile['protocol'] != 'grok_media':
            raise VideoSettingsError('settings unavailable')
        return profile

    def read(self) -> VideoSettings:
        profile = self._profile()
        return VideoSettings(profile['base_url'], profile['api_key'], profile['provider'])

    def public(self) -> dict[str, object]:
        profile = self._profile()
        return {
            'provider': profile['provider'],
            'base_url': profile['base_url'],
            'api_key_configured': bool(profile['api_key']),
            'api_key_masked': mask_video_key(profile['api_key']),
        }

    def update(self, *, base_url=None, api_key=None, provider=None):
        profile = self._profile()
        if provider is not None and str(provider).strip().lower() != profile['provider']:
            raise VideoSettingsError('unsupported provider')
        values = {}
        if base_url is not None:
            values['base_url'] = base_url
        if api_key is not None:
            values['api_key'] = api_key
        try:
            result = self.store.update_profile(
                profile['id'], revision=self.store.public()['revision'], **values,
            )
        except AIServiceError as exc:
            raise VideoSettingsError('settings unavailable') from exc
        item = next(value for value in result['profiles'] if value['id'] == profile['id'])
        return {
            'provider': item['provider'], 'base_url': item['base_url'],
            'api_key_configured': item['api_key_configured'], 'api_key_masked': item['api_key_masked'],
        }
