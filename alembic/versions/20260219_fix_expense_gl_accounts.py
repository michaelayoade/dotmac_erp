"""Fix expense GL accounts: add expense_payable_account_id, remap categories.

Revision ID: 20260219_fix_expense_gl_accounts
Revises: 20260218_add_stamp_duty_support
Create Date: 2026-02-19

Fixes:
1. Adds expense_payable_account_id column to core_org.organization
   (points to 2030 Employee Reimbursables instead of 2110 WHT)
2. Remaps 56 expense categories from catch-all 6099 to proper GL accounts
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260219_fix_expense_gl_accounts"
down_revision = "20260218_add_stamp_duty_support"
branch_labels = None
depends_on = None

# Key account UUIDs
ACCT_2030_EMPLOYEE_REIMBURSABLES = "e97d4b48-1662-4c78-8734-c696319bef1b"
ACCT_6099_OTHER_EXPENSES = "0760bf65-029b-4af5-9a43-50c30fae9937"
ORG_ID = "00000000-0000-0000-0000-000000000001"

# Expense category → target GL account mappings (grouped by target)
# Format: { target_gl_account_uuid: [list of category_ids] }
CATEGORY_REMAPS: dict[str, list[str]] = {
    # 6024 Fuel & Lubricant (all BTS generators + fuel categories)
    "d1078c5e-4dec-4da0-adb1-84e3b0a1da86": [
        "d578546c-1c7c-4222-b96f-5edb9306bcbd",  # Airport BTS Generator
        "29448818-47da-4c21-8829-4d87a6dcc4f7",  # Allen BTS Generator
        "ed40c964-c2fb-4eb8-8d5e-cce870ba1961",  # Fuel/Mileage Expenses
        "01ac6102-68d7-4a35-85c4-7aafed608e48",  # Generator Fuel expense
        "a3e7ecdd-cffb-4d94-a806-c0e9f565104a",  # Gudu BTS Generator
        "3d3e9dcf-50e8-431a-a4cb-9e82623cc862",  # Gwarimpa BTS Generator
        "a9413043-ac90-4101-b044-660448c30e2a",  # Idu BTS Generator
        "a0c44744-1d48-4033-824c-6f27f9260e82",  # Jabi BTS Generator
        "3e6dd1cf-0ac3-499a-afe9-bc5fdbabeacc",  # Karasana BTS Generator
        "967a2781-c1a9-44e6-b149-53fe77066039",  # Karu BTS Generator
        "c1d784e5-7061-4af3-9e25-da4809970c92",  # Kubwa BTS Generator
        "115e0546-c2f7-4b52-a6f1-9035cd116337",  # Lokogoma BTS Generator
        "6b514307-58b9-41bc-a8ac-eaed2473ce9b",  # Lugbe BTS Generator
        "15fa0a98-2493-48a3-a8d8-d0d8eba17ce4",  # Maitama BTS Generator
        "68f221e0-3bd5-4c8a-9996-ec433ea04ec6",  # SPDC BTS Generator
        "67b7b987-9f98-4e35-b8ac-347f44680622",  # Vehicle Fuel Expense (dup)
    ],
    # 6081 Transportation & Travelling Expenses
    "3aa268f1-6f2c-4991-9f81-f2170310a47a": [
        "17a9c686-169a-4de8-9513-20757ace51ef",  # Transportation Expense
        "c7bff9ab-3177-4362-9638-732b554b2f90",  # Travel
        "08867097-0c43-427b-ac34-e7304facf8fd",  # Travel Expense
        "3971e255-f598-49ea-86bc-48424e7bb066",  # Parking
    ],
    # 6082 Shipping & Delivery Expenses
    "8649711d-6664-4414-bf43-c688061b91a8": [
        "c8612d85-0f11-4272-8598-8db305339c04",  # Freight and delivery - COS
        "f760c710-34c8-4f5c-b1ae-8e6e3af64e2d",  # Shipping and delivery expense
        "fdbcd466-95f3-4a00-ba60-083d1147a1f5",  # Site Logistics
    ],
    # 6004 Contract Labour & Logistics
    "0b7fdd03-3169-4653-8c26-05d65589d1ed": [
        "07fe9c35-8e8a-4631-a65b-1763e632fad9",  # Direct labour - COS
        "994cccf1-4bfd-4bcd-9842-a332c95cd075",  # Subcontractors - COS
    ],
    # 5000 Purchases
    "4ffd63d9-a9f4-4c68-b156-c98bb696fa16": [
        "7fef132b-c80a-49ce-aa2b-92099e950f21",  # Materials - COS
        "5114d1da-09b1-4b26-b8e1-cfe32ce6818e",  # Purchases
    ],
    # 5030 Purchase of bandwidth and Interconnect
    "b3811a2c-e1d4-4ccd-9d1b-15f932588555": [
        "bb7b0722-c48f-4d54-bb89-9a692f042ea5",  # Bandwidth and Interconnect
    ],
    # 6083 Advertising Expenses
    "914f90e7-f087-40e3-ba4b-7a4bc3e08e24": [
        "7ee07f66-107e-4777-be91-10e5432cf384",  # Advertising Expenses and Marketing
    ],
    # 6080 Finance Cost
    "e3b904ab-57bd-4429-95a8-a1438ae4ecca": [
        "931317b5-1cf3-4a7b-bf0c-03dbb11faf33",  # Bank Charges
    ],
    # 6064 Base Station Repairs and Maintenance
    "9619cf78-ec93-4f64-a6d5-2c28b54f2676": [
        "a5f0547e-71ca-4ff7-9e58-6148317cc233",  # Base Station Repairs and Maintenance
    ],
    # 6023 Telephone bills
    "87791226-6b21-4fe9-9409-d253a6defb0e": [
        "23c3682d-eef7-49d8-84e3-55a677f4de49",  # Calls
        "97b40e77-f18f-4ec3-9931-237b98b8cf51",  # Telephone Expense
    ],
    # 6053 Motor Vehicle Repairs & Maintenance
    "fe5a9e2f-6575-49f2-a931-4a3454ec7c2a": [
        "46c017de-901d-4f5e-8952-58d9b2d6e872",  # Car Repairs and Maintenance
    ],
    # 6060 Commission & Fees
    "dd85442f-2546-4657-aa19-7edb8304693a": [
        "c5bf1ec4-4251-4585-9717-7f7ce323797e",  # Commissions and Fees
    ],
    # 6052 Equipment rental
    "9a868f89-59e5-42f5-87d2-a1b9a57f8197": [
        "38ebf6fe-8a54-4aa0-978e-5b77e1622a2c",  # Equipment Rental
    ],
    # 6010 Statutory Expenses
    "ce72965e-240e-4d01-bb0a-f65e286ba418": [
        "6c82fc9b-a37c-4f1c-95cd-a3740fd119a7",  # fees and Licenses
        "7082b8d4-9789-40cc-a522-61bc3ac7df75",  # Statutory Payments
    ],
    # 6051 Entertainment
    "9feea755-680e-49d6-a8e3-47e0723ec65b": [
        "e17c3574-a800-4f08-a9c9-708d4629325a",  # Food
        "cb19f9e4-1ae6-410e-985b-6fd6faaf081c",  # Meals and Entertainment
    ],
    # 6063 Contract Tender Fees
    "b9ab2119-911c-45ad-b980-3ff67347fb93": [
        "757e347f-cca8-43ff-88ad-6aa364272087",  # Government Tender Fees
    ],
    # 6061 Insurance Expenses
    "9af18122-3174-4018-82dc-d166bb90c9e1": [
        "852c2dd0-54a4-4a2f-991b-2a0f9d81ae5f",  # Insurance - General
    ],
    # 6032 IT & Internet Expenses
    "ec82a615-1b19-479a-98f1-1b44a9507a01": [
        "09e11f94-c6c8-4e3f-8e31-99e4424bba92",  # IT and Internet Expenses
        "2b03af42-969f-411c-9cd7-75ba880dd0b4",  # Repair of Computers
    ],
    # 6033 Janitorial Expenses
    "3cad9141-6084-47ee-bf6d-fdecab72c769": [
        "dcc620f9-9189-481d-90da-54fb24aa98ba",  # Janitorial Expense
    ],
    # 6070 Legal & Professional Fee
    "1f8d7c5d-da96-4428-b07c-2ca6e236b728": [
        "439ec4ce-2a38-4427-a184-5e847971adad",  # Legal and Professional Fees
    ],
    # 6013 Medical Expenses
    "aac67927-e973-4602-851e-f5a0b0396229": [
        "4f933082-1dc8-4aa3-8781-7e98d36b746d",  # Medical
        "5e5e3bfb-9ffb-4150-96a6-00bf3ebc567f",  # Medical Expenses
    ],
    # 6050 Office Repairs & Maintenance
    "a2bfa2c0-c4ef-4c81-948a-aa4eb4cefb46": [
        "fb5c43b0-1073-42da-b81d-cbf82ab6e6cb",  # Office Repairs and Maintenance
    ],
    # 6001 PAYE expenses
    "db3755fa-f480-4219-8776-9e4bbccca5c1": [
        "cea7bd80-164f-4b81-b9b1-3baf3887fe56",  # Paye Expense
    ],
    # 6011 Security Expenses
    "505eee37-5527-408f-9803-18f025e2f477": [
        "9c46a531-acda-464e-b0d8-6e0ade72bb17",  # Security and Guards
    ],
    # 6003 Staff Training
    "32362ce1-0d5f-4a73-b54b-15dcd758dc08": [
        "069224dc-8c14-4499-8a8d-b8ec2eceecd1",  # Staff training
        "c8c0203a-ce26-4151-bd62-6e66c7667eae",  # Training
    ],
    # 6020 Printing & stationery
    "321e2e76-ac05-4db0-9d61-38020e12315d": [
        "e9f57792-d868-4c0d-ac05-761b12e80619",  # Stationery and printing
    ],
    # 6012 Subscription & Renewal
    "e68f8ebc-7e8b-4015-89ad-4c5f39f5a4d6": [
        "1fa4e63b-1806-4af6-badc-027b48907484",  # Subscriptions and Renewals
    ],
    # 6022 Utilities
    "2b48d665-9774-48d9-8f4f-bcb7be9a2d79": [
        "af49e4c4-7761-4fe3-8c45-037e8df92e27",  # Utilities
    ],
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── Step 1: Add expense_payable_account_id column ──
    columns = {
        c["name"] for c in inspector.get_columns("organization", schema="core_org")
    }

    if "expense_payable_account_id" not in columns:
        op.add_column(
            "organization",
            sa.Column(
                "expense_payable_account_id",
                UUID(as_uuid=True),
                sa.ForeignKey("gl.account.account_id"),
                nullable=True,
                comment="Payable account for employee expense reimbursements (credit)",
            ),
            schema="core_org",
        )

    # ── Step 2: Set expense_payable_account_id to 2030 for production org ──
    conn.execute(
        sa.text(
            """
            UPDATE core_org.organization
            SET expense_payable_account_id = :account_id
            WHERE organization_id = :org_id
              AND (expense_payable_account_id IS NULL
                   OR expense_payable_account_id != :account_id)
            """
        ),
        {"account_id": ACCT_2030_EMPLOYEE_REIMBURSABLES, "org_id": ORG_ID},
    )

    # ── Step 3: Remap expense categories to proper GL accounts ──
    # Only update categories currently pointing to 6099 (idempotent guard).
    for target_account_id, category_ids in CATEGORY_REMAPS.items():
        placeholders = ", ".join(f":cat_{i}" for i in range(len(category_ids)))
        params: dict[str, str] = {
            "target": target_account_id,
            "old_account": ACCT_6099_OTHER_EXPENSES,
        }
        for i, cat_id in enumerate(category_ids):
            params[f"cat_{i}"] = cat_id

        conn.execute(
            sa.text(
                f"""
                UPDATE expense.expense_category
                SET expense_account_id = :target
                WHERE category_id IN ({placeholders})
                  AND expense_account_id = :old_account
                """
            ),
            params,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── Revert Step 3: Reset all remapped categories back to 6099 ──
    all_category_ids: list[str] = []
    for cat_ids in CATEGORY_REMAPS.values():
        all_category_ids.extend(cat_ids)

    if all_category_ids:
        placeholders = ", ".join(f":cat_{i}" for i in range(len(all_category_ids)))
        params: dict[str, str] = {"target": ACCT_6099_OTHER_EXPENSES}
        for i, cat_id in enumerate(all_category_ids):
            params[f"cat_{i}"] = cat_id

        conn.execute(
            sa.text(
                f"""
                UPDATE expense.expense_category
                SET expense_account_id = :target
                WHERE category_id IN ({placeholders})
                """
            ),
            params,
        )

    # ── Revert Step 2: Clear expense_payable_account_id ──
    conn.execute(
        sa.text(
            """
            UPDATE core_org.organization
            SET expense_payable_account_id = NULL
            WHERE organization_id = :org_id
            """
        ),
        {"org_id": ORG_ID},
    )

    # ── Revert Step 1: Drop column ──
    columns = {
        c["name"] for c in inspector.get_columns("organization", schema="core_org")
    }
    if "expense_payable_account_id" in columns:
        op.drop_column("organization", "expense_payable_account_id", schema="core_org")
