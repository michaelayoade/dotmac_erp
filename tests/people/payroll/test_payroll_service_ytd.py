from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.people.payroll.payroll_service import PayrollService


def _make_query(return_rows):
    query = MagicMock()
    query.select_from.return_value = query
    query.join.return_value = query
    query.outerjoin.return_value = query
    query.filter.return_value = query
    query.group_by.return_value = query
    query.order_by.return_value = query
    query.all.return_value = return_rows
    return query


def test_get_payroll_ytd_report_aggregates_totals_and_names():
    base_rows = [
        SimpleNamespace(
            employee_id="emp-1",
            employee_code="EMP001",
            employee_name="Ada Lovelace",
            department_name="Engineering",
            slip_count=1,
            total_gross=Decimal("1000.00"),
            total_deductions=Decimal("100.00"),
            total_net=Decimal("900.00"),
        ),
        SimpleNamespace(
            employee_id="emp-2",
            employee_code="EMP002",
            employee_name="Grace Hopper",
            department_name=None,
            slip_count=2,
            total_gross=Decimal("2000.00"),
            total_deductions=Decimal("250.00"),
            total_net=Decimal("1750.00"),
        ),
    ]

    deduction_rows = [
        SimpleNamespace(
            employee_id="emp-1", component_code="PAYE", total_amount=Decimal("50.00")
        ),
        SimpleNamespace(
            employee_id="emp-1", component_code="PENSION", total_amount=Decimal("30.00")
        ),
        SimpleNamespace(
            employee_id="emp-2", component_code="NHF", total_amount=Decimal("20.00")
        ),
    ]

    db = MagicMock()
    db.query.side_effect = [
        _make_query(base_rows),
        _make_query(deduction_rows),
    ]

    service = PayrollService(db)
    result = service.get_payroll_ytd_report("org-1", year=2026)

    assert result["totals"]["total_gross"] == Decimal("3000.00")
    assert result["totals"]["total_deductions"] == Decimal("350.00")
    assert result["totals"]["total_net"] == Decimal("2650.00")
    assert result["totals"]["total_paye"] == Decimal("50.00")
    assert result["totals"]["total_pension"] == Decimal("30.00")
    assert result["totals"]["total_nhf"] == Decimal("20.00")
    assert result["totals"]["slip_count"] == 3

    assert result["rows"][0]["employee_name"] == "Ada Lovelace"
    assert result["rows"][1]["employee_name"] == "Grace Hopper"
