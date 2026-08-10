"""
Audit and rebuild the inventory WAC ledger from historical transactions.

Plain terms:
- The system already has inventory movement history in `inv.inventory_transaction`.
- The valuation report reads `inv.item_wac_ledger`.
- This script replays the transaction history and repopulates the WAC ledger.

Usage:
    poetry run python -m scripts.rebuild_inventory_wac_ledger --dry-run
    poetry run python -m scripts.rebuild_inventory_wac_ledger --execute
    poetry run python -m scripts.rebuild_inventory_wac_ledger --dry-run --org-id <UUID>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models.finance.gl.account import Account  # noqa: E402
from app.models.finance.gl.account_balance import AccountBalance  # noqa: E402
from app.models.inventory.inventory_lot import InventoryLot  # noqa: E402
from app.models.inventory.inventory_lot_balance import InventoryLotBalance  # noqa: E402
from app.models.inventory.inventory_transaction import InventoryTransaction  # noqa: E402
from app.models.inventory.item import CostingMethod, Item  # noqa: E402
from app.models.inventory.item_wac_ledger import ItemWACLedger  # noqa: E402
from app.services.inventory.wac_valuation import WACValuationService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rebuild_inventory_wac_ledger")


def _audit_snapshot(org_id: UUID | None = None) -> dict[str, int]:
    with SessionLocal() as db:
        item_stmt = select(func.count(Item.item_id))
        wac_item_stmt = select(func.count(Item.item_id)).where(
            Item.costing_method == CostingMethod.WEIGHTED_AVERAGE
        )
        fifo_item_stmt = select(func.count(Item.item_id)).where(
            Item.costing_method == CostingMethod.FIFO
        )
        txn_stmt = select(func.count(InventoryTransaction.transaction_id))
        wac_ledger_stmt = select(func.count(ItemWACLedger.id))
        lot_stmt = select(func.count(InventoryLot.lot_id))
        lot_balance_stmt = select(func.count(InventoryLotBalance.lot_balance_id))
        gl_stmt = (
            select(func.count(AccountBalance.balance_id))
            .join(Account, Account.account_id == AccountBalance.account_id)
            .where(Account.subledger_type == "INVENTORY")
        )

        if org_id is not None:
            item_stmt = item_stmt.where(Item.organization_id == org_id)
            wac_item_stmt = wac_item_stmt.where(Item.organization_id == org_id)
            fifo_item_stmt = fifo_item_stmt.where(Item.organization_id == org_id)
            txn_stmt = txn_stmt.where(InventoryTransaction.organization_id == org_id)
            wac_ledger_stmt = wac_ledger_stmt.where(
                ItemWACLedger.organization_id == org_id
            )
            lot_stmt = lot_stmt.where(InventoryLot.organization_id == org_id)
            lot_balance_stmt = lot_balance_stmt.where(
                InventoryLotBalance.organization_id == org_id
            )
            gl_stmt = gl_stmt.where(AccountBalance.organization_id == org_id)

        return {
            "items": db.scalar(item_stmt) or 0,
            "wac_items": db.scalar(wac_item_stmt) or 0,
            "fifo_items": db.scalar(fifo_item_stmt) or 0,
            "transactions": db.scalar(txn_stmt) or 0,
            "wac_ledger_rows": db.scalar(wac_ledger_stmt) or 0,
            "fifo_lots": db.scalar(lot_stmt) or 0,
            "fifo_lot_balances": db.scalar(lot_balance_stmt) or 0,
            "gl_inventory_balances": db.scalar(gl_stmt) or 0,
        }


def _print_audit(audit: dict[str, int], *, label: str) -> None:
    logger.info("%s", label)
    logger.info("  Items: %s", audit["items"])
    logger.info("  WAC items: %s", audit["wac_items"])
    logger.info("  FIFO items: %s", audit["fifo_items"])
    logger.info("  Inventory transactions: %s", audit["transactions"])
    logger.info("  WAC ledger rows: %s", audit["wac_ledger_rows"])
    logger.info("  FIFO lots: %s", audit["fifo_lots"])
    logger.info("  FIFO lot balances: %s", audit["fifo_lot_balances"])
    logger.info("  GL inventory balances: %s", audit["gl_inventory_balances"])


def rebuild(
    *,
    execute: bool,
    org_id: UUID | None = None,
) -> None:
    before = _audit_snapshot(org_id)
    _print_audit(before, label="Audit before rebuild")

    with SessionLocal() as db:
        service = WACValuationService(db)
        result = service.rebuild_ledger_from_transactions(
            organization_id=org_id,
            persist=execute,
            replace_existing=execute,
        )

        logger.info("Replay result")
        logger.info("  Rows computed: %s", result["rows_computed"])
        logger.info("  Rows written: %s", result["rows_written"])
        logger.info("  Transactions replayed: %s", result["transactions_replayed"])

        if execute:
            db.commit()
            logger.info("WAC ledger rebuild committed.")
        else:
            db.rollback()
            logger.info("[DRY RUN] No changes committed.")

    after = _audit_snapshot(org_id)
    _print_audit(after, label="Audit after rebuild")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and rebuild inv.item_wac_ledger from inv.inventory_transaction"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview rebuild only")
    mode.add_argument("--execute", action="store_true", help="Persist rebuilt rows")
    parser.add_argument(
        "--org-id",
        type=str,
        default=None,
        help="Optional organization UUID scope",
    )
    args = parser.parse_args()

    org_id = UUID(args.org_id) if args.org_id else None
    rebuild(execute=args.execute, org_id=org_id)


if __name__ == "__main__":
    main()
