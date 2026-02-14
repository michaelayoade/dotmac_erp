"""
Backfill erpnext_id on master data models from sync.sync_entity records.

Many models have the erpnext_id column (from ERPNextSyncMixin or migration)
but 0% populated. This script populates them by joining to sync.sync_entity
which tracks the mapping from ERPNext document name to local UUID.

Usage:
    python scripts/backfill_erpnext_ids.py --dry-run   # Preview counts
    python scripts/backfill_erpnext_ids.py --execute    # Actually update
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Mapping: (schema, table, pk_column) -> ERPNext source_doctype
# target_table in sync_entity uses "schema.table" format
BACKFILL_TARGETS = [
    ("gl", "account", "account_id", "Account"),
    ("gl", "account_category", "category_id", "Account"),
    ("ap", "supplier", "supplier_id", "Supplier"),
    ("inv", "item", "item_id", "Item"),
    ("inv", "item_category", "category_id", "Item Group"),
    ("inv", "warehouse", "warehouse_id", "Warehouse"),
    ("fa", "asset", "asset_id", "Asset"),
    ("fa", "asset_category", "category_id", "Asset Category"),
    ("hr", "employee", "employee_id", "Employee"),
    ("expense", "expense_category", "category_id", "Expense Claim Type"),
    ("expense", "expense_claim", "claim_id", "Expense Claim"),
]


def backfill(execute: bool = False) -> None:
    """Backfill erpnext_id from sync.sync_entity records."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        conn = db.connection()

        for schema, table, pk_col, doctype in BACKFILL_TARGETS:
            fqn = f"{schema}.{table}"

            # Count how many records need backfill
            count_sql = f"""
                SELECT COUNT(*)
                FROM {fqn} t
                JOIN sync.sync_entity se
                  ON se.target_id = t.{pk_col}
                 AND se.source_system = 'erpnext'
                 AND se.source_doctype = :doctype
                 AND se.sync_status = 'SYNCED'
                 AND se.target_table = :target_table
                WHERE t.erpnext_id IS NULL
                  AND se.source_name IS NOT NULL
            """  # noqa: S608
            from sqlalchemy import text

            result = conn.execute(
                text(count_sql),
                {"doctype": doctype, "target_table": fqn},
            )
            pending = result.scalar() or 0

            # Count already populated
            already_sql = f"""
                SELECT COUNT(*) FROM {fqn} WHERE erpnext_id IS NOT NULL
            """  # noqa: S608
            already = conn.execute(text(already_sql)).scalar() or 0

            total_sql = f"SELECT COUNT(*) FROM {fqn}"  # noqa: S608
            total = conn.execute(text(total_sql)).scalar() or 0

            logger.info(
                "%s: %d total, %d already have erpnext_id, %d to backfill from sync_entity",
                fqn,
                total,
                already,
                pending,
            )

            if pending == 0:
                continue

            if not execute:
                logger.info("  [DRY RUN] Would update %d records", pending)
                continue

            # Perform the update
            update_sql = f"""
                UPDATE {fqn} AS t
                SET erpnext_id = se.source_name,
                    last_synced_at = se.synced_at
                FROM sync.sync_entity se
                WHERE se.target_id = t.{pk_col}
                  AND se.source_system = 'erpnext'
                  AND se.source_doctype = :doctype
                  AND se.sync_status = 'SYNCED'
                  AND se.target_table = :target_table
                  AND t.erpnext_id IS NULL
                  AND se.source_name IS NOT NULL
            """  # noqa: S608
            result = conn.execute(
                text(update_sql),
                {"doctype": doctype, "target_table": fqn},
            )
            updated = result.rowcount
            logger.info("  Updated %d records", updated)

        if execute:
            db.commit()
            logger.info("All backfills committed.")
        else:
            logger.info("\n[DRY RUN] No changes made. Use --execute to apply.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill erpnext_id on master data from sync_entity records"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview counts only")
    group.add_argument("--execute", action="store_true", help="Actually update records")
    args = parser.parse_args()

    backfill(execute=args.execute)


if __name__ == "__main__":
    main()
