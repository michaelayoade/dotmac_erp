"""The expense-claim posting backlog, extracted from a script into a service."""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.services.expense.posting_backlog import (
    POSTABLE_STATUSES,
    ExpensePostingResult,
    post_unposted_claims,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "post_unposted_expense_claims.py"
ORG = uuid.uuid4()
ACTOR = uuid.uuid4()


def _claim(*, created_by=None, approver=None, amount="100.00"):
    claim = MagicMock()
    claim.claim_id = uuid.uuid4()
    claim.claim_number = "EXP-001"
    claim.total_approved_amount = Decimal(amount)
    claim.created_by_id = created_by
    claim.approver_id = approver
    claim.journal_entry_id = None
    return claim


def _db(claims):
    db = MagicMock()
    db.scalars.return_value.all.return_value = claims
    return db


def test_approved_and_paid_are_both_postable():
    """Payment and GL posting are independent — a claim can be reimbursed
    before its journal is cut, which is exactly what this backlog catches."""
    assert set(POSTABLE_STATUSES) == {
        ExpenseClaimStatus.APPROVED,
        ExpenseClaimStatus.PAID,
    }
    assert ExpenseClaimStatus.DRAFT not in POSTABLE_STATUSES
    assert ExpenseClaimStatus.REJECTED not in POSTABLE_STATUSES


def test_dry_run_posts_nothing():
    with patch(
        "app.services.expense.expense_posting_adapter.ExpensePostingAdapter"
    ) as adapter:
        result = post_unposted_claims(
            _db([_claim()]), organization_id=ORG, fallback_user_id=ACTOR, dry_run=True
        )
    adapter.post_expense_claim.assert_not_called()
    assert result.found == 1
    assert result.posted == 0
    assert result.total_amount == Decimal("100.00")


def test_a_successful_posting_links_the_journal_entry():
    claim = _claim()
    journal_id = uuid.uuid4()
    with patch(
        "app.services.expense.expense_posting_adapter.ExpensePostingAdapter"
    ) as adapter:
        adapter.post_expense_claim.return_value = MagicMock(
            success=True, journal_entry_id=journal_id
        )
        result = post_unposted_claims(
            _db([claim]), organization_id=ORG, fallback_user_id=ACTOR, dry_run=False
        )
    assert claim.journal_entry_id == journal_id
    assert result.posted == 1


def test_attribution_prefers_the_creator_then_the_approver():
    """A backfill must not rewrite who was responsible for a claim just
    because it ran late."""
    creator, approver = uuid.uuid4(), uuid.uuid4()
    with patch(
        "app.services.expense.expense_posting_adapter.ExpensePostingAdapter"
    ) as adapter:
        adapter.post_expense_claim.return_value = MagicMock(
            success=True, journal_entry_id=uuid.uuid4()
        )
        post_unposted_claims(
            _db([_claim(created_by=creator, approver=approver)]),
            organization_id=ORG,
            fallback_user_id=ACTOR,
            dry_run=False,
        )
        assert (
            adapter.post_expense_claim.call_args.kwargs["posted_by_user_id"] is creator
        )

        adapter.reset_mock()
        adapter.post_expense_claim.return_value = MagicMock(
            success=True, journal_entry_id=uuid.uuid4()
        )
        post_unposted_claims(
            _db([_claim(approver=approver)]),
            organization_id=ORG,
            fallback_user_id=ACTOR,
            dry_run=False,
        )
        assert (
            adapter.post_expense_claim.call_args.kwargs["posted_by_user_id"] is approver
        )


def test_the_system_actor_is_only_the_last_resort():
    with patch(
        "app.services.expense.expense_posting_adapter.ExpensePostingAdapter"
    ) as adapter:
        adapter.post_expense_claim.return_value = MagicMock(
            success=True, journal_entry_id=uuid.uuid4()
        )
        post_unposted_claims(
            _db([_claim()]),
            organization_id=ORG,
            fallback_user_id=ACTOR,
            dry_run=False,
        )
    assert adapter.post_expense_claim.call_args.kwargs["posted_by_user_id"] is ACTOR


def test_a_failing_claim_does_not_abort_the_batch():
    with patch(
        "app.services.expense.expense_posting_adapter.ExpensePostingAdapter"
    ) as adapter:
        adapter.post_expense_claim.side_effect = [
            RuntimeError("boom"),
            MagicMock(success=True, journal_entry_id=uuid.uuid4()),
        ]
        result = post_unposted_claims(
            _db([_claim(), _claim()]),
            organization_id=ORG,
            fallback_user_id=ACTOR,
            dry_run=False,
        )
    assert result.posted == 1
    assert len(result.errors) == 1


def test_the_service_never_commits():
    db = _db([])
    post_unposted_claims(db, organization_id=ORG, fallback_user_id=ACTOR, dry_run=False)
    db.commit.assert_not_called()


def test_result_defaults_are_zero():
    r = ExpensePostingResult()
    assert (r.found, r.posted, r.skipped) == (0, 0, 0)
    assert r.total_amount == Decimal("0")


def test_the_script_no_longer_sets_the_rls_guc_by_interpolation():
    """It ran ``SET app.current_organization_id = '{ORG_ID}'`` through an
    f-string: SQL built by formatting, and one isolation layer of two."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
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
    executable = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]
    assert not [s for s in executable if "current_organization_id" in s]
    assert "session_for_org" in SCRIPT.read_text(encoding="utf-8")


def test_the_script_no_longer_hardcodes_an_organization():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert "ORG_ID" not in names
