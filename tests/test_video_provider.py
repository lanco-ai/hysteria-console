import json
import re
import urllib.error
import urllib.request

import pytest

from web_api.video_models import ImageRequest, VideoRequest
from web_api.video_provider import GrokVideoProvider, ProviderError, VideoSettings, _ProviderMediaRedirectHandler


class _Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return json.dumps(self._payload).encode()


def _opener(response, seen):
    def open(request, timeout):
        seen['url'] = request.full_url
        seen['method'] = request.method
        seen['authorization'] = request.get_header('Authorization')
        seen['body'] = json.loads(request.data.decode()) if request.data else None
        seen['timeout'] = timeout
        return response

    return open


def test_video_provider_builds_image_request_without_exposing_key():
    seen = {}
    provider = GrokVideoProvider(opener=_opener(_Response({'request_id': 'asset-1'}), seen))
    job = provider.generate_image(
        ImageRequest(prompt='blue circle', model='grok-imagine-image'),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['url'] == 'https://provider.test/v1/images/generations'
    assert seen['method'] == 'POST'
    assert seen['authorization'] == 'Bearer secret'
    assert seen['body'] == {'model': 'grok-imagine-image', 'prompt': 'blue circle'}
    assert job.provider_job_id == 'asset-1'


def test_video_provider_passes_storyboard_dimensions_to_upstream():
    seen = {}
    provider = GrokVideoProvider(opener=_opener(_Response({'request_id': 'asset-1'}), seen))
    provider.generate_image(
        ImageRequest(prompt='portrait', model='grok-imagine-image', aspect_ratio='9:16'),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['body'] == {
        'model': 'grok-imagine-image',
        'prompt': 'portrait',
        'aspect_ratio': '9:16',
    }


def test_video_provider_maps_quota_error_without_upstream_body():
    def opener(_request, **_kwargs):
        raise urllib.error.HTTPError(
            'https://provider.test/v1/videos/generations',
            429,
            'secret body should never escape',
            {'Retry-After': '9'},
            None,
        )

    provider = GrokVideoProvider(opener=opener)
    with pytest.raises(ProviderError) as error:
        provider.generate_video(
            VideoRequest(prompt='test', model='grok-imagine-video'),
            VideoSettings('https://provider.test/v1', 'secret'),
        )
    assert error.value.code == 'rate_limited'
    assert error.value.status == 429
    assert error.value.retry_after == '9'
    assert 'secret body' not in str(error.value)


def test_capabilities_hide_unverified_first_last_frame_and_merge():
    provider = GrokVideoProvider(
        opener=_opener(
            _Response({'data': [{'id': 'grok-imagine-image'}, {'id': 'grok-imagine-video'}]}),
            {},
        )
    )
    result = provider.capabilities(VideoSettings('https://provider.test/v1', 'secret'))
    assert result.image_models == ['grok-imagine-image']
    assert result.video_models == ['grok-imagine-video']
    assert result.first_last_frame.supported is False
    assert result.video_composition.supported is False


def test_video_provider_polls_and_normalizes_done_status():
    seen = {}
    provider = GrokVideoProvider(
        opener=_opener(_Response({'id': 'job-1', 'status': 'done', 'video_url': 'https://cdn.test/v.mp4'}), seen)
    )
    result = provider.get_job('job-1', VideoSettings('https://provider.test/v1', 'secret'))
    assert seen['url'] == 'https://provider.test/v1/videos/job-1'
    assert result.state == 'succeeded'
    assert result.asset_url == 'https://cdn.test/v.mp4'


def test_video_provider_uses_current_media_object_for_image_to_video():
    seen = {}
    provider = GrokVideoProvider(opener=_opener(_Response({'request_id': 'job-2'}), seen))
    provider.generate_video(
        VideoRequest(prompt='camera moves', model='grok-imagine-video', image_url='https://cdn.test/frame.png'),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['body'] == {
        'model': 'grok-imagine-video',
        'prompt': 'camera moves',
        'image': {'url': 'https://cdn.test/frame.png'},
    }


def test_video_provider_passes_video_duration_and_ratio():
    seen = {}
    provider = GrokVideoProvider(opener=_opener(_Response({'request_id': 'job-2'}), seen))
    provider.generate_video(
        VideoRequest(
            prompt='camera moves', model='grok-imagine-video',
            image_url='https://cdn.test/frame.png', duration=7, aspect_ratio='16:9',
        ),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['body'] == {
        'model': 'grok-imagine-video',
        'prompt': 'camera moves',
        'image': {'url': 'https://cdn.test/frame.png'},
        'duration': 7,
        'aspect_ratio': '16:9',
    }


def test_video_provider_accepts_openai_image_response_without_request_id():
    provider = GrokVideoProvider(opener=_opener(_Response({'created': 1, 'data': [{'url': 'https://cdn.test/image.png'}]}), {}))
    result = provider.generate_image(
        ImageRequest(prompt='blue circle', model='grok-imagine-image'),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert result.state == 'succeeded'
    assert result.provider_job_id == ''
    assert result.asset_url == 'https://cdn.test/image.png'


def test_video_provider_does_not_claim_unverified_first_last_frame_support():
    provider = GrokVideoProvider(opener=lambda *_args, **_kwargs: None)
    with pytest.raises(ProviderError) as error:
        provider.generate_video(
            VideoRequest(prompt='transition', model='grok-imagine-video', first_frame_url='https://cdn.test/a.png', last_frame_url='https://cdn.test/b.png'),
            VideoSettings('https://provider.test/v1', 'secret'),
        )
    assert error.value.code == 'first_last_frame_unsupported'


def test_video_provider_cancel_is_explicitly_unsupported():
    provider = GrokVideoProvider(opener=lambda *_args, **_kwargs: None)
    result = provider.cancel_job('job-1', VideoSettings('https://provider.test/v1', 'secret'))
    assert result.status == 'unsupported'


def test_provider_media_proxy_only_fetches_same_origin_archived_media():
    seen = {}

    class Headers(dict):
        def get_content_type(self):
            return self.get('Content-Type', '').split(';', 1)[0]

    class Response:
        status = 206
        headers = Headers({'Content-Type': 'video/mp4', 'Content-Length': '4', 'Content-Range': 'bytes 0-3/4'})

        def __init__(self):
            self.chunks = [b'mp4!', b'']
            self.closed = False

        def read(self, _size):
            return self.chunks.pop(0)

        def close(self):
            self.closed = True

    response = Response()

    def opener(request, timeout):
        seen['url'] = request.full_url
        seen['range'] = request.get_header('Range')
        seen['authorization'] = request.get_header('Authorization')
        return response

    provider = GrokVideoProvider(opener=opener)
    media = provider.open_asset(
        'http://provider.test/v1/media/videos/asset_123',
        VideoSettings('http://provider.test/v1', 'secret'),
        range_header='bytes=0-3',
    )
    assert seen == {
        'url': 'http://provider.test/v1/media/videos/asset_123',
        'range': 'bytes=0-3', 'authorization': 'Bearer secret',
    }
    assert media.status_code == 206
    assert media.content_type == 'video/mp4'
    assert media.content_range == 'bytes 0-3/4'
    assert b''.join(media.iter_bytes()) == b'mp4!'
    assert response.closed is True


def test_provider_media_proxy_rejects_untrusted_asset_origins_before_fetch():
    def unexpected_opener(*_args, **_kwargs):
        raise AssertionError('must not fetch a different origin')

    provider = GrokVideoProvider(opener=unexpected_opener)
    with pytest.raises(ProviderError) as error:
        provider.open_asset(
            'https://untrusted.test/secret.mp4',
            VideoSettings('http://provider.test/v1', 'secret'),
        )
    assert error.value.code == 'invalid_media_url'


def test_provider_media_proxy_respects_configured_api_path_prefix():
    class Headers(dict):
        def get_content_type(self):
            return 'image/png'

    class Response:
        status = 200
        headers = Headers({'Content-Type': 'image/png', 'Content-Length': '1'})

        def read(self, _size):
            return b''

        def close(self):
            return None

    provider = GrokVideoProvider(opener=lambda *_args, **_kwargs: Response())
    media = provider.open_asset(
        'https://provider.test/api/v1/media/images/asset_123',
        VideoSettings('https://provider.test/api/v1', 'secret'),
    )
    assert media.content_type == 'image/png'


def test_provider_media_proxy_never_redirects_authorized_request_to_other_origin():
    handler = _ProviderMediaRedirectHandler(
        ('https', 'provider.test', 443),
        re.compile(r'^/v1/media/videos/[A-Za-z0-9_-]{1,128}$'),
    )
    request = urllib.request.Request(
        'https://provider.test/v1/media/videos/asset_123',
        headers={'Authorization': 'Bearer secret'},
    )
    assert handler.redirect_request(
        request, None, 302, 'Found', {}, 'https://attacker.test/collect/asset_123',
    ) is None
    assert handler.redirect_request(
        request, None, 302, 'Found', {}, 'https://provider.test/v1/media/videos/asset_123',
    ) is not None
