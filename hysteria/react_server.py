"""Opt-in ASGI entrypoint for the staged React panel.

This module is intentionally separate from the legacy ``subscription_service``
listener.  A release may run it on loopback for pre-production verification;
the existing deploy script does not install or enable it until the cutover
checklist is approved.
"""

from pathlib import Path

import subscription_service
from web_api import create_app
from web_api.services import LegacyPanelServices

RUNTIME_ROOT = Path(__file__).resolve().parent
_REACT_DIST_CANDIDATES = (
    RUNTIME_ROOT / 'frontend' / 'dist',
    RUNTIME_ROOT.parent / 'frontend' / 'dist',
)
REACT_DIST = next(
    (candidate for candidate in _REACT_DIST_CANDIDATES if candidate.is_dir()),
    _REACT_DIST_CANDIDATES[0],
)


def build_app():
    """Construct the ASGI app from the authoritative legacy service module."""
    return create_app(
        LegacyPanelServices(subscription_service),
        react_dist=REACT_DIST,
    )


app = build_app()
