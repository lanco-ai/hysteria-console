from web_api.video_models import ProviderJob, ProviderJobStatus, VideoSettings
from web_api.video_service import RunService, VideoValidationError, WorkflowStore


class FakeProvider:
    def __init__(self):
        self.image_calls = 0
        self.video_calls = 0
        self.create_then_timeout = False
        self.image_done = False

    def generate_image(self, request, settings):
        del request, settings
        self.image_calls += 1
        if self.create_then_timeout:
            raise TimeoutError('transport timeout')
        return ProviderJob('image-job')

    def generate_video(self, request, settings):
        del request, settings
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
