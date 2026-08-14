"""amount_paid and balance_due for the documents that never had them

ADR-0016 stage 2, step 1 — EXPAND. Nothing reads coverage yet; this only makes
partial payment representable.

Three monetary documents carry an amount but no record of how much of it was
paid, so their status enums have `PAID` and no `PARTIALLY_PAID`:

    payroll.salary_slip            net_pay
    expense.expense_claim          net_payable_amount
    lease.lease_payment_schedule   total_payment

That is not a cosmetic gap. `payout_payroll_entry` and `mark_slip_paid` both
set `SalarySlipStatus.PAID` and neither takes an amount, so disbursing ₦50,000
against a ₦100,000 slip leaves the slip reading PAID — and no column anywhere
records that ₦50,000. Part-disbursement when cash is tight is ordinary
practice. The columns have to exist before that can even be fixed.

## Why `expense_claim.balance_due` is NULLABLE and the other two are not

`net_pay` and `total_payment` are NOT NULL, so their generated expressions
cannot yield NULL. `net_payable_amount` IS nullable — it is
`total_approved_amount - advance_adjusted`, and an unapproved claim has no
approved total. So its balance is genuinely UNDETERMINED rather than zero, and
the column says so instead of coercing it to `0 - amount_paid` and reporting a
draft claim as overpaid.

## Backfill

None. `amount_paid` defaults to 0, which is the honest starting value: for
every existing row the amount actually paid was never recorded, so there is
nothing to migrate. Rows already marked PAID will therefore read as UNPAID
coverage until the shadow step (stage 2 step 2) reconciles them against the
transfer batches. That disagreement is the point of the shadow step — it is
pre-existing data defect being surfaced, not created here.

## Operational note

`ADD COLUMN ... GENERATED ALWAYS AS (...) STORED` REWRITES the table and holds
an ACCESS EXCLUSIVE lock for the duration. `ADD COLUMN ... DEFAULT 0` does not
(PostgreSQL 11+ stores the default in the catalogue), but the generated column
in the same statement list does. Run in a maintenance window.

Revision ID: 20260812_coverage_expand
Revises: 20260811_balance_due_generated
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260812_coverage_expand"
down_revision = "20260811_balance_due_generated"
branch_labels = None
depends_on = None

# (schema, table, total column, NUMERIC precision, balance is nullable)
_DOCUMENTS = (
    ("payroll", "salary_slip", "net_pay", "18, 2", False),
    ("expense", "expense_claim", "net_payable_amount", "12, 2", True),
    ("lease", "lease_payment_schedule", "total_payment", "20, 6", False),
)


def _index_name(schema: str, table: str) -> str:
    return f"ix_{schema}_{table}_balance_due_outstanding"


def upgrade() -> None:
    for schema, table, total_column, precision, _nullable in _DOCUMENTS:
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "  # noqa: S608
                f"ADD COLUMN amount_paid NUMERIC({precision}) "
                f"NOT NULL DEFAULT 0"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "  # noqa: S608
                f"ADD COLUMN balance_due NUMERIC({precision}) "
                f"GENERATED ALWAYS AS ({total_column} - amount_paid) STORED"
            )
        )
        # Partial, as in stage 1: settled documents are the majority and are
        # never what an outstanding-balance view is looking for. A NULL balance
        # (an unapproved expense claim) is excluded too, which is correct — an
        # undetermined balance is not an outstanding one.
        op.execute(
            sa.text(
                f"CREATE INDEX {_index_name(schema, table)} "  # noqa: S608
                f"ON {schema}.{table} (balance_due) WHERE balance_due > 0"
            )
        )


def downgrade() -> None:
    for schema, table, _total, _precision, _nullable in _DOCUMENTS:
        op.execute(
            sa.text(f"DROP INDEX IF EXISTS {schema}.{_index_name(schema, table)}")
        )
        op.execute(sa.text(f"ALTER TABLE {schema}.{table} DROP COLUMN balance_due"))
        op.execute(sa.text(f"ALTER TABLE {schema}.{table} DROP COLUMN amount_paid"))
