from pathlib import Path

from web_api.document_routes import REACT_DOCUMENTS


ROOT = Path(__file__).resolve().parents[1]


def test_admin_video_is_an_exact_react_document_route():
    assert '/admin/video' in REACT_DOCUMENTS
    assert "'/admin/video'" in (ROOT / 'frontend/src/main.tsx').read_text()


def test_navigation_contains_ai_video_target():
    source = (ROOT / 'frontend/src/shared/navigation.ts').read_text()
    assert "href: '/admin/video'" in source


def test_video_client_does_not_store_api_keys():
    source = (ROOT / 'frontend/src/features/video/videoApi.ts').read_text()
    assert 'localStorage' not in source
