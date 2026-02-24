"""Tests for employee advanced filter contract and engine."""

import uuid

import pytest
from sqlalchemy import select

from app.models.people.hr import Employee
from app.services.people.hr.employee_filter_contract import FilterExpression
from app.services.people.hr.employee_filter_engine import (
    apply_employee_filter_expression,
    parse_employee_filter_payload_json,
)


def test_parse_employee_filter_payload_json_valid() -> None:
    payload = (
        '[["employees","status","=","ACTIVE"],'
        '{"or":[["Employee","department_id","is",null],'
        '["Employee","department_id","=",'
        '"00000000-0000-0000-0000-000000000001"]]}]'
    )

    expression = parse_employee_filter_payload_json(payload)

    assert expression is not None
    assert expression.doctype == "Employee"
    assert len(expression.terms) == 2


def test_parse_employee_filter_payload_json_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid filters JSON payload"):
        parse_employee_filter_payload_json("{not json}")


def test_parse_employee_filter_payload_json_rejects_mixed_doctypes() -> None:
    with pytest.raises(ValueError, match="unsupported doctype"):
        FilterExpression.parse_payload(
            [
                ["Employee", "status", "=", "ACTIVE"],
                ["Ticket", "status", "=", "open"],
            ]
        )


def test_parse_employee_filter_payload_json_rejects_bad_field() -> None:
    with pytest.raises(ValueError, match="unsupported field"):
        FilterExpression.parse_payload([["Employee", "unknown_field", "=", "x"]])


def test_apply_employee_filter_expression_joins_person_for_person_fields() -> None:
    expression = FilterExpression.parse_payload(
        [["Employee", "email", "like", "%@example.com"]]
    )

    stmt, joined_person = apply_employee_filter_expression(select(Employee), expression)

    assert joined_person is True
    sql = str(stmt)
    assert "JOIN" in sql
    assert "people" in sql


def test_apply_employee_filter_expression_builds_in_predicate() -> None:
    department_id = str(uuid.uuid4())
    expression = FilterExpression.parse_payload(
        [["Employee", "department_id", "in", [department_id]]]
    )

    stmt, _ = apply_employee_filter_expression(select(Employee), expression)

    sql = str(stmt)
    assert " IN " in sql
