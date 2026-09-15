"""Regression coverage for the optional Assigned Branch employee export field."""

import csv
import re
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from jinja2 import Environment
from sqlalchemy.orm import Session

from app.services.people.hr.web.employee_web import HRWebService
from app.web.deps import WebAuthContext


def _export(monkeypatch, *, employees, locations=(), fields=None):
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=uuid4(),
        roles=["hr_manager"],
        scopes=["hr:access"],
    )
    db = MagicMock(spec=Session)
    db.execute.return_value.all.return_value = locations

    def _list(service, filters, pagination, *, eager_load=False):
        assert service.organization_id == auth.organization_id
        assert service.db is db
        assert eager_load is True
        return SimpleNamespace(items=employees)

    monkeypatch.setattr(
        "app.services.people.hr.web.employee_web.EmployeeService.list_employees",
        _list,
    )
    response = HRWebService().export_employees_csv_response(
        auth=auth,
        db=db,
        fields=fields if fields is not None else ["assigned_branch"],
    )
    rows = list(csv.reader(StringIO(response.body.decode())))
    return rows, db, auth


def test_assigned_branch_is_selectable_without_changing_export_defaults():
    fields = HRWebService.EMPLOYEE_EXPORT_FIELDS
    assert fields["assigned_branch"][0] == "Assigned Branch"
    assert HRWebService.DEFAULT_EMPLOYEE_EXPORT_FIELDS == (
        "employee_code",
        "full_name",
        "work_email",
        "department",
        "designation",
        "employment_type",
        "status",
        "date_of_joining",
    )

    template_path = (
        Path(__file__).resolve().parents[3] / "templates/people/hr/employees.html"
    )
    field_loop = re.search(
        r"{% for field in employee_export_fields %}.*?{% endfor %}",
        template_path.read_text(),
        re.DOTALL,
    )
    assert field_loop is not None
    rendered = Environment(autoescape=True).from_string(field_loop.group()).render(
        employee_export_fields=[
            {"key": key, "label": label} for key, (label, _) in fields.items()
        ],
        default_employee_export_fields=HRWebService.DEFAULT_EMPLOYEE_EXPORT_FIELDS,
    )
    checkbox = re.search(r'<input[^>]*value="assigned_branch"[^>]*>', rendered)
    assert checkbox is not None
    assert "checked" not in checkbox.group()
    assert "Assigned Branch" in rendered


def test_assigned_branch_export_matches_employees_and_preserves_field_order(monkeypatch):
    abuja_id, lagos_id, missing_id = uuid4(), uuid4(), uuid4()
    employees = [
        SimpleNamespace(employee_code="EMP-001", assigned_location_id=lagos_id),
        SimpleNamespace(employee_code="EMP-002", assigned_location_id=abuja_id),
        SimpleNamespace(employee_code="EMP-003", assigned_location_id=None),
        SimpleNamespace(employee_code="EMP-004", assigned_location_id=missing_id),
    ]
    rows, db, auth = _export(
        monkeypatch,
        employees=employees,
        locations=[
            SimpleNamespace(location_id=abuja_id, location_name="Abuja"),
            SimpleNamespace(location_id=lagos_id, location_name="Lagos"),
        ],
        fields=["assigned_branch", "employee_code"],
    )

    assert rows == [
        ["Assigned Branch", "Employee Code"],
        ["Lagos", "EMP-001"],
        ["Abuja", "EMP-002"],
        ["", "EMP-003"],
        ["", "EMP-004"],
    ]
    db.execute.assert_called_once()
    statement = db.execute.call_args.args[0]
    compiled = statement.compile()
    assert "core_org.location.organization_id =" in str(compiled)
    assert compiled.params["organization_id_1"] == auth.organization_id
    # Existing assignments to inactive branches must retain their names.
    assert "is_active" not in str(compiled)


@pytest.mark.parametrize(
    ("branch_name", "expected"),
    [
        ("Lagos", "Lagos"),
        ('Abuja, "Head Office"', 'Abuja, "Head Office"'),
        ("Ikeja – Lagos", "Ikeja – Lagos"),
        ("=1+1", "'=1+1"),
        ("+Branch", "'+Branch"),
        ("-Branch", "'-Branch"),
        ("@Branch", "'@Branch"),
    ],
)
def test_assigned_branch_export_uses_existing_csv_safety(
    monkeypatch, branch_name, expected
):
    location_id = uuid4()
    rows, _, _ = _export(
        monkeypatch,
        employees=[SimpleNamespace(assigned_location_id=location_id)],
        locations=[
            SimpleNamespace(location_id=location_id, location_name=branch_name)
        ],
    )
    assert rows == [["Assigned Branch"], [expected]]


def test_export_does_not_load_branches_when_field_is_not_selected(monkeypatch):
    rows, db, _ = _export(
        monkeypatch,
        employees=[SimpleNamespace(employee_code="EMP-001")],
        fields=["employee_code"],
    )
    assert rows == [["Employee Code"], ["EMP-001"]]
    db.execute.assert_not_called()


def test_assigned_branch_export_with_no_employees_is_header_only(monkeypatch):
    rows, db, _ = _export(monkeypatch, employees=[])
    assert rows == [["Assigned Branch"]]
    db.execute.assert_not_called()
