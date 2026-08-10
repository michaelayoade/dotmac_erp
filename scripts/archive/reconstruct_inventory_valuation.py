"""Reconstruct inv.inventory_valuation snapshots from inventory_transaction history.

Why
---
``inv.inventory_valuation`` is empty even though ``1300 Materials`` carries
~NGN 136M in the GL. Without month-end valuation snapshots, the auditor
has no system-resident evidence that inventory at any 2025 month-end ties
to the GL balance. This script replays every ``inv.inventory_transaction``
chronologically, maintains running WAC per ``(item_id, warehouse_id)``,
and writes a valuation row at each requested as-of date.

Method
------
Weighted Average Cost (WAC) reconstruction:

* RECEIPT / RETURN   → qty_in × unit_cost; new WAC = total_value / total_qty
* ISSUE / SALE       → qty_out drawn at the *running WAC* at issue time
                       (the journal-entry unit_cost on the issue row may
                       be zero or stale; we ignore it for valuation)
* ADJUSTMENT (qty=0) → value-only adjustment; defaults to +total_cost
                       (review individually if the year-end total varies
                       from GL by an ADJUSTMENT-sized amount)
* to_warehouse_id    → treated as transfer: source decrements at WAC,
                       destination receives at the same per-unit value

Idempotency
-----------
Each (organization_id, fiscal_period_id, valuation_date, item_id,
warehouse_id, lot_id) tuple is the natural key. Re-runs delete and
re-insert for the requested as-of date — safe because nothing else
writes to ``inv.inventory_valuation`` (table is empty in production).

Usage
-----

    # Dry run for the year-end (default — shows totals and top items)
    python scripts/reconstruct_inventory_valuation.py

    # Execute for the year-end (writes inv.inventory_valuation rows)
    python scripts/reconstruct_inventory_valuation.py --execute

    # Other as-of dates
    python scripts/reconstruct_inventory_valuation.py --as-of 2025-06-30 --execute

    # All twelve 2025 month-ends in one run
    python scripts/reconstruct_inventory_valuation.py --all-2025-month-ends --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from collections.abc import Iterable
from uuid import UUID, uuid4

sys.path.insert(0, ".")

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.finance.gl.fiscal_period import FiscalPeriod  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("reconstruct_inventory_valuation")
for noisy in ("sqlalchemy.engine",):
    logging.getLogger(noisy).setLevel(logging.WARNING)


ZERO = Decimal("0")


@dataclass
class StockState:
    qty: Decimal = ZERO
    value: Decimal = ZERO

    @property
    def wac(self) -> Decimal:
        return (self.value / self.qty) if self.qty > 0 else ZERO


def fetch_transactions_through(db: Session, as_of: date) -> list[dict]:
    """All inventory transactions with transaction_date <= as_of, ordered for replay."""
    stmt = text(
        """
        SELECT transaction_id, organization_id, transaction_type,
               transaction_date::date AS tx_date,
               item_id, warehouse_id, lot_id,
               to_warehouse_id,
               quantity, unit_cost, total_cost
        FROM inv.inventory_transaction
        WHERE transaction_date::date <= :as_of
        ORDER BY transaction_date, transaction_id
        """
    )
    rows = db.execute(stmt, {"as_of": as_of}).mappings().all()
    return [dict(r) for r in rows]


def replay(transactions: Iterable[dict]) -> dict[tuple, StockState]:
    """Run all transactions and return final state per (org, item, warehouse, lot)."""
    state: dict[tuple, StockState] = defaultdict(StockState)

    for tx in transactions:
        key = (
            tx["organization_id"],
            tx["item_id"],
            tx["warehouse_id"],
            tx["lot_id"],
        )
        s = state[key]
        ttype = tx["transaction_type"]
        qty = Decimal(str(tx["quantity"] or 0))
        total_cost = Decimal(str(tx["total_cost"] or 0))

        if ttype in ("RECEIPT", "RETURN"):
            s.qty += qty
            s.value += total_cost
        elif ttype in ("ISSUE", "SALE"):
            cost_out = s.wac * qty
            s.qty -= qty
            s.value -= cost_out
            # If this is a transfer (has to_warehouse), credit the destination at same per-unit value
            if tx["to_warehouse_id"]:
                dest_key = (
                    tx["organization_id"],
                    tx["item_id"],
                    tx["to_warehouse_id"],
                    tx["lot_id"],
                )
                d = state[dest_key]
                d.qty += qty
                d.value += cost_out
        elif ttype == "ADJUSTMENT":
            # qty=0 by observation; treat as value-only +total_cost.
            s.value += total_cost
        else:
            logger.warning(
                "Unknown transaction_type %s — skipping tx %s",
                ttype,
                tx["transaction_id"],
            )

    return state


def fiscal_period_for(db: Session, org_id: UUID, valuation_date: date) -> UUID:
    stmt = select(FiscalPeriod).where(
        FiscalPeriod.organization_id == org_id,
        FiscalPeriod.start_date <= valuation_date,
        FiscalPeriod.end_date >= valuation_date,
    )
    period = db.scalar(stmt)
    if not period:
        raise RuntimeError(
            f"No fiscal period covers {valuation_date} for org {org_id}. "
            "Run the period-undo migration or create the period first."
        )
    return period.fiscal_period_id


def write_valuation(
    db: Session,
    state: dict[tuple, StockState],
    valuation_date: date,
) -> tuple[int, Decimal]:
    """Delete + insert valuation rows for the as-of date. Returns (rows, total_value)."""
    if not state:
        return 0, ZERO

    # Clear any existing rows for this as-of date so reruns are idempotent.
    db.execute(
        text("DELETE FROM inv.inventory_valuation WHERE valuation_date = :d"),
        {"d": valuation_date},
    )

    period_cache: dict[UUID, UUID] = {}
    rows_written = 0
    total_value = ZERO

    insert_sql = text(
        """
        INSERT INTO inv.inventory_valuation (
            valuation_id, organization_id, fiscal_period_id, valuation_date,
            item_id, warehouse_id, lot_id,
            quantity_on_hand, uom, unit_cost, total_cost,
            costing_method, carrying_amount, write_down_amount,
            currency_code, functional_currency_amount, created_at
        ) VALUES (
            :valuation_id, :organization_id, :fiscal_period_id, :valuation_date,
            :item_id, :warehouse_id, :lot_id,
            :qty, 'EA', :unit_cost, :total_cost,
            'WAC', :carrying, 0,
            'NGN', :functional, now()
        )
        """
    )

    for (org_id, item_id, warehouse_id, lot_id), s in state.items():
        # Skip empty / zero-quantity stock positions.
        if s.qty <= 0:
            continue

        if org_id not in period_cache:
            period_cache[org_id] = fiscal_period_for(db, org_id, valuation_date)

        wac = s.wac
        carrying = s.value
        db.execute(
            insert_sql,
            {
                "valuation_id": uuid4(),
                "organization_id": org_id,
                "fiscal_period_id": period_cache[org_id],
                "valuation_date": valuation_date,
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "lot_id": lot_id,
                "qty": s.qty,
                "unit_cost": wac,
                "total_cost": s.value,
                "carrying": carrying,
                "functional": s.value,
            },
        )
        rows_written += 1
        total_value += s.value

    return rows_written, total_value


def report(state: dict[tuple, StockState], valuation_date: date) -> Decimal:
    total_value = sum((s.value for s in state.values() if s.qty > 0), ZERO)
    nonempty = sum(1 for s in state.values() if s.qty > 0)
    negative = sum(1 for s in state.values() if s.qty < 0)
    logger.info(
        "As of %s: %d items×warehouses with qty>0, total value NGN %s",
        valuation_date,
        nonempty,
        f"{total_value:,.2f}",
    )
    if negative:
        logger.warning(
            "  %d (item,warehouse) positions have NEGATIVE qty — issues exceed receipts",
            negative,
        )
    # Top 10 by value
    top = sorted(
        ((k, s) for k, s in state.items() if s.qty > 0),
        key=lambda kv: kv[1].value,
        reverse=True,
    )[:10]
    logger.info("Top 10 positions by value:")
    for (org, item, warehouse, lot), s in top:
        logger.info(
            "  item=%s warehouse=%s qty=%s wac=%.4f value=%s",
            str(item)[:8],
            str(warehouse)[:8],
            s.qty,
            s.wac,
            f"{s.value:,.2f}",
        )
    return total_value


def run_for_date(db: Session, as_of: date, execute: bool) -> Decimal:
    logger.info("Loading transactions through %s...", as_of)
    transactions = fetch_transactions_through(db, as_of)
    logger.info("Loaded %d transactions. Replaying...", len(transactions))
    state = replay(transactions)
    total_value = report(state, as_of)
    if execute:
        rows, written_value = write_valuation(db, state, as_of)
        logger.info(
            "Wrote %d valuation rows (total NGN %s).", rows, f"{written_value:,.2f}"
        )
        db.commit()
    else:
        logger.info("[DRY RUN] no rows written — re-run with --execute to persist.")
    return total_value


MONTH_ENDS_2025 = [
    date(2025, 1, 31),
    date(2025, 2, 28),
    date(2025, 3, 31),
    date(2025, 4, 30),
    date(2025, 5, 31),
    date(2025, 6, 30),
    date(2025, 7, 31),
    date(2025, 8, 31),
    date(2025, 9, 30),
    date(2025, 10, 31),
    date(2025, 11, 30),
    date(2025, 12, 31),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        default="2025-12-31",
        help="ISO date for the snapshot (default: 2025-12-31)",
    )
    parser.add_argument(
        "--all-2025-month-ends",
        action="store_true",
        help="Snapshot at every month-end of 2025",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write rows to inv.inventory_valuation. Without this, dry-run.",
    )
    args = parser.parse_args()

    targets = (
        MONTH_ENDS_2025
        if args.all_2025_month_ends
        else [datetime.strptime(args.as_of, "%Y-%m-%d").date()]
    )

    with SessionLocal() as db:
        db.execute(text("SET app.bypass_rls = 'true'"))
        for d in targets:
            logger.info("=" * 50)
            run_for_date(db, d, args.execute)

    return 0


if __name__ == "__main__":
    sys.exit(main())
