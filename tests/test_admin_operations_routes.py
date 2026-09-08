"""Operations dispatcher must leave unrelated requests untouched."""

import importlib


def test_unknown_operation_does_not_touch_dependencies():
    module = importlib.import_module('admin_operations_routes')
    assert module.handle_write(object(), object(), path='/admin/change-password', form={}) is False
