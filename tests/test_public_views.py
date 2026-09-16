"""Extraction must preserve the existing public document body byte-for-byte."""

import hashlib
import importlib.util


def test_home_view_can_render_without_runtime_service_dependencies():
    assert importlib.util.find_spec('public_views') is not None
    import public_views

    body = public_views.render_home(
        html_page=lambda title, body, **kwargs: body,
    )
    assert (
        hashlib.sha256(body.encode()).hexdigest()
        == '65b5433353b5bcefe374b074738c90809894fbe63a7789ef0abf3011167f85b6'
    )
