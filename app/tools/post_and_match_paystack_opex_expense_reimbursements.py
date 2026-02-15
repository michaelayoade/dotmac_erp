"""
Backfill ExpenseClaim reimbursement journals and match Paystack OPEX bank lines.

Run from the Docker app container (it has DB connectivity):

    docker compose exec -T app python -m app.tools.post_and_match_paystack_opex_expense_reimbursements

This addresses ERPNext-synced expense claims that were marked PAID but did not
have the reimbursement (bank outflow) journal posted in DotMac ERP, which
prevents bank reconciliation from matching those Paystack OPEX statement lines.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select, text

from app.db import SessionLocal
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import BankStatement, BankStatementLine
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.expense.expense_posting_adapter import ExpensePostingAdapter
from app.services.finance.banking.bank_reconciliation import BankReconciliationService

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
PAYSTACK_OPEX_BANK_ACCOUNT_ID = UUID("548e51bd-2171-429c-87d0-d0ff631ab75a")
# Use an existing active "admin" person as the actor for postings/matches.
ADMIN_PERSON_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")


@dataclass(frozen=True)
class Stats:
    scanned: int = 0
    skipped_already_posted: int = 0
    missing_bank_line: int = 0
    posted: int = 0
    matched: int = 0
    post_failed: int = 0
    match_failed: int = 0


def _find_bank_line(
    db,
    *,
    paid_on: date,
    amount: Decimal,
    payment_reference: str | None,
    date_window_days: int,
) -> BankStatementLine | None:
    date_from = paid_on - timedelta(days=date_window_days)
    date_to = paid_on + timedelta(days=date_window_days)
    tol = Decimal("0.01")

    base_conditions = [
        BankStatement.organization_id == ORG_ID,
        BankStatement.bank_account_id == PAYSTACK_OPEX_BANK_ACCOUNT_ID,
        BankStatementLine.is_matched.is_(False),
        BankStatementLine.transaction_date >= date_from,
        BankStatementLine.transaction_date <= date_to,
        BankStatementLine.amount >= (amount - tol),
        BankStatementLine.amount <= (amount + tol),
    ]

    ref_conditions = list(base_conditions)
    if payment_reference:
        ref_conditions.append(
            BankStatementLine.description.ilike(f"%{payment_reference}%")
        )

    candidates = list(
        db.execute(
            select(BankStatementLine)
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .where(and_(*ref_conditions))
            .order_by(
                text("abs((banking.bank_statement_lines.transaction_date - :paid_on))"),
                BankStatementLine.line_number.asc(),
                BankStatementLine.line_id.asc(),
            )
            .params(paid_on=paid_on)
        ).scalars()
    )

    if not candidates and payment_reference:
        # Fallback: if the narration doesn't contain ACC-PAY (older exports),
        # try matching by amount/date only.
        candidates = list(
            db.execute(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatement.statement_id == BankStatementLine.statement_id,
                )
                .where(and_(*base_conditions))
                .order_by(
                    text(
                        "abs((banking.bank_statement_lines.transaction_date - :paid_on))"
                    ),
                    BankStatementLine.line_number.asc(),
                    BankStatementLine.line_id.asc(),
                )
                .params(paid_on=paid_on)
            ).scalars()
        )

    if not candidates:
        return None

    # If multiple candidates exist, consume the first. Ambiguity is expected
    # when many transfers share the same amount on the same date.
    return candidates[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default="2025-12-31")
    ap.add_argument("--date-window-days", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    stats = Stats()
    svc = BankReconciliationService()

    with SessionLocal() as db:
        # This is a backfill job; avoid app-level 30s timeouts.
        db.execute(text("SET statement_timeout TO '600s'"))
        # Keep this reasonably high; we prefer waiting over failing and leaving
        # half-posted journals.
        db.execute(text("SET lock_timeout TO '60s'"))

        bank_account = db.get(BankAccount, PAYSTACK_OPEX_BANK_ACCOUNT_ID)
        if not bank_account or bank_account.organization_id != ORG_ID:
            raise SystemExit("Paystack OPEX bank account not found")
        if not bank_account.gl_account_id:
            raise SystemExit("Paystack OPEX bank account has no linked GL account")
        bank_gl_account_id = bank_account.gl_account_id

        claim_iter = db.scalars(
            select(ExpenseClaim)
            .where(
                ExpenseClaim.organization_id == ORG_ID,
                ExpenseClaim.status == ExpenseClaimStatus.PAID,
                ExpenseClaim.paid_on.isnot(None),
                ExpenseClaim.paid_on >= from_date,
                ExpenseClaim.paid_on <= to_date,
                ExpenseClaim.reimbursement_journal_id.is_(None),
            )
            .order_by(ExpenseClaim.paid_on.asc(), ExpenseClaim.claim_number.asc())
        ).yield_per(200)

        pending = 0
        for claim in claim_iter:
            stats = Stats(**{**stats.__dict__, "scanned": stats.scanned + 1})
            if args.limit and stats.scanned > args.limit:
                break
            if stats.scanned % 25 == 0:
                print(  # noqa: T201
                    "progress "
                    f"scanned={stats.scanned} posted={stats.posted} matched={stats.matched} "
                    f"missing_bank_line={stats.missing_bank_line} post_failed={stats.post_failed} "
                    f"match_failed={stats.match_failed}"
                )

            if claim.reimbursement_journal_id:
                stats = Stats(
                    **{
                        **stats.__dict__,
                        "skipped_already_posted": stats.skipped_already_posted + 1,
                    }
                )
                continue

            if not claim.paid_on:
                continue

            amount = claim.net_payable_amount or Decimal("0")
            if amount <= 0:
                continue

            stmt_line = _find_bank_line(
                db,
                paid_on=claim.paid_on,
                amount=amount,
                payment_reference=claim.payment_reference,
                date_window_days=args.date_window_days,
            )
            if not stmt_line:
                stats = Stats(
                    **{
                        **stats.__dict__,
                        "missing_bank_line": stats.missing_bank_line + 1,
                    }
                )
                continue

            if args.dry_run:
                continue

            posting = ExpensePostingAdapter.post_expense_reimbursement(
                db,
                organization_id=ORG_ID,
                claim_id=claim.claim_id,
                posting_date=claim.paid_on,
                posted_by_user_id=ADMIN_PERSON_ID,
                bank_account_id=PAYSTACK_OPEX_BANK_ACCOUNT_ID,
                payment_reference=claim.payment_reference,
                correlation_id=claim.payment_reference,
                idempotency_key=f"{ORG_ID}:EXP:REIMB:{claim.claim_id}:post:v1",
            )

            if not posting.success or not posting.journal_entry_id:
                db.rollback()
                print(  # noqa: T201
                    "post_failed "
                    f"claim={claim.claim_number} paid_on={claim.paid_on} ref={claim.payment_reference} "
                    f"msg={posting.message}"
                )
                stats = Stats(
                    **{**stats.__dict__, "post_failed": stats.post_failed + 1}
                )
                continue

            stats = Stats(**{**stats.__dict__, "posted": stats.posted + 1})

            bank_jel = db.scalar(
                select(JournalEntryLine)
                .where(
                    JournalEntryLine.journal_entry_id == posting.journal_entry_id,
                    JournalEntryLine.account_id == bank_gl_account_id,
                    JournalEntryLine.credit_amount.isnot(None),
                    JournalEntryLine.credit_amount > 0,
                )
                .order_by(JournalEntryLine.line_number.asc())
            )
            if not bank_jel:
                db.rollback()
                print(  # noqa: T201
                    "match_failed "
                    f"claim={claim.claim_number} journal={posting.journal_entry_id} reason=no_bank_journal_line"
                )
                stats = Stats(
                    **{**stats.__dict__, "match_failed": stats.match_failed + 1}
                )
                continue

            try:
                svc.match_statement_line(
                    db,
                    organization_id=ORG_ID,
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=bank_jel.line_id,
                    matched_by=ADMIN_PERSON_ID,
                    force_match=False,
                )
                stats = Stats(**{**stats.__dict__, "matched": stats.matched + 1})
            except Exception as e:
                db.rollback()
                print(  # noqa: T201
                    "match_failed "
                    f"claim={claim.claim_number} stmt_line={stmt_line.line_id} "
                    f"err={type(e).__name__}:{e}"
                )
                stats = Stats(
                    **{**stats.__dict__, "match_failed": stats.match_failed + 1}
                )
                continue

            pending += 1
            if pending >= 1:
                db.commit()
                pending = 0

        db.commit()

    print(stats)  # noqa: T201


if __name__ == "__main__":
    main()
