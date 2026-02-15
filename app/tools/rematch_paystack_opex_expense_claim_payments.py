"""
Rematch mis-matched Paystack OPEX "Expense Claim Payment" bank lines.

Problem: a bank statement line that clearly says "Expense Claim Payment - ACC-PAY-..."
was sometimes matched to unrelated GL (e.g. AR customer payments) just because the
amount/date coincidentally matched. That consumes the bank line and prevents expense
reimbursement matching.

This tool:
1) Finds such bank lines where the narration contains ACC-PAY and the current matched
   journal entry is NOT an EXPENSE_REIMBURSEMENT.
2) Unmatches the bank line.
3) Posts the missing reimbursement journal for the referenced ExpenseClaim (if needed).
4) Matches the bank line to the reimbursement journal's bank-side GL line.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import BankStatement, BankStatementLine
from app.models.finance.gl.journal_entry import JournalEntry
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.expense.expense_posting_adapter import ExpensePostingAdapter
from app.services.finance.banking.bank_reconciliation import BankReconciliationService

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
PAYSTACK_OPEX_BANK_ACCOUNT_ID = UUID("548e51bd-2171-429c-87d0-d0ff631ab75a")
ADMIN_PERSON_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")

_ACC_PAY_RE = re.compile(r"(ACC-PAY-\d{4}-\d+)")


@dataclass(frozen=True)
class Stats:
    scanned: int = 0
    candidates: int = 0
    unmatch_ok: int = 0
    claim_missing: int = 0
    claim_not_paid: int = 0
    posted: int = 0
    matched: int = 0
    skipped_already_correct: int = 0
    failed: int = 0


def _extract_ref(description: str | None) -> str | None:
    if not description:
        return None
    m = _ACC_PAY_RE.search(description)
    return m.group(1) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default="2025-12-31")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    svc = BankReconciliationService()
    stats = Stats()

    with SessionLocal() as db:
        db.execute(text("SET statement_timeout TO '600s'"))
        db.execute(text("SET lock_timeout TO '60s'"))

        bank_account = db.get(BankAccount, PAYSTACK_OPEX_BANK_ACCOUNT_ID)
        if not bank_account or bank_account.organization_id != ORG_ID:
            raise SystemExit("Paystack OPEX bank account not found")
        if not bank_account.gl_account_id:
            raise SystemExit("Paystack OPEX bank account has no linked GL account")
        bank_gl_account_id = bank_account.gl_account_id

        # Find bank lines that look like expense claim payments but are matched to non-expense docs.
        q = (
            select(BankStatementLine)
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .join(
                JournalEntryLine,
                JournalEntryLine.line_id == BankStatementLine.matched_journal_line_id,
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                BankStatement.organization_id == ORG_ID,
                BankStatement.bank_account_id == PAYSTACK_OPEX_BANK_ACCOUNT_ID,
                BankStatementLine.transaction_date >= from_date,
                BankStatementLine.transaction_date <= to_date,
                BankStatementLine.is_matched.is_(True),
                BankStatementLine.description.ilike("%Expense Claim Payment%"),
                BankStatementLine.description.ilike("%ACC-PAY-%"),
                # Anything not already reconciled to an expense reimbursement is a candidate.
                (JournalEntry.source_document_type.is_(None))
                | (JournalEntry.source_document_type != "EXPENSE_REIMBURSEMENT"),
            )
            .order_by(
                BankStatementLine.transaction_date.asc(),
                BankStatementLine.line_number.asc(),
            )
        )

        candidates = db.scalars(q).yield_per(200)

        for stmt_line in candidates:
            stats = Stats(**{**stats.__dict__, "scanned": stats.scanned + 1})
            if args.limit and stats.scanned > args.limit:
                break

            ref = _extract_ref(stmt_line.description)
            if not ref:
                continue

            stats = Stats(**{**stats.__dict__, "candidates": stats.candidates + 1})

            claim = db.scalar(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == ORG_ID,
                    ExpenseClaim.payment_reference == ref,
                )
            )
            if not claim:
                stats = Stats(
                    **{**stats.__dict__, "claim_missing": stats.claim_missing + 1}
                )
                continue
            if claim.status != ExpenseClaimStatus.PAID:
                stats = Stats(
                    **{**stats.__dict__, "claim_not_paid": stats.claim_not_paid + 1}
                )
                continue

            if args.dry_run:
                continue

            try:
                # Unmatch first, then match to expense reimbursement.
                svc.unmatch_statement_line(
                    db,
                    organization_id=ORG_ID,
                    statement_line_id=stmt_line.line_id,
                )
                stats = Stats(**{**stats.__dict__, "unmatch_ok": stats.unmatch_ok + 1})

                if not claim.reimbursement_journal_id:
                    posting = ExpensePostingAdapter.post_expense_reimbursement(
                        db,
                        organization_id=ORG_ID,
                        claim_id=claim.claim_id,
                        posting_date=claim.paid_on or stmt_line.transaction_date,
                        posted_by_user_id=ADMIN_PERSON_ID,
                        bank_account_id=PAYSTACK_OPEX_BANK_ACCOUNT_ID,
                        payment_reference=ref,
                        correlation_id=ref,
                        idempotency_key=f"{ORG_ID}:EXP:REIMB:{claim.claim_id}:post:v1",
                    )
                    if not posting.success or not posting.journal_entry_id:
                        db.rollback()
                        stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})
                        continue
                    stats = Stats(**{**stats.__dict__, "posted": stats.posted + 1})

                bank_jel = db.scalar(
                    select(JournalEntryLine)
                    .where(
                        JournalEntryLine.journal_entry_id
                        == claim.reimbursement_journal_id,
                        JournalEntryLine.account_id == bank_gl_account_id,
                        JournalEntryLine.credit_amount.isnot(None),
                        JournalEntryLine.credit_amount > 0,
                    )
                    .order_by(JournalEntryLine.line_number.asc())
                )
                if not bank_jel:
                    db.rollback()
                    stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})
                    continue

                svc.match_statement_line(
                    db,
                    organization_id=ORG_ID,
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=bank_jel.line_id,
                    matched_by=ADMIN_PERSON_ID,
                    force_match=False,
                )
                db.commit()
                stats = Stats(**{**stats.__dict__, "matched": stats.matched + 1})

                if stats.matched % 25 == 0:
                    print(stats)  # noqa: T201
            except Exception:
                db.rollback()
                stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})

        db.commit()

    print(stats)  # noqa: T201


if __name__ == "__main__":
    main()
