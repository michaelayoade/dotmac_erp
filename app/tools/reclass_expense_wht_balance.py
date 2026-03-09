"""
Reclass net expense-related balance out of WHT account.

This posts a single GL journal that moves the current net balance impact created
by EXPENSE module journals from a source account (default: 2110 WHT) to a target
employee-payable account (default: 2030 Employee Reimbursables).
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.models.finance.gl.journal_entry import JournalType
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ACTOR_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", default=str(DEFAULT_ORG_ID))
    ap.add_argument("--actor-id", default=str(DEFAULT_ACTOR_ID))
    ap.add_argument("--source-account-code", default="2110")
    ap.add_argument("--target-account-code", default="2030")
    ap.add_argument("--posting-date", default=date.today().isoformat())
    ap.add_argument("--execute", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    org_id = UUID(args.org_id)
    actor_id = UUID(args.actor_id)
    posting_date = date.fromisoformat(args.posting_date)

    with SessionLocal() as db:
        source = db.execute(
            text(
                """
                select account_id, account_code, account_name
                from gl.account
                where organization_id = :org_id and account_code = :code and is_active = true
                limit 1
                """
            ),
            {"org_id": org_id, "code": args.source_account_code},
        ).first()
        target = db.execute(
            text(
                """
                select account_id, account_code, account_name
                from gl.account
                where organization_id = :org_id and account_code = :code and is_active = true
                limit 1
                """
            ),
            {"org_id": org_id, "code": args.target_account_code},
        ).first()

        if not source:
            raise SystemExit(
                f"Source account code not found: {args.source_account_code}"
            )
        if not target:
            raise SystemExit(
                f"Target account code not found: {args.target_account_code}"
            )

        row = db.execute(
            text(
                """
                select
                    coalesce(sum(jel.debit_amount), 0) as dr,
                    coalesce(sum(jel.credit_amount), 0) as cr
                from gl.journal_entry je
                join gl.journal_entry_line jel on jel.journal_entry_id = je.journal_entry_id
                where je.organization_id = :org_id
                  and je.source_module = 'EXPENSE'
                  and jel.account_id = :source_account_id
                """
            ),
            {"org_id": org_id, "source_account_id": source.account_id},
        ).first()

        total_dr = Decimal(str(row.dr or 0))
        total_cr = Decimal(str(row.cr or 0))
        net_credit = total_cr - total_dr

        print(  # noqa: T201
            "summary "
            f"org={org_id} source={source.account_code}:{source.account_name} "
            f"target={target.account_code}:{target.account_name} "
            f"expense_source_dr={total_dr} expense_source_cr={total_cr} net_credit={net_credit}"
        )

        if net_credit == Decimal("0"):
            print("no_reclass_needed net_credit=0")  # noqa: T201
            return

        amount = abs(net_credit)
        if net_credit > 0:
            # Source has net credit balance impact; clear source and move to target.
            lines = [
                JournalLineInput(
                    account_id=source.account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=amount,
                    credit_amount_functional=Decimal("0"),
                    description="Reclass expense net from WHT",
                ),
                JournalLineInput(
                    account_id=target.account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=amount,
                    description="Reclass expense net to employee payable",
                ),
            ]
        else:
            lines = [
                JournalLineInput(
                    account_id=source.account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=amount,
                    description="Reclass expense net from WHT",
                ),
                JournalLineInput(
                    account_id=target.account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=amount,
                    credit_amount_functional=Decimal("0"),
                    description="Reclass expense net to employee payable",
                ),
            ]

        if not args.execute:
            print(  # noqa: T201
                "dry_run amount="
                f"{amount} posting_date={posting_date} execute_with=--execute"
            )
            return

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=(
                f"Reclass EXPENSE net from {source.account_code} to {target.account_code}"
            ),
            reference=f"RECLASS-EXP-{source.account_code}-{target.account_code}-{posting_date.isoformat()}",
            currency_code=settings.default_functional_currency_code,
            exchange_rate=Decimal("1.0"),
            lines=lines,
            source_module="GL",
            source_document_type="RECLASS",
            correlation_id=f"reclass-expense-{source.account_code}-{target.account_code}",
        )

        journal, err = BasePostingAdapter.create_and_approve_journal(
            db, org_id, journal_input, actor_id, error_prefix="Reclass journal failed"
        )
        if err:
            db.rollback()
            raise SystemExit(err.message)

        post_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=(
                f"{org_id}:GL:RECLASS:EXPENSE:{source.account_code}:{target.account_code}:{posting_date.isoformat()}:v1"
            ),
            source_module="GL",
            correlation_id=f"reclass-expense-{source.account_code}-{target.account_code}",
            posted_by_user_id=actor_id,
            success_message="Reclass posted successfully",
        )
        if not post_result.success:
            db.rollback()
            raise SystemExit(post_result.message)

        db.commit()
        print(  # noqa: T201
            "posted "
            f"journal_entry_id={journal.journal_entry_id} "
            f"posting_batch_id={post_result.posting_batch_id}"
        )


if __name__ == "__main__":
    main()
