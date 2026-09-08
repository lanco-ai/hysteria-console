"""Presentation modules must be importable without bootstrapping the service."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    'module',
    [
        'admin_views',
        'user_views',
        'console_shell_views',
        'configuration_views',
        'operations_views',
        'landing_views',
    ],
)
def test_view_module_import_does_not_load_http_service(module):
    assert importlib.util.find_spec(module) is not None
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'hysteria'))
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            f'import {module}; import sys; assert "subscription_service" not in sys.modules',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
