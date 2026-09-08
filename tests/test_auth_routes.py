"""Authentication route ownership boundary."""

import importlib


def test_unknown_auth_route_does_not_access_credentials():
    module = importlib.import_module('auth_routes')
    assert (
        module.handle_write(object(), object(), path='/admin/rotate-token', form={}, meta={})
        is False
    )
