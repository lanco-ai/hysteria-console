"""Grok2API adapter used by the administrator-only video workflow.

Only the narrow, verified OpenAI-compatible endpoints are implemented here.
The adapter intentionally exposes sanitized error codes instead of upstream
response bodies, which may contain account or provider details.
"""

import json
import re
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
MEDIA_CHUNK_BYTES = 64 * 1024
MAX_MEDIA_BYTES = 128 * 1024 * 1024
_MEDIA_RANGE_RE = re.compile(r'bytes=(?:\d{0,16})-(?:\d{0,16})$')
_UNSATISFIED_CONTENT_RANGE_RE = re.compile(r'^bytes \*/\d{1,20}$')


class ProviderError(RuntimeError):
    """A safe, body-free provider error."""

    def __init__(
        self, code: str, *, status: int | None = None,
        retry_after: str | None = None, content_range: str | None = None,
    ):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.content_range = content_range
        super().__init__(code)


@dataclass
class ProviderMedia:
    response: object
    status_code: int
    content_type: str
    content_length: str | None
    content_range: str | None

    def iter_bytes(self):
        total = 0
        try:
            while True:
                chunk = self.response.read(MEDIA_CHUNK_BYTES)
                if not chunk:
                    return
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise ProviderError('media_too_large')
                yield chunk
        finally:
            self.close()

    def close(self):
        self.response.close()


class _ProviderMediaRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str | None, int], media_path_re: re.Pattern):
        super().__init__()
        self.origin = origin
        self.media_path_re = media_path_re

    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            target = urlsplit(new_url)
            port = target.port or (443 if target.scheme.lower() == 'https' else 80)
            origin = (target.scheme.lower(), target.hostname, port)
        except (TypeError, ValueError):
            return None
        if (
            origin != self.origin
            or target.username or target.password or target.query or target.fragment
            or not self.media_path_re.fullmatch(target.path)
        ):
            return None
        return super().redirect_request(request, response, code, message, headers, new_url)


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
        self._custom_opener = opener is not None
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

    def open_asset(self, asset_url: str, settings: VideoSettings, *, range_header: str | None = None) -> ProviderMedia:
        """Open only same-origin archived media routes and stream bounded bytes."""
        try:
            configured = urlsplit(settings.base_url.strip())
            target = urlsplit(str(asset_url))
            api_path = _base_url(settings.base_url)
            api_path = urlsplit(api_path).path.rstrip('/')
            if not api_path.endswith('/v1'):
                api_path += '/v1'
            media_path_re = re.compile(
                rf'^{re.escape(api_path)}/media/(?:images|videos)/[A-Za-z0-9_-]{{1,128}}$'
            )
            expected_origin = (configured.scheme.lower(), configured.hostname, configured.port or (443 if configured.scheme == 'https' else 80))
            target_origin = (target.scheme.lower(), target.hostname, target.port or (443 if target.scheme == 'https' else 80))
        except (ValueError, TypeError):
            raise ProviderError('invalid_media_url') from None
        if (
            target_origin != expected_origin
            or target.username or target.password or target.query or target.fragment
            or not media_path_re.fullmatch(target.path)
            or (range_header and not _MEDIA_RANGE_RE.fullmatch(range_header.strip()))
        ):
            raise ProviderError('invalid_media_url')
        headers = {
            'Accept': 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm',
            'Authorization': f'Bearer {settings.api_key}',
        }
        if range_header:
            headers['Range'] = range_header.strip()
        request = urllib.request.Request(str(asset_url), method='GET', headers=headers)
        try:
            if self._custom_opener:
                response = self.opener(request, timeout=REQUEST_TIMEOUT)
            else:
                redirect_handler = _ProviderMediaRedirectHandler(expected_origin, media_path_re)
                response = urllib.request.build_opener(redirect_handler).open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            content_range = exc.headers.get('Content-Range') if exc.headers else None
            exc.close()
            if exc.code == 416:
                if not isinstance(content_range, str) or not _UNSATISFIED_CONTENT_RANGE_RE.fullmatch(content_range.strip()):
                    content_range = None
                else:
                    content_range = content_range.strip()
                raise ProviderError(
                    'range_not_satisfiable', status=416, content_range=content_range,
                ) from None
            raise _error_for_status(exc.code, exc.headers.get('Retry-After')) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ProviderError('timeout') from None
        status = int(getattr(response, 'status', 200))
        if status not in (200, 206):
            response_headers = response.headers
            response.close()
            if status == 416:
                content_range = response_headers.get('Content-Range')
                if not isinstance(content_range, str) or not _UNSATISFIED_CONTENT_RANGE_RE.fullmatch(content_range.strip()):
                    content_range = None
                else:
                    content_range = content_range.strip()
                raise ProviderError(
                    'range_not_satisfiable', status=416, content_range=content_range,
                )
            raise _error_for_status(status, response.headers.get('Retry-After'))
        response_headers = response.headers
        content_type = response_headers.get_content_type().lower()
        if content_type not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'video/mp4', 'video/webm'}:
            response.close()
            raise ProviderError('invalid_media_type')
        content_length = response_headers.get('Content-Length')
        try:
            if content_length is not None and (int(content_length) < 0 or int(content_length) > MAX_MEDIA_BYTES):
                response.close()
                raise ProviderError('media_too_large')
        except ValueError:
            response.close()
            raise ProviderError('invalid_provider_response') from None
        return ProviderMedia(
            response=response,
            status_code=status,
            content_type=content_type,
            content_length=content_length,
            content_range=response_headers.get('Content-Range'),
        )

    def generate_image(self, request: ImageRequest, settings: VideoSettings) -> ProviderJob:
        payload = {'model': request.model, 'prompt': request.prompt}
        if request.width is not None:
            payload['width'] = request.width
        if request.height is not None:
            payload['height'] = request.height
        if request.aspect_ratio:
            payload['aspect_ratio'] = request.aspect_ratio
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
