#!/usr/bin/env bash
# Read-only checks. No automatic fixes and no access to production state.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
quality_python="${PYTHON:-python3}"

"$quality_python" -m ruff check hysteria scripts tests

# Incremental adoption: expand this list when a module is cleaned up.
adopted=(
  hysteria/web_api/__init__.py
  hysteria/web_api/account_models.py
  hysteria/web_api/account_routes.py
  hysteria/web_api/operation_models.py
  hysteria/web_api/operation_routes.py
  hysteria/web_api/health_routes.py
  hysteria/web_api/health_models.py
  hysteria/web_api/app.py
  hysteria/web_api/models.py
  hysteria/web_api/overview_models.py
  hysteria/web_api/usage_models.py
  hysteria/web_api/incident_models.py
  hysteria/web_api/config_models.py
  hysteria/web_api/rules_routes.py
  hysteria/web_api/landing_models.py
  hysteria/web_api/landing_routes.py
  hysteria/web_api/user_models.py
  hysteria/web_api/requests.py
  hysteria/web_api/services.py
  hysteria/web_api/compat_routes.py
  hysteria/web_api/document_routes.py
  hysteria/web_api/user_detail_models.py
  hysteria/web_api/user_detail_routes.py
  hysteria/password_change_service.py tests/test_password_change_service.py
  hysteria/account_mutation_service.py tests/test_account_mutation_service.py
  hysteria/overview_mutation_result.py
  hysteria/traffic_mutation_service.py
  hysteria/user_status_service.py
  tests/test_overview_operation_services.py
  tests/test_web_api_login.py
  tests/test_web_api_logout.py
  tests/test_web_api_accounts.py
  tests/test_web_api_operations.py
  tests/test_web_api_password_changes.py
  tests/test_web_api_password_page_reads.py
  tests/test_web_api_reads.py
  tests/test_web_api_overview_page.py
  tests/test_web_api_config.py
  tests/test_web_api_rules.py
  tests/test_web_api_usage.py
  tests/test_web_api_health.py
  tests/test_web_api_incidents.py
  tests/test_web_api_landing.py
  tests/test_web_api_landing_egresses.py
  tests/test_web_api_user_panel.py
  tests/test_web_api_documents.py
  tests/test_web_api_user_detail.py
  tests/test_rules_react_contract.py
  tests/test_user_panel_react_contract.py
  tests/test_user_detail_react_contract.py
  tests/test_react_route_parity.py
  hysteria/reset_log_data.py tests/test_reset_log_data.py
  hysteria/web_assets.py tests/test_web_assets.py
  tests/preview_http_server.py
  tests/workspace_preview_server.py tests/run_frontend_browser.py
  tests/test_preview_isolation.py tests/test_preview_page_parity.py
  # Includes isolated React overview/account/operation fixture contracts.
  tests/react_preview_server.py tests/run_react_browser.py tests/test_react_preview.py
  hysteria/public_views.py hysteria/auth_views.py hysteria/admin_read_routes.py
  hysteria/admin_config_routes.py
  hysteria/admin_operations_routes.py
  hysteria/user_views.py
  hysteria/admin_views.py
  hysteria/admin_overview_data.py tests/test_admin_overview_data.py
  hysteria/console_shell_views.py
  hysteria/operations_views.py
  hysteria/configuration_views.py
  hysteria/template_store.py
  hysteria/session_store.py
  hysteria/login_throttle.py
  hysteria/login_service.py
  hysteria/billing_service.py
  hysteria/authorization_service.py
  hysteria/credential_service.py
  hysteria/admin_credential_service.py
  hysteria/user_deletion_service.py
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
  tests/test_admin_credential_services.py
  tests/test_web_api_credential_operations.py
  tests/test_authorization_service.py
  tests/test_billing_service.py
  tests/test_login_throttle.py
  tests/test_login_service.py
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
