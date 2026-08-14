"""Architecture guard for ERP's released ``dotmac-ui`` contract.

ERP supplies Jinja and the FastAPI static mount. The package supplies inert,
namespaced templates and compiled CSS; neither surface is copied into ERP.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

UI_DIST = "dotmac-ui"
UI_PIN = "0.1.0a7"
UI_CONTRACT = 1
FORGEJO_SOURCE = "forgejo"
LEGACY_EMPTY_STATE_BASELINE = (25, 22)
LEGACY_EMPTY_STATE_CLASS = re.compile(r'class="empty-state(?:\s|"|$)')

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
UI_SHELLS = (
    PROJECT_ROOT / "templates" / "base.html",
    PROJECT_ROOT / "templates" / "careers" / "base_careers.html",
    PROJECT_ROOT / "templates" / "onboarding" / "portal" / "base_onboarding.html",
    PROJECT_ROOT / "templates" / "finance" / "payments" / "callback.html",
)


def _ui_dependency() -> dict[str, object]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependency = data["tool"]["poetry"]["dependencies"].get(UI_DIST)
    assert isinstance(dependency, dict), (
        f"{UI_DIST} must use an inline table with an exact version and source"
    )
    return dependency


def _assert_shell_consumes_ui_contract(source: str, path: Path) -> None:
    local_css_positions = [
        source.index(match)
        for match in ("/static/css/app.css", "/static/css/font-overrides.css")
        if match in source
    ]
    assert local_css_positions, f"{path} has no ERP product stylesheet"
    contract_position = source.find("{{ dotmac_ui_stylesheet_url }}")
    assert contract_position >= 0, (
        f"{path} must link the installed dotmac-ui stylesheet global"
    )
    assert contract_position > max(local_css_positions), (
        f"{path} must load dotmac-ui after ERP product CSS"
    )


def _legacy_empty_state_inventory(sources: dict[Path, str]) -> tuple[int, int]:
    matches_by_file = {
        path: len(LEGACY_EMPTY_STATE_CLASS.findall(source))
        for path, source in sources.items()
    }
    live = {path: count for path, count in matches_by_file.items() if count}
    return sum(live.values()), len(live)


def _assert_legacy_empty_state_baseline(inventory: tuple[int, int]) -> None:
    assert inventory == LEGACY_EMPTY_STATE_BASELINE, (
        "legacy empty-state debt changed; migrate the affected callers and "
        f"lower the surface/file baseline deliberately: {inventory}"
    )


def test_ui_is_pinned_to_one_private_release() -> None:
    dependency = _ui_dependency()
    version = str(dependency.get("version", ""))
    bare = version.removeprefix("==").strip()
    assert not re.search(r"[\^~*<>|,!]|>=|<=", version)
    assert bare == UI_PIN
    assert dependency.get("source") == FORGEJO_SOURCE


def test_installed_ui_release_and_component_contract_are_exact() -> None:
    import dotmac_ui

    assert importlib.metadata.version(UI_DIST) == UI_PIN
    assert dotmac_ui.__version__ == UI_PIN
    assert dotmac_ui.UI_CONTRACT_VERSION == UI_CONTRACT
    assert UI_CONTRACT in dotmac_ui.SUPPORTED_UI_CONTRACT_VERSIONS
    assert ".dark" in dotmac_ui.DARK_THEME_SELECTORS
    assert dotmac_ui.EMPTY_STATE.template == ("dotmac_ui/components/empty_state.html")
    assert dotmac_ui.EMPTY_STATE.parameters == (
        "title",
        "message",
        "action_label",
        "action_url",
    )
    assert dotmac_ui.EMPTY_STATE.classes <= dotmac_ui.PUBLISHED_COMPONENT_CLASSES


def test_ui_composition_boundary_uses_public_package_paths() -> None:
    import dotmac_ui
    from dotmac_ui.assets import ASSET_NAMESPACE

    from app.ui import (
        UI_ASSET_DIRECTORY,
        UI_ASSET_MOUNT,
        UI_STYLESHEET_URL,
        UI_TEMPLATE_DIRECTORY,
        ui_template_globals,
    )

    assert dotmac_ui.static_dir() / ASSET_NAMESPACE == UI_ASSET_DIRECTORY
    assert dotmac_ui.template_dir() == UI_TEMPLATE_DIRECTORY
    assert UI_ASSET_DIRECTORY.is_dir()
    assert UI_TEMPLATE_DIRECTORY.is_dir()
    assert dotmac_ui.stylesheet_path().is_file()
    assert f"/static/{ASSET_NAMESPACE}" == UI_ASSET_MOUNT
    assert dotmac_ui.stylesheet_url() == UI_STYLESHEET_URL
    assert ui_template_globals() == {
        "dotmac_ui_stylesheet_url": UI_STYLESHEET_URL,
    }


def test_shared_erp_loader_resolves_the_packaged_component_and_adapter() -> None:
    import dotmac_ui

    from app.templates import templates

    packaged = templates.env.get_template(dotmac_ui.EMPTY_STATE.template)
    assert packaged.name == dotmac_ui.EMPTY_STATE.template

    rendered = templates.env.from_string(
        """
{% from "components/macros.html" import empty_state %}
{{ empty_state("No Invoices", "Create one", "document", "New Invoice", "/finance/ar/invoices/new") }}
"""
    ).render()
    assert 'class="dmui-empty-state"' in rendered
    assert 'class="dmui-empty-state__title"' in rendered
    assert 'href="/finance/ar/invoices/new"' in rendered
    assert "/static/img/illustrations/" not in rendered
    assert 'class="empty-state' not in rendered


def test_ui_asset_has_a_dedicated_mount_before_erp_static() -> None:
    from app.main import app
    from app.ui import UI_ASSET_DIRECTORY, UI_ASSET_MOUNT, UI_STYLESHEET_URL

    mounts = [
        route
        for route in app.routes
        if getattr(route, "path", None) in {UI_ASSET_MOUNT, "/static"}
    ]
    assert [route.path for route in mounts] == [UI_ASSET_MOUNT, "/static"]
    assert Path(mounts[0].app.directory).resolve() == UI_ASSET_DIRECTORY.resolve()

    response = TestClient(app).get(UI_STYLESHEET_URL)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--dmui-" in response.text
    assert ".dmui-empty-state" in response.text


def test_every_browser_shell_links_the_ui_contract_after_product_css() -> None:
    for path in UI_SHELLS:
        _assert_shell_consumes_ui_contract(path.read_text(encoding="utf-8"), path)


def test_ui_shell_guard_is_red_sensitive() -> None:
    with pytest.raises(AssertionError, match="must link"):
        _assert_shell_consumes_ui_contract(
            '<link href="/static/css/app.css" rel="stylesheet">',
            Path("synthetic-shell.html"),
        )


def test_ui_package_is_not_vendored_into_erp() -> None:
    assert not (PROJECT_ROOT / "static" / "dotmac-ui").exists()
    assert not (PROJECT_ROOT / "templates" / "dotmac_ui").exists()


def test_legacy_inline_empty_state_debt_is_two_directionally_frozen() -> None:
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "templates").rglob("*.html")
        if path != PROJECT_ROOT / "templates" / "components" / "macros.html"
    }
    _assert_legacy_empty_state_baseline(_legacy_empty_state_inventory(sources))


def test_legacy_empty_state_ratchet_is_red_sensitive() -> None:
    synthetic = {Path("legacy.html"): '<div class="empty-state p-4"></div>'}
    assert _legacy_empty_state_inventory(synthetic) == (1, 1)
    with pytest.raises(AssertionError, match="debt changed"):
        _assert_legacy_empty_state_baseline((26, 22))
