"""Tier-A exact-match allocation, and the two defects the extraction fixed.

The load-bearing one is tenancy: the original query had **no organization
filter at all**, and the script opened a raw `SessionLocal()`, so neither the
ORM listener nor the RLS GUC bounded it either. Nothing at any layer scoped
it to a tenant.
"""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from tests._helpers.source_introspection import (
    calls_named,
    mentions_in_code,
)

from app.services.finance.ar.exact_match_allocation import (
    AMOUNT_TOLERANCE,
    OPEN_INVOICE_STATUSES,
    MatchCandidate,
    allocate_candidate,
    allocate_exact_matches,
    find_exact_match_candidates,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "allocate_exact_match_payments.py"
SERVICE = (
    REPO_ROOT / "app" / "services" / "finance" / "ar" / "exact_match_allocation.py"
)
ORG = uuid.uuid4()


def _db(rows=()):
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = list(rows)
    return db


def _candidate(amount="100.00", outstanding="100.00"):
    return MatchCandidate(
        payment_id=uuid.uuid4(),
        payment_number="PAY-001",
        payment_date=None,
        payment_amount=Decimal(amount),
        customer_id=uuid.uuid4(),
        customer_name="Acme",
        invoice_id=uuid.uuid4(),
        invoice_number="INV-001",
        invoice_total=Decimal("100.00"),
        invoice_outstanding=Decimal(outstanding),
    )


# --------------------------------------------------------------------------
# Tenancy — the defect that mattered
# --------------------------------------------------------------------------


def test_the_query_binds_the_organization():
    db = _db()
    find_exact_match_candidates(db, organization_id=ORG)
    params = db.execute.call_args[0][1]
    assert params["org_id"] == str(ORG)


def test_the_organization_filter_is_applied_on_every_table():
    """Payments, customers AND invoices. Filtering one leaves the join free to
    reach across tenants through the others."""
    sql = SERVICE.read_text(encoding="utf-8")
    assert "cp.organization_id = :org_id" in sql
    assert "c.organization_id = :org_id" in sql
    assert "i.organization_id = :org_id" in sql


def test_the_service_carries_its_own_scope_and_does_not_rely_on_rls_alone():
    """RLS is a second line of defence, never the only one — the original had
    neither."""
    assert "organization_id" in SERVICE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# No SQL built by string formatting
# --------------------------------------------------------------------------


def test_year_and_limit_are_bound_parameters_not_formatted_in():
    db = _db()
    find_exact_match_candidates(db, organization_id=ORG, year=2025, limit=50)
    params = db.execute.call_args[0][1]
    assert params["date_from"] == "2025-01-01"
    assert params["date_to"] == "2026-01-01"
    assert params["row_limit"] == 50


def test_no_year_means_no_date_bounds():
    db = _db()
    find_exact_match_candidates(db, organization_id=ORG)
    params = db.execute.call_args[0][1]
    assert params["date_from"] is None and params["date_to"] is None


def test_no_limit_binds_null_rather_than_dropping_the_clause():
    """`LIMIT NULL` is 'no limit' in Postgres, so the clause stays a bound
    parameter instead of being conditionally formatted back in."""
    db = _db()
    find_exact_match_candidates(db, organization_id=ORG)
    assert db.execute.call_args[0][1]["row_limit"] is None


def test_the_sql_is_a_single_module_level_statement_with_no_fstring():
    """A formatted query is the habit this replaced. There should be exactly
    one `text(...)` in the module and no f-string anywhere near it."""
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    joined = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    # f-strings are fine in log/error messages, not in the SQL constant.
    sql_assign = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "_CANDIDATE_SQL" for t in n.targets)
    ]
    assert len(sql_assign) == 1
    assert not [n for n in ast.walk(sql_assign[0]) if isinstance(n, ast.JoinedStr)]
    assert joined, "sanity: the module does use f-strings elsewhere"


# --------------------------------------------------------------------------
# Allocation behaviour
# --------------------------------------------------------------------------


def test_only_open_invoice_statuses_are_matched():
    assert set(OPEN_INVOICE_STATUSES) == {"POSTED", "OVERDUE", "PARTIALLY_PAID"}
    assert "PAID" not in OPEN_INVOICE_STATUSES
    assert "DRAFT" not in OPEN_INVOICE_STATUSES


def test_a_settled_invoice_is_skipped_not_over_allocated():
    """The live balance is re-read, so a second payment in the same batch
    cannot allocate against an invoice the first already settled."""
    db = MagicMock()
    invoice = MagicMock()
    invoice.total_amount = Decimal("100.00")
    invoice.amount_paid = Decimal("100.00")
    db.get.return_value = invoice
    assert allocate_candidate(db, _candidate()) is False
    db.add.assert_not_called()


def test_allocation_never_exceeds_the_live_outstanding():
    db = MagicMock()
    invoice = MagicMock()
    invoice.total_amount = Decimal("100.00")
    invoice.amount_paid = Decimal("70.00")
    db.get.return_value = invoice
    assert allocate_candidate(db, _candidate(amount="100.00")) is True
    allocation = db.add.call_args[0][0]
    assert allocation.allocated_amount == Decimal("30.00")


def test_dry_run_allocates_nothing():
    db = _db(
        [
            (
                uuid.uuid4(),
                "PAY-1",
                None,
                Decimal("50"),
                uuid.uuid4(),
                "Acme",
                uuid.uuid4(),
                "INV-1",
                Decimal("50"),
                Decimal("50"),
            )
        ]
    )
    result = allocate_exact_matches(db, organization_id=ORG, dry_run=True)
    assert result.candidates == 1
    assert result.allocated == 0
    db.add.assert_not_called()


def test_the_service_never_commits():
    db = _db()
    allocate_exact_matches(db, organization_id=ORG, dry_run=False)
    db.commit.assert_not_called()


def test_the_tolerance_is_sub_cent():
    assert Decimal("0.01") == AMOUNT_TOLERANCE


# --------------------------------------------------------------------------
# Regression guards on the script
# --------------------------------------------------------------------------


def test_the_script_requires_an_org_id_and_no_longer_owns_the_query():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--org-id"' in source and "required=True" in source
    assert "session_for_org" in source
    assert "batch_operation(" in source
    # Only the docstring may mention SessionLocal — it explains what changed.
    assert mentions_in_code(SCRIPT, "SessionLocal") == []


def test_the_script_issues_no_sql_of_its_own():
    assert calls_named(SCRIPT, "text") == []
