#!/usr/bin/env bash
set -euo pipefail

# Playwright E2E runner (headful by default for "journey viewing")
# Usage:
#   E2E_BASE_URL=http://localhost:8002 \
#   E2E_TEST_USERNAME=e2e_testuser \
#   E2E_TEST_PASSWORD=e2e_testpassword123 \
#   ./scripts/run_e2e_playwright.sh
#
# Optional:
#   E2E_HEADFUL=1      # show browser (default: 1)
#   E2E_SLOWMO_MS=50   # slow motion in ms
#   E2E_BROWSER=chromium
#   E2E_INSTALL_BROWSERS=1

E2E_HEADFUL="${E2E_HEADFUL:-1}"
E2E_SLOWMO_MS="${E2E_SLOWMO_MS:-0}"
E2E_BROWSER="${E2E_BROWSER:-chromium}"
E2E_INSTALL_BROWSERS="${E2E_INSTALL_BROWSERS:-0}"

if [[ "${E2E_INSTALL_BROWSERS}" == "1" ]]; then
  poetry run playwright install "${E2E_BROWSER}"
fi

PYTEST_ARGS=(tests/e2e -v)

if [[ "${E2E_HEADFUL}" == "1" ]]; then
  PYTEST_ARGS+=(--headed)
fi

if [[ "${E2E_SLOWMO_MS}" != "0" ]]; then
  PYTEST_ARGS+=(--slowmo "${E2E_SLOWMO_MS}")
fi

poetry run pytest "${PYTEST_ARGS[@]}"
