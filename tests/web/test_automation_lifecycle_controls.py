"""Automation lifecycle controls remain explicit and non-destructive."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _template(name: str) -> str:
    return (REPO_ROOT / "templates" / "admin" / "automation" / name).read_text(
        encoding="utf-8"
    )


def test_workflow_controls_archive_restore_and_toggle() -> None:
    detail = _template("workflow_detail.html")
    listing = _template("workflow_list.html")

    assert "/archive" in detail
    assert "/restore" in detail
    assert "/toggle" in detail
    assert "/workflows/archived" in listing
    assert "/workflows/{{ rule.rule_id }}/delete" not in detail


def test_custom_field_controls_deactivate_and_reactivate() -> None:
    detail = _template("field_detail.html")

    assert "/deactivate" in detail
    assert "/reactivate" in detail
    assert "/fields/{{ field.field_id }}/delete" not in detail


def test_mutation_routes_pass_current_organization_scope() -> None:
    routes = (REPO_ROOT / "app" / "web" / "automation.py").read_text(encoding="utf-8")

    assert "workflow_service.archive(" in routes
    assert "workflow_service.restore(" in routes
    assert "custom_fields_service.deactivate(" in routes
    assert "custom_fields_service.reactivate(" in routes
    assert routes.count("auth.organization_id") >= 10
