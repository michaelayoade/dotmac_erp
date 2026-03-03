"""
Phase 2: Map ERPNext accounts to DotMac numbered chart of accounts.

For each entry in ACCOUNT_MAPPING:
  1. Look up the DotMac account by code
  2. If missing, create a new account with correct category, normal_balance, type=POSTING
  3. Build account_map: ERPNext name → DotMac account_id UUID

Output: scripts/clean_sweep/_account_map.json

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase2_accounts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from scripts.clean_sweep.config import (
    ACCOUNT_MAPPING,
    NEW_ACCOUNTS,
    ORG_ID,
    USER_ID,
    setup_logging,
)

logger = setup_logging("phase2_accounts")

OUTPUT_FILE = Path(__file__).parent / "_account_map.json"


def main() -> None:
    from app.db import SessionLocal
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    with SessionLocal() as db:
        # Load all existing accounts by code
        stmt = select(Account).where(Account.organization_id == ORG_ID)
        existing = {a.account_code: a for a in db.scalars(stmt).all()}
        logger.info("Loaded %d existing accounts", len(existing))

        # Build the account map: ERPNext name → DotMac account_id
        account_map: dict[str, str] = {}  # erpnext_name → account_id (str)
        created_count = 0
        mapped_count = 0

        # First pass: create any new accounts that don't exist
        for code, (name, normal_bal, parent_code) in NEW_ACCOUNTS.items():
            if code in existing:
                logger.info("  Account %s (%s) already exists", code, name)
                continue

            # Inherit category_id from a sibling/parent account
            parent = existing.get(parent_code)
            if not parent:
                logger.warning(
                    "  Cannot create %s: parent %s not found", code, parent_code
                )
                continue

            new_account = Account(
                organization_id=ORG_ID,
                category_id=parent.category_id,
                account_code=code,
                account_name=name,
                account_type=AccountType.POSTING,
                normal_balance=(
                    NormalBalance.DEBIT
                    if normal_bal == "DEBIT"
                    else NormalBalance.CREDIT
                ),
                is_active=True,
                is_posting_allowed=True,
                created_by_user_id=USER_ID,
            )
            db.add(new_account)
            db.flush()
            existing[code] = new_account
            created_count += 1
            logger.info("  Created account %s: %s", code, name)

        db.commit()

        # Second pass: map every ERPNext account to a DotMac account_id
        unmapped: list[str] = []

        for erpnext_name, dotmac_code in ACCOUNT_MAPPING.items():
            account = existing.get(dotmac_code)
            if account:
                account_map[erpnext_name] = str(account.account_id)
                mapped_count += 1
            else:
                unmapped.append(f"{erpnext_name} → {dotmac_code}")

        if unmapped:
            logger.warning("  %d ERPNext accounts could not be mapped:", len(unmapped))
            for entry in unmapped:
                logger.warning("    UNMAPPED: %s", entry)

        # Write output file
        OUTPUT_FILE.write_text(json.dumps(account_map, indent=2))
        logger.info("-" * 60)
        logger.info(
            "Phase 2 complete. Mapped: %d, Created: %d, Unmapped: %d",
            mapped_count,
            created_count,
            len(unmapped),
        )
        logger.info("Output: %s", OUTPUT_FILE)


def load_account_map() -> dict[str, UUID]:
    """Load the account map from the JSON file (used by Phase 3+)."""
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(
            f"Account map not found: {OUTPUT_FILE}. Run phase2_accounts first."
        )
    raw = json.loads(OUTPUT_FILE.read_text())
    return {name: UUID(uid) for name, uid in raw.items()}


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 2 failed")
        sys.exit(1)
