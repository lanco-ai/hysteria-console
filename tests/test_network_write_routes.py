"""Network configuration route ownership boundaries."""

import importlib

import pytest


@pytest.mark.parametrize('name', ['landing_write_routes', 'rule_pack_routes'])
def test_unknown_network_route_does_not_access_state(name):
    module = importlib.import_module(name)
    assert (
        module.handle_write(
            object(), object(), path='/admin/delete', form={}, query={}, request_user_revision=''
        )
        is False
    )
