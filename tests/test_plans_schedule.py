import pytest
from fastapi.testclient import TestClient

from web_api import create_app
from web_api.plans_service import PlanStore
from web_api.schedule_service import ScheduleStore
from web_api.services import LoginRequired


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def routine_block(**changes):
    value = {'id': 'sleep', 'title': '睡觉', 'category': 'rest', 'start': '23:30', 'end': '07:00', 'weekdays': [0, 1, 2, 3, 4, 5, 6]}
    value.update(changes)
    return value


def day_block(**changes):
    value = {'id': 'call-mom', 'title': '给妈妈打电话', 'category': 'connect', 'start': '20:00', 'end': '20:30', 'notes': '', 'status': 'planned'}
    value.update(changes)
    return value


def test_schedule_store_keeps_routine_and_days_with_independent_revisions(tmp_path):
    store = ScheduleStore(tmp_path / 'schedule.json')
    initial = store.read('2026-10-10')
    assert initial['routine'] == {'blocks': [], 'targets': {}}
    assert initial['day'] == {'blocks': [], 'routine_status': {}, 'note': ''}

    saved_routine = store.replace_routine('2026-10-10', {'blocks': [routine_block(weekdays=[6, 0])], 'targets': {'study': 90, 'rest': 0}}, initial['routine_revision'])
    assert saved_routine['routine']['blocks'][0]['weekdays'] == [0, 6]
    assert saved_routine['routine']['targets'] == {'study': 90}

    # Saving the day with the original day revision still succeeds: the routine change does not touch it.
    saved_day = store.replace_day('2026-10-10', {'blocks': [day_block()], 'routine_status': {'sleep': 'done'}, 'note': '早睡'}, initial['day_revision'])
    assert saved_day['day']['blocks'][0]['title'] == '给妈妈打电话'
    assert saved_day['routine_revision'] == saved_routine['routine_revision']
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700

    reread = ScheduleStore(store.path).read('2026-10-10')
    assert reread == saved_day
    other_day = store.read('2026-10-11')
    assert other_day['day']['blocks'] == []
    assert other_day['routine'] == saved_routine['routine']


def test_schedule_store_rejects_stale_revisions(tmp_path):
    store = ScheduleStore(tmp_path / 'schedule.json')
    initial = store.read('2026-10-10')
    store.replace_day('2026-10-10', {'blocks': [day_block()]}, initial['day_revision'])
    with pytest.raises(ValueError, match='conflict'):
        store.replace_day('2026-10-10', {'blocks': []}, initial['day_revision'])
    store.replace_routine('2026-10-10', {'blocks': [routine_block()]}, initial['routine_revision'])
    with pytest.raises(ValueError, match='conflict'):
        store.replace_routine('2026-10-10', {'blocks': []}, initial['routine_revision'])


def test_clearing_a_day_removes_it_from_storage(tmp_path):
    store = ScheduleStore(tmp_path / 'schedule.json')
    saved = store.replace_day('2026-10-10', {'blocks': [day_block()]}, store.read('2026-10-10')['day_revision'])
    store.replace_day('2026-10-10', {'blocks': []}, saved['day_revision'])
    assert '2026-10-10' not in store.path.read_text(encoding='utf-8')


@pytest.mark.parametrize('block', [
    day_block(category='party'),
    day_block(start='24:00'),
    day_block(start='09:00', end='09:00'),
    day_block(status='maybe'),
    day_block(title=''),
])
def test_schedule_store_rejects_invalid_day_blocks(tmp_path, block):
    store = ScheduleStore(tmp_path / 'schedule.json')
    with pytest.raises(ValueError):
        store.replace_day('2026-10-10', {'blocks': [block]}, store.read('2026-10-10')['day_revision'])


@pytest.mark.parametrize('routine', [
    {'blocks': [routine_block(weekdays=[7])]},
    {'blocks': [routine_block(weekdays=[])]},
    {'blocks': [routine_block(), routine_block()]},
    {'targets': {'work': 1000, 'study': 600}},
    {'targets': {'naps': 30}},
])
def test_schedule_store_rejects_invalid_routines(tmp_path, routine):
    store = ScheduleStore(tmp_path / 'schedule.json')
    with pytest.raises(ValueError):
        store.replace_routine('2026-10-10', routine, store.read('2026-10-10')['routine_revision'])


def test_schedule_api_requires_admin_and_round_trips(tmp_path):
    schedule = ScheduleStore(tmp_path / 'schedule.json')
    app = create_app(Sessions(), plans_store=PlanStore(tmp_path / 'plans.json'), schedule_store=schedule)
    with TestClient(app) as client:
        assert client.get('/api/plans/schedule?date=2026-10-10').status_code == 401
        assert client.get('/api/plans/schedule?date=2026-10-10', headers={'Cookie': 'sid=user'}).status_code == 403
        assert client.get('/api/plans/schedule?date=2026-13-40', headers=HEADERS).status_code == 400
        initial = client.get('/api/plans/schedule?date=2026-10-10', headers=HEADERS).json()

        cross_site = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}
        body = {'date': '2026-10-10', 'revision': initial['day_revision'], 'day': {'blocks': [day_block()]}}
        assert client.put('/api/plans/schedule/day', headers=cross_site, json=body).status_code == 403

        saved = client.put('/api/plans/schedule/day', headers=HEADERS, json=body)
        assert saved.status_code == 200
        assert saved.json()['day']['blocks'][0]['id'] == 'call-mom'
        assert client.put('/api/plans/schedule/day', headers=HEADERS, json=body).status_code == 409

        invalid = {'date': '2026-10-10', 'revision': initial['routine_revision'], 'routine': {'blocks': [routine_block(category='nap')]}}
        assert client.put('/api/plans/schedule/routine', headers=HEADERS, json=invalid).status_code == 422
        routine = {'date': '2026-10-10', 'revision': initial['routine_revision'], 'routine': {'blocks': [routine_block()], 'targets': {'exercise': 30}}}
        saved_routine = client.put('/api/plans/schedule/routine', headers=HEADERS, json=routine)
        assert saved_routine.status_code == 200
        assert saved_routine.json()['day']['blocks'][0]['id'] == 'call-mom'

        reread = client.get('/api/plans/schedule?date=2026-10-10', headers=HEADERS).json()
        assert reread['routine']['targets'] == {'exercise': 30}
        assert reread['day_revision'] == saved_routine.json()['day_revision']
