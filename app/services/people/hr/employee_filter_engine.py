"""Employee advanced filter parser and SQLAlchemy predicate builder."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.sql import Select

from app.models.people.hr import Employee
from app.models.people.hr.employee import EmployeeStatus
from app.models.person import Person

from .employee_filter_contract import (
    FILTER_SCHEMA,
    FilterExpression,
    FilterOrGroup,
    FilterTerm,
)


def parse_employee_filter_payload_json(
    filters: str | None,
) -> FilterExpression | None:
    """Parse `filters` JSON query param into a validated expression."""
    if not filters:
        return None
    try:
        payload = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid filters JSON payload") from exc
    return FilterExpression.parse_payload(payload)


def apply_employee_filter_expression(
    stmt: Select[Any],
    expression: FilterExpression | None,
) -> tuple[Select[Any], bool]:
    """Apply validated filter expression and return (stmt, joined_person)."""
    if expression is None:
        return stmt, False
    if expression.doctype != "Employee":
        raise ValueError("unsupported doctype in employee filter expression")

    joined_person = _requires_person_join(expression)
    if joined_person:
        stmt = stmt.join(Person, Employee.person_id == Person.id)

    predicates = []
    for item in expression.terms:
        if isinstance(item, FilterOrGroup):
            predicates.append(or_(*[_build_predicate(term) for term in item.terms]))
        else:
            predicates.append(_build_predicate(item))

    if predicates:
        stmt = stmt.where(and_(*predicates))
    return stmt, joined_person


def _requires_person_join(expression: FilterExpression) -> bool:
    for item in expression.terms:
        terms = item.terms if isinstance(item, FilterOrGroup) else [item]
        for term in terms:
            if FILTER_SCHEMA[term.field].model == "person":
                return True
    return False


def _build_predicate(term: FilterTerm):
    spec = FILTER_SCHEMA[term.field]
    model = Person if spec.model == "person" else Employee
    column = getattr(model, spec.column)
    operator = term.operator
    value = _coerce_value(spec.value_type, term.value, operator)

    if operator == "=":
        return column == value
    if operator == "!=":
        return column != value
    if operator == "like":
        return column.ilike(value)
    if operator == "not like":
        return ~column.ilike(value)
    if operator == "in":
        return column.in_(value)
    if operator == "not in":
        return column.notin_(value)
    if operator == ">":
        return column > value
    if operator == "<":
        return column < value
    if operator == ">=":
        return column >= value
    if operator == "<=":
        return column <= value
    if operator == "is":
        return column.is_(value)
    if operator == "is not":
        return column.is_not(value)

    raise ValueError(f"unsupported operator: {operator}")


def _coerce_value(value_type: str, value: Any, operator: str) -> Any:
    if operator in {"in", "not in"}:
        if not isinstance(value, list):
            raise ValueError(f"operator {operator} expects list value")
        return [_coerce_scalar(value_type, item) for item in value]
    if operator in {"is", "is not"} and value is None:
        return None
    return _coerce_scalar(value_type, value)


def _coerce_scalar(value_type: str, value: Any) -> Any:
    if value_type == "uuid":
        return UUID(str(value))

    if value_type == "date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if not isinstance(value, str):
            raise ValueError("date value must be an ISO date string")
        raw = value.strip()
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return datetime.fromisoformat(raw).date()

    if value_type == "enum":
        if not isinstance(value, str):
            raise ValueError("enum value must be a string")
        return EmployeeStatus(value.upper())

    if value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError("boolean value must be true/false")
        return value

    if value_type == "string":
        if not isinstance(value, str):
            raise ValueError("string value must be a string")
        return value

    raise ValueError(f"unsupported value type: {value_type}")
