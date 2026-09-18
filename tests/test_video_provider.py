import json
import urllib.error

import pytest

from web_api.video_models import ImageRequest, VideoRequest
from web_api.video_provider import GrokVideoProvider, ProviderError, VideoSettings


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


def test_video_provider_cancel_is_explicitly_unsupported():
    provider = GrokVideoProvider(opener=lambda *_args, **_kwargs: None)
    result = provider.cancel_job('job-1', VideoSettings('https://provider.test/v1', 'secret'))
    assert result.status == 'unsupported'
