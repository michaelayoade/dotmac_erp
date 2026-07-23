from types import SimpleNamespace

from app.templates import templates


def _render_module_switcher(
    accessible_modules: list[str],
    *,
    current_module: str = "people",
    is_admin: bool = False,
) -> str:
    return templates.env.get_template("partials/_module_switcher.html").render(
        accessible_modules=accessible_modules,
        current_module=current_module,
        user=SimpleNamespace(is_admin=is_admin),
    )


def test_driver_module_switcher_shows_fleet_from_accessible_modules():
    html = _render_module_switcher(["self_service", "fleet"])

    assert 'data-module-key="fleet"' in html
    assert 'href="/fleet"' in html


def test_module_switcher_hides_fleet_without_fleet_access():
    html = _render_module_switcher(["self_service", "expense"])

    assert 'data-module-key="fleet"' not in html
    assert 'href="/fleet"' not in html


def test_module_switcher_does_not_repeat_current_module():
    html = _render_module_switcher(["self_service", "fleet"], current_module="fleet")

    assert 'data-module-key="fleet"' not in html
