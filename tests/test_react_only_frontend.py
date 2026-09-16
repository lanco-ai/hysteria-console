"""Regression boundaries for the React-only document frontend."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_source_has_no_legacy_frontend_directory_or_asset_references():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend").rglob("*")
        if path.is_file() and path.suffix in {".css", ".ts", ".tsx", ".html"}
    )
    for forbidden in (
        "hysteria/styles",
        "hysteria/admin.css",
        "/static/style.css",
        "/static/shell.js",
        "/static/ui-core.js",
        "/static/admin-poll.js",
        "/static/usage.js",
        "/static/home.js",
    ):
        assert forbidden not in source


def test_legacy_frontend_build_chain_and_assets_are_removed():
    removed = (
        ROOT / "hysteria/admin.css",
        ROOT / "hysteria/admin_poll.js",
        ROOT / "hysteria/usage.js",
        ROOT / "hysteria/web_assets.py",
        ROOT / "hysteria/static/config-editor.js",
        ROOT / "hysteria/static/home.js",
        ROOT / "hysteria/static/login.js",
        ROOT / "hysteria/static/rules.js",
        ROOT / "hysteria/static/shell-preferences.js",
        ROOT / "hysteria/static/shell.js",
        ROOT / "hysteria/static/ui-core.js",
        ROOT / "hysteria/static/user-panel.js",
        ROOT / "hysteria/static/user-poll.js",
        ROOT / "scripts/build-css.cjs",
    )
    assert all(not path.exists() for path in removed)


def test_legacy_styles_directory_is_not_a_runtime_source():
    assert not (ROOT / "hysteria/styles").exists()


def test_legacy_static_routes_are_not_registered_in_subscription_service():
    source = (ROOT / "hysteria/subscription_service.py").read_text(encoding="utf-8")
    for route in (
        "if path == '/static/style.css'",
        "if path == '/static/admin-poll.js'",
        "if path == '/static/usage.js'",
        "if path == '/static/home.js'",
        "web_assets.ASSETS",
    ):
        assert route not in source
