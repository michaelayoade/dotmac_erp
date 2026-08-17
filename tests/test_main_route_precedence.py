import os
import subprocess
import sys

import pytest

from app.main import app


def _route_paths() -> list[str]:
    return [route.path for route in app.router.routes if hasattr(route, "path")]


def test_finance_settings_routes_precede_generic_settings_route():
    """Finance settings routes must be registered before /settings/{module_key}.

    finance_settings_web_router (app/web/finance/settings.py) is mounted
    directly on the app with prefix "/settings" so its static paths
    (e.g. /settings/numbering, /settings/payroll, /settings/reports) sit
    on the same path namespace as the generic catch-all
    /settings/{module_key} from module_settings_web_router. If the catch-all
    is registered first, FastAPI matches it for every /settings/<word>
    request and the static paths become unreachable. This test pins the
    inclusion order in app/main.py.
    """
    paths = _route_paths()
    generic_index = paths.index("/settings/{module_key}")

    # Each of these is a static finance settings path that would be shadowed
    # by /settings/{module_key} if order were wrong.
    static_paths = [
        "/settings/numbering",
        "/settings/automation-settings",
        "/settings/payroll",
        "/settings/reports",
        "/settings/exchange-rates",
    ]
    for path in static_paths:
        assert paths.index(path) < generic_index, (
            f"{path} must be registered before /settings/{{module_key}}"
        )


@pytest.mark.timeout(300)
def test_public_careers_routes_are_not_gated_by_people_module():
    env = os.environ.copy()
    env["ENABLED_MODULES"] = "finance"
    env["DOTMAC_DEV_MODE"] = "true"
    env["DOTMAC_DEFER_RUNTIME_OBSERVABILITY"] = "1"

    script = """
import app.main as main_module
paths = [route.path for route in main_module.app.router.routes if hasattr(route, "path")]
assert "/careers/{org_slug}" in paths
assert "/api/v1/careers/{org_slug}/jobs" in paths
"""
    subprocess.run(  # noqa: S603 - controlled interpreter/script for import isolation
        [sys.executable, "-c", script],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )
