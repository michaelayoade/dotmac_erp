"""
Fix remaining unmatched 2025 Paystack OPEX ACC-PAY statement lines.

Strategy:
- For Employee Payment Entries: post an "extra" expense reimbursement journal
  (debit employee payable, credit Paystack OPEX bank GL) keyed by ACC-PAY token,
  and match the bank statement line to that journal's bank line.
- For Supplier Payment Entries: post the synced SupplierPayment to GL, then match.

This is intended to clean up the last edge cases where ERPNext Payment Entries
have no child "references" rows but still carry a custom expense claim pointer,
or where an ACC-PAY line was an AP supplier payment.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models.expense.expense_claim import ExpenseClaim
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import BankStatement, BankStatementLine
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.sync import SyncEntity
from app.services.erpnext.client import ERPNextClient, ERPNextConfig
from app.services.expense.expense_posting_adapter import ExpensePostingAdapter
from app.services.finance.ap.posting.payment import (
    post_payment as post_supplier_payment,
)
from app.services.finance.banking.bank_reconciliation import BankReconciliationService
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
PAYSTACK_OPEX_BANK_ACCOUNT_ID = UUID("548e51bd-2171-429c-87d0-d0ff631ab75a")
ADMIN_PERSON_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")

_ACC_PAY_RE = re.compile(r"(ACC-PAY-\d{4}-\d+(?:-\d+)?)")


@dataclass(frozen=True)
class Stats:
    scanned: int = 0
    employee_fixed: int = 0
    supplier_fixed: int = 0
    skipped_no_token: int = 0
    skipped_no_erp_doc: int = 0
    skipped_no_claim: int = 0
    failed: int = 0


def _extract_token(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _ACC_PAY_RE.search(desc)
    return m.group(1) if m else None


def _resolve_expense_claim_name_from_payment_entry(pe: dict) -> str | None:
    refs = pe.get("references") or []
    for r in refs:
        if r.get("reference_doctype") == "Expense Claim" and r.get("reference_name"):
            return str(r["reference_name"])

    # Fallback for legacy/custom implementations (seen in ERPNext instance)
    for k in ("custom_expense_claim", "reference_no"):
        v = pe.get(k)
        if isinstance(v, str) and v.startswith("HR-EXP-"):
            return v
    return None


def _ensure_extra_reimbursement_journal(
    db,
    *,
    bank_gl_account_id: UUID,
    claim: ExpenseClaim,
    token: str,
    paid_on: date,
    amount: Decimal,
) -> JournalEntry:
    # Idempotency: reuse existing journal if we already posted it for this token.
    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.correlation_id == token,
            JournalEntry.source_document_type == "EXPENSE_REIMBURSEMENT",
            JournalEntry.source_document_id == claim.claim_id,
        )
    )
    if existing:
        return existing

    payable_account_id = ExpensePostingAdapter._get_employee_payable_account(  # noqa: SLF001
        db, ORG_ID
    )
    if not payable_account_id:
        raise RuntimeError("Employee payable account not configured")

    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=paid_on,
        posting_date=paid_on,
        description=f"Expense Reimbursement (extra) {claim.claim_number} {token}",
        reference=token[:100],
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        lines=[
            JournalLineInput(
                account_id=payable_account_id,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=amount,
                credit_amount_functional=Decimal("0"),
                description=f"Expense reimbursement: {claim.claim_number}",
            ),
            JournalLineInput(
                account_id=bank_gl_account_id,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=amount,
                description=f"Bank outflow: {token}",
            ),
        ],
        source_module="EXPENSE",
        source_document_type="EXPENSE_REIMBURSEMENT",
        source_document_id=claim.claim_id,
        correlation_id=token,
    )

    journal, err = BasePostingAdapter.create_and_approve_journal(
        db,
        ORG_ID,
        journal_input,
        ADMIN_PERSON_ID,
        error_prefix="Journal creation failed",
    )
    if err:
        raise RuntimeError(err.message)

    idempotency_key = f"{ORG_ID}:EXP:REIMB_EXTRA:{claim.claim_id}:{token}:post:v1"
    post = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=ORG_ID,
        journal_entry_id=journal.journal_entry_id,
        posting_date=paid_on,
        idempotency_key=idempotency_key,
        source_module="EXPENSE",
        correlation_id=token,
        posted_by_user_id=ADMIN_PERSON_ID,
        success_message="Extra reimbursement posted",
    )
    if not post.success:
        raise RuntimeError(post.message)

    return journal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default="2025-12-31")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    cfg = ERPNextConfig(
        url=os.environ.get("ERPNEXT_URL", ""),
        api_key=os.environ.get("ERPNEXT_API_KEY", ""),
        api_secret=os.environ.get("ERPNEXT_API_SECRET", ""),
        company=os.environ.get("ERPNEXT_COMPANY") or "Dotmac Technologies",
    )

    stats = Stats()
    svc = BankReconciliationService()

    with SessionLocal() as db, ERPNextClient(cfg) as client:
        db.execute(text("SET statement_timeout TO '600s'"))
        db.execute(text("SET lock_timeout TO '60s'"))

        bank = db.get(BankAccount, PAYSTACK_OPEX_BANK_ACCOUNT_ID)
        if not bank or bank.organization_id != ORG_ID or not bank.gl_account_id:
            raise SystemExit("Paystack OPEX bank account missing or not linked to GL")
        bank_gl_account_id = bank.gl_account_id

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
                BankStatementLine.is_matched.is_(False),
                BankStatementLine.description.ilike("%ACC-PAY-%"),
            )
            .order_by(
                BankStatementLine.transaction_date.asc(),
                BankStatementLine.line_number.asc(),
            )
        ).yield_per(200)

        for stmt_line in stmt_iter:
            stats = Stats(**{**stats.__dict__, "scanned": stats.scanned + 1})
            token = _extract_token(stmt_line.description)
            if not token:
                stats = Stats(
                    **{**stats.__dict__, "skipped_no_token": stats.skipped_no_token + 1}
                )
                continue

            try:
                pe = client.get_document("Payment Entry", token)
            except Exception:
                stats = Stats(
                    **{
                        **stats.__dict__,
                        "skipped_no_erp_doc": stats.skipped_no_erp_doc + 1,
                    }
                )
                continue

            if args.dry_run:
                continue

            try:
                payment_type = pe.get("payment_type")
                party_type = pe.get("party_type")

                if payment_type == "Pay" and party_type == "Employee":
                    exp_name = _resolve_expense_claim_name_from_payment_entry(pe)
                    if not exp_name:
                        stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})
                        continue

                    claim = db.scalar(
                        select(ExpenseClaim).where(
                            ExpenseClaim.organization_id == ORG_ID,
                            ExpenseClaim.erpnext_id == exp_name,
                        )
                    )
                    if not claim:
                        stats = Stats(
                            **{
                                **stats.__dict__,
                                "skipped_no_claim": stats.skipped_no_claim + 1,
                            }
                        )
                        continue

                    paid_on = date.fromisoformat(str(pe.get("posting_date")))
                    amount = Decimal(
                        str(pe.get("paid_amount") or pe.get("received_amount") or "0")
                    )

                    journal = _ensure_extra_reimbursement_journal(
                        db,
                        bank_gl_account_id=bank_gl_account_id,
                        claim=claim,
                        token=token,
                        paid_on=paid_on,
                        amount=amount,
                    )

                    bank_jel = db.scalar(
                        select(JournalEntryLine).where(
                            JournalEntryLine.journal_entry_id
                            == journal.journal_entry_id,
                            JournalEntryLine.account_id == bank_gl_account_id,
                            JournalEntryLine.credit_amount.isnot(None),
                            JournalEntryLine.credit_amount > 0,
                        )
                    )
                    if not bank_jel:
                        raise RuntimeError("No bank journal line found")

                    svc.match_statement_line(
                        db,
                        organization_id=ORG_ID,
                        statement_line_id=stmt_line.line_id,
                        journal_line_id=bank_jel.line_id,
                        matched_by=ADMIN_PERSON_ID,
                        force_match=False,
                    )
                    db.commit()
                    stats = Stats(
                        **{**stats.__dict__, "employee_fixed": stats.employee_fixed + 1}
                    )
                    continue

                if payment_type == "Pay" and party_type == "Supplier":
                    se = db.scalar(
                        select(SyncEntity).where(
                            SyncEntity.organization_id == ORG_ID,
                            SyncEntity.source_system == "erpnext",
                            SyncEntity.source_doctype == "Payment Entry",
                            SyncEntity.source_name == token,
                        )
                    )
                    if not se or not se.target_id:
                        stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})
                        continue

                    post_res = post_supplier_payment(
                        db,
                        organization_id=ORG_ID,
                        payment_id=se.target_id,
                        posting_date=date.fromisoformat(str(pe.get("posting_date"))),
                        posted_by_user_id=ADMIN_PERSON_ID,
                        idempotency_key=f"{ORG_ID}:AP:PAY:{se.target_id}:{token}:post:v1",
                    )
                    if not post_res.success or not post_res.journal_entry_id:
                        raise RuntimeError(post_res.message)

                    # Credit line uses SupplierPayment.bank_account_id (ERPNext account mapping)
                    credit_jel = db.scalar(
                        select(JournalEntryLine).where(
                            JournalEntryLine.journal_entry_id
                            == post_res.journal_entry_id,
                            JournalEntryLine.credit_amount.isnot(None),
                            JournalEntryLine.credit_amount > 0,
                        )
                    )
                    if not credit_jel:
                        raise RuntimeError("No credit journal line found")

                    svc.match_statement_line(
                        db,
                        organization_id=ORG_ID,
                        statement_line_id=stmt_line.line_id,
                        journal_line_id=credit_jel.line_id,
                        matched_by=ADMIN_PERSON_ID,
                        force_match=False,
                    )
                    db.commit()
                    stats = Stats(
                        **{**stats.__dict__, "supplier_fixed": stats.supplier_fixed + 1}
                    )
                    continue

                stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})
            except Exception:
                db.rollback()
                stats = Stats(**{**stats.__dict__, "failed": stats.failed + 1})

    print(stats)  # noqa: T201


if __name__ == "__main__":
    main()
