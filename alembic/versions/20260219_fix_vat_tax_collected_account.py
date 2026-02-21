"""Fix VAT-7.5 tax_collected_account_id: 2000 → 2120 (VAT Payables).

Revision ID: 20260219_fix_vat_tax_collected_account
Revises: 20260219_fix_expense_gl_accounts
Create Date: 2026-02-19

The VAT-7.5 tax code had tax_collected_account_id pointing to account 2000
(Trade Payables) instead of account 2120 (VAT Payables). This caused 18,951
AR invoice journals to credit Trade Payables instead of the VAT liability
account, misposting NGN 107.9M.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260219_fix_vat_tax_collected_account"
down_revision = "20260219_fix_expense_gl_accounts"
branch_labels = None
depends_on = None

# UUIDs
ORG_ID = "00000000-0000-0000-0000-000000000001"
VAT_75_TAX_CODE_ID = "4b180259-b0b0-41fb-955b-0e089df66b42"
ACCT_2000_TRADE_PAYABLES = "d6fcaecf-e1b7-4dce-9743-368eb5b1775c"  # old (wrong)
ACCT_2120_VAT_PAYABLES = "f46dd075-1c51-4cb3-8033-41c89735e438"  # correct


def upgrade() -> None:
    conn = op.get_bind()

    # Fix VAT-7.5 tax_collected_account_id: 2000 → 2120
    conn.execute(
        sa.text(
            """
            UPDATE tax.tax_code
            SET tax_collected_account_id = :new_account
            WHERE tax_code_id = :tax_code_id
              AND organization_id = :org_id
              AND (tax_collected_account_id = :old_account
                   OR tax_collected_account_id IS NULL)
            """
        ),
        {
            "new_account": ACCT_2120_VAT_PAYABLES,
            "old_account": ACCT_2000_TRADE_PAYABLES,
            "tax_code_id": VAT_75_TAX_CODE_ID,
            "org_id": ORG_ID,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Revert VAT-7.5 tax_collected_account_id: 2120 → 2000
    conn.execute(
        sa.text(
            """
            UPDATE tax.tax_code
            SET tax_collected_account_id = :old_account
            WHERE tax_code_id = :tax_code_id
              AND organization_id = :org_id
              AND tax_collected_account_id = :new_account
            """
        ),
        {
            "old_account": ACCT_2000_TRADE_PAYABLES,
            "new_account": ACCT_2120_VAT_PAYABLES,
            "tax_code_id": VAT_75_TAX_CODE_ID,
            "org_id": ORG_ID,
        },
    )
