"""Template canaries for leave list filtering."""

import ast
from pathlib import Path


def _read_template(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_leave_allocations_results_target_exists_for_empty_results() -> None:
    """HTMX can replace the same target even when a search has no matches."""
    html = _read_template("templates/people/leave/allocations.html")

    assert html.count('id="results-container"') == 1
    assert html.index('id="results-container"') < html.index("{% if allocations %}")


def test_leave_applications_exposes_and_preserves_employee_search() -> None:
    """The filter card and pagination use the same employee search parameter."""
    html = _read_template("templates/people/leave/applications.html")

    assert "show_search=true" in html
    assert 'search_name="employee_search"' in html
    assert '"employee_search": employee_search' in html


def test_leave_types_pagination_preserves_active_status() -> None:
    """Paging a searched leave-type list retains the selected status."""
    html = _read_template("templates/people/leave/types.html")

    assert 'filters={"is_active": is_active}' in html


def test_leave_types_route_accepts_blank_active_status() -> None:
    """A blank HTML select value must not be rejected by FastAPI as a boolean."""
    source = _read_template("app/web/people/leave.py")
    module = ast.parse(source)
    route = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "leave_types"
    )
    is_active = next(arg for arg in route.args.args if arg.arg == "is_active")

    assert ast.unparse(is_active.annotation) == "str | None"
