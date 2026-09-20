"""Protocol routing for the plan and storyboard text assistants."""

from ..chat_service import (
    ChatSettings, ChatUpstreamError, forward_chat, forward_structured_json,
)


_JSON_SCHEMA_TYPES = {
    'OBJECT': 'object', 'ARRAY': 'array', 'STRING': 'string',
    'INTEGER': 'integer', 'NUMBER': 'number', 'BOOLEAN': 'boolean',
}


def _openai_schema(value):
    if isinstance(value, list):
        return [_openai_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {key: _openai_schema(item) for key, item in value.items()}
    schema_type = normalized.get('type')
    if isinstance(schema_type, str):
        normalized['type'] = _JSON_SCHEMA_TYPES.get(schema_type, schema_type)
    if normalized.get('type') == 'object':
        normalized['additionalProperties'] = False
    return normalized


def _chat_settings(profile):
    return ChatSettings(
        base_url=profile['base_url'], api_key=profile['api_key'],
        temperature=profile['temperature'],
    )


def generate_assistant_json(profile, model, prompt, schema, *, gemini_adapter):
    """Return a parsed structured value plus an honest output-mode label."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError('model_not_selected')
    if profile.get('protocol') == 'gemini_native':
        return gemini_adapter.generate_json(profile, model, prompt, schema), 'gemini_native_schema'
    if profile.get('protocol') == 'openai_compatible':
        value, mode = forward_structured_json(
            _chat_settings(profile), model=model, prompt=prompt,
            schema=_openai_schema(schema),
        )
        return value, mode
    raise ValueError('unsupported_service_protocol')


def _assistant_text(response):
    try:
        choices = response.get('choices') if isinstance(response, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get('message') if isinstance(choice, dict) else None
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(message, dict) or message.get('refusal'):
            raise ValueError
        if choice.get('finish_reason') not in (None, 'stop'):
            raise ValueError
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        return content
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise ChatUpstreamError(200, code='invalid_model_response') from None


def generate_assistant_text(profile, model, prompt, *, gemini_adapter):
    """Send a small explicit-model text request for an admin capability test."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError('model_not_selected')
    messages = [{'role': 'user', 'content': prompt}]
    if profile.get('protocol') == 'gemini_native':
        response = gemini_adapter.generate_chat(
            profile, model, messages, temperature=profile['temperature'],
        )
    elif profile.get('protocol') == 'openai_compatible':
        response = forward_chat(_chat_settings(profile), messages, model=model)
    else:
        raise ValueError('unsupported_service_protocol')
    return _assistant_text(response)
