"""Traffic dispatcher ownership boundary."""

import importlib


def test_unknown_traffic_route_leaves_state_untouched():
    module = importlib.import_module('admin_traffic_routes')
    assert (
        module.handle_write(
            object(), object(), path='/admin/delete', form={}, request_user_revision=''
        )
        is False
    )
