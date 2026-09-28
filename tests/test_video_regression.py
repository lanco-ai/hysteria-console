from pathlib import Path

from fastapi.testclient import TestClient

from web_api import create_app
from web_api.document_routes import REACT_DOCUMENTS
from web_api.services import LoginRequired


ROOT = Path(__file__).resolve().parents[1]


class _Services:
    def read_session(self, *, headers, path):
        del headers, path
        raise LoginRequired


def test_video_and_existing_routes_remain_declared():
    assert '/admin/video' in REACT_DOCUMENTS
    assert '/admin/chat' in REACT_DOCUMENTS
    assert '/chat' not in REACT_DOCUMENTS


def test_anonymous_video_api_is_denied_and_old_page_is_not_added():
    with TestClient(create_app(_Services())) as client:
        assert client.get('/api/video/settings').status_code == 401
        assert client.get('/api/video/capabilities').status_code == 401
        assert client.get('/chat').status_code == 404


def test_video_frontend_has_no_browser_secret_persistence():
    source = '\n'.join(path.read_text() for path in (ROOT / 'frontend/src/features/video').glob('*.ts*'))
    assert 'localStorage' not in source
    assert 'Authorization' not in source
