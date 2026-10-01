from pathlib import Path

from web_api.document_routes import REACT_DOCUMENTS


ROOT = Path(__file__).resolve().parents[1]


def test_admin_video_is_an_exact_react_document_route():
    assert '/admin/video' in REACT_DOCUMENTS
    assert "'/admin/video'" in (ROOT / 'frontend/src/main.tsx').read_text()


def test_public_navigation_contains_ai_video_target():
    portal = (ROOT / 'frontend/src/features/public/PortalShell.tsx').read_text()
    sidebar = (ROOT / 'frontend/src/shared/navigation.ts').read_text()
    assert 'href="/?view=video"' in portal
    assert 'AI 视频' in portal
    assert "href: '/admin/video'" not in sidebar


def test_video_client_does_not_store_api_keys():
    source = (ROOT / 'frontend/src/features/video/videoApi.ts').read_text()
    assert 'localStorage' not in source
