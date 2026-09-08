"""Credential dispatcher must not intercept unrelated account actions."""

import importlib


def test_unknown_credential_route_does_not_access_state():
    module = importlib.import_module('credential_routes')
    assert (
        module.handle_write(
            object(), object(), path='/admin/delete', form={}, query={}, request_user_revision=''
        )
        is False
    )
