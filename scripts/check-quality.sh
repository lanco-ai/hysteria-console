#!/usr/bin/env bash
# Read-only checks for the shop service. No automatic fixes and no access to production state.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

quality_python="${PYTHON:-python3}"
"$quality_python" -m ruff check server scripts tests
# Files held to the full lint (F, I) and format gate.
adopted=(
  server/shop_server.py
  server/web_api/__init__.py
  server/web_api/app.py
  server/web_api/services.py
  server/web_api/requests.py
  server/web_api/document_routes.py
  scripts/hy2_panel_release.py
  tests/test_shop_service_boundary.py
  tests/react_preview_server.py
  tests/run_react_browser.py
  tests/test_react_release.py
)
"$quality_python" -m ruff check --select E9,F,I "${adopted[@]}"
"$quality_python" -m ruff format --check "${adopted[@]}"
if [[ "${1:-}" != "--lint-only" ]]; then
  "$quality_python" -m pytest -q --tb=short
fi
