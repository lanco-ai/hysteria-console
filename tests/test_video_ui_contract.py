from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_video_canvas_styles_cover_mobile_safe_area_and_tokens():
    css = ''.join((ROOT / f'frontend/src/styles/sections/{name}').read_text() for name in (
        '21-video.css', '22-video-storyboard.css',
    ))
    assert 'env(safe-area-inset-bottom)' in css
    assert '100dvh' in css
    assert 'var(--surface' in css


def test_page_uses_one_canvas_and_has_no_removed_service_calls():
    page = (ROOT / 'frontend/src/features/video/VideoPage.tsx').read_text()
    api = (ROOT / 'frontend/src/features/video/videoApi.ts').read_text()
    assert 'VideoCanvas' in page
    assert 'VideoRunPanel' in page
    assert 'storyboard: legacyStoryboard' in page
    assert 'loadVideoRuns' not in page + api
    assert '/api/video/assistant/draft' not in page + api
    assert '/admin/services?tab=ai' not in page
    assert 'VideoCreativeAssistant' not in page
