"""The AP posting backlog, extracted from a script into a service.

Also guards the three defects the extraction removed, because each of them is
the kind of thing that creeps back into a maintenance script.
"""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.finance.ap.posting_backlog import (
    POSTABLE_STATUSES,
    PostingBacklogResult,
    post_unposted_invoices,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "post_unposted_ap_invoices.py"
ORG = uuid.uuid4()
ACTOR = uuid.uuid4()


def _invoice(total="100.00"):
    inv = MagicMock()
    inv.invoice_id = uuid.uuid4()
    inv.invoice_number = "BILL-001"
    inv.total_amount = Decimal(total)
    inv.created_by_user_id = None
    inv.journal_entry_id = None
    return inv


def _db(invoices):
    db = MagicMock()
    db.scalars.return_value.all.return_value = invoices
    return db


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_dry_run_posts_nothing():
    db = _db([_invoice()])
    with patch(
        "app.services.finance.ap.ap_posting_adapter.APPostingAdapter"
    ) as adapter:
        result = post_unposted_invoices(
            db, organization_id=ORG, fallback_user_id=ACTOR, dry_run=True
        )
    adapter.post_invoice.assert_not_called()
    assert result.found == 1
    assert result.posted == 0


def test_a_successful_posting_links_the_journal_entry():
    inv = _invoice()
    journal_id = uuid.uuid4()
    db = _db([inv])
    outcome = MagicMock(success=True, journal_entry_id=journal_id)
    with patch(
        "app.services.finance.ap.ap_posting_adapter.APPostingAdapter"
    ) as adapter:
        adapter.post_invoice.return_value = outcome
        result = post_unposted_invoices(
            db, organization_id=ORG, fallback_user_id=ACTOR, dry_run=False
        )
    assert inv.journal_entry_id == journal_id
    assert result.posted == 1


def test_a_failing_invoice_does_not_abort_the_batch():
    good, bad = _invoice(), _invoice()
    db = _db([bad, good])
    with patch(
        "app.services.finance.ap.ap_posting_adapter.APPostingAdapter"
    ) as adapter:
        adapter.post_invoice.side_effect = [
            RuntimeError("posting blew up"),
            MagicMock(success=True, journal_entry_id=uuid.uuid4()),
        ]
        result = post_unposted_invoices(
            db, organization_id=ORG, fallback_user_id=ACTOR, dry_run=False
        )
    assert result.errors == 1
    assert result.posted == 1


def test_the_service_never_commits():
    """The caller owns the transaction — that is what lets the task, the CLI
    and any future admin action share one implementation."""
    db = _db([])
    post_unposted_invoices(
        db, organization_id=ORG, fallback_user_id=ACTOR, dry_run=False
    )
    db.commit.assert_not_called()


def test_only_ledger_accepted_statuses_are_postable():
    assert set(POSTABLE_STATUSES) == {
        SupplierInvoiceStatus.APPROVED,
        SupplierInvoiceStatus.POSTED,
        SupplierInvoiceStatus.PAID,
        SupplierInvoiceStatus.PARTIALLY_PAID,
    }
    assert SupplierInvoiceStatus.DRAFT not in POSTABLE_STATUSES
    assert SupplierInvoiceStatus.VOID not in POSTABLE_STATUSES


def test_result_defaults_are_zero_not_none():
    r = PostingBacklogResult()
    assert (r.found, r.posted, r.skipped, r.errors) == (0, 0, 0, 0)
    assert r.total_amount == Decimal("0")


# --------------------------------------------------------------------------
# Regression guards on what the extraction removed
# --------------------------------------------------------------------------


def test_the_script_no_longer_hardcodes_an_organization():
    """It carried `ORG_ID = UUID("0000...0001")` at module level, so a
    multi-tenant system had a maintenance tool that served exactly one
    tenant."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    module_level_names = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert "ORG_ID" not in module_level_names


def test_the_script_requires_an_org_id_argument():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--org-id"' in source
    assert "required=True" in source


def _executable_strings(path: Path) -> list[str]:
    """Every string literal in the file EXCEPT docstrings.

    The script's own docstring describes the defect it used to have, so a
    plain substring search over the source would match the documentation and
    fail forever. Only executable code is the subject here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_the_script_does_not_set_the_rls_guc_by_interpolation():
    """It ran `SET app.current_organization_id = '{ORG_ID}'` through an
    f-string: string-interpolated SQL, and only one of the two isolation
    layers. `session_for_org` sets both."""
    assert not [
        s for s in _executable_strings(SCRIPT) if "current_organization_id" in s
    ]
    assert "session_for_org" in SCRIPT.read_text(encoding="utf-8")


def test_the_script_issues_no_raw_sql_at_all():
    """The GUC was only half of it — the script also owned its own queries.
    Selection now lives in the service, so no `text(...)` should remain."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    raw_sql = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "text"
    ]
    assert raw_sql == []


def test_the_script_records_a_batch_operation():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "batch_operation(" in source
