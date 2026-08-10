"""The amount_paid reconciler, and the rules that were buried in its script.

`ar.payment_allocation` is the authority; `invoice.amount_paid` is the
projection being repaired. Three rules governed that repair and none were
visible outside a 206-line script: it only repairs upward, VOID/DRAFT are
excluded, and a write happens only when something actually changes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from tests._helpers.source_introspection import (
    calls_named,
    mentions_in_code,
    module_level_assignments,
)

from app.services.finance.ar.amount_paid_reconciler import (
    EXCLUDED_STATUSES,
    ReconcileResult,
    StaleInvoice,
    find_stale_amount_paid,
    reconcile_amount_paid,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reconcile_invoice_amount_paid.py"
SERVICE = (
    REPO_ROOT / "app" / "services" / "finance" / "ar" / "amount_paid_reconciler.py"
)
ORG = uuid.uuid4()
TODAY = dt.date(2026, 8, 10)


def _row(
    total="100.00", paid="40.00", alloc="100.00", status="PARTIALLY_PAID", due=None
):
    return (
        uuid.uuid4(),
        "INV-001",
        Decimal(total),
        Decimal(paid),
        status,
        Decimal(alloc),
        due,
    )


def _db(rows=()):
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = list(rows)
    return db


# --------------------------------------------------------------------------
# Tenancy — this one WRITES, so an unscoped run is worse than a read
# --------------------------------------------------------------------------


def test_the_select_binds_the_organization():
    db = _db()
    find_stale_amount_paid(db, organization_id=ORG)
    assert db.execute.call_args[0][1]["org_id"] == str(ORG)


def test_the_update_is_also_scoped_to_the_organization():
    """Belt and braces: the row is addressed by primary key, but the UPDATE
    still carries organization_id so a wrong id cannot reach another tenant."""
    sql = SERVICE.read_text(encoding="utf-8")
    assert "UPDATE ar.invoice" in sql
    assert "AND organization_id = :org_id" in sql


def test_the_update_actually_runs_with_the_org_bound():
    db = _db([_row()])
    reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=False)
    update_params = db.execute.call_args_list[-1][0][1]
    assert update_params["org_id"] == str(ORG)


# --------------------------------------------------------------------------
# Money stays Decimal
# --------------------------------------------------------------------------


def test_the_new_amount_is_bound_as_decimal_not_float():
    """The old UPDATE bound `float(new_amount_paid)` — an exact Decimal
    round-tripped through binary floating point on its way into a numeric
    column."""
    db = _db([_row(alloc="100.00")])
    reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=False)
    new_paid = db.execute.call_args_list[-1][0][1]["new_paid"]
    assert isinstance(new_paid, Decimal)
    assert not isinstance(new_paid, float)


def test_no_float_call_survives_in_the_service():
    assert calls_named(SERVICE, "float") == []


# --------------------------------------------------------------------------
# The three rules
# --------------------------------------------------------------------------


def test_void_and_draft_are_excluded():
    assert set(EXCLUDED_STATUSES) == {"VOID", "DRAFT"}


def test_it_only_repairs_upward():
    """A reconciler that could also reduce amount_paid would fight the payment
    path, where every in-flight allocation looks like an over-count until it
    commits. The asymmetry lives in the candidate query."""
    sql = SERVICE.read_text(encoding="utf-8")
    assert "alloc.alloc_sum > i.amount_paid + :dust" in sql
    assert "i.amount_paid > alloc.alloc_sum" not in sql


def test_nothing_is_written_when_nothing_changes():
    """A correction within dust AND an unchanged status means no UPDATE —
    otherwise every run rewrites every row it looks at and destroys the
    diagnostic value of updated_at."""
    db = _db([_row(total="100.00", paid="100.00", alloc="100.00", status="PAID")])
    result = reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=False)
    assert result.examined == 1
    assert result.updated == 0
    # one execute for the SELECT, none for an UPDATE
    assert db.execute.call_count == 1


def test_a_status_change_alone_is_enough_to_write():
    """Even with the amount already correct, a stale status must be fixed."""
    db = _db(
        [_row(total="100.00", paid="100.00", alloc="100.00", status="PARTIALLY_PAID")]
    )
    result = reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=False)
    assert result.updated == 1
    assert result.status_changes == {"PARTIALLY_PAID -> PAID": 1}


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing():
    db = _db([_row()])
    result = reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=True)
    assert result.updated == 1  # it would have updated
    assert db.execute.call_count == 1  # but only the SELECT ran


def test_the_service_never_commits():
    db = _db([_row()])
    reconcile_amount_paid(db, organization_id=ORG, today=TODAY, dry_run=False)
    db.commit.assert_not_called()


def test_the_month_range_rolls_over_december_correctly():
    db = _db()
    find_stale_amount_paid(db, organization_id=ORG, month="2026-12")
    params = db.execute.call_args[0][1]
    assert params["month_start"] == "2026-12-01"
    assert params["month_end"] == "2027-01-01"


def test_a_mid_year_month_range():
    db = _db()
    find_stale_amount_paid(db, organization_id=ORG, month="2026-01")
    params = db.execute.call_args[0][1]
    assert params["month_start"] == "2026-01-01"
    assert params["month_end"] == "2026-02-01"


def test_no_month_means_no_date_bounds():
    db = _db()
    find_stale_amount_paid(db, organization_id=ORG)
    params = db.execute.call_args[0][1]
    assert params["month_start"] is None and params["month_end"] is None


def test_one_clock_for_the_whole_run():
    """Injected, so an invoice due today cannot resolve differently depending
    on where in the batch it fell."""
    db = _db(
        [
            _row(
                total="100",
                paid="0",
                alloc="0",
                status="POSTED",
                due=dt.date(2026, 8, 9),
            )
        ]
    )
    result = reconcile_amount_paid(
        db, organization_id=ORG, today=dt.date(2026, 8, 10), dry_run=True
    )
    assert result.status_changes == {"POSTED -> OVERDUE": 1}


def test_correction_is_derived_not_stored():
    invoice = StaleInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-1",
        total_amount=Decimal("100"),
        current_amount_paid=Decimal("40"),
        current_status="PARTIALLY_PAID",
        allocation_total=Decimal("90"),
        due_date=None,
    )
    assert invoice.correction == Decimal("50")


def test_result_defaults_are_zero():
    r = ReconcileResult()
    assert (r.examined, r.updated) == (0, 0)
    assert r.total_correction == Decimal("0")
    assert r.status_changes == {}


# --------------------------------------------------------------------------
# Regression guards on the script
# --------------------------------------------------------------------------


def test_the_script_requires_an_org_id_and_no_longer_owns_the_sql():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--org-id"' in source and "required=True" in source
    assert "session_for_org" in source and "batch_operation(" in source
    assert calls_named(SCRIPT, "text") == []
    assert mentions_in_code(SCRIPT, "SessionLocal") == []
    assert mentions_in_code(SCRIPT, "UPDATE ar.invoice") == []


def test_the_script_hardcodes_no_organization():
    assert "ORG_ID" not in module_level_assignments(SCRIPT)
