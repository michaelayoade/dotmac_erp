"""Contract and parser for employee advanced filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from app.models.people.hr.employee import EmployeeStatus

SUPPORTED_OPERATORS = {
    "=",
    "!=",
    "like",
    "not like",
    "in",
    "not in",
    ">",
    "<",
    ">=",
    "<=",
    "is",
    "is not",
}

DOCTYPE_ALIASES = {
    "employee": "Employee",
    "employees": "Employee",
    "Employee": "Employee",
}


@dataclass(frozen=True)
class FilterFieldSpec:
    """Allowed filter field metadata."""

    model: Literal["employee", "person"]
    column: str
    value_type: Literal["uuid", "date", "string", "enum", "bool"]
    operators: set[str]
    enum_values: set[str] | None = None


FILTER_SCHEMA: dict[str, FilterFieldSpec] = {
    "employee_code": FilterFieldSpec(
        model="employee",
        column="employee_code",
        value_type="string",
        operators={"=", "!=", "like", "not like", "in", "not in"},
    ),
    "status": FilterFieldSpec(
        model="employee",
        column="status",
        value_type="enum",
        operators={"=", "!=", "in", "not in", "is", "is not"},
        enum_values={status.value for status in EmployeeStatus},
    ),
    "department_id": FilterFieldSpec(
        model="employee",
        column="department_id",
        value_type="uuid",
        operators={"=", "!=", "in", "not in", "is", "is not"},
    ),
    "designation_id": FilterFieldSpec(
        model="employee",
        column="designation_id",
        value_type="uuid",
        operators={"=", "!=", "in", "not in", "is", "is not"},
    ),
    "employment_type_id": FilterFieldSpec(
        model="employee",
        column="employment_type_id",
        value_type="uuid",
        operators={"=", "!=", "in", "not in", "is", "is not"},
    ),
    "reports_to_id": FilterFieldSpec(
        model="employee",
        column="reports_to_id",
        value_type="uuid",
        operators={"=", "!=", "in", "not in", "is", "is not"},
    ),
    "expense_approver_id": FilterFieldSpec(
        model="employee",
        column="expense_approver_id",
        value_type="uuid",
        operators={"=", "!=", "in", "not in", "is", "is not"},
    ),
    "date_of_joining": FilterFieldSpec(
        model="employee",
        column="date_of_joining",
        value_type="date",
        operators={"=", "!=", ">", "<", ">=", "<=", "is", "is not"},
    ),
    "date_of_leaving": FilterFieldSpec(
        model="employee",
        column="date_of_leaving",
        value_type="date",
        operators={"=", "!=", ">", "<", ">=", "<=", "is", "is not"},
    ),
    "is_deleted": FilterFieldSpec(
        model="employee",
        column="is_deleted",
        value_type="bool",
        operators={"=", "!=", "is", "is not"},
    ),
    "first_name": FilterFieldSpec(
        model="person",
        column="first_name",
        value_type="string",
        operators={"=", "!=", "like", "not like", "in", "not in"},
    ),
    "last_name": FilterFieldSpec(
        model="person",
        column="last_name",
        value_type="string",
        operators={"=", "!=", "like", "not like", "in", "not in"},
    ),
    "email": FilterFieldSpec(
        model="person",
        column="email",
        value_type="string",
        operators={"=", "!=", "like", "not like", "in", "not in"},
    ),
}


@dataclass(frozen=True)
class FilterTerm:
    """Single [doctype, field, operator, value] term."""

    doctype: str
    field: str
    operator: str
    value: Any


@dataclass(frozen=True)
class FilterOrGroup:
    """OR group containing terms."""

    terms: list[FilterTerm]


@dataclass(frozen=True)
class FilterExpression:
    """Parsed filter expression tree."""

    doctype: str
    terms: list[FilterTerm | FilterOrGroup]

    @classmethod
    def parse_payload(cls, payload: Any) -> FilterExpression:
        """Parse and validate raw payload."""
        if not isinstance(payload, list):
            raise ValueError("filters payload must be a JSON array")

        parsed_terms: list[FilterTerm | FilterOrGroup] = []
        doctype_seen: str | None = None

        for item in payload:
            if isinstance(item, dict) and "or" in item:
                or_value = item.get("or")
                if not isinstance(or_value, list) or not or_value:
                    raise ValueError("or group must be a non-empty array of terms")
                or_terms: list[FilterTerm] = []
                for raw_term in or_value:
                    term = _parse_term(raw_term)
                    doctype_seen = _ensure_single_doctype(doctype_seen, term.doctype)
                    or_terms.append(term)
                parsed_terms.append(FilterOrGroup(terms=or_terms))
                continue

            term = _parse_term(item)
            doctype_seen = _ensure_single_doctype(doctype_seen, term.doctype)
            parsed_terms.append(term)

        if doctype_seen is None:
            raise ValueError("filters payload cannot be empty")

        return cls(doctype=doctype_seen, terms=parsed_terms)


def _ensure_single_doctype(current: str | None, incoming: str) -> str:
    """Reject mixed doctypes in one payload."""
    if current is None:
        return incoming
    if current != incoming:
        raise ValueError("mixed doctypes are not allowed in one filters payload")
    return current


def _normalize_doctype(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("doctype must be a string")
    normalized = DOCTYPE_ALIASES.get(value.strip(), DOCTYPE_ALIASES.get(value))
    if not normalized:
        raise ValueError(f"unsupported doctype: {value}")
    if normalized != "Employee":
        raise ValueError(f"unsupported doctype: {value}")
    return normalized


def _parse_term(raw: Any) -> FilterTerm:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(
            "each filter term must be a 4-item array: [doctype, field, operator, value]"
        )

    doctype_raw, field_raw, operator_raw, value = raw
    doctype = _normalize_doctype(doctype_raw)
    if not isinstance(field_raw, str):
        raise ValueError("field must be a string")
    field = field_raw.strip()
    if field not in FILTER_SCHEMA:
        raise ValueError(f"unsupported field: {field}")

    if not isinstance(operator_raw, str):
        raise ValueError("operator must be a string")
    operator = operator_raw.strip().lower()
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported operator: {operator_raw}")

    spec = FILTER_SCHEMA[field]
    if operator not in spec.operators:
        raise ValueError(f"operator {operator} is not allowed for field {field}")

    _validate_value_compatibility(field, spec, operator, value)
    return FilterTerm(doctype=doctype, field=field, operator=operator, value=value)


def _validate_value_compatibility(
    field: str,
    spec: FilterFieldSpec,
    operator: str,
    value: Any,
) -> None:
    if operator in {"in", "not in"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"field {field} with operator {operator} requires a list")
        for item in value:
            _validate_scalar(field, spec, item, allow_null=False)
        return

    if operator in {"is", "is not"}:
        if value is None:
            return
        if spec.value_type == "bool" and isinstance(value, bool):
            return
        raise ValueError(
            f"field {field} with operator {operator} supports null"
            " (and bool for boolean fields)"
        )

    _validate_scalar(field, spec, value, allow_null=False)


def _validate_scalar(
    field: str,
    spec: FilterFieldSpec,
    value: Any,
    *,
    allow_null: bool,
) -> None:
    if value is None:
        if allow_null:
            return
        raise ValueError(f"field {field} does not allow null for this operator")

    if spec.value_type == "uuid":
        try:
            UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"field {field} expects a UUID value") from exc
        return

    if spec.value_type == "date":
        _parse_date(value, field)
        return

    if spec.value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"field {field} expects a boolean value")
        return

    if spec.value_type == "enum":
        if not isinstance(value, str):
            raise ValueError(f"field {field} expects a string value")
        enum_values = spec.enum_values or set()
        if value.upper() not in enum_values:
            raise ValueError(f"unsupported enum value for field {field}: {value}")
        return

    if spec.value_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"field {field} expects a string value")
        return

    raise ValueError(f"unsupported field type for {field}")


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise ValueError(f"field {field} expects an ISO date string")
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        try:
            return datetime.fromisoformat(raw).date()
        except ValueError as exc:
            raise ValueError(f"field {field} expects an ISO date string") from exc
