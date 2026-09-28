import io
import pytest

from web_api.video_models import ProviderJob, ProviderJobStatus, VideoSettings
from web_api.video_provider import ProviderError, ProviderMedia
from web_api.video_service import AssetStore, RunService, VideoValidationError, WorkflowStore
import web_api.video_service as video_service


class FakeProvider:
    def __init__(self):
        self.image_calls = 0
        self.video_calls = 0
        self.image_requests = []
        self.video_requests = []
        self.create_then_timeout = False
        self.image_done = False
        self.job_poll_calls = 0

    def generate_image(self, request, settings):
        del settings
        self.image_requests.append(request)
        self.image_calls += 1
        if self.create_then_timeout:
            raise TimeoutError('transport timeout')
        return ProviderJob('image-job')

    def generate_video(self, request, settings):
        del settings
        self.video_requests.append(request)
        self.video_calls += 1
        return ProviderJob('video-job')

    def get_job(self, job_id, settings):
        del settings
        self.job_poll_calls += 1
        if job_id == 'image-job' and not self.image_done:
            return ProviderJobStatus(job_id, 'running')
        return ProviderJobStatus(job_id, 'succeeded', asset_url=f'https://cdn.test/{job_id}.mp4')

    def cancel_job(self, job_id, settings):
        del job_id, settings
        return type('Cancel', (), {'status': 'unsupported'})()


class ImmediateImageProvider(FakeProvider):
    def generate_image(self, request, settings):
        del request, settings
        self.image_calls += 1
        return ProviderJob('', state='succeeded', asset_url='https://cdn.test/immediate.png')


class ProviderErrorOnImage(FakeProvider):
    def generate_image(self, request, settings):
        del request, settings
        raise ProviderError('rate_limited', status=429, retry_after='8')


class CandidateProvider(FakeProvider):
    def __init__(self, count=3):
        super().__init__()
        self.image_urls = [f'https://provider.test/v1/media/images/candidate-{index}' for index in range(count)]

    def generate_image(self, request, settings):
        del settings
        self.image_requests.append(request)
        self.image_calls += 1
        return ProviderJob('', state='succeeded', asset_urls=self.image_urls)

    def open_asset(self, asset_url, settings, *, range_header=None):
        del asset_url, settings, range_header
        return ProviderMedia(io.BytesIO(b'png'), 200, 'image/png', '3', None)


def test_image_target_archives_all_candidates_and_excludes_sibling(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'batch', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image-model', 'n': 3}},
        {'id': 'sibling', 'type': 'image_asset', 'data': {'asset_ref': 'asset://other'}},
    ], 'edges': [{'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'}]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'))
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'succeeded'
    assert run['order'] == ['prompt', 'image']
    assert provider.image_requests[0].n == 3
    assert len(run['assets']['image']) == 3
    assert all(ref.startswith('asset://') for ref in run['assets']['image'])


def test_incomplete_draft_saves_and_only_selected_run_branch_must_be_complete(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'draft', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image-model'}},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
    ]})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'),
                         FakeProvider(), tmp_path / 'runs.json')

    image_run = service.submit(saved['id'], target_node_id='image')

    assert image_run['order'] == ['prompt', 'image']
    assert [node['id'] for node in saved['nodes']] == ['prompt', 'image', 'video']
    with pytest.raises(VideoValidationError, match='missing required input: (first_frame|last_frame)'):
        service.submit(saved['id'], target_node_id='video')


@pytest.mark.parametrize('source_data', [
    {'text': ''},
    {'text': '', 'prompt': 'stale source field'},
])
def test_connected_empty_prompt_is_rejected_before_run_is_queued(tmp_path, source_data):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'empty prompt', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': source_data},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image-model', 'prompt': 'stale local prompt'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
    ]})
    provider = FakeProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'),
                         provider, tmp_path / 'runs.json')

    with pytest.raises(VideoValidationError, match='missing required input: prompt'):
        service.submit(saved['id'], target_node_id='image')

    assert service.list() == []
    assert provider.image_calls == 0


def test_tick_does_not_use_stale_local_prompt_when_connected_source_is_empty(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'stale prompt', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'initial source'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image-model', 'prompt': 'stale local prompt'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
    ]})
    provider = FakeProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'),
                         provider, tmp_path / 'runs.json')
    run = service.submit(saved['id'], target_node_id='image')
    run['workflow']['nodes'][0]['data']['text'] = ''
    run['workflow']['nodes'][0]['data']['prompt'] = 'stale source field'
    service._replace(run)

    result = service.tick(run['id'])

    assert result['state'] == 'failed'
    assert result['error'] == 'missing_prompt'
    assert provider.image_calls == 0


def test_video_target_reuses_selected_candidates_without_image_generation(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    first = assets.save_upload('first.png', 'image/png', b'first')
    last = assets.save_upload('last.png', 'image/png', b'last')
    first_ref, last_ref = f"asset://{first['id']}", f"asset://{last['id']}"
    saved = workflows.save({'title': 'reuse', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image-model', 'candidate_asset_refs': [first_ref, last_ref], 'selected_first_asset_ref': first_ref, 'selected_last_asset_ref': last_ref}},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model', 'prompt': 'camera moves'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    assert provider.image_calls == 0
    assert provider.video_calls == 1
    assert provider.video_requests[0].first_frame_url.startswith('data:image/png;base64,')
    assert provider.video_requests[0].last_frame_url.startswith('data:image/png;base64,')
    assert run['assets']['image'] == [first_ref, last_ref]


@pytest.mark.parametrize('connected', [True, False])
def test_video_target_reuses_cached_images_without_an_image_prompt(tmp_path, connected):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    first = assets.save_upload('first.png', 'image/png', b'first')
    last = assets.save_upload('last.png', 'image/png', b'last')
    first_ref, last_ref = f"asset://{first['id']}", f"asset://{last['id']}"
    nodes = [
        {'id': 'image', 'type': 'text_to_image', 'data': {
            'model': 'image-model', 'prompt': '', 'candidate_asset_refs': [first_ref, last_ref],
            'selected_first_asset_ref': first_ref, 'selected_last_asset_ref': last_ref,
        }},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ]
    edges = [
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]
    if connected:
        nodes.insert(0, {'id': 'prompt', 'type': 'prompt', 'data': {'text': '', 'prompt': 'stale source field'}})
        edges.insert(0, {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'})
    saved = workflows.save({'nodes': nodes, 'edges': edges})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=assets)

    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    run = service.tick(run['id'])

    assert run['state'] == 'succeeded'
    assert run['assets']['image'] == [first_ref, last_ref]
    assert provider.image_calls == 0
    assert provider.video_calls == 1
    with pytest.raises(VideoValidationError, match='missing required input: prompt'):
        service.submit(saved['id'], target_node_id='image')
    assert provider.image_calls == 0


def test_invalid_cached_images_do_not_exempt_missing_prompt(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {
            'prompt': '', 'candidate_asset_refs': ['https://untrusted.test/image.png'],
        }},
        {'id': 'video', 'type': 'image_to_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'image', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'))

    with pytest.raises(VideoValidationError, match='missing required input: prompt'):
        service.submit(saved['id'], target_node_id='video')

    assert service.list() == []
    assert provider.image_calls == provider.video_calls == 0


def test_video_target_rejects_missing_frame_selection_before_paid_call(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'missing', 'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'candidate_asset_refs': ['asset://one'], 'selected_first_asset_ref': 'asset://one'}},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'))
    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'missing_frame_selection'
    assert provider.image_calls == provider.video_calls == 0


def test_unknown_target_is_rejected(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [{'id': 'prompt', 'type': 'prompt', 'data': {'text': 'x'}}], 'edges': []})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), CandidateProvider(), tmp_path / 'runs.json')
    with pytest.raises(VideoValidationError, match='target node not found'):
        service.submit(saved['id'], target_node_id='absent')


def test_batch_without_asset_store_fails_instead_of_persisting_external_urls(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [{'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'n': 3}}], 'edges': []})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), CandidateProvider(), tmp_path / 'runs.json')
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'asset_archive_failed'
    assert 'image' not in run['assets']


def test_candidate_archive_reports_full_storage_without_raw_provider_url(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [{'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'n': 3}}], 'edges': []})
    assets = AssetStore(tmp_path / 'assets', max_total_bytes=2)
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), CandidateProvider(),
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'asset_storage_full'
    assert 'provider.test' not in str(run)


def test_candidate_archive_rejects_provider_media_outside_same_origin_boundary(tmp_path):
    from web_api.video_provider import GrokVideoProvider

    class UntrustedProvider(CandidateProvider):
        def open_asset(self, asset_url, settings, *, range_header=None):
            provider = GrokVideoProvider(opener=lambda *_args, **_kwargs: pytest.fail('network fetch'))
            return provider.open_asset(
                asset_url, settings, range_header=range_header)

    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [{'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'n': 3}}], 'edges': []})
    provider = UntrustedProvider()
    provider.image_urls = ['https://elsewhere.test/v1/media/images/one']
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'))
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'invalid_media_url'
    assert 'image' not in run['assets']


def test_invalid_cached_candidate_branch_never_submits_video(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    first = assets.save_upload('first.png', 'image/png', b'first')
    first_ref = f"asset://{first['id']}"
    saved = workflows.save({'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {
            'prompt': 'forest', 'candidate_asset_refs': [first_ref, 'https://untrusted.test/last'],
            'selected_first_asset_ref': first_ref, 'selected_last_asset_ref': 'https://untrusted.test/last',
        }},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    assert run['state'] == 'failed'
    assert provider.video_calls == 0


def test_missing_selected_candidate_asset_blocks_video_submission(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    first = assets.save_upload('first.png', 'image/png', b'first')
    first_ref = f"asset://{first['id']}"
    missing_ref = 'asset://missing123'
    saved = workflows.save({'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {
            'prompt': 'forest', 'candidate_asset_refs': [first_ref, missing_ref],
            'selected_first_asset_ref': first_ref, 'selected_last_asset_ref': missing_ref,
        }},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'input_asset_unavailable'
    assert provider.video_calls == 0


def test_same_candidate_cannot_fill_both_video_frame_roles(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    asset = assets.save_upload('frame.png', 'image/png', b'frame')
    ref = f"asset://{asset['id']}"
    saved = workflows.save({'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {
            'prompt': 'forest', 'candidate_asset_refs': [ref],
            'selected_first_asset_ref': ref, 'selected_last_asset_ref': ref,
        }},
        {'id': 'video', 'type': 'first_last_frame_video', 'data': {'model': 'video-model'}},
    ], 'edges': [
        {'source': 'image', 'sourceHandle': 'first_frame', 'target': 'video', 'targetHandle': 'first_frame'},
        {'source': 'image', 'sourceHandle': 'last_frame', 'target': 'video', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='video')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'identical_frame_selection'
    assert provider.video_calls == 0


def test_oversized_provider_candidate_list_is_not_archived(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'nodes': [{'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'n': 4}}], 'edges': []})
    assets = AssetStore(tmp_path / 'assets')
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), CandidateProvider(count=11),
                         tmp_path / 'runs.json', asset_store=assets)
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'failed'
    assert run['error'] == 'invalid_provider_response'
    assert assets._metadata() == []


def workflow_store(tmp_path):
    store = WorkflowStore(tmp_path / 'workflows.json')
    return store


def test_run_waits_for_image_before_submitting_video(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'demo', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
        {'id': 'video', 'type': 'image_to_video', 'data': {'model': 'video'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
        {'source': 'image', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
    ]})
    provider = FakeProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider, tmp_path / 'runs.json')
    run = service.submit(saved['id'])
    service.tick(run['id'])
    assert provider.image_calls == 1
    assert provider.video_calls == 0
    provider.image_done = True
    service.tick(run['id'])
    assert provider.video_calls == 1


def test_run_marks_immediate_image_response_succeeded(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'demo', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
    ]})
    provider = ImmediateImageProvider()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider, tmp_path / 'runs.json')
    run = service.submit(saved['id'])
    result = service.tick(run['id'])
    assert result['state'] == 'succeeded'
    assert result['node_status']['image']['state'] == 'succeeded'
    assert result['assets']['image'] == 'https://cdn.test/immediate.png'


def test_submit_timeout_does_not_duplicate_paid_request(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'demo', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
    ], 'edges': [{'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'}]})
    provider = FakeProvider()
    provider.create_then_timeout = True
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider, tmp_path / 'runs.json')
    run = service.submit(saved['id'])
    service.tick(run['id'])
    service.tick(run['id'])
    assert provider.image_calls == 1
    assert service.get(run['id'])['node_status']['image']['state'] == 'running'


def test_run_expires_after_bounded_total_duration_without_another_provider_poll(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'bounded', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
    ], 'edges': [
        {'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'},
    ]})
    provider = FakeProvider()
    service = RunService(
        workflows, VideoSettings('https://provider.test/v1', 'secret'),
        provider, tmp_path / 'runs.json',
    )
    run = service.submit(saved['id'])
    run = service.tick(run['id'])
    assert provider.job_poll_calls == 0
    run['created_at'] -= 24 * 60 * 60
    service._replace(run)

    result = service.tick(run['id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'run_timeout'
    assert provider.job_poll_calls == 0


def test_run_update_reads_latest_records_inside_the_file_lock(tmp_path, monkeypatch):
    path = tmp_path / 'runs.json'
    existing = {'id': 'existing-run', 'state': 'queued'}
    concurrent = {'id': 'concurrent-run', 'state': 'queued'}
    video_service.state_store.save_json(path, [existing])

    class ConcurrentWriter:
        def __enter__(self):
            video_service.state_store.save_json(path, [existing, concurrent])
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(video_service.state_store, 'file_lock', lambda _path: ConcurrentWriter())
    service = RunService(
        workflow_store(tmp_path), VideoSettings('https://provider.test/v1', 'secret'),
        FakeProvider(), path,
    )

    service._replace({'id': 'new-run', 'state': 'queued'})

    assert {record['id'] for record in service._records()} == {
        'existing-run', 'concurrent-run', 'new-run',
    }


def test_classified_provider_error_fails_run_without_retrying(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'demo', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
    ], 'edges': [{'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'}]})
    provider = ProviderErrorOnImage()
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), provider, tmp_path / 'runs.json')
    run = service.submit(saved['id'])
    service.tick(run['id'])
    result = service.get(run['id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'rate_limited'
    assert result['node_status']['image']['state'] == 'failed'


def test_cancel_reports_unsupported_instead_of_faking_success(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'demo', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'a lake'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {'model': 'image'}},
    ], 'edges': [{'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'}]})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), FakeProvider(), tmp_path / 'runs.json')
    run = service.submit(saved['id'])
    result = service.cancel(run['id'])
    assert result['state'] == 'cancel_unsupported'


def test_submit_can_limit_run_to_one_storyboard_shot(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'story', 'storyboard': {'shots': [
        {'id': 'shot-1', 'title': '一', 'image_prompt': 'lake'},
        {'id': 'shot-2', 'title': '二', 'image_prompt': 'forest'},
    ]}, 'nodes': [
        {'id': 'prompt-1', 'type': 'prompt', 'data': {'text': 'lake', 'shot_id': 'shot-1'}},
        {'id': 'image-1', 'type': 'text_to_image', 'data': {'model': 'image', 'shot_id': 'shot-1'}},
        {'id': 'prompt-2', 'type': 'prompt', 'data': {'text': 'forest', 'shot_id': 'shot-2'}},
        {'id': 'image-2', 'type': 'text_to_image', 'data': {'model': 'image', 'shot_id': 'shot-2'}},
    ], 'edges': [
        {'source': 'prompt-1', 'sourceHandle': 'text', 'target': 'image-1', 'targetHandle': 'prompt'},
        {'source': 'prompt-2', 'sourceHandle': 'text', 'target': 'image-2', 'targetHandle': 'prompt'},
    ]})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), FakeProvider(), tmp_path / 'runs.json')
    run = service.submit(saved['id'], shot_id='shot-2')
    assert {node['id'] for node in run['workflow']['nodes']} == {'prompt-2', 'image-2'}


def test_uploaded_image_is_materialized_for_provider_and_video_parameters_are_kept(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    asset = assets.save_upload('frame.png', 'image/png', b'\x89PNG\r\n')
    saved = workflows.save({'title': 'uploaded', 'nodes': [
        {'id': 'source', 'type': 'image_asset', 'data': {'asset_ref': f"asset://{asset['id']}"}},
        {'id': 'video', 'type': 'image_to_video', 'data': {'model': 'video', 'duration': 7, 'aspect_ratio': '9:16'}},
    ], 'edges': [
        {'source': 'source', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
    ]})
    provider = FakeProvider()
    service = RunService(
        workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
        tmp_path / 'runs.json', asset_store=assets,
    )
    run = service.submit(saved['id'])
    result = service.tick(run['id'])
    assert provider.video_calls == 1
    request = provider.video_requests[0]
    assert request.image_url.startswith('data:image/png;base64,')
    assert request.duration == 7
    assert request.aspect_ratio == '9:16'
    assert result['assets']['source'] == f"asset://{asset['id']}"


def test_uploaded_image_over_provider_limit_fails_without_request(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    asset = assets.save_upload('frame.png', 'image/png', b'X' * (8 * 1024 * 1024 + 1))
    saved = workflows.save({'title': 'too large', 'nodes': [
        {'id': 'source', 'type': 'image_asset', 'data': {'asset_ref': f"asset://{asset['id']}"}},
        {'id': 'video', 'type': 'image_to_video', 'data': {'model': 'video'}},
    ], 'edges': [
        {'source': 'source', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
    ]})
    provider = FakeProvider()
    service = RunService(
        workflows, VideoSettings('https://provider.test/v1', 'secret'), provider,
        tmp_path / 'runs.json', asset_store=assets,
    )
    result = service.tick(service.submit(saved['id'])['id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'input_asset_unavailable'
    assert provider.video_calls == 0


def test_empty_image_asset_fails_instead_of_staying_running(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'missing asset', 'nodes': [
        {'id': 'source', 'type': 'image_asset', 'data': {'label': '首帧'}},
        {'id': 'video', 'type': 'image_to_video', 'data': {'model': 'video'}},
    ], 'edges': [
        {'source': 'source', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
    ]})
    service = RunService(
        workflows, VideoSettings('https://provider.test/v1', 'secret'), FakeProvider(),
        tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'),
    )
    result = service.tick(service.submit(saved['id'])['id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'missing_asset'


def durable_candidate_service(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'original', 'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest', 'n': 4}},
        {'id': 'other', 'type': 'prompt', 'data': {'text': 'keep me'}},
    ], 'edges': []})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), CandidateProvider(),
                         tmp_path / 'runs.json', asset_store=AssetStore(tmp_path / 'assets'))
    return workflows, saved, service


def test_candidate_completion_is_durable_without_browser(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    submitted = service.submit(saved['id'], target_node_id='image')
    queued = workflows.get(saved['id'])['nodes'][0]['data']
    assert queued.get('candidate_run_id') == submitted['id']
    run = service.tick(submitted['id'])
    restored = WorkflowStore(workflows.path).get(saved['id'])['nodes'][0]['data']
    assert restored.get('candidate_asset_refs') == run['assets']['image']
    assert restored['candidate_batch_id'] == run['id']
    assert restored['candidate_run_state'] == 'succeeded'


def test_candidate_completion_preserves_edits_and_rejects_stale_autosave(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    run = service.submit(saved['id'], target_node_id='image')
    saved['title'] = 'edited during run'
    saved['nodes'][1]['data']['text'] = 'concurrent edit'
    workflows.save(saved)
    result = service.tick(run['id'])
    saved['nodes'][0]['position'] = {'x': 42, 'y': 31}
    restored = workflows.save(saved)
    assert restored['title'] == 'edited during run'
    assert restored['nodes'][1]['data']['text'] == 'concurrent edit'
    assert restored['nodes'][0]['position'] == {'x': 42, 'y': 31}
    assert restored['nodes'][0]['data'].get('candidate_asset_refs') == result['assets']['image']
    restored['nodes'][0]['data']['selected_first_asset_ref'] = result['assets']['image'][0]
    restored['nodes'][0]['data']['selected_last_asset_ref'] = result['assets']['image'][1]
    workflows.save(restored)
    service.tick(run['id'])
    again = workflows.save(saved)['nodes'][0]['data']
    assert again['selected_first_asset_ref'] == result['assets']['image'][0]
    assert again['selected_last_asset_ref'] == result['assets']['image'][1]


def test_older_candidate_run_cannot_replace_newer_batch_or_selections(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    older = service.submit(saved['id'], target_node_id='image')
    newer = service.submit(saved['id'], target_node_id='image')
    result = service.tick(newer['id'])
    current = workflows.get(saved['id'])
    current['nodes'][0]['data']['selected_first_asset_ref'] = result['assets']['image'][0]
    current['nodes'][0]['data']['selected_last_asset_ref'] = result['assets']['image'][1]
    workflows.save(current)
    service.tick(older['id'])
    restored = workflows.get(saved['id'])['nodes'][0]['data']
    assert restored.get('candidate_asset_refs') == result['assets']['image']
    assert restored['selected_last_asset_ref'] == result['assets']['image'][1]


def test_candidate_completion_does_not_recreate_deleted_node_or_workflow(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    run = service.submit(saved['id'], target_node_id='image')
    saved['nodes'] = saved['nodes'][1:]
    workflows.save(saved)
    service.tick(run['id'])
    assert [node['id'] for node in workflows.get(saved['id'])['nodes']] == ['other']
    saved['nodes'].append({'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'new', 'n': 4}})
    workflows.save(saved)
    service.tick(run['id'])
    assert not workflows.get(saved['id'])['nodes'][1]['data'].get('candidate_asset_refs')
    newer = service.submit(saved['id'], target_node_id='image')
    workflows.delete(saved['id'])
    service.tick(newer['id'])
    assert workflows.get(saved['id']) is None


def test_running_candidate_recovers_with_new_service_and_workflow_store(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    class DelayedCandidates(CandidateProvider):
        def generate_image(self, request, settings):
            return ProviderJob('pending-image', state='running')

        def get_job(self, job_id, settings):
            return ProviderJobStatus('pending-image', state='succeeded', asset_url=self.image_urls[0])

    service.provider = DelayedCandidates()
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    assert run['state'] == 'running'
    restored = WorkflowStore(workflows.path)
    assert restored.get(saved['id'])['nodes'][0]['data']['candidate_run_state'] == 'running'
    resumed = RunService(restored, service.settings, service.provider, service.path, asset_store=service.asset_store)
    result = resumed.tick(run['id'])
    assert result['state'] == 'succeeded'
    assert restored.get(saved['id'])['nodes'][0]['data']['candidate_asset_refs'] == result['assets']['image']


def test_worker_recovers_completion_interrupted_before_workflow_merge(tmp_path, monkeypatch):
    workflows, saved, service = durable_candidate_service(tmp_path)
    run = service.submit(saved['id'], target_node_id='image')
    original = workflows.reconcile_candidate_run
    def crash_after_run_write(run, **kwargs):
        if run['state'] == 'succeeded':
            raise OSError('simulated interruption')
        return original(run, **kwargs)
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', crash_after_run_write)
    with pytest.raises(OSError, match='simulated interruption'):
        service.tick(run['id'])
    assert service.get(run['id'])['state'] == 'succeeded'
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', original)
    service.resume_pending()
    assert workflows.get(saved['id'])['nodes'][0]['data'].get('candidate_asset_refs') == service.get(run['id'])['assets']['image']


def test_copying_candidate_node_does_not_copy_run_ownership(tmp_path):
    import copy
    workflows, saved, service = durable_candidate_service(tmp_path)
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    current = workflows.get(saved['id'])
    duplicate = copy.deepcopy(current['nodes'][0])
    duplicate['id'] = 'copied-image'
    current['nodes'].append(duplicate)
    copied = workflows.save(current)['nodes'][-1]['data']
    assert copied['candidate_asset_refs'] == run['assets']['image']
    assert 'candidate_run_id' not in copied
    assert 'candidate_batch_version' not in copied


def test_submit_interruption_preserves_run_for_claim_recovery(tmp_path, monkeypatch):
    workflows, saved, service = durable_candidate_service(tmp_path)
    original = workflows.reconcile_candidate_run
    def crash_claim(run, **kwargs):
        if kwargs.get('claim'):
            raise SystemExit('claim interrupted')
        return original(run, **kwargs)
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', crash_claim)
    with pytest.raises(SystemExit, match='claim interrupted'):
        service.submit(saved['id'], target_node_id='image')
    assert len(service.list()) == 1
    run = service.list()[0]
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', original)
    service.resume_pending()
    assert workflows.get(saved['id'])['nodes'][0]['data']['candidate_run_id'] == run['id']
    service.tick(run['id'])
    assert workflows.get(saved['id'])['nodes'][0]['data']['candidate_asset_refs'] == service.get(run['id'])['assets']['image']


def test_stale_same_batch_save_preserves_newer_frame_selection(tmp_path):
    import copy
    workflows, saved, service = durable_candidate_service(tmp_path)
    run = service.tick(service.submit(saved['id'], target_node_id='image')['id'])
    current = workflows.get(saved['id'])
    stale = copy.deepcopy(current)
    current['nodes'][0]['data']['selected_first_asset_ref'] = run['assets']['image'][0]
    current['nodes'][0]['data']['selected_last_asset_ref'] = run['assets']['image'][1]
    updated = workflows.save(current)
    stale['title'] = 'concurrent title edit'
    restored = workflows.save(stale)
    assert restored['title'] == 'concurrent title edit'
    assert restored['nodes'][0]['data']['selected_first_asset_ref'] == run['assets']['image'][0]
    assert restored['nodes'][0]['data']['selected_last_asset_ref'] == run['assets']['image'][1]
    updated['nodes'][0]['data']['selected_last_asset_ref'] = run['assets']['image'][2]
    final = workflows.save(updated)
    assert final['nodes'][0]['data']['selected_last_asset_ref'] == run['assets']['image'][2]


def test_worker_does_not_reclaim_deleted_and_recreated_node(tmp_path):
    import copy
    workflows, saved, service = durable_candidate_service(tmp_path)
    original_node = copy.deepcopy(saved['nodes'][0])
    run = service.submit(saved['id'], target_node_id='image')
    saved['nodes'] = saved['nodes'][1:]
    workflows.save(saved)
    saved['nodes'].insert(0, original_node)
    recreated = workflows.save(saved)
    assert recreated['nodes'][0]['data']['candidate_node_token'] != original_node['data']['candidate_node_token']
    service.resume_pending()
    service.tick(run['id'])
    service.resume_pending()
    assert 'candidate_asset_refs' not in workflows.get(saved['id'])['nodes'][0]['data']


def test_worker_does_not_reclaim_superseded_run_after_record_reordering(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    older = service.submit(saved['id'], target_node_id='image')
    newer = service.submit(saved['id'], target_node_id='image')
    result = service.tick(newer['id'])
    service.tick(older['id'])
    service.resume_pending()
    data = workflows.get(saved['id'])['nodes'][0]['data']
    assert data['candidate_run_id'] == newer['id']
    assert data['candidate_asset_refs'] == result['assets']['image']


def test_legacy_run_update_does_not_become_a_new_submission(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    older = service.submit(saved['id'], target_node_id='image')
    newer = service.submit(saved['id'], target_node_id='image')
    result = service.tick(newer['id'])
    records = service.list()
    next(item for item in records if item['id'] == older['id']).pop('submission_sequence')
    video_service.state_store.save_json(service.path, records)
    service.tick(older['id'])
    service.resume_pending()
    data = workflows.get(saved['id'])['nodes'][0]['data']
    assert data['candidate_run_id'] == newer['id']
    assert data['candidate_asset_refs'] == result['assets']['image']


def test_durable_submission_returns_receipt_when_workflow_claim_fails(tmp_path, monkeypatch):
    workflows, saved, service = durable_candidate_service(tmp_path)
    original = workflows.reconcile_candidate_run
    def broken_claim(*args, **kwargs):
        raise OSError('workflow write unavailable')
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', broken_claim)
    # Once the run is durable, the caller must receive its ID, not a failure
    # that invites retry while the worker still owns an executable paid task.
    accepted = service.submit(saved['id'], target_node_id='image')
    assert accepted['state'] == 'queued'
    assert service.get(accepted['id']) is not None
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', original)
    result = service.tick(accepted['id'])
    assert service.provider.image_calls == 1
    assert workflows.get(saved['id'])['nodes'][0]['data']['candidate_asset_refs'] == result['assets']['image']


def test_worker_never_submits_superseded_queued_candidate_run(tmp_path):
    workflows, saved, service = durable_candidate_service(tmp_path)
    older = service.submit(saved['id'], target_node_id='image')
    newer = service.submit(saved['id'], target_node_id='image')
    for pending in service.resume_pending():
        service.tick(pending['id'])
    assert service.provider.image_calls == 1
    assert service.get(older['id'])['state'] == 'cancelled'
    assert service.get(older['id'])['error'] == 'superseded_before_submission'
    assert service.get(newer['id'])['state'] == 'succeeded'


def durable_video_service(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'video draft', 'nodes': [
        {'id': 'input', 'type': 'image_asset', 'data': {'asset_url': 'https://cdn.test/input.png'}},
        {'id': 'video', 'type': 'image_to_video', 'data': {'prompt': 'move'}},
        {'id': 'preview', 'type': 'preview', 'data': {}},
    ], 'edges': [
        {'source': 'input', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'image'},
        {'source': 'video', 'sourceHandle': 'video', 'target': 'preview', 'targetHandle': 'media'},
    ]})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), FakeProvider(), tmp_path / 'runs.json')
    return workflows, saved, service


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('failure', ['crash', 'timeout', 'provider_timeout', 'invalid_response'])
def test_uncertain_submission_survives_restart_expiry_and_cancel(tmp_path, monkeypatch, kind, failure):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    calls = []
    def accepted_then_lost(*args):
        calls.append(True)
        if failure == 'crash':
            raise SystemExit('process lost after acceptance')
        if failure == 'timeout':
            raise TimeoutError('response lost')
        raise ProviderError('timeout' if failure == 'provider_timeout' else 'invalid_provider_response')
    monkeypatch.setattr(service.provider, f'generate_{kind}', accepted_then_lost)
    run = service.submit(saved['id'], target_node_id=kind)
    if failure == 'crash':
        with pytest.raises(SystemExit):
            service.tick(run['id'])
    else:
        service.tick(run['id'])
    resumed = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path, asset_store=service.asset_store)
    for pending in resumed.resume_pending():
        resumed.tick(pending['id'])
    current = resumed.get(run['id'])
    assert len(calls) == 1
    assert current['state'] == 'running'
    assert current['node_status'][kind]['submission_pending'] is True
    current['created_at'] -= 24 * 60 * 60
    resumed._replace(current)
    assert resumed.tick(run['id'])['state'] == 'running'
    cancelled = resumed.cancel(run['id'])
    assert cancelled['state'] == 'running'
    assert cancelled['error'] == 'submission_unknown'
    resumed.tick(run['id'])
    assert len(calls) == 1


def test_video_completion_survives_closed_browser_and_stale_autosave(tmp_path):
    workflows, saved, service = durable_video_service(tmp_path)
    run = service.submit(saved['id'], target_node_id='video')
    queued = workflows.get(saved['id'])['nodes'][1]['data']
    assert queued.get('video_run_id') == run['id']
    service.tick(run['id'])
    resumed = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path)
    done = resumed.tick(run['id'])
    saved['title'] = 'concurrent title'
    saved['nodes'][1]['position'] = {'x': 42, 'y': 31}
    restored = workflows.save(saved)
    assert restored['title'] == 'concurrent title'
    assert restored['nodes'][1]['position'] == {'x': 42, 'y': 31}
    assert done['assets']['video'] == 'https://cdn.test/video-job.mp4'
    assert restored['nodes'][1]['data'].get('video_url') == f'/api/video/runs/{run["id"]}/assets/video/content'
    assert restored['nodes'][1]['data']['video_run_state'] == 'succeeded'
    assert service.provider.video_calls == 1


def test_old_video_completion_cannot_replace_newer_run_or_recreated_node(tmp_path):
    import copy
    workflows, saved, service = durable_video_service(tmp_path)
    older = service.submit(saved['id'], target_node_id='video')
    service.tick(older['id'])
    older = service.tick(older['id'])
    newer = service.submit(saved['id'], target_node_id='video')
    assert newer['id'] != older['id']
    service.tick(newer['id'])
    service.tick(newer['id'])
    original_poll = service.provider.get_job
    service.provider.get_job = lambda job_id, settings: ProviderJobStatus(job_id, 'succeeded', asset_url='https://cdn.test/obsolete.mp4')
    older['assets']['video'] = 'https://cdn.test/obsolete.mp4'
    workflows.reconcile_video_run(older)
    current = workflows.get(saved['id'])
    assert current['nodes'][1]['data'].get('video_run_id') == newer['id']
    assert current['nodes'][1]['data']['video_url'] == f'/api/video/runs/{newer["id"]}/assets/video/content'
    original_node = copy.deepcopy(current['nodes'][1])
    current['nodes'] = [node for node in current['nodes'] if node['id'] != 'video']
    current['edges'] = []
    workflows.save(current)
    current['nodes'].append(original_node)
    recreated = workflows.save(current)
    assert recreated['nodes'][-1]['data']['video_node_token'] != original_node['data']['video_node_token']
    service.provider.get_job = original_poll
    service.resume_pending()
    service.tick(newer['id'])
    assert not workflows.get(saved['id'])['nodes'][-1]['data'].get('video_run_id')
    assert not workflows.get(saved['id'])['nodes'][-1]['data'].get('video_url')


def test_video_completion_recovers_interrupted_workflow_merge(tmp_path, monkeypatch):
    workflows, saved, service = durable_video_service(tmp_path)
    run = service.submit(saved['id'], target_node_id='video')
    service.tick(run['id'])
    original = workflows.reconcile_video_run
    def interrupted(run, **kwargs):
        if run['state'] == 'succeeded':
            raise SystemExit('completion merge lost')
        return original(run, **kwargs)
    monkeypatch.setattr(workflows, 'reconcile_video_run', interrupted)
    with pytest.raises(SystemExit):
        service.tick(run['id'])
    resumed = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path)
    resumed.resume_pending()
    assert resumed.workflows.get(saved['id'])['nodes'][1]['data']['video_url'] == f'/api/video/runs/{run["id"]}/assets/video/content'
    assert service.provider.video_calls == 1


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_process_exit_after_provider_acceptance_never_replays_submission(tmp_path, kind):
    import multiprocessing
    import os
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    run = service.submit(saved['id'], target_node_id=kind)
    receipt = tmp_path / 'provider-accepted'
    def accepted_then_exit(*args):
        with receipt.open('ab') as output:
            output.write(b'accepted\n')
            output.flush()
            os.fsync(output.fileno())
        os._exit(73)
    setattr(service.provider, f'generate_{kind}', accepted_then_exit)
    worker = multiprocessing.get_context('fork').Process(target=service.tick, args=(run['id'],))
    worker.start()
    worker.join(5)
    assert worker.exitcode == 73
    assert receipt.read_bytes() == b'accepted\n'
    setattr(service.provider, f'generate_{kind}', lambda *_args: pytest.fail('paid request replayed'))
    resumed = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path, asset_store=service.asset_store)
    for pending in resumed.resume_pending():
        result = resumed.tick(pending['id'])
        assert result['node_status'][kind]['submission_pending']
    assert receipt.read_bytes() == b'accepted\n'


def test_legacy_video_draft_gets_incarnation_before_submission_without_losing_edits(tmp_path):
    workflows, saved, service = durable_video_service(tmp_path)
    saved['nodes'][1]['data'].pop('video_node_token', None)
    video_service.state_store.save_json(workflows.path, [saved])
    run = service.submit(saved['id'], target_node_id='video')
    assert run['workflow']['nodes'][1]['data'].get('video_node_token')
    service.tick(run['id'])
    saved['title'] = 'legacy title edit'
    workflows.save(saved)
    done = service.tick(run['id'])
    assert done['state'] == 'succeeded'
    current = workflows.get(saved['id'])
    assert current['title'] == 'legacy title edit'
    assert current['nodes'][1]['data']['video_url'] == f'/api/video/runs/{run["id"]}/assets/video/content'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_submission_durability_failure_prevents_provider_call(tmp_path, monkeypatch, kind):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    run = service.submit(saved['id'], target_node_id=kind)
    def unavailable(_records):
        raise OSError('durable write unavailable')
    monkeypatch.setattr(service, '_write', unavailable)
    with pytest.raises(OSError):
        service.tick(run['id'])
    assert service.provider.image_calls == service.provider.video_calls == 0
    assert service.get(run['id'])['state'] == 'queued'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_simultaneous_ticks_claim_paid_submission_once(tmp_path, monkeypatch, kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    other = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path, asset_store=service.asset_store)
    run = service.submit(saved['id'], target_node_id=kind)
    barrier = Barrier(2)
    for worker in (service, other):
        original = worker._mark_ready_sources
        def ready(run, original=original):
            original(run)
            barrier.wait(timeout=5)
        monkeypatch.setattr(worker, '_mark_ready_sources', ready)
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(worker.tick, run['id']) for worker in (service, other)]
        for result in results:
            result.result(timeout=10)
    assert getattr(service.provider, f'{kind}_calls') == 1
    assert kind in service.get(run['id'])['provider_jobs']


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_new_submit_between_ownership_check_and_marker_supersedes_unpaid_run(tmp_path, monkeypatch, kind):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    older = service.submit(saved['id'], target_node_id=kind)
    original = service._mark_ready_sources
    newer = []
    def ready(run):
        original(run)
        if not newer:
            newer.append(service.submit(saved['id'], target_node_id=kind))
    monkeypatch.setattr(service, '_mark_ready_sources', ready)
    service.tick(older['id'])
    service.tick(newer[0]['id'])
    assert getattr(service.provider, f'{kind}_calls') == 1
    assert service.get(older['id'])['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_submit_after_marker_before_paid_call_returns_same_receipt(tmp_path, monkeypatch, kind):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    older = service.submit(saved['id'], target_node_id=kind)
    original = getattr(service.provider, f'generate_{kind}')
    receipts = []
    def generate(*args):
        receipts.append(service.submit(saved['id'], target_node_id=kind))
        return original(*args)
    monkeypatch.setattr(service.provider, f'generate_{kind}', generate)
    service.tick(older['id'])
    assert receipts[0]['id'] == older['id']
    assert len(service.list()) == 1


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('failure_method', ['GET', 'POST'])
def test_capability_timeout_is_unpaid_but_post_timeout_is_unknown(tmp_path, monkeypatch, kind, failure_method):
    from web_api.video_provider import GrokVideoProvider
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    if kind == 'video':
        saved['nodes'][1]['type'] = 'first_last_frame_video'
        saved['nodes'].insert(1, {'id': 'last', 'type': 'image_asset', 'data': {'asset_url': 'https://cdn.test/last.png'}})
        saved['edges'][0]['targetHandle'] = 'first_frame'
        saved['edges'].append({'source': 'last', 'sourceHandle': 'image', 'target': 'video', 'targetHandle': 'last_frame'})
    else:
        saved['nodes'][0]['data']['n'] = 2
    saved = workflows.save(saved)
    provider = GrokVideoProvider()
    methods = []
    def request(method, *args):
        methods.append(method)
        if method == failure_method:
            raise ProviderError('timeout')
        return {'data': [{'id': 'grok-imagine-image', 'capabilities': {'image_batch': True}},
                         {'id': 'grok-imagine-video', 'capabilities': {'first_last_frame': True}}]}
    monkeypatch.setattr(provider, '_request', request)
    service.provider = provider
    run = service.tick(service.submit(saved['id'], target_node_id=kind)['id'])
    if failure_method == 'GET':
        assert methods == ['GET']
        assert run['state'] == 'failed'
        assert not run['node_status'][kind].get('submission_pending')
        assert run['error'] != 'submission_unknown'
    else:
        assert methods == ['GET', 'POST']
        assert run['node_status'][kind]['submission_pending']
        assert run['error'] == 'submission_unknown'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_recreated_target_between_read_and_claim_never_submits_old_snapshot(tmp_path, monkeypatch, kind):
    import copy
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    older = service.submit(saved['id'], target_node_id=kind)
    original = service._mark_ready_sources
    def ready(run):
        original(run)
        current = workflows.get(saved['id'])
        node = copy.deepcopy(next(node for node in current['nodes'] if node['id'] == kind))
        edges = current['edges']
        current['nodes'] = [node for node in current['nodes'] if node['id'] != kind]
        current['edges'] = []
        workflows.save(current)
        current['nodes'].append(node)
        current['edges'] = edges
        workflows.save(current)
    monkeypatch.setattr(service, '_mark_ready_sources', ready)
    result = service.tick(older['id'])
    assert result['state'] == 'cancelled'
    assert getattr(service.provider, f'{kind}_calls') == 0


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('stage', ['before_marker', 'after_marker'])
def test_process_exit_before_paid_call_preserves_safe_claim_boundary(tmp_path, kind, stage):
    import multiprocessing
    import os
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    run = service.submit(saved['id'], target_node_id=kind)
    def worker():
        if stage == 'before_marker':
            setattr(service.provider, f'prepare_{kind}', lambda *_: os._exit(73))
        else:
            setattr(service.provider, f'generate_{kind}', lambda *_: os._exit(73))
        service.tick(run['id'])
    process = multiprocessing.get_context('fork').Process(target=worker)
    process.start()
    process.join(5)
    assert process.exitcode == 73
    resumed = RunService(WorkflowStore(workflows.path), service.settings, service.provider, service.path, asset_store=service.asset_store)
    for pending in resumed.resume_pending():
        resumed.tick(pending['id'])
    if stage == 'before_marker':
        assert getattr(service.provider, f'{kind}_calls') == 1
    else:
        assert getattr(service.provider, f'{kind}_calls') == 0
        assert resumed.get(run['id'])['node_status'][kind]['submission_pending']
        assert resumed.submit(saved['id'], target_node_id=kind)['id'] == run['id']


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_stale_tick_write_cannot_erase_accepted_job(tmp_path, kind):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    run = service.submit(saved['id'], target_node_id=kind)
    stale = service.get(run['id'])
    completed = service.tick(run['id'])
    stale['error'] = 'stale_preflight_failure'
    stale['state'] = 'failed'
    assert service._replace(stale) is False
    assert service.get(run['id']) == completed


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_running_job_receipt_blocks_replacement_until_completion(tmp_path, kind):
    workflows, saved, service = (durable_candidate_service if kind == 'image' else durable_video_service)(tmp_path)
    service.provider = FakeProvider()
    run = service.submit(saved['id'], target_node_id=kind)
    active = service.tick(run['id'])
    assert active['state'] == 'running'
    assert not active['node_status'][kind].get('submission_pending')
    assert service.submit(saved['id'], target_node_id=kind)['id'] == run['id']
    assert len(service.list()) == 1
