"""Remap ERPNext-style GL accounts to numbered chart of accounts.

The ERPNext account sync imported accounts using truncated ERPNext names as
account_code (max 20 chars). GL entries were posted against these ERPNext-style
account IDs. A proper numbered chart of accounts was later created, but existing
GL entries were never remapped. This causes the trial balance to show an 843M NGN
imbalance because the TB only aggregates numbered accounts.

This migration:
1. Creates 5 new numbered bank accounts (1204–1208)
2. Records every remap in `_migration_account_remap` audit table
3. Updates account_id + account_code on ~16,363 posted_ledger_line rows
4. Updates account_id on corresponding journal_entry_line rows
5. Marks affected account_balance rows as stale for rebuild
6. Deactivates the 25 remapped ERPNext-style accounts

Revision ID: 20260301_remap_erpnext_accounts
Revises: 20260301_delete_voided_erpnext_invoices
Create Date: 2026-03-01
"""

from sqlalchemy import text as sa_text

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260301_remap_erpnext_accounts"
down_revision = "20260301_delete_voided_erpnext_invoices"
branch_labels = None
depends_on = None

ORG_ID = "00000000-0000-0000-0000-000000000001"
BANK_CATEGORY_ID = "2065b3bc-052e-42f7-8536-9af1bec26eae"

# 13 fiscal periods spanning Jan 2025 — Jan 2026
AFFECTED_PERIOD_IDS = [
    "9dd78ba6-a02d-4554-bd2b-fb98d7aa05be",  # Jan 2025
    "36acbe73-d949-475d-beab-6bbe556e57a0",  # Feb 2025
    "553aa0d1-f11e-4ce3-b1b7-a9da9b1e7251",  # Mar 2025
    "c7239527-fe4a-4d6d-a58a-2bc11af2959b",  # Apr 2025
    "15633447-3702-4983-8ef5-33d05db47c82",  # May 2025
    "ea5e26cd-2a11-4e07-b71f-a5ca442b4530",  # Jun 2025
    "7403406d-5c4a-41ed-8fb0-59d508ee7a27",  # Jul 2025
    "4714e7d4-b06b-46d0-b9e2-13be515e0f57",  # Aug 2025
    "5eef793a-4dba-4595-9ad7-56a02e9780e8",  # Sep 2025
    "3d7bdf66-e04b-4240-98be-f8ab41a16944",  # Oct 2025
    "b7fb70a9-4e54-4f27-aaa5-da73293cf5b8",  # Nov 2025
    "24b4727d-ca2e-48b5-8925-401098fdc2bb",  # Dec 2025
    "09c14950-61e4-4504-88d7-ba1ec8f8cc88",  # Jan 2026
]

# 5 new bank accounts to create (code, name)
NEW_BANK_ACCOUNTS = [
    ("1204", "Zenith 523 Bank"),
    ("1205", "Zenith 461 Bank"),
    ("1206", "Zenith 454 Bank"),
    ("1207", "Zenith USD Bank"),
    ("1208", "TAJ Bank"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Step 0: Create audit tracking table
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            CREATE TABLE IF NOT EXISTS _migration_account_remap (
                old_account_id UUID PRIMARY KEY,
                old_account_code TEXT,
                new_account_id UUID,
                new_account_code TEXT,
                rows_remapped_pll INTEGER DEFAULT 0,
                rows_remapped_jel INTEGER DEFAULT 0,
                remapped_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 1: Create 5 new numbered bank accounts (idempotent)
    # ------------------------------------------------------------------
    for code, name in NEW_BANK_ACCOUNTS:
        conn.execute(
            sa_text(
                """
                INSERT INTO gl.account (
                    account_id, organization_id, category_id, account_code,
                    account_name, account_type, normal_balance, is_active,
                    is_posting_allowed, is_budgetable, is_multi_currency,
                    is_reconciliation_required, is_cash_equivalent,
                    is_financial_instrument, subledger_type
                ) VALUES (
                    gen_random_uuid(), :org_id, :cat_id, :code,
                    :name, 'POSTING', 'DEBIT', true,
                    true, false, false,
                    true, true,
                    false, 'BANK'
                )
                ON CONFLICT (organization_id, account_code) DO NOTHING
                """
            ),
            {
                "org_id": ORG_ID,
                "cat_id": BANK_CATEGORY_ID,
                "code": code,
                "name": name,
            },
        )

    # ------------------------------------------------------------------
    # Step 2: Build mapping in audit table (ERPNext code → numbered code)
    #
    # Resolves account_ids via JOIN on account_code + org_id.
    # Note: some ERPNext codes have trailing spaces due to VARCHAR(20) truncation.
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            INSERT INTO _migration_account_remap
                (old_account_id, old_account_code, new_account_id, new_account_code)
            SELECT
                old_a.account_id, old_a.account_code,
                new_a.account_id, new_a.account_code
            FROM (VALUES
                ('Paystack OPEX - DT'::text,  '1211'::text),
                ('Zenith 523 Bank - DT',       '1204'),
                ('UBA Bank - DT',              '1202'),
                ('Paystack - DT',              '1211'),
                ('Expense Payable - DT',       '2000'),
                ('Zenith 461 Bank - DT',       '1205'),
                ('Zenith 454 Bank - DT',       '1206'),
                ('Zenith USD - DT',            '1207'),
                ('cash sales - DT',            '4010'),
                ('Accounts Receivable ',       '1400'),
                ('Trade and Other Paya',       '2000'),
                ('Flutterwave - DT',           '1212'),
                ('TAJ Bank - DT',              '1208'),
                ('Bandwidth and Interc',       '5030'),
                ('PAYE Payables - DT',         '2131'),
                ('Inventory - DT',             '1300'),
                ('Pension Payables - D',       '2130'),
                ('Telephone Expense - ',       '6023'),
                ('Base Station Repairs',       '6064'),
                ('Paye Expense - DT',          '6001'),
                ('Security and guards ',       '6011'),
                ('Stationery and print',       '6020'),
                ('Current Tax Payable ',       '2100'),
                ('Cash CBD - DT',              '1220'),
                ('NHF Payables - DT',          '2132')
            ) AS mapping(old_code, new_code)
            JOIN gl.account old_a
                ON old_a.account_code = mapping.old_code
                AND old_a.organization_id = :org_id
            JOIN gl.account new_a
                ON new_a.account_code = mapping.new_code
                AND new_a.organization_id = :org_id
            ON CONFLICT (old_account_id) DO NOTHING
            """
        ),
        {"org_id": ORG_ID},
    )

    # ------------------------------------------------------------------
    # Step 3: Pre-count rows per old account (for audit trail)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE _migration_account_remap r
            SET rows_remapped_pll = (
                    SELECT COUNT(*)
                    FROM gl.posted_ledger_line
                    WHERE account_id = r.old_account_id
                ),
                rows_remapped_jel = (
                    SELECT COUNT(*)
                    FROM gl.journal_entry_line
                    WHERE account_id = r.old_account_id
                )
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 4: Remap posted_ledger_line (account_id + denormalized account_code)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE gl.posted_ledger_line pll
            SET account_id = remap.new_account_id,
                account_code = remap.new_account_code
            FROM _migration_account_remap remap
            WHERE pll.account_id = remap.old_account_id
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 5: Remap journal_entry_line (account_id only — no denormalized code)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE gl.journal_entry_line jel
            SET account_id = remap.new_account_id
            FROM _migration_account_remap remap
            WHERE jel.account_id = remap.old_account_id
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 6: Mark account_balance stale for affected periods
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE gl.account_balance
            SET is_stale = true, stale_since = NOW()
            WHERE fiscal_period_id = ANY(:period_ids)
              AND organization_id = :org_id
            """
        ),
        {"period_ids": AFFECTED_PERIOD_IDS, "org_id": ORG_ID},
    )

    # ------------------------------------------------------------------
    # Step 7: Deactivate the 25 remapped ERPNext-style accounts
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE gl.account
            SET is_active = false
            WHERE account_id IN (
                SELECT old_account_id FROM _migration_account_remap
            )
            """
        )
    )


def downgrade() -> None:
    # The remap is reversible using the _migration_account_remap audit table:
    #
    #   UPDATE gl.posted_ledger_line pll
    #   SET account_id = r.old_account_id, account_code = r.old_account_code
    #   FROM _migration_account_remap r
    #   WHERE pll.account_id = r.new_account_id;
    #
    # However, two source accounts map to the same target (e.g. "Paystack OPEX - DT"
    # and "Paystack - DT" both → 1211), so a bulk reverse UPDATE cannot distinguish
    # which rows belonged to which source. Manual reversal with row-level tracking
    # would be required.
    #
    # The audit table is preserved for this purpose.
    pass
