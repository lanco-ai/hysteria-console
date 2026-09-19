"""Grok2API adapter used by the administrator-only video workflow.

Only the narrow, verified OpenAI-compatible endpoints are implemented here.
The adapter intentionally exposes sanitized error codes instead of upstream
response bodies, which may contain account or provider details.
"""

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .video_models import (
    CancelResult,
    Capabilities,
    Capability,
    ImageRequest,
    ProviderJob,
    ProviderJobStatus,
    VideoRequest,
    VideoSettings,
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 60


class ProviderError(RuntimeError):
    """A safe, body-free provider error."""

    def __init__(self, code: str, *, status: int | None = None, retry_after: str | None = None):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(code)


def _base_url(value: str) -> str:
    parsed = urlsplit(str(value or '').strip().rstrip('/'))
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ProviderError('invalid_base_url')
    path = parsed.path.rstrip('/')
    while path.endswith('/v1/v1'):
        path = path[:-3]
    if path.endswith('/images/generations'):
        path = path[:-len('/images/generations')]
    if path.endswith('/videos/generations'):
        path = path[:-len('/videos/generations')]
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', '')).rstrip('/')


def _url(settings: VideoSettings, suffix: str) -> str:
    base = _base_url(settings.base_url)
    if not base.endswith('/v1'):
        base += '/v1'
    return f'{base}/{suffix.lstrip("/")}'


def _error_for_status(status: int, retry_after: str | None = None) -> ProviderError:
    if status in (401, 403):
        code = 'authentication_failed'
    elif status == 404:
        code = 'model_or_endpoint_not_found'
    elif status == 429:
        code = 'rate_limited'
    elif status >= 500:
        code = 'provider_unavailable'
    else:
        code = 'provider_request_failed'
    return ProviderError(code, status=status, retry_after=retry_after)


def _read_json(response) -> dict:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError('response_too_large')
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError('invalid_provider_response') from None
    if not isinstance(payload, dict):
        raise ProviderError('invalid_provider_response')
    return payload


def _job_id(payload: dict) -> str:
    for key in ('id', 'request_id', 'requestId', 'task_id', 'taskId'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = payload.get('data')
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _job_id(data[0])
    if isinstance(data, dict):
        return _job_id(data)
    raise ProviderError('invalid_provider_response')


def _asset_url(payload: dict) -> str | None:
    for key in ('video_url', 'image_url', 'url', 'output_url'):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return value
    data = payload.get('data')
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _asset_url(data[0])
    if isinstance(data, dict):
        return _asset_url(data)
    for key in ('video', 'image', 'output', 'result'):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _asset_url(nested)
            if found:
                return found
    return None


class GrokVideoProvider:
    def __init__(self, *, opener: Callable | None = None):
        self.opener = opener or urllib.request.urlopen

    def _request(self, method: str, url: str, settings: VideoSettings, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                'Accept': 'application/json',
                'Authorization': f'Bearer {settings.api_key}',
                'Content-Type': 'application/json',
            },
        )
        try:
            with self.opener(request, timeout=REQUEST_TIMEOUT) as response:
                status = int(getattr(response, 'status', 200))
                if status < 200 or status >= 300:
                    raise _error_for_status(status, response.headers.get('Retry-After'))
                return _read_json(response)
        except ProviderError:
            raise
        except urllib.error.HTTPError as exc:
            raise _error_for_status(exc.code, exc.headers.get('Retry-After')) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ProviderError('timeout') from None

    def capabilities(self, settings: VideoSettings) -> Capabilities:
        payload = self._request('GET', _url(settings, 'models'), settings)
        entries = payload.get('data')
        if not isinstance(entries, list):
            raise ProviderError('invalid_provider_response')
        ids = []
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get('id'), str):
                ids.append(entry['id'])
        image_models = [model for model in ids if 'imagine-image' in model]
        video_models = [model for model in ids if 'imagine-video' in model]
        return Capabilities(
            image_models=image_models,
            video_models=video_models,
            first_last_frame=Capability(False, 'provider capability not verified'),
            video_composition=Capability(False, 'provider capability not verified'),
        )

    def generate_image(self, request: ImageRequest, settings: VideoSettings) -> ProviderJob:
        payload = {'model': request.model, 'prompt': request.prompt}
        if request.width is not None:
            payload['width'] = request.width
        if request.height is not None:
            payload['height'] = request.height
        result = self._request('POST', _url(settings, 'images/generations'), settings, payload)
        asset_url = _asset_url(result)
        # Grok2API follows the OpenAI image response shape and returns the
        # generated URL immediately (`data[].url`).  It does not return a
        # video-style request_id for this endpoint.
        if asset_url:
            return ProviderJob('', state='succeeded', asset_url=asset_url, metadata={'kind': 'image'})
        return ProviderJob(_job_id(result), asset_url=None, metadata={'kind': 'image'})

    def generate_video(self, request: VideoRequest, settings: VideoSettings) -> ProviderJob:
        payload = {'model': request.model, 'prompt': request.prompt}
        if request.image_url:
            # Current Grok2API schema uses the official xAI media object:
            # {"image": {"url": "..."}}. Keep this provider boundary
            # explicit so old top-level image_url payloads cannot regress.
            payload['image'] = {'url': request.image_url}
        if request.first_frame_url:
            payload['image'] = {'url': request.first_frame_url}
        if request.last_frame_url:
            # Independent first/last-frame generation is not advertised by
            # capabilities yet. Do not silently send an undocumented field.
            raise ProviderError('first_last_frame_unsupported')
        if request.duration is not None:
            payload['duration'] = request.duration
        if request.aspect_ratio:
            payload['aspect_ratio'] = request.aspect_ratio
        result = self._request('POST', _url(settings, 'videos/generations'), settings, payload)
        return ProviderJob(_job_id(result), asset_url=_asset_url(result), metadata={'kind': 'video'})

    def get_job(self, provider_job_id: str, settings: VideoSettings) -> ProviderJobStatus:
        result = self._request('GET', _url(settings, f'videos/{provider_job_id}'), settings)
        state = str(result.get('status') or result.get('state') or 'running').lower()
        mapped = {'done': 'succeeded', 'completed': 'succeeded', 'success': 'succeeded', 'error': 'failed'}
        state = mapped.get(state, state if state in {'queued', 'running', 'failed', 'succeeded'} else 'running')
        return ProviderJobStatus(
            provider_job_id=provider_job_id,
            state=state,
            asset_url=_asset_url(result),
            error_code='provider_failed' if state == 'failed' else None,
        )

    def cancel_job(self, provider_job_id: str, settings: VideoSettings) -> CancelResult:
        del provider_job_id, settings
        return CancelResult('unsupported')
