"""Shared presentation keeps escaping and accessible markup intact."""

import importlib.util

import subscription_service as ss


def test_shared_views_module_exists():
    assert importlib.util.find_spec('shared_views') is not None


def test_alert_escapes_content_and_announces_errors():
    import shared_views

    rendered = shared_views.render_alert(ss._shared_views_context(), '<script>', 'err')
    assert '&lt;script&gt;' in rendered
    assert '<script>' not in rendered
    assert 'role="alert"' in rendered
    assert 'aria-live="assertive"' in rendered
