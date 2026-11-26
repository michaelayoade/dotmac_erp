from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.models.fleet.enums import VehicleStatus
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


class _FakeExecuteResult:
    def __init__(
        self,
        *,
        all_result=None,
        one_result=None,
        scalar_one_result=None,
    ):
        self._all_result = all_result if all_result is not None else []
        self._one_result = one_result
        self._scalar_one_result = scalar_one_result

    def all(self):
        return self._all_result

    def one(self):
        return self._one_result

    def scalar_one(self):
        return self._scalar_one_result


class _FakeScalarsResult:
    def __init__(self, values=None):
        self._values = values if values is not None else []

    def all(self):
        return self._values


class _FakeDb:
    def __init__(
        self, report_rows=None, filtered_report_rows=None, report_vehicles=None
    ):
        self.executed = []
        self.scalars_calls = []
        self.report_rows = report_rows if report_rows is not None else []
        self.filtered_report_rows = filtered_report_rows
        self.report_vehicles = report_vehicles if report_vehicles is not None else []

    def get_bind(self):
        return object()

    def execute(self, stmt):
        self.executed.append(stmt)
        stmt_text = str(stmt)
        if "GROUP BY" in stmt_text:
            rows = self.report_rows
            if self.filtered_report_rows is not None and (
                "claim_date >=" in stmt_text or "invoice_date >=" in stmt_text
            ):
                rows = self.filtered_report_rows
            return _FakeExecuteResult(all_result=rows)
        if "count(distinct(expense.expense_claim.claim_id))" in stmt_text:
            return _FakeExecuteResult(
                one_result=SimpleNamespace(
                    claim_count=0,
                    total_amount=Decimal("0"),
                )
            )
        if "count(*)" in stmt_text:
            return _FakeExecuteResult(scalar_one_result=0)
        return _FakeExecuteResult(all_result=[])

    def scalars(self, stmt):
        self.scalars_calls.append(stmt)
        if "FROM fleet.vehicle" in str(stmt):
            vehicle_ids = set(stmt.compile().params["vehicle_id_1"])
            excludes_disposed = "fleet.vehicle.status !=" in str(stmt)
            return _FakeScalarsResult(
                [
                    v
                    for v in self.report_vehicles
                    if v.vehicle_id in vehicle_ids
                    and (not excludes_disposed or v.status != VehicleStatus.DISPOSED)
                ]
            )
        return _FakeScalarsResult([])


class _InspectorStub:
    def has_table(self, name, schema=None):
        return True


class _VehicleServiceStub:
    vehicle_id = uuid4()

    def __init__(self, db, organization_id):
        self.db = db
        self.organization_id = organization_id

    def list_vehicles(self, params):
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    vehicle_id=self.vehicle_id,
                    registration_number="ABC-123",
                    make="Toyota",
                    model="Hilux",
                )
            ]
        )

    def get_or_raise(self, vehicle_id):
        return SimpleNamespace(
            vehicle_id=vehicle_id,
            registration_number="ABC-123",
            make="Toyota",
            model="Hilux",
            assigned_employee=None,
            assigned_department_name=None,
        )


def test_reports_expenses_context_filters_to_paid_claims(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.inspect", lambda bind: _InspectorStub()
    )
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.VehicleService",
        _VehicleServiceStub,
    )

    service = FleetWebService(db)
    context = service.reports_expenses_context(TEST_ORG_ID)

    assert context["vehicle_rows"] == []
    assert context["total_amount"] == Decimal("0")

    expense_stmt = next(
        stmt
        for stmt in db.executed
        if "expense.expense_claim.vehicle_id" in str(stmt)
        and "count(distinct(expense.expense_claim.claim_id))" in str(stmt)
    )
    params = expense_stmt.compile().params

    assert "expense.expense_claim.status = :status_1" in str(expense_stmt)
    assert ExpenseClaimStatus.PAID in params.values()
    assert (
        tuple(params["category_name_1"])
        == FleetWebService.REPORT_EXPENSE_CATEGORY_NAMES
    )


@pytest.mark.parametrize(
    ("method_name", "date_column"),
    [
        ("reports_expenses_context", "expense.expense_claim.claim_date"),
        ("reports_invoices_context", "ap.supplier_invoice.invoice_date"),
    ],
)
def test_report_summary_uses_date_range_and_matches_listed_vehicle_totals(
    monkeypatch, method_name, date_column
):
    other_vehicle_id = uuid4()
    disposed_vehicle_id = uuid4()
    db = _FakeDb(
        report_rows=[
            (_VehicleServiceStub.vehicle_id, 2, Decimal("12.50")),
            (other_vehicle_id, 1, Decimal("7.25")),
            (disposed_vehicle_id, 1, Decimal("9.99")),
        ],
        filtered_report_rows=[
            (_VehicleServiceStub.vehicle_id, 1, Decimal("5.00")),
            (disposed_vehicle_id, 1, Decimal("9.99")),
        ],
        report_vehicles=[
            SimpleNamespace(
                vehicle_id=vehicle_id,
                registration_number=registration_number,
                make="Toyota",
                model="Hilux",
                status=status,
            )
            for vehicle_id, registration_number, status in [
                (_VehicleServiceStub.vehicle_id, "ABC-123", VehicleStatus.ACTIVE),
                (other_vehicle_id, "DEF-456", VehicleStatus.ACTIVE),
                (disposed_vehicle_id, "GHI-789", VehicleStatus.DISPOSED),
            ]
        ],
    )
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.inspect", lambda bind: _InspectorStub()
    )
    service = FleetWebService(db)
    unfiltered = getattr(service, method_name)(TEST_ORG_ID)
    context = getattr(service, method_name)(
        TEST_ORG_ID, start_date=date(2026, 4, 1), end_date=date(2026, 4, 30)
    )

    assert unfiltered["total_amount"] == Decimal("19.75")
    assert unfiltered["filter_start_date"] == ""
    assert unfiltered["filter_end_date"] == ""
    assert context["total_amount"] == Decimal("5.00")
    assert len(unfiltered["vehicle_rows"]) == 2
    assert len(context["vehicle_rows"]) == 1
    assert (
        sum(row["total_amount"] for row in context["vehicle_rows"])
        == context["total_amount"]
    )
    assert context["vehicle_rows"][0]["total_amount"] == Decimal("5.00")
    assert context["filter_start_date"] == "2026-04-01"
    assert context["filter_end_date"] == "2026-04-30"
    statement = db.executed[1]
    assert f"{date_column} >= :" in str(statement)
    assert f"{date_column} <= :" in str(statement)
    assert date(2026, 4, 1) in statement.compile().params.values()
    assert date(2026, 4, 30) in statement.compile().params.values()
    vehicle_statement = db.scalars_calls[1]
    assert " LIMIT " not in str(vehicle_statement)
    assert "fleet.vehicle.status !=" in str(vehicle_statement)


def test_reports_expense_vehicle_context_filters_all_queries_to_paid_claims(
    monkeypatch,
):
    db = _FakeDb()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.inspect", lambda bind: _InspectorStub()
    )
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.VehicleService",
        _VehicleServiceStub,
    )

    service = FleetWebService(db)
    service.reports_expense_vehicle_context(TEST_ORG_ID, uuid4())

    statements = db.executed + db.scalars_calls
    expense_statements = [
        stmt for stmt in statements if "expense.expense_claim" in str(stmt)
    ]

    assert expense_statements
    for stmt in expense_statements:
        stmt_text = str(stmt)
        params = stmt.compile().params
        status_params = {
            key: value for key, value in params.items() if key.startswith("status_")
        }
        category_values = [
            value for key, value in params.items() if key.startswith("category_name_")
        ]

        assert "expense.expense_claim.status = :" in stmt_text
        assert ExpenseClaimStatus.PAID in status_params.values()
        assert FleetWebService.REPORT_EXPENSE_CATEGORY_NAMES in [
            tuple(value) for value in category_values
        ]
