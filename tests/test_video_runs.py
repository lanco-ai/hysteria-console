from web_api.video_models import ProviderJob, ProviderJobStatus, VideoSettings
from web_api.video_provider import ProviderError
from web_api.video_service import AssetStore, RunService, VideoValidationError, WorkflowStore


class FakeProvider:
    def __init__(self):
        self.image_calls = 0
        self.video_calls = 0
        self.image_requests = []
        self.video_requests = []
        self.create_then_timeout = False
        self.image_done = False

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
