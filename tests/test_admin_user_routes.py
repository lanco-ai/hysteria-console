"""User route dispatchers must not consume unrelated requests."""

import importlib

import pytest


@pytest.mark.parametrize(
    'module_name', ['admin_account_routes', 'admin_user_status_routes', 'admin_user_delete_routes']
)
def test_unknown_user_route_does_not_access_state(module_name):
    module = importlib.import_module(module_name)
    assert (
        module.handle_write(
            object(),
            object(),
            path='/admin/rotate-token',
            form={},
            query={},
            request_user_revision='',
        )
        is False
    )
