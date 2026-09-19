import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from web_api import create_app
from web_api.plans_service import PlanStore
from web_api.ai.service_store import AIServiceStore
from web_api.services import LoginRequired


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def task(**changes):
    value = {
        'id': 'task-1',
        'title': '改论文',
        'notes': '',
        'quadrant': 'important_urgent',
        'plan_date': '2026-09-19',
        'timezone': 'America/Los_Angeles',
        'start_time': '09:00',
        'due_at': '2026-09-19T18:00:00-07:00',
        'estimate_minutes': 60,
        'reminder_at': '2026-09-19T16:00:00Z',
        'status': 'todo',
        'created_at': '2026-09-19T15:00:00Z',
        'updated_at': '2026-09-19T15:00:00Z',
    }
    value.update(changes)
    return value


def test_plan_store_persists_validated_snapshot_with_private_permissions(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    initial = store.read()
    saved = store.replace([task()], initial['revision'])
    assert saved['items'][0]['title'] == '改论文'
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert PlanStore(store.path).read() == saved


@pytest.mark.parametrize('changes', [
    {'quadrant': 'urgentish'},
    {'timezone': 'Mars/Olympus'},
    {'plan_date': 'tomorrow'},
    {'estimate_minutes': 0},
])
def test_plan_store_rejects_invalid_task_fields(tmp_path, changes):
    store = PlanStore(tmp_path / 'plans.json')
    with pytest.raises(ValueError):
        store.replace([task(**changes)], store.read()['revision'])


def test_plan_store_uses_revision_conflict_to_protect_other_device_edits(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    initial = store.read()
    store.replace([task()], initial['revision'])
    with pytest.raises(ValueError, match='conflict'):
        store.replace([], initial['revision'])


def test_plans_api_requires_admin_same_origin_and_returns_saved_items(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    with TestClient(create_app(Sessions(), plans_store=store)) as client:
        endpoint = '/api/plans'
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers={'Cookie': 'sid=user'}).status_code == 403
        assert client.put(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
        initial = client.get(endpoint, headers=HEADERS).json()
        saved = client.put(endpoint, headers=HEADERS, json={'revision': initial['revision'], 'items': [task()]})
        assert saved.status_code == 200
        assert saved.json()['items'][0]['title'] == '改论文'
        assert client.get(endpoint, headers=HEADERS).json() == saved.json()


def test_plans_api_rejects_oversized_request_and_stale_revision(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    with TestClient(create_app(Sessions(), plans_store=store)) as client:
        endpoint = '/api/plans'
        initial = client.get(endpoint, headers=HEADERS).json()
        payload = {'revision': initial['revision'], 'items': [task()]}
        assert client.put(endpoint, headers=HEADERS, json=payload).status_code == 200
        assert client.put(endpoint, headers=HEADERS, json=payload).status_code == 409
        assert client.put(endpoint, headers={**HEADERS, 'Content-Type': 'application/json'}, content='x' * (128 * 1024 + 1)).status_code == 413


def test_plans_document_requires_admin_and_serves_the_react_shell(tmp_path):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'assets').mkdir()
    (dist / 'index.html').write_text('<!doctype html><title>Panel</title><body><div id="root" data-public-host=""></div></body>')
    with TestClient(create_app(Sessions(), react_dist=dist, plans_store=PlanStore(tmp_path / 'plans.json'))) as client:
        anonymous = client.get('/admin/plans', follow_redirects=False)
        assert anonymous.status_code == 303
        assert anonymous.headers['location'] == '/login?next=%2Fadmin%2Fplans'
        response = client.get('/admin/plans', headers=HEADERS)
        assert response.status_code == 200
        assert '<title>今日计划</title>' in response.text


def test_due_reminders_can_be_snoozed_or_dismissed_without_replacing_other_tasks(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    now = datetime(2026, 9, 19, 20, tzinfo=timezone.utc)
    due = task(reminder_at='2026-09-19T19:00:00Z')
    other = task(id='task-2', title='健身', reminder_at='2026-09-20T19:00:00Z')
    saved = store.replace([due, other], store.read()['revision'])
    assert [item['id'] for item in store.due_reminders(now)] == ['task-1']
    updated = store.update_reminder('task-1', 'snooze', now=now)
    assert datetime.fromisoformat(updated['reminder_at'].replace('Z', '+00:00')) == now + timedelta(minutes=10)
    assert [item['id'] for item in store.read()['items']] == ['task-1', 'task-2']


def test_reminder_api_is_admin_only_and_acknowledges_due_items(tmp_path):
    store = PlanStore(tmp_path / 'plans.json')
    due = task(reminder_at='2020-01-01T00:00:00Z')
    store.replace([due], store.read()['revision'])
    with TestClient(create_app(Sessions(), plans_store=store)) as client:
        endpoint = '/api/plans/reminders'
        assert client.get(endpoint).status_code == 401
        response = client.get(endpoint, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()['items'][0]['id'] == 'task-1'
        assert client.post(f'{endpoint}/task-1', headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={'action': 'dismiss'}).status_code == 403
        assert client.post(f'{endpoint}/task-1', headers=HEADERS, json={'action': 'dismiss'}).status_code == 200
        assert client.get(endpoint, headers=HEADERS).json()['items'] == []


class PlanGeminiStub:
    def __init__(self):
        self.calls = []

    def list_models(self, profile):
        assert profile['api_key'] == 'server-only-key'
        return [{'id': 'gemini-test-flash', 'name': 'Gemini Test Flash'}]

    def generate_json(self, profile, model, prompt, schema):
        self.calls.append({'model': model, 'prompt': prompt, 'schema': schema})
        assert profile['api_key'] == 'server-only-key'
        return {
            'summary': '先完成最重要的任务，再安排休息。',
            'suggestions': [{
                'title': '整理本周计划', 'notes': '拆成可执行的小步骤。',
                'quadrant': 'important', 'start_time': '10:30',
                'estimate_minutes': 45, 'reminder_offset_minutes': 10,
                'reason': '为后续工作建立清晰顺序。',
            }],
        }


def make_ai_store(tmp_path):
    return AIServiceStore(
        tmp_path / 'ai' / 'registry.json',
        chat_legacy_path=tmp_path / 'chat.json',
        video_legacy_path=tmp_path / 'video.json',
        backup_dir=tmp_path / 'ai' / 'migration-backup',
    )


def configure_gemini(store):
    initial = store.public()
    saved = store.update_profile(
        'gemini-primary', revision=initial['revision'], api_key='server-only-key',
    )
    return saved


def test_plan_assistant_returns_a_preview_without_changing_saved_plans(tmp_path):
    plan_store = PlanStore(tmp_path / 'plans.json')
    ai_store = make_ai_store(tmp_path)
    configure_gemini(ai_store)
    gemini = PlanGeminiStub()
    with TestClient(create_app(
        Sessions(), plans_store=plan_store,
        ai_services_store=ai_store, gemini_adapter=gemini,
    )) as client:
        response = client.post('/api/plans/assistant', headers=HEADERS, json={
            'date': '2026-09-19', 'timezone': 'America/Los_Angeles',
            'request': '帮我安排今天的工作，优先写完计划。',
            'existing_tasks': [{'title': '给客户回邮件', 'quadrant': 'urgent', 'status': 'todo'}],
        })
        assert response.status_code == 200
        data = response.json()
        assert data['model'] == 'gemini-test-flash'
        assert data['suggestions'][0]['quadrant'] == 'important'
        assert 'server-only-key' not in response.text
        assert 'server-only-key' not in gemini.calls[0]['prompt']
        assert gemini.calls[0]['model'] == 'gemini-test-flash'
        assert plan_store.read()['items'] == []


def test_plan_assistant_is_admin_same_origin_and_validates_model_output(tmp_path):
    class InvalidGemini(PlanGeminiStub):
        def generate_json(self, profile, model, prompt, schema):
            return {'summary': 'bad', 'suggestions': [{'title': 'bad', 'quadrant': 'other'}]}

    ai_store = make_ai_store(tmp_path)
    configure_gemini(ai_store)
    with TestClient(create_app(
        Sessions(), plans_store=PlanStore(tmp_path / 'plans.json'),
        ai_services_store=ai_store, gemini_adapter=InvalidGemini(),
    )) as client:
        endpoint = '/api/plans/assistant'
        payload = {'date': '2026-09-19', 'timezone': 'UTC', 'request': '安排任务', 'existing_tasks': []}
        assert client.post(endpoint, json=payload).status_code == 401
        assert client.post(endpoint, headers={'Cookie': 'sid=user'}, json=payload).status_code == 403
        assert client.post(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json=payload).status_code == 403
        response = client.post(endpoint, headers=HEADERS, json=payload)
        assert response.status_code == 502
        assert response.json() == {'error': 'invalid_model_response'}


def test_plan_assistant_requires_a_configured_gemini_service(tmp_path):
    with TestClient(create_app(
        Sessions(), plans_store=PlanStore(tmp_path / 'plans.json'),
        ai_services_store=make_ai_store(tmp_path), gemini_adapter=PlanGeminiStub(),
    )) as client:
        response = client.post('/api/plans/assistant', headers=HEADERS, json={
            'date': '2026-09-19', 'timezone': 'UTC', 'request': '安排计划', 'existing_tasks': [],
        })
        assert response.status_code == 422
        assert response.json() == {'error': 'service_not_configured'}


@pytest.mark.parametrize('date_value', ['2026-99-19', '2026-02-30', '0000-01-01'])
def test_plan_assistant_rejects_impossible_calendar_dates(tmp_path, date_value):
    ai_store = make_ai_store(tmp_path)
    configure_gemini(ai_store)
    gemini = PlanGeminiStub()
    with TestClient(create_app(
        Sessions(), plans_store=PlanStore(tmp_path / 'plans.json'),
        ai_services_store=ai_store, gemini_adapter=gemini,
    )) as client:
        response = client.post('/api/plans/assistant', headers=HEADERS, json={
            'date': date_value, 'timezone': 'UTC', 'request': '安排计划', 'existing_tasks': [],
        })
    assert response.status_code == 400
    assert gemini.calls == []


def test_plan_assistant_rejects_more_than_eight_suggestions(tmp_path):
    class TooManySuggestions(PlanGeminiStub):
        def generate_json(self, profile, model, prompt, schema):
            suggestion = {
                'title': '任务', 'notes': '', 'quadrant': 'important',
                'start_time': '', 'estimate_minutes': 30,
                'reminder_offset_minutes': 0, 'reason': '',
            }
            return {'summary': '安排计划', 'suggestions': [suggestion] * 9}

    ai_store = make_ai_store(tmp_path)
    configure_gemini(ai_store)
    with TestClient(create_app(
        Sessions(), plans_store=PlanStore(tmp_path / 'plans.json'),
        ai_services_store=ai_store, gemini_adapter=TooManySuggestions(),
    )) as client:
        response = client.post('/api/plans/assistant', headers=HEADERS, json={
            'date': '2026-09-19', 'timezone': 'UTC', 'request': '安排计划', 'existing_tasks': [],
        })
    assert response.status_code == 502
    assert response.json() == {'error': 'invalid_model_response'}
