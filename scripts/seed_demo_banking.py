#!/usr/bin/env python3
"""Seed banking-reconciliation test data into the demo DB:
- 3 posted GL journals crediting cash (Dr Cash / Cr Sales Revenue) — these are
  the journal lines a bank statement reconciles against.
- 1 bank statement with 4 lines: 3 that match the journal deposits + 1 that has
  no counterpart (unmatched) → also produces a book-vs-bank difference.

Idempotent via a statement_number marker. Demo / disposable DB only:
    ENFORCE_ORG_FILTER=false PYTHONPATH=/root/dotmac \
        .venv/bin/python scripts/seed_demo_banking.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session_context import session_for_org
from app.models.auth import UserCredential
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    StatementLineType,
)
from app.models.finance.gl.account import Account
from app.models.person import Person
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
    JournalService,
)
from app.models.finance.gl.journal_entry import JournalType

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("seed_demo_banking")

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
STMT_MARKER = "E2E-DEMO-STMT-001"
# (amount, days_ago, matched?) — last one has no journal → unmatched line.
DEPOSITS = [
    (Decimal("50000"), 6, True),
    (Decimal("30000"), 5, True),
    (Decimal("20000"), 4, True),
    (Decimal("5000"), 3, False),
]


def _acct(db, org_id, code):
    return db.scalar(
        select(Account).where(
            Account.organization_id == org_id, Account.account_code == code
        )
    )


def main() -> int:
    results = {"journals": 0, "statement_lines": 0, "errors": []}
    # One known organization, so this is per-org work: scope the session
    # to it rather than running the whole seed unscoped.
    org_id = DEFAULT_ORG_ID
    with session_for_org(org_id) as db:
        cred = db.scalar(
            select(UserCredential).where(UserCredential.username == "e2e_testuser")
        )
        if not cred:
            print("e2e_testuser not found")
            return 1
        user_id = db.get(Person, cred.person_id).id

        cash = _acct(db, org_id, "1000")
        revenue = _acct(db, org_id, "4000")
        bank = db.scalar(
            select(BankAccount).where(BankAccount.organization_id == org_id)
        )
        if not (cash and revenue and bank):
            print(
                f"missing prereqs cash={bool(cash)} rev={bool(revenue)} bank={bool(bank)}"
            )
            return 1

        if db.scalar(
            select(BankStatement).where(
                BankStatement.organization_id == org_id,
                BankStatement.statement_number == STMT_MARKER,
            )
        ):
            print("banking demo data already seeded")
            return 0

        today = date.today()

        # 1) Post cash-deposit journals (Dr Cash / Cr Sales Revenue) for matched lines.
        for amount, days, matched in DEPOSITS:
            if not matched:
                continue
            d = today - timedelta(days=days)
            try:
                j = JournalService.create_journal(
                    db,
                    org_id,
                    JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=d,
                        posting_date=d,
                        description=f"Demo cash deposit {amount}",
                        currency_code=bank.currency_code or "USD",
                        lines=[
                            JournalLineInput(
                                account_id=cash.account_id,
                                debit_amount=amount,
                                description="Deposit",
                            ),
                            JournalLineInput(
                                account_id=revenue.account_id,
                                credit_amount=amount,
                                description="Sales",
                            ),
                        ],
                    ),
                    user_id,
                )
                db.flush()
                JournalService.submit_journal(db, org_id, j.journal_entry_id, user_id)
                JournalService.approve_journal(db, org_id, j.journal_entry_id, user_id)
                JournalService.post_journal(db, org_id, j.journal_entry_id, user_id)
                db.commit()
                results["journals"] += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                results["errors"].append(f"journal {amount}: {e}")

        # 2) Bank statement + lines (3 matchable + 1 unmatched).
        total_credits = sum(a for a, _, _ in DEPOSITS)
        try:
            stmt = BankStatement(
                organization_id=org_id,
                bank_account_id=bank.bank_account_id,
                statement_number=STMT_MARKER,
                statement_date=today,
                period_start=today - timedelta(days=30),
                period_end=today,
                opening_balance=Decimal("0"),
                closing_balance=total_credits,
                total_credits=total_credits,
                total_debits=Decimal("0"),
                currency_code=bank.currency_code or "USD",
                status=BankStatementStatus.imported,
                import_source="seed",
                imported_at=datetime.now(timezone.utc),
                total_lines=len(DEPOSITS),
                unmatched_lines=len(DEPOSITS),
            )
            db.add(stmt)
            db.flush()
            running = Decimal("0")
            for i, (amount, days, _matched) in enumerate(DEPOSITS, start=1):
                running += amount
                db.add(
                    BankStatementLine(
                        statement_id=stmt.statement_id,
                        line_number=i,
                        transaction_id=f"{STMT_MARKER}-{i}",
                        transaction_date=today - timedelta(days=days),
                        transaction_type=StatementLineType.credit,
                        amount=amount,
                        running_balance=running,
                        description=f"Deposit {amount}"
                        if _matched
                        else "Unidentified deposit",
                        reference=f"DEP{i}",
                    )
                )
                results["statement_lines"] += 1
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            results["errors"].append(f"statement: {e}")

    print(
        f"Banking seed: journals_posted={results['journals']}, "
        f"statement_lines={results['statement_lines']}, errors={len(results['errors'])}"
    )
    for e in results["errors"][:5]:
        print(f"  - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
