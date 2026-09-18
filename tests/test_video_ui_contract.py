from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_video_css_uses_existing_tokens_and_mobile_drawers():
    css = (ROOT / 'frontend/src/styles/sections/21-video.css').read_text()
    assert 'env(safe-area-inset-bottom)' in css
    assert '100dvh' in css
    assert '--' in css


def test_video_settings_never_renders_raw_key_or_local_storage():
    source = (ROOT / 'frontend/src/features/video/VideoSettingsDrawer.tsx').read_text()
    assert 'api_key_masked' in source
    assert 'localStorage' not in source
