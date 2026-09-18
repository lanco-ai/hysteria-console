from fastapi.testclient import TestClient
import pytest

from web_api import create_app
from web_api.services import LoginRequired
from web_api.video_service import AssetStore, VideoValidationError


class _Services:
    def read_session(self, *, headers, path):
        del path
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        raise LoginRequired


def test_asset_store_rejects_path_traversal_and_unsupported_mime(tmp_path):
    store = AssetStore(tmp_path / 'assets', max_bytes=1024)
    with pytest.raises(VideoValidationError):
        store.save_upload('../../secret', 'text/plain', b'x')
    with pytest.raises(VideoValidationError):
        store.save_upload('x.exe', 'application/octet-stream', b'x')


def test_asset_content_route_does_not_expose_files_to_anonymous(tmp_path):
    store = AssetStore(tmp_path / 'assets', max_bytes=1024)
    asset = store.save_upload('x.png', 'image/png', b'png')
    app = create_app(_Services(), video_asset_store=store)
    with TestClient(app) as client:
        response = client.get(f"/api/video/assets/{asset['id']}/content")
    assert response.status_code == 401


def test_authorized_asset_upload_and_content(tmp_path):
    store = AssetStore(tmp_path / 'assets', max_bytes=1024)
    app = create_app(_Services(), video_asset_store=store)
    headers = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin', 'X-File-Name': 'frame.png', 'Content-Type': 'image/png'}
    with TestClient(app) as client:
        upload = client.post('/api/video/assets', headers=headers, content=b'png-data')
        assert upload.status_code == 200
        asset_id = upload.json()['id']
        assert 'path' not in upload.text
        content = client.get(f'/api/video/assets/{asset_id}/content', headers={'Cookie': 'sid=admin'})
    assert content.status_code == 200
    assert content.content == b'png-data'
    assert content.headers['content-type'].startswith('image/png')
