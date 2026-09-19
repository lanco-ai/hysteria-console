from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_video_css_uses_existing_tokens_and_mobile_drawers():
    css = (ROOT / 'frontend/src/styles/sections/21-video.css').read_text()
    assert 'env(safe-area-inset-bottom)' in css
    assert '100dvh' in css
    assert '--' in css


def test_video_provider_configuration_lives_in_service_center_and_assistant_only_drafts():
    page = (ROOT / 'frontend/src/features/video/VideoPage.tsx').read_text()
    assistant = (ROOT / 'frontend/src/features/video/VideoCreativeAssistant.tsx').read_text()
    assert 'VideoSettingsDrawer' not in page
    assert 'href="/admin/services?tab=ai"' in page
    assert '不会自动生成图片或视频' in assistant
    assert '应用到当前工作流' in assistant
    assert 'localStorage' not in assistant
