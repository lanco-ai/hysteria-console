"""Behavior checks for the private life and learning journal."""

from fastapi.testclient import TestClient

from web_api import create_app
from web_api.services import LoginRequired


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def paper(**changes):
    value = {
        'kind': 'paper', 'occurred_at': '2026-09-30T06:30:00Z',
        'ended_at': '2026-09-30T07:10:00Z', 'timezone': 'America/Los_Angeles',
        'title': 'LSM tree paper', 'body': 'Read the compaction section.',
        'question': 'Why compact?', 'explanation': 'To bound read amplification.',
        'source': 'Paper section 3', 'uncertainty': 'Write cost?',
        'next_check': 'Compare leveled and tiered compaction.',
    }
    value.update(changes)
    return value


def test_journal_api_creates_and_filters_persisted_paper_by_local_date(tmp_path):
    from web_api.journal_service import JournalStore

    store = JournalStore(tmp_path / 'private' / 'journal.json')
    with TestClient(create_app(Sessions(), journal_store=store)) as client:
        created = client.post('/api/journal', headers=HEADERS, json=paper())
        assert created.status_code == 201
        item = created.json()['item']
        assert item['kind'] == 'paper'
        assert item['local_date'] == '2026-09-29'
        assert item['revision'] == 1
        listing = client.get('/api/journal?date=2026-09-29', headers=HEADERS)
        assert [record['id'] for record in listing.json()['items']] == [item['id']]
        assert client.get('/api/journal?date=2026-09-30', headers=HEADERS).json()['items'] == []
        edited = client.put(f"/api/journal/{item['id']}", headers=HEADERS,
                            json={**paper(title='Updated paper'), 'revision': 1})
        assert edited.status_code == 200
        assert edited.json()['item']['revision'] == 2
    assert JournalStore(store.path).read(date='2026-09-29')['items'][0]['title'] == 'Updated paper'
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


def test_journal_rejects_invalid_fields_and_keeps_previous_record(tmp_path):
    from web_api.journal_service import JournalStore

    with TestClient(create_app(Sessions(), journal_store=JournalStore(tmp_path / 'journal.json'))) as client:
        created = client.post('/api/journal', headers=HEADERS, json=paper()).json()['item']
        endpoint = f"/api/journal/{created['id']}"
        for invalid in [paper(kind='made_up'), paper(timezone='Mars/Olympus'),
                        paper(occurred_at='2026-09-30T06:30:00'),
                        paper(ended_at='2026-09-29T05:00:00Z'),
                        paper(unexpected='secret'), paper(title='x' * 161)]:
            assert client.put(endpoint, headers=HEADERS, json={**invalid, 'revision': 1}).status_code == 422
        assert client.put(endpoint, headers=HEADERS, json={**paper(title='stale'), 'revision': 2}).status_code == 409
        assert client.get('/api/journal', headers=HEADERS).json()['items'][0]['title'] == 'LSM tree paper'


def test_journal_auth_origin_limits_delete_and_export(tmp_path):
    from web_api.journal_service import JournalStore

    with TestClient(create_app(Sessions(), journal_store=JournalStore(tmp_path / 'journal.json'))) as client:
        assert client.get('/api/journal').status_code == 401
        assert client.get('/api/journal', headers={'Cookie': 'sid=user'}).status_code == 403
        assert client.get('/api/journal/export').status_code == 401
        assert client.post('/api/journal', headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json=paper()).status_code == 403
        assert client.post('/api/journal', headers={**HEADERS, 'Content-Type': 'application/json'}, content='x' * (64 * 1024 + 1)).status_code == 413
        created = client.post('/api/journal', headers=HEADERS, json=paper()).json()['item']
        assert client.get('/api/journal/export', headers=HEADERS).json()['items'][0]['id'] == created['id']
        endpoint = f"/api/journal/{created['id']}"
        assert client.request('DELETE', endpoint, headers=HEADERS, json={'revision': 2}).status_code == 409
        assert client.request('DELETE', endpoint, headers=HEADERS, json={'revision': 1}).status_code == 200
        assert client.get('/api/journal', headers=HEADERS).json()['items'] == []


def test_journal_search_chronology_and_week_summary(tmp_path):
    from web_api.journal_service import JournalStore

    with TestClient(create_app(Sessions(), journal_store=JournalStore(tmp_path / 'journal.json'))) as client:
        for entry in [paper(title='Morning paper'),
                      {'kind': 'ielts', 'occurred_at': '2026-09-30T05:00:00Z', 'timezone': 'America/Los_Angeles',
                       'title': 'Listening', 'body': 'Transcribed a clip', 'skill': 'listening'},
                      {'kind': 'thought', 'occurred_at': '2026-10-01T09:00:00Z', 'timezone': 'America/Los_Angeles',
                       'title': 'Changed view', 'body': 'Evidence changed my mind'}]:
            assert client.post('/api/journal', headers=HEADERS, json=entry).status_code == 201
        listed = client.get('/api/journal?date=2026-09-29', headers=HEADERS).json()['items']
        assert [item['title'] for item in listed] == ['Morning paper', 'Listening']
        assert [item['title'] for item in client.get('/api/journal?kind=paper&q=compaction', headers=HEADERS).json()['items']] == ['Morning paper']
        summary = client.get('/api/journal/summary?week_start=2026-09-28', headers=HEADERS).json()
        assert summary['counts'] == {'paper': 1, 'ielts': 1, 'thought': 1}
        assert summary['total'] == 3


def test_journal_storage_errors_fail_closed(tmp_path, monkeypatch):
    from web_api.journal_service import JournalStore
    import state_store

    store = JournalStore(tmp_path / 'journal.json')
    monkeypatch.setattr(store, 'read', lambda **_kwargs: (_ for _ in ()).throw(state_store.StateStoreError('private detail')))
    with TestClient(create_app(Sessions(), journal_store=store)) as client:
        response = client.get('/api/journal', headers=HEADERS)
    assert response.status_code == 503
    assert 'private detail' not in response.text


def test_journal_requires_meaningful_content_but_accepts_structured_daily_review(tmp_path):
    from web_api.journal_service import JournalStore

    with TestClient(create_app(Sessions(), journal_store=JournalStore(tmp_path / 'journal.json'))) as client:
        empty = client.post('/api/journal', headers=HEADERS, json={
            'kind': 'life', 'occurred_at': '2026-09-29T18:00:00Z',
            'timezone': 'America/Los_Angeles', 'title': 'A title only', 'body': '  ',
        })
        assert empty.status_code == 422
        review = client.post('/api/journal', headers=HEADERS, json={
            'kind': 'daily_review', 'occurred_at': '2026-09-29T18:00:00Z',
            'timezone': 'America/Los_Angeles', 'learning_state': 'Focused after explaining the idea aloud',
            'evidence': 'Could reconstruct the example without notes', 'next_check': 'Try another example',
        })
        assert review.status_code == 201
        assert review.json()['item']['learning_state'].startswith('Focused')


def test_journal_orders_by_instant_across_different_timestamp_offsets(tmp_path):
    from web_api.journal_service import JournalStore

    store = JournalStore(tmp_path / 'journal.json')
    store.create({'kind': 'life', 'occurred_at': '2026-09-30T01:30:00Z',
                  'timezone': 'America/Los_Angeles', 'body': 'Earlier'})
    store.create({'kind': 'life', 'occurred_at': '2026-09-29T19:00:00-07:00',
                  'timezone': 'America/Los_Angeles', 'body': 'Later'})
    assert [item['body'] for item in store.read(date='2026-09-29')['items']] == ['Later', 'Earlier']


def test_journal_autosave_create_retry_has_only_one_persisted_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    from web_api.journal_service import JournalStore

    store = JournalStore(tmp_path / 'journal.json')
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: store.create(paper(), idempotency_key=key), range(4)))
    assert len({result['item']['id'] for result in results}) == 1
    first = results[0]['item']
    updated = store.update(first['id'], {**paper(body='Continued typing'), 'revision': 1})['item']
    retried = store.create(paper(), idempotency_key=key)['item']
    assert (retried['id'], retried['revision'], retried['body']) == (updated['id'], 2, 'Continued typing')
    assert len(JournalStore(store.path).read()['items']) == 1


def test_journal_optional_create_key_validates_after_auth_and_origin(tmp_path):
    from uuid import uuid4
    from web_api.journal_service import JournalStore

    with TestClient(create_app(Sessions(), journal_store=JournalStore(tmp_path / 'journal.json'))) as client:
        for key in ['', 'arbitrary-url', 'x' * 300, '00000000-0000-0000-0000-000000000000']:
            assert client.post('/api/journal', headers={**HEADERS, 'Idempotency-Key': key}, json=paper()).status_code == 422
        keyed = {**HEADERS, 'Idempotency-Key': str(uuid4())}
        first = client.post('/api/journal', headers=keyed, json=paper()).json()['item']
        assert client.post('/api/journal', headers=keyed, json=paper()).json()['item']['id'] == first['id']
        assert client.post('/api/journal', headers={'Idempotency-Key': keyed['Idempotency-Key']}, json=paper()).status_code == 401
        assert client.post('/api/journal', headers={**keyed, 'Sec-Fetch-Site': 'cross-site'}, json=paper()).status_code == 403
        # Existing clients without a key still create distinct entries.
        assert client.post('/api/journal', headers=HEADERS, json=paper()).json()['item']['id'] != first['id']
