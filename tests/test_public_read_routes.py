"""Public-facing dispatchers must leave admin requests untouched."""

import importlib

import pytest


@pytest.mark.parametrize('name', ['public_page_routes', 'user_panel_routes', 'subscription_routes'])
def test_unrelated_read_does_not_access_state(name):
    module = importlib.import_module(name)
    assert (
        module.handle_read(
            object(),
            object(),
            path='/admin',
            query={},
            host='host',
            base_url='http://host',
            send_payload=False,
        )
        is False
    )
