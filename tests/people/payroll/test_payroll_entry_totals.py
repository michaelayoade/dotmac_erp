from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.people.payroll.payroll_totals import (
    recalculate_payroll_entry_totals,
)


def test_recalculate_flushes_pending_slip_link_and_scopes_query_to_tenant() -> None:
    organization_id = uuid4()
    entry_id = uuid4()
    entry = SimpleNamespace(
        organization_id=organization_id,
        entry_id=entry_id,
    )
    slips = [
        SimpleNamespace(
            gross_pay=Decimal("100"),
            total_deduction=Decimal("25"),
            net_pay=Decimal("75"),
        ),
        SimpleNamespace(
            gross_pay=Decimal("200"),
            total_deduction=Decimal("50"),
            net_pay=Decimal("150"),
        ),
    ]
    db = MagicMock()
    db.scalars.return_value.all.return_value = slips

    result = recalculate_payroll_entry_totals(
        db,
        organization_id,
        entry_id,
        entry=entry,
    )

    assert result is entry
    assert entry.employee_count == 2
    assert entry.total_gross_pay == Decimal("300")
    assert entry.total_deductions == Decimal("75")
    assert entry.total_net_pay == Decimal("225")
    assert db.flush.call_count == 2

    statement = db.scalars.call_args.args[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "payroll.salary_slip.organization_id" in compiled
    assert str(organization_id) in compiled
    assert "payroll.salary_slip.payroll_entry_id" in compiled
    assert str(entry_id) in compiled
