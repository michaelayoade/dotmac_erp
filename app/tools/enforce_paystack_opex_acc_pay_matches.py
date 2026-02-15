"""
Enforce deterministic matching for Paystack OPEX Expense Claim payments (ACC-PAY).

For 2025, Paystack OPEX bank lines contain an ACC-PAY token in the narration.
That token is a Payment Entry name in ERPNext and is also stored on
expense.expense_claim.payment_reference.

This tool:
- Iterates all Paystack OPEX statement lines for a date range that contain ACC-PAY.
- Finds the ExpenseClaim with payment_reference == ACC-PAY token.
- Ensures a reimbursement journal is posted for that claim.
- Ensures the statement line is matched to that reimbursement journal's bank GL line.

It will unmatch any existing match on that statement line if it is incorrect
and then re-match to the correct expense reimbursement.
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
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.expense.expense_posting_adapter import ExpensePostingAdapter
from app.services.finance.banking.bank_reconciliation import BankReconciliationService

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
PAYSTACK_OPEX_BANK_ACCOUNT_ID = UUID("548e51bd-2171-429c-87d0-d0ff631ab75a")
ADMIN_PERSON_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")

_ACC_PAY_RE = re.compile(r"(ACC-PAY-\d{4}-\d+(?:-\d+)?)")


@dataclass(frozen=True)
class Stats:
    scanned: int = 0
    has_token: int = 0
    claim_found: int = 0
    claim_missing: int = 0
    claim_not_paid: int = 0
    posted: int = 0
    already_correct: int = 0
    rematched: int = 0
    failed: int = 0


def _extract_token(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _ACC_PAY_RE.search(desc)
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

    stats = Stats()
    svc = BankReconciliationService()

    with SessionLocal() as db:
        db.execute(text("SET statement_timeout TO '600s'"))
        db.execute(text("SET lock_timeout TO '60s'"))

        bank_account = db.get(BankAccount, PAYSTACK_OPEX_BANK_ACCOUNT_ID)
        if not bank_account or bank_account.organization_id != ORG_ID:
            raise SystemExit("Paystack OPEX bank account not found")
        if not bank_account.gl_account_id:
            raise SystemExit("Paystack OPEX bank account has no linked GL account")
        bank_gl_account_id = bank_account.gl_account_id

        stmt_iter = db.scalars(
            select(BankStatementLine)
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .where(
                BankStatement.organization_id == ORG_ID,
                BankStatement.bank_account_id == PAYSTACK_OPEX_BANK_ACCOUNT_ID,
                BankStatementLine.transaction_date >= from_date,
                BankStatementLine.transaction_date <= to_date,
                BankStatementLine.description.ilike("%ACC-PAY-%"),
            )
            .order_by(
                BankStatementLine.transaction_date.asc(),
                BankStatementLine.line_number.asc(),
            )
        ).yield_per(500)

        for stmt_line in stmt_iter:
            stats = Stats(**{**stats.__dict__, "scanned": stats.scanned + 1})
            if args.limit and stats.scanned > args.limit:
                break

            token = _extract_token(stmt_line.description)
            if not token:
                continue
            stats = Stats(**{**stats.__dict__, "has_token": stats.has_token + 1})

            claim = db.scalar(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == ORG_ID,
                    ExpenseClaim.payment_reference == token,
                )
            )
            if not claim:
                stats = Stats(
                    **{**stats.__dict__, "claim_missing": stats.claim_missing + 1}
                )
                continue
            stats = Stats(**{**stats.__dict__, "claim_found": stats.claim_found + 1})
            if claim.status != ExpenseClaimStatus.PAID:
                stats = Stats(
                    **{**stats.__dict__, "claim_not_paid": stats.claim_not_paid + 1}
                )
                continue

            if args.dry_run:
                continue

            try:
                if not claim.reimbursement_journal_id:
                    posting = ExpensePostingAdapter.post_expense_reimbursement(
                        db,
                        organization_id=ORG_ID,
                        claim_id=claim.claim_id,
                        posting_date=claim.paid_on or stmt_line.transaction_date,
                        posted_by_user_id=ADMIN_PERSON_ID,
                        bank_account_id=PAYSTACK_OPEX_BANK_ACCOUNT_ID,
                        payment_reference=token,
                        correlation_id=token,
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

                if (
                    stmt_line.is_matched
                    and stmt_line.matched_journal_line_id == bank_jel.line_id
                ):
                    stats = Stats(
                        **{
                            **stats.__dict__,
                            "already_correct": stats.already_correct + 1,
                        }
                    )
                    continue

                if stmt_line.is_matched:
                    svc.unmatch_statement_line(
                        db,
                        organization_id=ORG_ID,
                        statement_line_id=stmt_line.line_id,
                    )

                svc.match_statement_line(
                    db,
                    organization_id=ORG_ID,
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=bank_jel.line_id,
                    matched_by=ADMIN_PERSON_ID,
                    force_match=False,
                )
                db.commit()
                stats = Stats(**{**stats.__dict__, "rematched": stats.rematched + 1})

                if (stats.rematched + stats.already_correct) % 250 == 0:
                    print(stats)  # noqa: T201
            except Exception:
                db.rollback()
                stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})

        db.commit()

    print(stats)  # noqa: T201


if __name__ == "__main__":
    main()
