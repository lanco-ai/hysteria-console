#!/usr/bin/env bash
# Read-only checks. No automatic fixes and no access to production state.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
quality_python="${PYTHON:-python3}"

"$quality_python" -m ruff check hysteria scripts tests

# Incremental adoption: expand this list when a module is cleaned up.
adopted=(
  hysteria/web_assets.py tests/test_web_assets.py
  tests/workspace_preview_server.py tests/run_frontend_browser.py
  tests/test_preview_isolation.py
  hysteria/public_views.py hysteria/auth_views.py hysteria/admin_read_routes.py
  hysteria/admin_config_routes.py
  hysteria/admin_operations_routes.py
  hysteria/user_views.py
  hysteria/admin_views.py
  hysteria/console_shell_views.py
  hysteria/operations_views.py
  hysteria/configuration_views.py
  hysteria/template_store.py
  hysteria/session_store.py
  hysteria/login_throttle.py
  hysteria/billing_service.py
  hysteria/authorization_service.py
  hysteria/credential_service.py
  hysteria/revocation_service.py
  hysteria/identity_service.py
  hysteria/landing_views.py
  hysteria/shared_views.py
  hysteria/health_presentation.py
  hysteria/user_state_service.py
  hysteria/operational_service.py
  hysteria/audit_log.py
  hysteria/user_panel_data.py
  tests/test_final_domain_boundaries.py
  tests/test_operational_service.py
  tests/test_user_state_service.py
  tests/test_health_presentation.py
  tests/test_shared_views.py
  tests/test_identity_service.py
  tests/test_revocation_service.py
  tests/test_credential_service.py
  tests/test_authorization_service.py
  tests/test_billing_service.py
  tests/test_login_throttle.py
  tests/test_session_store.py
  tests/test_template_store.py
  hysteria/public_page_routes.py
  hysteria/user_panel_routes.py
  hysteria/subscription_routes.py
  hysteria/admin_console_routes.py
  hysteria/landing_write_routes.py
  hysteria/rule_pack_routes.py
  hysteria/credential_routes.py
  hysteria/auth_routes.py
  hysteria/admin_traffic_routes.py
  hysteria/admin_account_routes.py
  hysteria/admin_user_status_routes.py
  hysteria/admin_user_delete_routes.py
  tests/conftest.py tests/test_test_environment.py tests/test_public_views.py
  tests/test_auth_views.py tests/test_admin_read_routes.py
  tests/test_admin_config_routes.py
  tests/test_admin_operations_routes.py
  tests/test_panel_view_modules.py
  tests/test_public_read_routes.py
  tests/test_admin_console_routes.py
  tests/test_network_write_routes.py
  tests/test_credential_routes.py
  tests/test_auth_routes.py
  tests/test_admin_traffic_routes.py
  tests/test_admin_user_routes.py
)
"$quality_python" -m ruff check --select E9,F,I "${adopted[@]}"
"$quality_python" -m ruff format --check "${adopted[@]}"
# The composition root keeps compatibility exports, but its layout is now checked too.
"$quality_python" -m ruff format --check hysteria/subscription_service.py

if [[ "${1:-}" != "--lint-only" ]]; then
  "$quality_python" -m pytest -q --tb=short
fi
