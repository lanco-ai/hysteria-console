"""Extraction must preserve the existing public document body byte-for-byte."""

import hashlib
import importlib.util


def test_home_view_can_render_without_runtime_service_dependencies():
    assert importlib.util.find_spec('public_views') is not None
    import public_views

    body = public_views.render_home(
        html_page=lambda title, body, **kwargs: body,
        asset_version='test-version',
    )
    assert (
        hashlib.sha256(body.encode()).hexdigest()
        == 'ccbd6593b79cf68f252645269254ebe4a56c824842ed7d24df3fc3e6cc082919'
    )
