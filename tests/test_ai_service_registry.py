import json
import os

import pytest

from web_api.ai.service_store import AIServiceError, AIServiceStore


def test_registry_keeps_secrets_private_and_permissions_restrictive(tmp_path):
    store = AIServiceStore(
        tmp_path / 'ai' / 'registry.json',
        chat_legacy_path=tmp_path / 'legacy-chat.json',
        video_legacy_path=tmp_path / 'legacy-video.json',
        backup_dir=tmp_path / 'migration-backup',
    )
    initial = store.public()
    result = store.update_profile(
        'gemini-primary', revision=initial['revision'],
        api_key='test-gemini-secret',
    )

    gemini = next(item for item in result['profiles'] if item['id'] == 'gemini-primary')
    assert gemini['api_key_configured'] is True
    assert gemini['api_key_masked'] != 'test-gemini-secret'
    assert 'api_key' not in gemini
    assert 'test-gemini-secret' not in json.dumps(result)
    assert store.profile('gemini-primary')['api_key'] == 'test-gemini-secret'
    assert os.stat(store.path.parent).st_mode & 0o777 == 0o700
    assert os.stat(store.path).st_mode & 0o777 == 0o600


def test_profile_update_keeps_secret_when_omitted_and_clears_only_explicitly(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    first = store.public()
    saved = store.update_profile('gemini-primary', revision=first['revision'], api_key='secret-value')
    kept = store.update_profile('gemini-primary', revision=saved['revision'], name='My Gemini')
    assert store.profile('gemini-primary')['api_key'] == 'secret-value'

    cleared = store.update_profile('gemini-primary', revision=kept['revision'], clear_api_key=True)
    assert store.profile('gemini-primary')['api_key'] == ''
    assert next(item for item in cleared['profiles'] if item['id'] == 'gemini-primary')['api_key_configured'] is False


def test_gemini_base_url_accepts_loopback_http_and_https_but_rejects_public_http(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()

    saved = store.update_profile(
        'gemini-primary', revision=initial['revision'],
        base_url='http://127.0.0.1:8317/v1',
    )
    gemini = next(item for item in saved['profiles'] if item['id'] == 'gemini-primary')
    assert gemini['base_url'] == 'http://127.0.0.1:8317/v1'

    saved = store.update_profile(
        'gemini-primary', revision=saved['revision'],
        base_url='https://gateway.example/gemini/v1',
    )
    gemini = next(item for item in saved['profiles'] if item['id'] == 'gemini-primary')
    assert gemini['base_url'] == 'https://gateway.example/gemini/v1'

    with pytest.raises(AIServiceError, match='loopback'):
        store.update_profile(
            'gemini-primary', revision=saved['revision'],
            base_url='http://gateway.example/v1',
        )


def test_empty_gemini_base_url_is_rejected_without_changing_url_or_key(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    configured_url = 'http://127.0.0.1:8317/v1'
    saved = store.update_profile(
        'gemini-primary', revision=initial['revision'],
        base_url=configured_url, api_key='retained-gemini-key',
    )

    with pytest.raises(AIServiceError, match='base_url'):
        store.update_profile(
            'gemini-primary', revision=saved['revision'], base_url='',
        )

    profile = store.profile('gemini-primary')
    assert profile['base_url'] == configured_url
    assert profile['api_key'] == 'retained-gemini-key'


def test_changing_gemini_base_url_clears_verified_model_metadata(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    verified = store.update_catalog(
        'gemini-primary', [{'id': 'gemini-fast', 'name': 'Gemini Fast'}],
        capabilities=['chat', 'structured_output'], checked_at='2026-09-19T00:00:00Z',
        revision=initial['revision'],
    )

    changed = store.update_profile(
        'gemini-primary', revision=verified['revision'],
        base_url='http://127.0.0.1:8317/v1',
    )
    gemini = next(item for item in changed['profiles'] if item['id'] == 'gemini-primary')
    assert gemini['models'] == []
    assert gemini['last_verified_at'] == ''
    assert gemini['verified_capabilities'] == []


def test_changing_chat_profile_url_or_key_invalidates_old_model_catalog(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    saved = store.update_profile(
        'chat-primary', revision=initial['revision'],
        base_url='https://one.example/v1', api_key='first-key',
    )
    catalog = store.update_catalog(
        'chat-primary', [{'id': 'old-model'}], capabilities=['chat'],
        checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    bound = store.set_binding(
        'plan_assistant', 'chat-primary', model_id='old-model', revision=catalog['revision'],
    )
    changed_url = store.update_profile(
        'chat-primary', revision=bound['revision'], base_url='https://two.example/v1',
    )
    chat = next(item for item in changed_url['profiles'] if item['id'] == 'chat-primary')
    assert chat['models'] == []
    assert chat['last_verified_at'] == ''
    assert chat['verified_capabilities'] == []
    assert changed_url['model_bindings']['plan_assistant'] == 'old-model'


def test_bindings_accept_only_compatible_service_protocols(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    changed = store.set_binding('plan_assistant', 'gemini-primary', revision=initial['revision'])
    assert changed['bindings']['plan_assistant'] == 'gemini-primary'
    assert changed['model_bindings']['plan_assistant'] == ''
    with pytest.raises(AIServiceError, match='incompatible'):
        store.set_binding('image_generation', 'gemini-primary', revision=changed['revision'])


def test_assistant_binding_saves_an_explicit_model_and_keeps_legacy_models_empty(tmp_path):
    from web_api.ai.service_store import _default_state

    path = tmp_path / 'ai.json'
    old_state = _default_state()
    old_state.pop('model_bindings', None)
    path.write_text(json.dumps(old_state))
    store = AIServiceStore(path, chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')

    legacy = store.public()
    assert legacy['model_bindings']['plan_assistant'] == ''
    configured = store.update_profile('chat-primary', revision=legacy['revision'], api_key='test-chat-key')
    catalog = store.update_catalog(
        'chat-primary', [{'id': 'listed-first'}, {'id': 'explicit-choice'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=configured['revision'],
    )
    bound = store.set_binding(
        'plan_assistant', 'chat-primary', model_id='explicit-choice', revision=catalog['revision'],
    )
    assert bound['bindings']['plan_assistant'] == 'chat-primary'
    assert bound['model_bindings']['plan_assistant'] == 'explicit-choice'
    assistant = store.bound_assistant('plan_assistant')
    assert assistant['profile']['id'] == 'chat-primary'
    assert assistant['model_id'] == 'explicit-choice'


def test_assistant_binding_rejects_unlisted_models_and_preserves_stale_selection(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    saved = store.update_profile('gemini-primary', revision=initial['revision'], api_key='test-gemini-key')
    catalog = store.update_catalog(
        'gemini-primary', [{'id': 'model-a'}, {'id': 'model-b'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    with pytest.raises(AIServiceError, match='model'):
        store.set_binding(
            'plan_assistant', 'gemini-primary', model_id='not-listed', revision=catalog['revision'],
        )
    chosen = store.set_binding(
        'plan_assistant', 'gemini-primary', model_id='model-b', revision=catalog['revision'],
    )
    changed_catalog = store.update_catalog(
        'gemini-primary', [{'id': 'model-a'}], capabilities=['chat'],
        checked_at='2026-09-20T00:01:00Z', revision=chosen['revision'],
    )
    assert changed_catalog['model_bindings']['plan_assistant'] == 'model-b'
    assert store.bound_assistant('plan_assistant')['model_id'] == 'model-b'


def test_revision_conflict_does_not_overwrite_a_newer_profile(tmp_path):
    store = AIServiceStore(tmp_path / 'ai.json', chat_legacy_path=tmp_path / 'missing-chat.json', video_legacy_path=tmp_path / 'missing-video.json')
    initial = store.public()
    saved = store.update_profile('gemini-primary', revision=initial['revision'], api_key='secret-value')
    with pytest.raises(AIServiceError, match='conflict'):
        store.update_profile('gemini-primary', revision=initial['revision'], name='stale write')
    assert store.profile('gemini-primary')['name'] == 'Gemini'
    assert saved['revision'] != initial['revision']


def test_legacy_chat_and_video_settings_are_backed_up_and_imported_once(tmp_path):
    chat_path = tmp_path / 'chat' / 'settings.json'
    video_path = tmp_path / 'video' / 'settings.json'
    chat_path.parent.mkdir()
    video_path.parent.mkdir()
    chat_path.write_text(json.dumps({'base_url': 'https://chat.example/v1', 'api_key': 'chat-secret', 'temperature': 0.4}))
    video_path.write_text(json.dumps({'base_url': 'https://media.example/v1', 'api_key': 'media-secret', 'provider': 'grok'}))
    store = AIServiceStore(tmp_path / 'ai' / 'registry.json', chat_legacy_path=chat_path, video_legacy_path=video_path, backup_dir=tmp_path / 'ai-backup')

    first = store.public()
    assert store.profile('chat-primary')['api_key'] == 'chat-secret'
    assert store.profile('media-primary')['api_key'] == 'media-secret'
    assert first['bindings']['chat'] == 'chat-primary'
    assert first['bindings']['image_generation'] == 'media-primary'
    assert store.profile('chat-primary')['temperature'] == 0.4
    assert (tmp_path / 'ai-backup' / 'chat-settings.json').read_text() == chat_path.read_text()
    assert (tmp_path / 'ai-backup' / 'video-settings.json').read_text() == video_path.read_text()
    assert os.stat(tmp_path / 'ai-backup').st_mode & 0o777 == 0o700
    assert os.stat(tmp_path / 'ai-backup' / 'chat-settings.json').st_mode & 0o777 == 0o600

    changed = store.update_profile('chat-primary', revision=first['revision'], name='New Chat API')
    second = store.public()
    assert changed['revision'] == second['revision']
    assert store.profile('chat-primary')['name'] == 'New Chat API'
    assert store.profile('chat-primary')['api_key'] == 'chat-secret'


def test_malformed_legacy_settings_fail_closed_without_registry_or_secret_leak(tmp_path):
    chat_path = tmp_path / 'chat.json'
    chat_path.write_text('{broken-json')
    store = AIServiceStore(tmp_path / 'ai' / 'registry.json', chat_legacy_path=chat_path, video_legacy_path=tmp_path / 'missing-video.json', backup_dir=tmp_path / 'ai-backup')
    with pytest.raises(AIServiceError, match='migration'):
        store.public()
    assert not store.path.exists()
    assert (tmp_path / 'ai-backup' / 'chat-settings.json').read_text() == '{broken-json'
