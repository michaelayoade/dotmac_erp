"""fix_bank_account_gl_remap

The March 2026 Clean Sweep migration remapped ERPNext legacy GL accounts
(e.g. "Paystack - DT") to new numbered accounts (e.g. "1211") via the
``_migration_account_remap`` table.  However, it did not update
``banking.bank_accounts.gl_account_id``, leaving 9 active bank accounts
pointing to inactive legacy GL accounts.

This migration fixes the stale references using the existing remap table.

Revision ID: 964b7e6deaf7
Revises: 20260311_seed_ap_perms
Create Date: 2026-03-12 13:59:44.937039

"""

from alembic import op

revision = "964b7e6deaf7"
down_revision = "20260311_seed_ap_perms"
branch_labels = None
depends_on = None

# (bank_account_id, stale_gl_account_id, correct_gl_account_id)
_REMAP = [
    # Flutterwave -> 1212
    (
        "c3bef537-29c0-41c6-bd39-53b457ef3ada",
        "f361a518-7b03-4a46-b9c9-23b1cdd9c2ae",
        "4b4d61c3-b6db-4e58-819b-956d48efe71c",
    ),
    # Paystack -> 1211
    (
        "b8bc656d-3912-4b71-8ea5-9a06339741e2",
        "3c5eab01-be59-45c6-b5c4-9bab3cac9a68",
        "0ebe38df-36cc-4834-b3be-948410bd9565",
    ),
    # Paystack OPEX -> 1211
    (
        "894d00b1-1f90-4b71-8224-5493ab4e0d4b",
        "78ae1d9d-5fdd-4d98-b492-b0d50dba7622",
        "0ebe38df-36cc-4834-b3be-948410bd9565",
    ),
    # TAJ Bank -> 1208
    (
        "f0f0eef9-1dcb-47be-904e-df04580cab7a",
        "8db05682-1e3d-4dab-be14-15172f50e7cf",
        "cfa71fe7-09ce-498b-8f81-e76fb46d255d",
    ),
    # UBA Bank -> 1202
    (
        "08a39aba-65d6-4467-adbe-bd5a1588cee3",
        "9e438125-f8b2-4edf-b448-b832b3fff8f0",
        "0a060c86-2959-4123-a916-85c017e8a854",
    ),
    # Zenith 454 Bank -> 1206
    (
        "3f7fb574-6157-40d6-8a9d-60d2e2f3f5c3",
        "e83771ca-3cbc-48e8-a4dc-bd5ca090e1a0",
        "3606f308-815f-49aa-a002-9037cf7ed230",
    ),
    # Zenith 461 Bank -> 1205
    (
        "3e4e3ae5-254e-4dec-a4e6-0b83a39be936",
        "13e2ba89-ae0b-4315-8dc0-b7d101a3e6e9",
        "363538c5-914e-49b5-96ee-98dad2e1cc3a",
    ),
    # Zenith 523 Bank -> 1204
    (
        "6623d8a0-2716-4dfe-9bad-f71b1b82eca0",
        "2660a324-4fea-416b-9c42-5e8d501b1463",
        "42887868-731a-4e7e-8382-cef24606a741",
    ),
    # Zenith USD -> 1207
    (
        "b7bd91ae-b2af-477e-a5ef-e26546560066",
        "51c95069-bef1-4189-817b-386c53d5c13a",
        "2a0cd356-af53-4e5b-a8bb-2165cc621165",
    ),
]


def upgrade() -> None:
    for bank_id, stale_gl, correct_gl in _REMAP:
        # Idempotent: only update if still pointing to the stale GL
        op.execute(
            f"UPDATE banking.bank_accounts "
            f"SET gl_account_id = '{correct_gl}', "
            f"    updated_at = NOW() "
            f"WHERE bank_account_id = '{bank_id}' "
            f"  AND gl_account_id = '{stale_gl}'"
        )


def downgrade() -> None:
    for bank_id, stale_gl, correct_gl in _REMAP:
        op.execute(
            f"UPDATE banking.bank_accounts "
            f"SET gl_account_id = '{stale_gl}', "
            f"    updated_at = NOW() "
            f"WHERE bank_account_id = '{bank_id}' "
            f"  AND gl_account_id = '{correct_gl}'"
        )
