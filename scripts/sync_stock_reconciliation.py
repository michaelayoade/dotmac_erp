#!/usr/bin/env python3
"""
Sync ERPNext Stock Reconciliation → DotMac InventoryCount + InventoryCountLine.

Imports submitted (docstatus=1) Stock Reconciliation records as physical
stock counts with their line items.

Usage:
    python scripts/sync_stock_reconciliation.py              # Preview
    python scripts/sync_stock_reconciliation.py --sync       # Run sync
"""

import argparse
import logging
import os
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db import SessionLocal
from app.models.inventory.inventory_count import CountStatus, InventoryCount
from app.models.inventory.inventory_count_line import InventoryCountLine
from app.models.sync import SyncEntity, SyncStatus
from app.services.erpnext.client import ERPNextClient, ERPNextConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erp.dotmac.ng")
ERPNEXT_API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")
ERPNEXT_COMPANY = "Dotmac Technologies"


def _resolve_entity_id(
    db, organization_id: uuid.UUID, source_name: str, source_doctype: str
) -> uuid.UUID | None:
    """Resolve DotMac entity ID from ERPNext source name via SyncEntity."""
    result = db.execute(
        select(SyncEntity).where(
            SyncEntity.organization_id == organization_id,
            SyncEntity.source_system == "erpnext",
            SyncEntity.source_doctype == source_doctype,
            SyncEntity.source_name == source_name,
        )
    ).scalar_one_or_none()
    if result and result.target_id:
        return result.target_id
    return None


def _resolve_fiscal_period_id(
    db, organization_id: uuid.UUID, posting_date: date, cache: dict
) -> uuid.UUID | None:
    """Resolve fiscal period for a posting date, auto-creating if needed."""
    cache_key = f"{posting_date.year}-{posting_date.month:02d}"
    if cache_key in cache:
        return cache[cache_key]

    from app.services.finance.gl.period_guard import PeriodGuardService

    period = PeriodGuardService._ensure_period_exists(db, organization_id, posting_date)
    if period:
        cache[cache_key] = period.fiscal_period_id
        return period.fiscal_period_id
    return None


def _already_synced(db, organization_id: uuid.UUID, source_name: str) -> bool:
    """Check if a Stock Reconciliation has already been synced."""
    return (
        db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Stock Reconciliation",
                SyncEntity.source_name == source_name,
                SyncEntity.sync_status == SyncStatus.SYNCED,
            )
        ).scalar_one_or_none()
        is not None
    )


def _parse_date(posting_date: str | None) -> date:
    if not posting_date:
        return date.today()
    try:
        return datetime.strptime(str(posting_date).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def sync_stock_reconciliations(dry_run: bool = True) -> dict:
    """Sync Stock Reconciliation records from ERPNext."""
    config = ERPNextConfig(
        url=ERPNEXT_URL,
        api_key=ERPNEXT_API_KEY,
        api_secret=ERPNEXT_API_SECRET,
        company=ERPNEXT_COMPANY,
    )

    stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0, "lines_created": 0}
    period_cache: dict[str, uuid.UUID] = {}

    db = SessionLocal()
    try:
        # Get org + user
        from app.models.finance.core_org import Organization
        from app.models.person import Person

        org = db.query(Organization).first()
        if not org:
            print("ERROR: No organization found")
            return stats
        organization_id = org.organization_id

        person = (
            db.query(Person)
            .filter(
                Person.organization_id == organization_id, Person.is_active.is_(True)
            )
            .first()
        )
        if not person:
            print("ERROR: No active user found")
            return stats
        user_id = person.id

        print(f"Organization: {org.legal_name} ({organization_id})")
        print(f"User: {person.id}")

        with ERPNextClient(config) as client:
            # Get all Stock Reconciliation names
            all_recs = client.list_documents(
                "Stock Reconciliation",
                filters={"docstatus": 1, "company": ERPNEXT_COMPANY},
                fields=["name"],
                limit_page_length=100,
                order_by="posting_date asc",
            )
            stats["total"] = len(all_recs)
            print(
                f"\nFound {stats['total']} submitted Stock Reconciliations in ERPNext\n"
            )

            for rec_ref in all_recs:
                source_name = rec_ref["name"]

                # Skip already synced
                if _already_synced(db, organization_id, source_name):
                    print(f"  SKIP {source_name} (already synced)")
                    stats["skipped"] += 1
                    continue

                try:
                    # Fetch full document with child items
                    doc = client.get_document("Stock Reconciliation", source_name)
                    items = doc.get("items", [])
                    posting_date = _parse_date(doc.get("posting_date"))

                    if dry_run:
                        print(
                            f"  PREVIEW {source_name}: {posting_date}, {len(items)} items"
                        )
                        for it in items:
                            print(
                                f"    {it.get('item_code')} @ {it.get('warehouse')}: "
                                f"system={it.get('current_qty')}, counted={it.get('qty')}, "
                                f"diff={it.get('quantity_difference')}"
                            )
                        stats["synced"] += 1
                        continue

                    # Resolve fiscal period
                    fiscal_period_id = _resolve_fiscal_period_id(
                        db, organization_id, posting_date, period_cache
                    )
                    if not fiscal_period_id:
                        print(
                            f"  ERROR {source_name}: Cannot resolve fiscal period for {posting_date}"
                        )
                        stats["errors"] += 1
                        continue

                    # Determine warehouse (use first item's warehouse, or None if mixed)
                    warehouses_in_doc = {
                        it.get("warehouse") for it in items if it.get("warehouse")
                    }
                    warehouse_id = None
                    if len(warehouses_in_doc) == 1:
                        wh_name = next(iter(warehouses_in_doc))
                        warehouse_id = _resolve_entity_id(
                            db, organization_id, wh_name, "Warehouse"
                        )

                    # Count variances
                    items_with_variance = sum(
                        1
                        for it in items
                        if it.get("quantity_difference")
                        and float(it["quantity_difference"]) != 0
                    )

                    # Create InventoryCount header
                    count = InventoryCount(
                        organization_id=organization_id,
                        count_number=source_name,
                        count_description=f"ERPNext Stock Reconciliation: {doc.get('purpose', '')}",
                        count_date=posting_date,
                        fiscal_period_id=fiscal_period_id,
                        warehouse_id=warehouse_id,
                        is_full_count=False,
                        is_cycle_count=False,
                        status=CountStatus.POSTED,
                        total_items=len(items),
                        items_counted=len(items),
                        items_with_variance=items_with_variance,
                        created_by_user_id=user_id,
                        posted_by_user_id=user_id,
                    )
                    db.add(count)
                    db.flush()  # Get count_id

                    # Create InventoryCountLine for each item
                    lines_created = 0
                    for it in items:
                        item_code = it.get("item_code")
                        wh_name = it.get("warehouse")

                        item_id = _resolve_entity_id(
                            db, organization_id, item_code, "Item"
                        )
                        if not item_id:
                            logger.warning(
                                "Cannot resolve item_id for %s, skipping line",
                                item_code,
                            )
                            continue

                        line_warehouse_id = _resolve_entity_id(
                            db, organization_id, wh_name, "Warehouse"
                        )
                        if not line_warehouse_id:
                            logger.warning(
                                "Cannot resolve warehouse_id for %s, skipping line",
                                wh_name,
                            )
                            continue

                        current_qty = Decimal(str(it.get("current_qty") or 0))
                        counted_qty = Decimal(str(it.get("qty") or 0))
                        qty_diff = Decimal(str(it.get("quantity_difference") or 0))
                        val_rate = Decimal(str(it.get("valuation_rate") or 0))
                        amt_diff = Decimal(str(it.get("amount_difference") or 0))

                        # Variance percent
                        variance_pct = None
                        if current_qty != 0:
                            variance_pct = (qty_diff / current_qty) * Decimal("100")

                        line = InventoryCountLine(
                            count_id=count.count_id,
                            item_id=item_id,
                            warehouse_id=line_warehouse_id,
                            system_quantity=current_qty,
                            uom=it.get("stock_uom") or "Nos",
                            counted_quantity=counted_qty,
                            final_quantity=counted_qty,
                            variance_quantity=qty_diff,
                            variance_value=amt_diff,
                            variance_percent=variance_pct,
                            unit_cost=val_rate,
                            counted_by_user_id=user_id,
                        )
                        db.add(line)
                        lines_created += 1

                    # Create SyncEntity tracking record
                    sync_entity = SyncEntity(
                        organization_id=organization_id,
                        source_system="erpnext",
                        source_doctype="Stock Reconciliation",
                        source_name=source_name,
                        target_table="inv.inventory_count",
                        target_id=count.count_id,
                        sync_status=SyncStatus.SYNCED,
                    )
                    db.add(sync_entity)
                    db.flush()

                    stats["synced"] += 1
                    stats["lines_created"] += lines_created
                    print(
                        f"  SYNCED {source_name}: {posting_date}, {lines_created} lines, {items_with_variance} variances"
                    )

                except Exception:
                    logger.exception("Error syncing %s", source_name)
                    stats["errors"] += 1
                    db.rollback()

            if not dry_run:
                db.commit()
                print("\nCommitted to database.")

    except Exception as e:
        db.rollback()
        logger.exception("Sync failed: %s", e)
    finally:
        db.close()

    return stats


def main():
    print("ERPNext API sync is disabled. Use SQL-based sync tooling.")
    raise SystemExit(2)

    parser = argparse.ArgumentParser(
        description="Sync ERPNext Stock Reconciliation to DotMac InventoryCount"
    )
    parser.add_argument(
        "--sync", action="store_true", help="Run actual sync (default: preview only)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("ERPNext Stock Reconciliation → DotMac Inventory Counts")
    print("=" * 60)

    if not args.sync:
        print("MODE: Preview (use --sync to write to DB)\n")
    else:
        print("MODE: Sync\n")

    stats = sync_stock_reconciliations(dry_run=not args.sync)

    print(f"\n{'=' * 60}")
    print(f"Total:   {stats['total']}")
    print(f"Synced:  {stats['synced']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Errors:  {stats['errors']}")
    if stats["lines_created"]:
        print(f"Lines:   {stats['lines_created']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
