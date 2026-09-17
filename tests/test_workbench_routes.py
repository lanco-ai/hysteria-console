from tests.test_web_api_documents import StubDocumentServices, _dist

from fastapi.testclient import TestClient
from web_api import create_app


def test_auth_alias_uses_workbench_bootstrap(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        response = client.get('/auth')
        login = client.get('/login')
        user_login = client.get('/user/login')

    assert response.status_code == 200
    assert 'id="root"' in response.text
    assert 'page-auth page-admin-login' not in response.text
    assert 'data-password-max-length="128"' in response.text
    assert 'data-public-host="panel.example.test"' in response.text
    assert 'data-public-host="panel.example.test"' in login.text
    assert 'data-public-host="panel.example.test"' in user_login.text
    assert '<body class="has-shell page-workbench">' in login.text
    assert '<body class="has-shell page-workbench">' in user_login.text


def test_anonymous_admin_document_preserves_safe_return_path(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        response = client.get('/admin/health', follow_redirects=False)

    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Fadmin%2Fhealth'


def test_guard_rejects_external_return_path(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        response = client.get('/admin?next=https://evil.example', follow_redirects=False)

    assert response.status_code == 303
    assert response.headers['location'].startswith('/login?next=%2Fadmin')
    assert 'evil.example' not in response.headers['location']


def test_guard_does_not_forward_sensitive_query_parameters(tmp_path):
    sensitive_keys = ('api_key', 'password', 'authorization', 'access_token', 'API_KEY', 'Password')
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        for key in sensitive_keys:
            response = client.get(
                f'/admin?{key}=secret-value',
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers['location'] == '/login?next=%2Fadmin'
            assert key.lower() not in response.headers['location'].lower()
            assert 'secret-value' not in response.headers['location']


def test_guard_preserves_benign_query_parameters_only(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        response = client.get(
            '/admin?msg=hello&tab=health&unknown=discard&password=secret',
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers['location'] == (
        '/login?next=%2Fadmin%3Fmsg%3Dhello%26tab%3Dhealth'
    )
