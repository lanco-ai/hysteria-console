"""Small, bounded OpenAI-compatible probes; credentials are never persisted."""
import asyncio
import ipaddress
import json
import socket
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

# These exact public gateways are administered on this server. Their local
# upstreams avoid hairpin routing and the IP gateway's self-signed certificate.
LOCAL_GATEWAYS = {
    'https://lancoai.site:9445/v1': 'http://127.0.0.1:8317/v1',
    'https://64.83.30.207:13004/v1': 'http://127.0.0.1:13003/v1',
}


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_base: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(min_length=1, max_length=4096, repr=False, pattern=r'^[!-~]+$')
    kind: Literal['models', 'chat'] = 'models'
    model: str = Field(default='', max_length=256)

    @field_validator('model')
    @classmethod
    def valid_model(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('invalid model')
        return value


async def resolve_addresses(host, port):
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(answer[4][0] for answer in answers))


async def resolve_target(base):
    raw = base.strip().rstrip('/')
    parsed = urlsplit(raw)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or '\\' in raw or any(ord(c) <= 32 for c in raw)):
        raise ValueError('invalid target')
    url = httpx.URL(raw)
    if raw in LOCAL_GATEWAYS:
        return httpx.URL(LOCAL_GATEWAYS[raw]), {}, {}
    # Pin DNS before sending credentials. Mixed public/private responses are
    # rejected, and redirects are never followed.
    addresses = await resolve_addresses(url.host, url.port or 443)
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('private target')
    return url.copy_with(host=addresses[0]), {'Host': url.netloc.decode('ascii')}, {'sni_hostname': url.host}


async def run_probe(value, *, transport=None):
    endpoint = '/models' if value.kind == 'models' else '/chat/completions'
    result = {'endpoint': endpoint, 'status': 'network_error', 'models': [], 'http_status': None}
    start = time.monotonic()
    try:
        async with asyncio.timeout(35):
            base, headers, extensions = await resolve_target(value.api_base)
            if value.kind == 'chat' and not value.model:
                raise ValueError('model required')
            headers.update({'Authorization': 'Bearer ' + value.api_key, 'Accept': 'application/json'})
            payload = None if value.kind == 'models' else {
                'model': value.model, 'messages': [{'role': 'user', 'content': 'Reply with OK only.'}],
                'max_tokens': 16, 'stream': False,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
                                          trust_env=False, transport=transport) as client:
                async with client.stream('GET' if value.kind == 'models' else 'POST',
                                         base.copy_with(path=base.path.rstrip('/') + endpoint),
                                         headers=headers, extensions=extensions, json=payload) as response:
                    result['http_status'] = response.status_code
                    if response.status_code != 200:
                        result['status'] = {401: 'authentication_failed', 403: 'permission_denied',
                                            404: 'not_found', 429: 'rate_limited'}.get(
                                                response.status_code, 'redirect_blocked' if response.is_redirect else 'upstream_error')
                        return result
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 512 * 1024:
                            result['status'] = 'response_too_large'
                            return result
                    try:
                        data = json.loads(raw)
                    except (ValueError, UnicodeError):
                        data = None
                    result['status'] = 'invalid_response'
                    if not isinstance(data, dict):
                        return result
                    if value.kind == 'models' and isinstance(data.get('data'), list):
                        models = [item['id'] for item in data['data'] if isinstance(item, dict)
                                  and isinstance(item.get('id'), str) and 0 < len(item['id']) <= 256
                                  and not any(ord(c) < 32 for c in item['id'])]
                        result['models'] = list(dict.fromkeys(models))[:1000]
                        if result['models']:
                            result['status'] = 'verified'
                        elif not data['data']:
                            result['status'] = 'empty_models'
                    elif value.kind == 'chat':
                        choices = data.get('choices')
                        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                            message = choices[0].get('message')
                            if isinstance(message, dict) and isinstance(message.get('content'), str) and message['content'].strip():
                                result['status'] = 'verified'
    except (ValueError, httpx.InvalidURL):
        result['status'] = 'invalid_target'
    except (TimeoutError, httpx.TimeoutException):
        result['status'] = 'timeout'
    except (httpx.HTTPError, OSError):
        result['status'] = 'network_error'
    finally:
        result['elapsed_ms'] = round((time.monotonic() - start) * 1000)
    return result
