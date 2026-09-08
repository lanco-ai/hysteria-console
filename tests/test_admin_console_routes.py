"""Admin console dispatcher does not consume authentication or public paths."""

import importlib


def test_unknown_console_route_leaves_dependencies_untouched():
    module = importlib.import_module('admin_console_routes')
    assert (
        module.handle_read(
            object(),
            object(),
            path='/login',
            query={},
            host='host',
            base_url='http://host',
            send_payload=False,
        )
        is False
    )
