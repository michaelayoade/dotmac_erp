# Phase 4: Reservations & Extensibility — Stock Reservation & Service Hooks

**Timeline**: ~18 days | **Depends on**: Phase 2 (Field-Level Change Tracking), Phase 3 (Inventory Valuation)

---

## Overview

Phase 4 completes the Odoo adaptation roadmap with two foundational capabilities:

1. **Stock Reservation System** (10 days) — Reserve inventory on sales order confirmation, prevent overselling, auto-release expired holds
2. **Service Hook / Plugin System** (8 days) — Lightweight event-driven extensibility allowing org-scoped hooks on domain events

---

## 1. Stock Reservation System

### Problem

When a sales order is confirmed, DotMac does not reserve stock. Multiple orders can be confirmed for the same limited inventory, leading to **overselling**. The fulfilment team discovers shortages only at shipment time, causing delays and customer dissatisfaction.

The existing `InventoryBalanceService` has `allocate_inventory()` / `deallocate_inventory()` methods and `InventoryLot.quantity_allocated` already exists, but there is:
- No dedicated reservation model (allocations are opaque — no lifecycle, no expiry, no audit trail)
- No integration between SO confirmation and inventory allocation
- No scheduler to release stale reservations
- No partial reservation handling (all-or-nothing)

### Odoo Pattern Being Adapted

Odoo uses a "quant reservation" model where `stock.quant` tracks reserved quantities per (product, location, lot). When a sales order is confirmed, `stock.move` records are created with `state=confirmed` and quantities are reserved from quants via `_action_assign()`. If stock is insufficient, the move stays in `waiting` state. Odoo also supports reservation by priority and FIFO lot allocation.

We adapt this by creating an **explicit `StockReservation` model** with lifecycle tracking, rather than Odoo's tightly-coupled move/quant system. This preserves DotMac's clean service-layer architecture while gaining Odoo's reliability.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Sales Order Confirmation                           │
│  POST /finance/sales-orders/{so_id}/confirm         │
├─────────────────────────────────────────────────────┤
│  SalesOrderService.confirm()                        │
│  ├─ Validate SO status (APPROVED → CONFIRMED)       │
│  ├─ For each line with item.track_inventory:        │
│  │   └─ StockReservationService.reserve()           │
│  │       ├─ Check available qty (on_hand - reserved)│
│  │       ├─ SELECT ... FOR UPDATE (row lock on lot) │
│  │       ├─ Create StockReservation record          │
│  │       ├─ Increment lot.quantity_allocated        │
│  │       └─ Return ReservationResult (full/partial) │
│  ├─ Set line fulfillment_status based on result     │
│  └─ Emit event: sales.order.confirmed              │
├─────────────────────────────────────────────────────┤
│  Shipment Creation                                  │
│  StockReservationService.fulfill()                  │
│  ├─ Transition reservation RESERVED → FULFILLED     │
│  ├─ Decrement lot.quantity_allocated                │
│  └─ Create InventoryTransaction (type=SALE)         │
├─────────────────────────────────────────────────────┤
│  Celery: release_expired_reservations               │
│  Every 15 minutes                                   │
│  ├─ Find reservations past expires_at               │
│  ├─ Transition RESERVED → EXPIRED                   │
│  └─ Deallocate lot.quantity_allocated               │
└─────────────────────────────────────────────────────┘
```

### Data Model

#### `StockReservation`

```python
# app/models/inventory/stock_reservation.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ReservationStatus(str, enum.Enum):
    RESERVED = "RESERVED"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ReservationSourceType(str, enum.Enum):
    SALES_ORDER = "SALES_ORDER"
    TRANSFER_ORDER = "TRANSFER_ORDER"
    MANUFACTURING_ORDER = "MANUFACTURING_ORDER"


class StockReservation(Base):
    """Explicit stock reservation linking a demand document to allocated inventory."""

    __tablename__ = "stock_reservation"
    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_line_id", "lot_id",
            name="uq_reservation_source_lot",
        ),
        Index("ix_reservation_org_status", "organization_id", "status"),
        Index("ix_reservation_expires", "status", "expires_at"),
        Index("ix_reservation_item", "organization_id", "item_id", "warehouse_id"),
        {"schema": "inv"},
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id"),
        nullable=False,
    )

    # What item is reserved and where
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=False,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_lot.lot_id"),
        nullable=True,  # NULL for non-lot-tracked items
    )

    # How much
    quantity_reserved: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    quantity_fulfilled: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    quantity_cancelled: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )

    # Demand source (e.g., sales order line)
    source_type: Mapped[ReservationSourceType] = mapped_column(
        Enum(ReservationSourceType, name="reservation_source_type"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False  # e.g., so_id
    )
    source_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False  # e.g., line_id
    )

    # Status lifecycle
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status"),
        nullable=False,
        default=ReservationStatus.RESERVED,
    )

    # Expiry for temporary holds
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Priority (lower = higher priority, used for allocation ordering)
    priority: Mapped[int] = mapped_column(default=10)

    # Audit
    reserved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )

    # Relationships
    item = relationship("Item", lazy="select")
    warehouse = relationship("Warehouse", lazy="select")
    lot = relationship("InventoryLot", lazy="select")

    @property
    def quantity_remaining(self) -> Decimal:
        """Quantity still reserved (not yet fulfilled or cancelled)."""
        return self.quantity_reserved - self.quantity_fulfilled - self.quantity_cancelled

    @property
    def is_fully_fulfilled(self) -> bool:
        return self.quantity_fulfilled >= self.quantity_reserved
```

### Domain Settings

Add these to the `inventory` domain in `settings_spec.py`:

```python
# app/services/settings_spec.py — add to INVENTORY_SPECS

SettingSpec(
    domain=SettingDomain.inventory,
    key="stock_reservation_enabled",
    env_var="",
    value_type=SettingValueType.boolean,
    default="false",
    required=False,
    label="Enable Stock Reservation",
    description="Reserve inventory when sales orders are confirmed.",
),
SettingSpec(
    domain=SettingDomain.inventory,
    key="stock_reservation_expiry_hours",
    env_var="",
    value_type=SettingValueType.integer,
    default="0",  # 0 = no expiry
    required=False,
    min_value=0,
    max_value=720,  # 30 days max
    label="Reservation Expiry (hours)",
    description="Auto-release reservations after this many hours. 0 = never expire.",
),
SettingSpec(
    domain=SettingDomain.inventory,
    key="stock_reservation_allow_partial",
    env_var="",
    value_type=SettingValueType.boolean,
    default="true",
    required=False,
    label="Allow Partial Reservation",
    description="If insufficient stock, reserve what is available instead of failing.",
),
SettingSpec(
    domain=SettingDomain.inventory,
    key="stock_reservation_auto_on_confirm",
    env_var="",
    value_type=SettingValueType.boolean,
    default="true",
    required=False,
    label="Auto-Reserve on SO Confirmation",
    description="Automatically reserve stock when a sales order is confirmed.",
),
```

### Feature Flag

```python
# app/services/feature_flags.py — add constant
FEATURE_STOCK_RESERVATION = "enable_stock_reservation"  # Default: False
```

### Service Layer

#### `StockReservationService`

```python
# app/services/inventory/stock_reservation.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.inventory.stock_reservation import (
    ReservationSourceType,
    ReservationStatus,
    StockReservation,
)

logger = logging.getLogger(__name__)


@dataclass
class ReservationResult:
    """Result of a reservation attempt."""
    success: bool
    reservation_id: UUID | None
    quantity_reserved: Decimal
    quantity_requested: Decimal
    shortfall: Decimal  # quantity_requested - quantity_reserved (0 if fully reserved)
    message: str


@dataclass
class ReservationConfig:
    """Org-scoped reservation configuration loaded from DomainSettings."""
    enabled: bool
    expiry_hours: int  # 0 = no expiry
    allow_partial: bool
    auto_on_confirm: bool


class StockReservationService:
    """
    Manages stock reservations for demand documents (sales orders, transfers).

    Lifecycle: RESERVED → PARTIALLY_FULFILLED → FULFILLED
                       → CANCELLED
                       → EXPIRED (via scheduler)
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Configuration ────────────────────────────────────────

    @staticmethod
    def load_config(db: Session, organization_id: UUID) -> ReservationConfig:
        """Load reservation settings for an organization."""
        from app.services.settings_spec import resolve_value, SettingDomain

        def _resolve(key: str) -> str | None:
            return resolve_value(db, SettingDomain.inventory, key, organization_id)

        return ReservationConfig(
            enabled=(_resolve("stock_reservation_enabled") or "false").lower() == "true",
            expiry_hours=int(_resolve("stock_reservation_expiry_hours") or "0"),
            allow_partial=(_resolve("stock_reservation_allow_partial") or "true").lower() == "true",
            auto_on_confirm=(_resolve("stock_reservation_auto_on_confirm") or "true").lower() == "true",
        )

    # ── Core Operations ──────────────────────────────────────

    def reserve(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        quantity: Decimal,
        source_type: ReservationSourceType,
        source_id: UUID,
        source_line_id: UUID,
        reserved_by_user_id: UUID,
        *,
        lot_id: UUID | None = None,
        priority: int = 10,
        config: ReservationConfig | None = None,
    ) -> ReservationResult:
        """
        Reserve stock for a demand document line.

        Uses SELECT ... FOR UPDATE on the lot row to prevent concurrent
        over-allocation. For non-lot-tracked items, uses the synthetic
        "__GENERAL__" lot from InventoryBalanceService.

        Args:
            organization_id: Tenant ID
            item_id: Item to reserve
            warehouse_id: Warehouse to reserve from
            quantity: Requested quantity
            source_type: Type of demand document (SALES_ORDER, etc.)
            source_id: ID of demand document (e.g., so_id)
            source_line_id: ID of demand line (e.g., line_id)
            reserved_by_user_id: User performing the reservation
            lot_id: Specific lot (for lot-tracked items)
            priority: Allocation priority (lower = higher)
            config: Pre-loaded config (avoids re-querying settings)

        Returns:
            ReservationResult with success flag, quantities, and message.
        """
        if config is None:
            config = self.load_config(self.db, organization_id)

        if not config.enabled:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=quantity,
                shortfall=quantity,
                message="Stock reservation is not enabled for this organization.",
            )

        # Check for existing reservation on same source line + lot
        existing = self._find_existing(source_type, source_line_id, lot_id)
        if existing and existing.status == ReservationStatus.RESERVED:
            return ReservationResult(
                success=True,
                reservation_id=existing.reservation_id,
                quantity_reserved=existing.quantity_remaining,
                quantity_requested=quantity,
                shortfall=Decimal("0"),
                message="Reservation already exists.",
            )

        # Get available quantity with row lock
        available = self._get_available_with_lock(
            organization_id, item_id, warehouse_id, lot_id
        )

        if available <= Decimal("0"):
            if not config.allow_partial:
                return ReservationResult(
                    success=False,
                    reservation_id=None,
                    quantity_reserved=Decimal("0"),
                    quantity_requested=quantity,
                    shortfall=quantity,
                    message=f"No stock available for item {item_id} in warehouse {warehouse_id}.",
                )
            # Allow partial with zero — create a "backorder" reservation
            # that can be fulfilled later when stock arrives

        qty_to_reserve = min(quantity, available) if available > Decimal("0") else Decimal("0")
        shortfall = quantity - qty_to_reserve

        if qty_to_reserve <= Decimal("0") and not config.allow_partial:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=quantity,
                shortfall=quantity,
                message="Insufficient stock and partial reservation is disabled.",
            )

        # Calculate expiry
        expires_at = None
        if config.expiry_hours > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=config.expiry_hours)

        # Create reservation record
        reservation = StockReservation(
            organization_id=organization_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            quantity_reserved=qty_to_reserve,
            source_type=source_type,
            source_id=source_id,
            source_line_id=source_line_id,
            status=ReservationStatus.RESERVED,
            expires_at=expires_at,
            priority=priority,
            reserved_by_user_id=reserved_by_user_id,
        )
        self.db.add(reservation)

        # Update lot allocation
        if qty_to_reserve > Decimal("0"):
            self._increment_allocation(
                organization_id, item_id, warehouse_id, lot_id, qty_to_reserve
            )

        self.db.flush()

        logger.info(
            "Reserved %s of %s for %s/%s (reservation=%s, shortfall=%s)",
            qty_to_reserve, quantity, source_type.value, source_line_id,
            reservation.reservation_id, shortfall,
        )

        return ReservationResult(
            success=True,
            reservation_id=reservation.reservation_id,
            quantity_reserved=qty_to_reserve,
            quantity_requested=quantity,
            shortfall=shortfall,
            message="Fully reserved." if shortfall == Decimal("0") else f"Partially reserved. Shortfall: {shortfall}",
        )

    def fulfill(
        self,
        reservation_id: UUID,
        quantity: Decimal,
    ) -> StockReservation:
        """
        Fulfill (partially or fully) a reservation when a shipment is created.

        Decrements lot.quantity_allocated and transitions reservation status.

        Args:
            reservation_id: The reservation to fulfill
            quantity: Quantity being shipped

        Returns:
            Updated StockReservation

        Raises:
            ValueError: If reservation not found, already fulfilled, or quantity exceeds remaining.
        """
        reservation = self.db.get(StockReservation, reservation_id)
        if not reservation:
            raise ValueError(f"Reservation {reservation_id} not found.")
        if reservation.status in (
            ReservationStatus.FULFILLED,
            ReservationStatus.CANCELLED,
            ReservationStatus.EXPIRED,
        ):
            raise ValueError(
                f"Cannot fulfill reservation in status {reservation.status.value}."
            )

        remaining = reservation.quantity_remaining
        if quantity > remaining:
            raise ValueError(
                f"Cannot fulfill {quantity} — only {remaining} remaining on reservation."
            )

        reservation.quantity_fulfilled += quantity

        # Release the allocation from the lot
        self._decrement_allocation(
            reservation.organization_id,
            reservation.item_id,
            reservation.warehouse_id,
            reservation.lot_id,
            quantity,
        )

        # Transition status
        if reservation.is_fully_fulfilled:
            reservation.status = ReservationStatus.FULFILLED
            reservation.fulfilled_at = datetime.now(timezone.utc)
        else:
            reservation.status = ReservationStatus.PARTIALLY_FULFILLED

        self.db.flush()
        logger.info(
            "Fulfilled %s on reservation %s (status=%s)",
            quantity, reservation_id, reservation.status.value,
        )
        return reservation

    def cancel(
        self,
        reservation_id: UUID,
        reason: str = "Cancelled by user",
    ) -> StockReservation:
        """
        Cancel a reservation, releasing allocated stock.

        Args:
            reservation_id: Reservation to cancel
            reason: Cancellation reason for audit trail

        Returns:
            Updated StockReservation

        Raises:
            ValueError: If reservation not found or already terminal.
        """
        reservation = self.db.get(StockReservation, reservation_id)
        if not reservation:
            raise ValueError(f"Reservation {reservation_id} not found.")
        if reservation.status in (
            ReservationStatus.FULFILLED,
            ReservationStatus.CANCELLED,
            ReservationStatus.EXPIRED,
        ):
            raise ValueError(
                f"Cannot cancel reservation in status {reservation.status.value}."
            )

        remaining = reservation.quantity_remaining
        reservation.quantity_cancelled = remaining
        reservation.status = ReservationStatus.CANCELLED
        reservation.cancelled_at = datetime.now(timezone.utc)
        reservation.cancellation_reason = reason

        # Release allocation
        if remaining > Decimal("0"):
            self._decrement_allocation(
                reservation.organization_id,
                reservation.item_id,
                reservation.warehouse_id,
                reservation.lot_id,
                remaining,
            )

        self.db.flush()
        logger.info(
            "Cancelled reservation %s (released %s, reason=%s)",
            reservation_id, remaining, reason,
        )
        return reservation

    def release_expired(self, batch_size: int = 200) -> dict[str, int]:
        """
        Release all expired reservations. Called by Celery scheduler.

        Returns:
            Dict with counts: {"released": N, "errors": N}
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(StockReservation)
            .where(
                StockReservation.status.in_([
                    ReservationStatus.RESERVED,
                    ReservationStatus.PARTIALLY_FULFILLED,
                ]),
                StockReservation.expires_at.isnot(None),
                StockReservation.expires_at <= now,
            )
            .limit(batch_size)
        )
        expired = list(self.db.scalars(stmt).all())

        results = {"released": 0, "errors": 0}
        for reservation in expired:
            try:
                self.cancel(reservation.reservation_id, reason="Reservation expired")
                results["released"] += 1
            except (ValueError, RuntimeError) as e:
                logger.exception("Failed to release expired reservation %s: %s",
                                 reservation.reservation_id, e)
                results["errors"] += 1

        return results

    # ── Queries ──────────────────────────────────────────────

    def get_reservations_for_source(
        self,
        source_type: ReservationSourceType,
        source_id: UUID,
    ) -> list[StockReservation]:
        """Get all reservations for a demand document (e.g., all lines of an SO)."""
        stmt = select(StockReservation).where(
            StockReservation.source_type == source_type,
            StockReservation.source_id == source_id,
            StockReservation.status.in_([
                ReservationStatus.RESERVED,
                ReservationStatus.PARTIALLY_FULFILLED,
            ]),
        )
        return list(self.db.scalars(stmt).all())

    def get_reservation_for_line(
        self,
        source_type: ReservationSourceType,
        source_line_id: UUID,
    ) -> StockReservation | None:
        """Get active reservation for a specific demand line."""
        stmt = select(StockReservation).where(
            StockReservation.source_type == source_type,
            StockReservation.source_line_id == source_line_id,
            StockReservation.status.in_([
                ReservationStatus.RESERVED,
                ReservationStatus.PARTIALLY_FULFILLED,
            ]),
        )
        return self.db.scalar(stmt)

    def get_reserved_quantity(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> Decimal:
        """Get total reserved quantity for an item (optionally in a specific warehouse)."""
        from sqlalchemy import func as sqla_func

        stmt = select(sqla_func.coalesce(
            sqla_func.sum(
                StockReservation.quantity_reserved
                - StockReservation.quantity_fulfilled
                - StockReservation.quantity_cancelled
            ),
            Decimal("0"),
        )).where(
            StockReservation.organization_id == organization_id,
            StockReservation.item_id == item_id,
            StockReservation.status.in_([
                ReservationStatus.RESERVED,
                ReservationStatus.PARTIALLY_FULFILLED,
            ]),
        )
        if warehouse_id:
            stmt = stmt.where(StockReservation.warehouse_id == warehouse_id)

        return self.db.scalar(stmt) or Decimal("0")

    # ── Private Helpers ──────────────────────────────────────

    def _find_existing(
        self,
        source_type: ReservationSourceType,
        source_line_id: UUID,
        lot_id: UUID | None,
    ) -> StockReservation | None:
        """Find existing active reservation for the same source line + lot."""
        stmt = select(StockReservation).where(
            StockReservation.source_type == source_type,
            StockReservation.source_line_id == source_line_id,
            StockReservation.status.in_([
                ReservationStatus.RESERVED,
                ReservationStatus.PARTIALLY_FULFILLED,
            ]),
        )
        if lot_id:
            stmt = stmt.where(StockReservation.lot_id == lot_id)
        return self.db.scalar(stmt)

    def _get_available_with_lock(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        lot_id: UUID | None,
    ) -> Decimal:
        """
        Get available quantity with a row-level lock to prevent concurrent over-allocation.

        Uses SELECT ... FOR UPDATE on the relevant InventoryLot row(s).
        For non-lot-tracked items, queries the synthetic "__GENERAL__" lot.
        """
        from app.models.inventory.inventory_lot import InventoryLot

        stmt = (
            select(InventoryLot)
            .where(
                InventoryLot.organization_id == organization_id,
                InventoryLot.item_id == item_id,
                InventoryLot.is_active == True,
            )
            .with_for_update()  # Row lock — prevents concurrent allocation race
        )

        if lot_id:
            stmt = stmt.where(InventoryLot.lot_id == lot_id)
        if warehouse_id:
            stmt = stmt.where(InventoryLot.warehouse_id == warehouse_id)

        lots = list(self.db.scalars(stmt).all())

        total_available = Decimal("0")
        for lot in lots:
            available = lot.quantity_on_hand - lot.quantity_allocated
            if available > Decimal("0"):
                total_available += available

        return total_available

    def _increment_allocation(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        lot_id: UUID | None,
        quantity: Decimal,
    ) -> None:
        """Increment quantity_allocated on the lot row(s)."""
        from app.models.inventory.inventory_lot import InventoryLot

        if lot_id:
            # Specific lot — direct update
            stmt = (
                update(InventoryLot)
                .where(InventoryLot.lot_id == lot_id)
                .values(quantity_allocated=InventoryLot.quantity_allocated + quantity)
            )
        else:
            # Non-lot-tracked — update the general lot for this item+warehouse
            stmt = (
                update(InventoryLot)
                .where(
                    InventoryLot.organization_id == organization_id,
                    InventoryLot.item_id == item_id,
                    InventoryLot.warehouse_id == warehouse_id,
                    InventoryLot.is_active == True,
                )
                .values(quantity_allocated=InventoryLot.quantity_allocated + quantity)
            )
        self.db.execute(stmt)

    def _decrement_allocation(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        lot_id: UUID | None,
        quantity: Decimal,
    ) -> None:
        """Decrement quantity_allocated on the lot row(s)."""
        from app.models.inventory.inventory_lot import InventoryLot

        if lot_id:
            stmt = (
                update(InventoryLot)
                .where(InventoryLot.lot_id == lot_id)
                .values(quantity_allocated=InventoryLot.quantity_allocated - quantity)
            )
        else:
            stmt = (
                update(InventoryLot)
                .where(
                    InventoryLot.organization_id == organization_id,
                    InventoryLot.item_id == item_id,
                    InventoryLot.warehouse_id == warehouse_id,
                    InventoryLot.is_active == True,
                )
                .values(quantity_allocated=InventoryLot.quantity_allocated - quantity)
            )
        self.db.execute(stmt)
```

### Integration Points

#### SO Confirmation → Reserve Stock

```python
# app/services/finance/ar/sales_order_service.py — modify confirm() method

def confirm(self, so_id: UUID, confirmed_by_user_id: UUID) -> SalesOrder:
    """Confirm a sales order, optionally reserving stock."""
    so = self._get_or_404(so_id)
    self._validate_status_transition(so.status, SOStatus.CONFIRMED)

    so.status = SOStatus.CONFIRMED

    # Reserve stock if feature is enabled
    from app.services.feature_flags import is_feature_enabled, FEATURE_STOCK_RESERVATION
    if is_feature_enabled(self.db, FEATURE_STOCK_RESERVATION):
        config = StockReservationService.load_config(self.db, so.organization_id)
        if config.enabled and config.auto_on_confirm:
            self._reserve_stock_for_order(so, confirmed_by_user_id, config)

    self.db.flush()
    return so

def _reserve_stock_for_order(
    self,
    so: SalesOrder,
    user_id: UUID,
    config: ReservationConfig,
) -> None:
    """Reserve stock for all inventory-tracked lines on a sales order."""
    from app.services.inventory.stock_reservation import (
        ReservationSourceType,
        StockReservationService,
    )

    reservation_svc = StockReservationService(self.db)

    for line in so.line_items:
        if not line.item_id:
            continue  # Service/description-only lines

        # Check if item tracks inventory
        from app.models.inventory.item import Item
        item = self.db.get(Item, line.item_id)
        if not item or not item.track_inventory:
            continue

        # Determine warehouse (org default or SO-level if implemented)
        warehouse_id = self._get_default_warehouse(so.organization_id)
        if not warehouse_id:
            logger.warning("No default warehouse for org %s, skipping reservation", so.organization_id)
            continue

        result = reservation_svc.reserve(
            organization_id=so.organization_id,
            item_id=line.item_id,
            warehouse_id=warehouse_id,
            quantity=line.quantity_ordered,
            source_type=ReservationSourceType.SALES_ORDER,
            source_id=so.so_id,
            source_line_id=line.line_id,
            reserved_by_user_id=user_id,
            config=config,
        )

        if result.shortfall > Decimal("0"):
            line.fulfillment_status = FulfillmentStatus.BACKORDERED
            line.quantity_backordered = result.shortfall
            logger.info(
                "Partial reservation for SO line %s: reserved=%s, shortfall=%s",
                line.line_id, result.quantity_reserved, result.shortfall,
            )
```

#### Shipment Creation → Fulfill Reservation

```python
# app/services/finance/ar/shipment_service.py — modify create_shipment()

def create_shipment(self, shipment_data: ShipmentCreate, user_id: UUID) -> Shipment:
    """Create a shipment and fulfill corresponding reservations."""
    # ... existing shipment creation logic ...

    # Fulfill reservations for shipped lines
    from app.services.feature_flags import is_feature_enabled, FEATURE_STOCK_RESERVATION
    if is_feature_enabled(self.db, FEATURE_STOCK_RESERVATION):
        from app.services.inventory.stock_reservation import (
            ReservationSourceType,
            StockReservationService,
        )
        reservation_svc = StockReservationService(self.db)

        for shipment_line in shipment.lines:
            reservation = reservation_svc.get_reservation_for_line(
                ReservationSourceType.SALES_ORDER,
                shipment_line.so_line_id,
            )
            if reservation:
                try:
                    reservation_svc.fulfill(
                        reservation.reservation_id,
                        shipment_line.quantity_shipped,
                    )
                except ValueError as e:
                    logger.warning("Could not fulfill reservation: %s", e)
                    # Continue — shipment proceeds even if reservation tracking fails

    self.db.flush()
    return shipment
```

#### SO Cancellation → Cancel Reservations

```python
# app/services/finance/ar/sales_order_service.py — modify cancel()

def cancel(self, so_id: UUID, reason: str = "Cancelled") -> SalesOrder:
    """Cancel a sales order and release all reservations."""
    so = self._get_or_404(so_id)
    self._validate_status_transition(so.status, SOStatus.CANCELLED)

    so.status = SOStatus.CANCELLED

    # Release reservations
    from app.services.feature_flags import is_feature_enabled, FEATURE_STOCK_RESERVATION
    if is_feature_enabled(self.db, FEATURE_STOCK_RESERVATION):
        from app.services.inventory.stock_reservation import (
            ReservationSourceType,
            StockReservationService,
        )
        reservation_svc = StockReservationService(self.db)
        reservations = reservation_svc.get_reservations_for_source(
            ReservationSourceType.SALES_ORDER, so.so_id
        )
        for r in reservations:
            try:
                reservation_svc.cancel(r.reservation_id, reason=reason)
            except ValueError as e:
                logger.warning("Could not cancel reservation %s: %s", r.reservation_id, e)

    self.db.flush()
    return so
```

### Celery Task

```python
# app/tasks/inventory.py — add task

@shared_task
def release_expired_reservations() -> dict:
    """Release stock reservations that have passed their expiry time."""
    logger.info("Starting expired reservation release")

    with SessionLocal() as db:
        from app.services.inventory.stock_reservation import StockReservationService

        svc = StockReservationService(db)
        results = svc.release_expired(batch_size=200)
        db.commit()

    logger.info(
        "Expired reservation release complete: released=%s, errors=%s",
        results["released"], results["errors"],
    )
    return results


# Celery beat schedule:
# 'release-expired-reservations': {
#     'task': 'app.tasks.inventory.release_expired_reservations',
#     'schedule': crontab(minute='*/15'),  # Every 15 minutes
# },
```

### Migration

```python
# alembic/versions/xxx_add_stock_reservation.py

def upgrade() -> None:
    # Create enum types
    op.execute("CREATE TYPE reservation_status AS ENUM ('RESERVED', 'PARTIALLY_FULFILLED', 'FULFILLED', 'CANCELLED', 'EXPIRED')")
    op.execute("CREATE TYPE reservation_source_type AS ENUM ('SALES_ORDER', 'TRANSFER_ORDER', 'MANUFACTURING_ORDER')")

    op.create_table(
        'stock_reservation',
        sa.Column('reservation_id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.organization_id'), nullable=False),
        sa.Column('item_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('inv.item.item_id'), nullable=False),
        sa.Column('warehouse_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('inv.warehouse.warehouse_id'), nullable=False),
        sa.Column('lot_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('inv.inventory_lot.lot_id'), nullable=True),
        sa.Column('quantity_reserved', sa.Numeric(20, 6), nullable=False),
        sa.Column('quantity_fulfilled', sa.Numeric(20, 6), nullable=False, server_default='0'),
        sa.Column('quantity_cancelled', sa.Numeric(20, 6), nullable=False, server_default='0'),
        sa.Column('source_type', sa.Enum('SALES_ORDER', 'TRANSFER_ORDER', 'MANUFACTURING_ORDER', name='reservation_source_type', create_type=False), nullable=False),
        sa.Column('source_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_line_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.Enum('RESERVED', 'PARTIALLY_FULFILLED', 'FULFILLED', 'CANCELLED', 'EXPIRED', name='reservation_status', create_type=False), nullable=False, server_default='RESERVED'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('priority', sa.Integer, nullable=False, server_default='10'),
        sa.Column('reserved_by_user_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('reserved_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancellation_reason', sa.String(200), nullable=True),
        sa.UniqueConstraint('source_type', 'source_line_id', 'lot_id', name='uq_reservation_source_lot'),
        schema='inv',
    )

    op.create_index('ix_reservation_org_status', 'stock_reservation', ['organization_id', 'status'], schema='inv')
    op.create_index('ix_reservation_expires', 'stock_reservation', ['status', 'expires_at'], schema='inv')
    op.create_index('ix_reservation_item', 'stock_reservation', ['organization_id', 'item_id', 'warehouse_id'], schema='inv')


def downgrade() -> None:
    op.drop_table('stock_reservation', schema='inv')
    op.execute("DROP TYPE IF EXISTS reservation_status")
    op.execute("DROP TYPE IF EXISTS reservation_source_type")
```

### UI Components

#### Reservation Badge on SO Detail Page

Show reservation status alongside each SO line item:

```html
<!-- templates/finance/ar/sales_order_detail.html — add to line items table -->
<td class="text-center">
  {% if reservation_map.get(line.line_id) %}
    {% set res = reservation_map[line.line_id] %}
    {% if res.status.value == 'RESERVED' %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium
                   bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400
                   border border-blue-200 dark:border-blue-800">
        Reserved: {{ res.quantity_remaining | format_number }}
      </span>
    {% elif res.status.value == 'PARTIALLY_FULFILLED' %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium
                   bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400
                   border border-amber-200 dark:border-amber-800">
        Partial: {{ res.quantity_fulfilled }}/{{ res.quantity_reserved }}
      </span>
    {% elif res.status.value == 'FULFILLED' %}
      {{ status_badge('FULFILLED', 'sm') }}
    {% endif %}
    {% if res.expires_at %}
      <div class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
        Expires: {{ res.expires_at | format_datetime }}
      </div>
    {% endif %}
  {% else %}
    <span class="text-xs text-slate-400">—</span>
  {% endif %}
</td>
```

#### Inventory Availability Widget

On item detail pages, show available vs reserved:

```html
<!-- templates/inventory/items/detail.html — add to stock section -->
<div class="grid grid-cols-3 gap-4">
  {{ stats_card(label="On Hand", value=stock.on_hand | format_number, icon="box", color="blue") }}
  {{ stats_card(label="Reserved", value=stock.reserved | format_number, icon="lock", color="amber") }}
  {{ stats_card(label="Available", value=stock.available | format_number, icon="check-circle", color="emerald") }}
</div>
```

### Testing Plan

```
tests/ifrs/inventory/test_stock_reservation.py
├── test_reserve_full_quantity_success
├── test_reserve_partial_when_insufficient
├── test_reserve_fails_when_disabled
├── test_reserve_fails_when_no_stock_and_partial_disabled
├── test_reserve_idempotent_same_source_line
├── test_fulfill_full_quantity
├── test_fulfill_partial_quantity
├── test_fulfill_exceeds_remaining_raises
├── test_cancel_releases_allocation
├── test_cancel_already_fulfilled_raises
├── test_release_expired_reservations
├── test_concurrent_reservation_row_lock (PostgreSQL only)
├── test_so_confirm_reserves_stock
├── test_so_cancel_releases_reservations
├── test_shipment_fulfills_reservation
└── test_multi_tenancy_isolation
```

### Implementation Schedule (10 days)

| Day | Task |
|-----|------|
| 1 | Model + migration + enums |
| 2 | `StockReservationService` — `reserve()`, `_get_available_with_lock()`, `_increment_allocation()` |
| 3 | `StockReservationService` — `fulfill()`, `cancel()`, `release_expired()` |
| 4 | Domain settings specs + feature flag |
| 5 | SO confirmation integration (`_reserve_stock_for_order`) |
| 6 | Shipment integration + SO cancellation integration |
| 7 | Celery task + beat schedule |
| 8 | UI — reservation badges on SO detail, availability widget on item detail |
| 9 | Tests — unit + integration (12+ test cases) |
| 10 | End-to-end testing, SQLite test compatibility, edge cases |

---

## 2. Service Hook / Plugin System

### Problem

DotMac is a monolithic application with tightly-coupled domain services. When organizations need custom behavior on events (e.g., "send a Slack notification when an invoice is overdue", "create a task in Jira when a PO is approved", "sync new customers to an external CRM"), developers must modify core service code. This creates:

- **Maintenance burden** — custom code mixed with core logic
- **Deployment risk** — org-specific changes require full deployments
- **Scalability limit** — cannot enable/disable behaviors per organization

### Odoo Pattern Being Adapted

Odoo uses Python's inheritance mechanism extensively (`_inherit`), allowing any module to override or extend any method on any model. While powerful, this creates fragile method-resolution chains. Odoo also uses `ir.actions.server` for configurable automation rules.

We adapt this with a **lightweight event-hook system**: domain services emit events at key lifecycle points, and registered hooks execute in response. This preserves DotMac's clean service boundaries while enabling extensibility.

### Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Domain Service (e.g., InvoiceService.create())          │
│  ├─ Execute business logic                               │
│  ├─ Flush to DB                                          │
│  └─ Emit event: hook_registry.emit(                      │
│       "ar.invoice.created", payload={...}, db=db          │
│     )                                                     │
├──────────────────────────────────────────────────────────┤
│  HookRegistry (in-process, synchronous)                  │
│  ├─ Match event name against registered ServiceHook rows │
│  ├─ Filter by organization_id (org-scoped hooks)         │
│  ├─ Execute SYNC hooks in-process (notifications, etc.)  │
│  └─ Queue ASYNC hooks via Celery (webhooks, email, etc.) │
├──────────────────────────────────────────────────────────┤
│  Hook Handlers                                           │
│  ├─ NOTIFICATION — create in-app notification            │
│  ├─ WEBHOOK — POST to external URL (async via Celery)    │
│  ├─ EMAIL — send email via SMTP (async via Celery)       │
│  ├─ INTERNAL_SERVICE — call another DotMac service       │
│  └─ EVENT_OUTBOX — write to EventOutbox for pub/sub      │
├──────────────────────────────────────────────────────────┤
│  Celery Worker                                           │
│  └─ execute_async_hook task                              │
│     ├─ Execute webhook/email/etc.                        │
│     ├─ Record result in ServiceHookExecution              │
│     └─ Retry on failure (exponential backoff)            │
└──────────────────────────────────────────────────────────┘
```

### Data Model

#### `ServiceHook`

```python
# app/models/platform/service_hook.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class HookHandlerType(str, enum.Enum):
    NOTIFICATION = "NOTIFICATION"       # In-app notification via NotificationService
    WEBHOOK = "WEBHOOK"                 # HTTP POST to external URL
    EMAIL = "EMAIL"                     # Send email via SMTP
    INTERNAL_SERVICE = "INTERNAL_SERVICE"  # Call another DotMac service method
    EVENT_OUTBOX = "EVENT_OUTBOX"       # Write to EventOutbox for downstream consumers


class HookExecutionMode(str, enum.Enum):
    SYNC = "SYNC"    # Execute in-process (blocks caller, use for fast ops like notifications)
    ASYNC = "ASYNC"  # Execute via Celery (non-blocking, use for webhooks/email)


class ServiceHook(Base):
    """
    A registered hook that fires when a domain event occurs.

    Hooks are org-scoped (organization_id) or global (organization_id=NULL).
    Multiple hooks can be registered for the same event.
    """

    __tablename__ = "service_hook"
    __table_args__ = (
        Index("ix_hook_event_org", "event_name", "organization_id"),
        Index("ix_hook_active", "is_active", "event_name"),
        {"schema": "platform"},
    )

    hook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id"),
        nullable=True,  # NULL = global hook (fires for all orgs)
    )

    # What event triggers this hook
    # Convention: "{module}.{entity}.{action}"
    # Examples: "ar.invoice.created", "ar.invoice.overdue", "inventory.stock.low",
    #           "sales.order.confirmed", "ap.payment.approved"
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # What to do when triggered
    handler_type: Mapped[HookHandlerType] = mapped_column(
        Enum(HookHandlerType, name="hook_handler_type"), nullable=False
    )
    execution_mode: Mapped[HookExecutionMode] = mapped_column(
        Enum(HookExecutionMode, name="hook_execution_mode"),
        nullable=False,
        default=HookExecutionMode.ASYNC,
    )

    # Handler configuration (varies by handler_type)
    # WEBHOOK:          {"url": "https://...", "headers": {"Authorization": "..."}, "method": "POST"}
    # EMAIL:            {"to": ["user@example.com"], "subject_template": "...", "body_template": "..."}
    # NOTIFICATION:     {"recipient_role": "finance_manager", "channel": "BOTH", "title_template": "..."}
    # INTERNAL_SERVICE: {"service": "app.services.crm.CRMService", "method": "sync_customer", "args_map": {"customer_id": "$.entity_id"}}
    # EVENT_OUTBOX:     {"event_name_override": "custom.event.name"}
    handler_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Filtering — optional JSONB conditions to narrow when the hook fires
    # Example: {"status": "OVERDUE", "amount_gt": 100000}
    # Empty dict = always fire on matching event_name
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Human-readable
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Lifecycle
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=10)  # Lower = earlier execution

    # Retry policy for async hooks
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, default=60)  # Base backoff

    # Audit
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    executions = relationship("ServiceHookExecution", back_populates="hook", lazy="dynamic")
```

#### `ServiceHookExecution`

```python
# app/models/platform/service_hook_execution.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExecutionStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    DEAD = "DEAD"  # Max retries exhausted


class ServiceHookExecution(Base):
    """Execution log for a service hook invocation."""

    __tablename__ = "service_hook_execution"
    __table_args__ = (
        Index("ix_execution_hook_status", "hook_id", "status"),
        Index("ix_execution_created", "created_at"),
        {"schema": "platform"},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform.service_hook.hook_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Event context
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Execution result
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="hook_execution_status"),
        nullable=False,
        default=ExecutionStatus.PENDING,
    )
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    hook = relationship("ServiceHook", back_populates="executions")
```

### Feature Flag

```python
# app/services/feature_flags.py — add constant
FEATURE_SERVICE_HOOKS = "enable_service_hooks"  # Default: False
```

### Predefined Event Names

```python
# app/services/hooks/events.py
from __future__ import annotations

# ── Accounts Receivable ──────────────────────────────────
AR_INVOICE_CREATED = "ar.invoice.created"
AR_INVOICE_POSTED = "ar.invoice.posted"
AR_INVOICE_OVERDUE = "ar.invoice.overdue"
AR_INVOICE_PAID = "ar.invoice.paid"
AR_INVOICE_VOIDED = "ar.invoice.voided"
AR_RECEIPT_CREATED = "ar.receipt.created"
AR_CUSTOMER_CREATED = "ar.customer.created"

# ── Accounts Payable ─────────────────────────────────────
AP_INVOICE_CREATED = "ap.invoice.created"
AP_INVOICE_APPROVED = "ap.invoice.approved"
AP_PAYMENT_CREATED = "ap.payment.created"
AP_PAYMENT_APPROVED = "ap.payment.approved"

# ── General Ledger ───────────────────────────────────────
GL_JOURNAL_POSTED = "gl.journal.posted"
GL_JOURNAL_REVERSED = "gl.journal.reversed"
GL_PERIOD_CLOSED = "gl.period.closed"

# ── Sales ────────────────────────────────────────────────
SALES_ORDER_CONFIRMED = "sales.order.confirmed"
SALES_ORDER_SHIPPED = "sales.order.shipped"
SALES_ORDER_COMPLETED = "sales.order.completed"
SALES_ORDER_CANCELLED = "sales.order.cancelled"

# ── Inventory ────────────────────────────────────────────
INVENTORY_STOCK_LOW = "inventory.stock.low"
INVENTORY_STOCK_RESERVED = "inventory.stock.reserved"
INVENTORY_RECEIPT_CREATED = "inventory.receipt.created"

# ── Banking ──────────────────────────────────────────────
BANKING_RECONCILIATION_COMPLETED = "banking.reconciliation.completed"
BANKING_STATEMENT_IMPORTED = "banking.statement.imported"

# ── People / HR ──────────────────────────────────────────
HR_LEAVE_SUBMITTED = "hr.leave.submitted"
HR_LEAVE_APPROVED = "hr.leave.approved"
PAYROLL_RUN_COMPLETED = "payroll.run.completed"

# ── Expense ──────────────────────────────────────────────
EXPENSE_CLAIM_SUBMITTED = "expense.claim.submitted"
EXPENSE_CLAIM_APPROVED = "expense.claim.approved"
```

### Service Layer

#### `HookRegistry` (Core Dispatcher)

```python
# app/services/hooks/registry.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)

logger = logging.getLogger(__name__)


@dataclass
class HookEvent:
    """Event payload passed to hook handlers."""
    event_name: str
    organization_id: UUID
    entity_type: str       # e.g., "Invoice", "SalesOrder"
    entity_id: UUID
    actor_user_id: UUID | None
    payload: dict[str, Any]  # Event-specific data


class HookRegistry:
    """
    Central dispatcher for service hooks.

    Matches events against registered ServiceHook rows and dispatches
    to the appropriate handler (sync or async).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def emit(self, event: HookEvent) -> list[UUID]:
        """
        Emit an event and trigger all matching hooks.

        Sync hooks execute immediately in-process.
        Async hooks are queued via Celery for background execution.

        Args:
            event: The event to emit

        Returns:
            List of ServiceHookExecution IDs created
        """
        from app.services.feature_flags import is_feature_enabled, FEATURE_SERVICE_HOOKS
        if not is_feature_enabled(self.db, FEATURE_SERVICE_HOOKS):
            return []

        hooks = self._find_matching_hooks(event)
        if not hooks:
            return []

        execution_ids: list[UUID] = []
        for hook in hooks:
            if not self._evaluate_conditions(hook, event):
                continue

            execution = self._create_execution(hook, event)
            execution_ids.append(execution.execution_id)

            if hook.execution_mode == HookExecutionMode.SYNC:
                self._execute_sync(hook, execution, event)
            else:
                self._queue_async(hook, execution, event)

        self.db.flush()
        logger.info(
            "Emitted event %s: %d hooks matched, %d executions created",
            event.event_name, len(hooks), len(execution_ids),
        )
        return execution_ids

    def _find_matching_hooks(self, event: HookEvent) -> list[ServiceHook]:
        """Find all active hooks registered for this event name and organization."""
        stmt = (
            select(ServiceHook)
            .where(
                ServiceHook.event_name == event.event_name,
                ServiceHook.is_active == True,
            )
            .where(
                # Match org-specific hooks OR global hooks
                ServiceHook.organization_id.in_([event.organization_id])
                | ServiceHook.organization_id.is_(None)
            )
            .order_by(ServiceHook.priority.asc())
        )
        return list(self.db.scalars(stmt).all())

    def _evaluate_conditions(self, hook: ServiceHook, event: HookEvent) -> bool:
        """
        Evaluate hook conditions against event payload.

        Conditions are simple key-value matches against the payload dict.
        Supports: exact match, _gt, _gte, _lt, _lte, _in suffixes.

        Example conditions:
            {"status": "OVERDUE"}                → payload["status"] == "OVERDUE"
            {"amount_gt": 100000}                → payload["amount"] > 100000
            {"customer_type_in": ["VIP", "KEY"]} → payload["customer_type"] in [...]
        """
        if not hook.conditions:
            return True  # No conditions = always match

        for key, expected in hook.conditions.items():
            if key.endswith("_gt"):
                field = key[:-3]
                if event.payload.get(field, 0) <= expected:
                    return False
            elif key.endswith("_gte"):
                field = key[:-4]
                if event.payload.get(field, 0) < expected:
                    return False
            elif key.endswith("_lt"):
                field = key[:-3]
                if event.payload.get(field, 0) >= expected:
                    return False
            elif key.endswith("_lte"):
                field = key[:-4]
                if event.payload.get(field, 0) > expected:
                    return False
            elif key.endswith("_in"):
                field = key[:-3]
                if event.payload.get(field) not in expected:
                    return False
            else:
                # Exact match
                if event.payload.get(key) != expected:
                    return False

        return True

    def _create_execution(
        self, hook: ServiceHook, event: HookEvent
    ) -> ServiceHookExecution:
        """Create an execution record for auditing."""
        execution = ServiceHookExecution(
            hook_id=hook.hook_id,
            organization_id=event.organization_id,
            event_name=event.event_name,
            event_payload=event.payload,
            status=ExecutionStatus.PENDING,
        )
        self.db.add(execution)
        self.db.flush()  # Get ID for Celery task
        return execution

    def _execute_sync(
        self,
        hook: ServiceHook,
        execution: ServiceHookExecution,
        event: HookEvent,
    ) -> None:
        """Execute a sync hook immediately in-process."""
        start = time.monotonic()
        try:
            handler = self._get_handler(hook.handler_type)
            handler.execute(self.db, hook, event)
            execution.status = ExecutionStatus.SUCCESS
            execution.duration_ms = int((time.monotonic() - start) * 1000)
            execution.executed_at = func_now()
        except Exception as e:
            logger.exception("Sync hook %s failed: %s", hook.hook_id, e)
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)[:500]
            execution.duration_ms = int((time.monotonic() - start) * 1000)
            # Sync hooks fail silently — never break the main flow

    def _queue_async(
        self,
        hook: ServiceHook,
        execution: ServiceHookExecution,
        event: HookEvent,
    ) -> None:
        """Queue an async hook for Celery execution."""
        from app.tasks.hooks import execute_async_hook

        execute_async_hook.delay(
            execution_id=str(execution.execution_id),
            hook_id=str(hook.hook_id),
        )

    def _get_handler(self, handler_type: HookHandlerType) -> HookHandler:
        """Get the appropriate handler for a hook type."""
        from app.services.hooks.handlers import (
            NotificationHookHandler,
            WebhookHookHandler,
            EmailHookHandler,
            InternalServiceHookHandler,
            EventOutboxHookHandler,
        )

        handler_map: dict[HookHandlerType, type[HookHandler]] = {
            HookHandlerType.NOTIFICATION: NotificationHookHandler,
            HookHandlerType.WEBHOOK: WebhookHookHandler,
            HookHandlerType.EMAIL: EmailHookHandler,
            HookHandlerType.INTERNAL_SERVICE: InternalServiceHookHandler,
            HookHandlerType.EVENT_OUTBOX: EventOutboxHookHandler,
        }
        handler_cls = handler_map.get(handler_type)
        if not handler_cls:
            raise ValueError(f"Unknown handler type: {handler_type}")
        return handler_cls()


def func_now():
    """Helper to get current UTC time."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
```

#### Hook Handlers

```python
# app/services/hooks/handlers.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.models.platform.service_hook import ServiceHook
from app.services.hooks.registry import HookEvent

logger = logging.getLogger(__name__)


class HookHandler(ABC):
    """Base class for hook handlers."""

    @abstractmethod
    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        """
        Execute the hook action.

        Args:
            db: Database session
            hook: The hook configuration
            event: The triggering event

        Returns:
            Dict with execution results (varies by handler type)
        """
        ...


class NotificationHookHandler(HookHandler):
    """Create in-app notifications via NotificationService."""

    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        from app.services.notification import NotificationService
        from app.models.notification import EntityType, NotificationType, NotificationChannel
        from app.services.rbac import get_users_with_role

        config = hook.handler_config
        channel = NotificationChannel(config.get("channel", "IN_APP"))

        # Determine recipients
        recipient_ids: list = []
        if "recipient_role" in config:
            recipient_ids = get_users_with_role(
                db, event.organization_id, config["recipient_role"]
            )
        elif "recipient_ids" in config:
            from app.services.file_upload import coerce_uuid
            recipient_ids = [coerce_uuid(r) for r in config["recipient_ids"]]

        # Build notification from template
        title = config.get("title_template", f"Event: {event.event_name}")
        # Simple template substitution from payload
        for key, value in event.payload.items():
            title = title.replace(f"${{{key}}}", str(value))

        notification_svc = NotificationService()
        created = 0
        for recipient_id in recipient_ids:
            try:
                notification_svc.create(
                    db,
                    organization_id=event.organization_id,
                    recipient_id=recipient_id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=event.entity_id,
                    notification_type=NotificationType.INFO,
                    title=title,
                    message=config.get("message_template", ""),
                    channel=channel,
                    action_url=config.get("action_url"),
                )
                created += 1
            except Exception as e:
                logger.warning("Failed to create notification for %s: %s", recipient_id, e)

        return {"notifications_created": created}


class WebhookHookHandler(HookHandler):
    """POST event payload to an external URL."""

    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        import httpx

        config = hook.handler_config
        url = config["url"]
        headers = config.get("headers", {})
        method = config.get("method", "POST").upper()
        timeout = config.get("timeout_seconds", 30)

        payload = {
            "event": event.event_name,
            "organization_id": str(event.organization_id),
            "entity_type": event.entity_type,
            "entity_id": str(event.entity_id),
            "data": event.payload,
        }

        with httpx.Client(timeout=timeout) as client:
            if method == "POST":
                response = client.post(url, json=payload, headers=headers)
            elif method == "PUT":
                response = client.put(url, json=payload, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()

        return {
            "status_code": response.status_code,
            "response_body": response.text[:1000],
        }


class EmailHookHandler(HookHandler):
    """Send email notification."""

    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        from app.services.email import send_email

        config = hook.handler_config
        to_addresses = config["to"]
        subject = config.get("subject_template", f"Event: {event.event_name}")
        body = config.get("body_template", "")

        # Simple template substitution
        for key, value in event.payload.items():
            subject = subject.replace(f"${{{key}}}", str(value))
            body = body.replace(f"${{{key}}}", str(value))

        send_email(to=to_addresses, subject=subject, body=body)
        return {"emails_sent": len(to_addresses)}


class InternalServiceHookHandler(HookHandler):
    """Call another DotMac service method."""

    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        import importlib

        config = hook.handler_config
        module_path, class_name = config["service"].rsplit(".", 1)

        # Dynamic import
        module = importlib.import_module(module_path)
        service_cls = getattr(module, class_name)
        service = service_cls(db)

        method = getattr(service, config["method"])

        # Map event payload to method args using args_map
        kwargs: dict[str, Any] = {}
        for param_name, source_path in config.get("args_map", {}).items():
            if source_path.startswith("$."):
                # Extract from event attributes
                attr = source_path[2:]
                kwargs[param_name] = getattr(event, attr, None)
            elif source_path.startswith("$payload."):
                # Extract from payload
                key = source_path[9:]
                kwargs[param_name] = event.payload.get(key)

        result = method(**kwargs)
        return {"result": str(result)}


class EventOutboxHookHandler(HookHandler):
    """Write event to the EventOutbox for downstream consumers."""

    def execute(self, db: Session, hook: ServiceHook, event: HookEvent) -> dict[str, Any]:
        from app.models.finance.platform.event_outbox import EventOutbox, EventStatus

        config = hook.handler_config
        outbox_event_name = config.get("event_name_override", event.event_name)

        outbox_entry = EventOutbox(
            event_name=outbox_event_name,
            producer_module=event.event_name.split(".")[0],
            aggregate_type=event.entity_type,
            aggregate_id=str(event.entity_id),
            payload=event.payload,
            headers={
                "organization_id": str(event.organization_id),
                "user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "source": "service_hook",
            },
            status=EventStatus.PENDING,
            idempotency_key=f"hook:{hook.hook_id}:{event.entity_id}",
        )
        db.add(outbox_entry)
        db.flush()

        return {"outbox_event_id": str(outbox_entry.event_id)}
```

#### `ServiceHookService` (CRUD + Management)

```python
# app/services/hooks/service_hook_service.py
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.platform.service_hook import (
    HookHandlerType,
    HookExecutionMode,
    ServiceHook,
)
from app.models.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)

logger = logging.getLogger(__name__)


class ServiceHookService:
    """CRUD and management for service hooks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        organization_id: UUID | None,
        event_name: str,
        handler_type: HookHandlerType,
        handler_config: dict,
        name: str,
        *,
        execution_mode: HookExecutionMode = HookExecutionMode.ASYNC,
        conditions: dict | None = None,
        description: str | None = None,
        priority: int = 10,
        max_retries: int = 3,
        created_by_user_id: UUID | None = None,
    ) -> ServiceHook:
        """Create a new service hook."""
        hook = ServiceHook(
            organization_id=organization_id,
            event_name=event_name,
            handler_type=handler_type,
            execution_mode=execution_mode,
            handler_config=handler_config,
            conditions=conditions or {},
            name=name,
            description=description,
            priority=priority,
            max_retries=max_retries,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(hook)
        self.db.flush()
        logger.info("Created service hook %s: %s on %s", hook.hook_id, name, event_name)
        return hook

    def update(
        self,
        hook_id: UUID,
        **kwargs: dict,
    ) -> ServiceHook:
        """Update hook configuration."""
        hook = self.db.get(ServiceHook, hook_id)
        if not hook:
            raise ValueError(f"Hook {hook_id} not found.")

        for key, value in kwargs.items():
            if hasattr(hook, key):
                setattr(hook, key, value)

        self.db.flush()
        logger.info("Updated service hook %s", hook_id)
        return hook

    def delete(self, hook_id: UUID) -> None:
        """Delete a hook and its execution history."""
        hook = self.db.get(ServiceHook, hook_id)
        if not hook:
            raise ValueError(f"Hook {hook_id} not found.")

        self.db.delete(hook)
        self.db.flush()
        logger.info("Deleted service hook %s", hook_id)

    def toggle(self, hook_id: UUID, is_active: bool) -> ServiceHook:
        """Enable or disable a hook."""
        hook = self.db.get(ServiceHook, hook_id)
        if not hook:
            raise ValueError(f"Hook {hook_id} not found.")
        hook.is_active = is_active
        self.db.flush()
        return hook

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        event_name: str | None = None,
        is_active: bool | None = None,
    ) -> list[ServiceHook]:
        """List hooks for an organization (including global hooks)."""
        stmt = select(ServiceHook).where(
            ServiceHook.organization_id.in_([organization_id])
            | ServiceHook.organization_id.is_(None)
        )
        if event_name:
            stmt = stmt.where(ServiceHook.event_name == event_name)
        if is_active is not None:
            stmt = stmt.where(ServiceHook.is_active == is_active)

        stmt = stmt.order_by(ServiceHook.priority.asc(), ServiceHook.created_at.asc())
        return list(self.db.scalars(stmt).all())

    def get_execution_stats(
        self,
        hook_id: UUID,
        days: int = 30,
    ) -> dict[str, int]:
        """Get execution statistics for a hook over the last N days."""
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = (
            select(
                ServiceHookExecution.status,
                func.count(ServiceHookExecution.execution_id),
            )
            .where(
                ServiceHookExecution.hook_id == hook_id,
                ServiceHookExecution.created_at >= cutoff,
            )
            .group_by(ServiceHookExecution.status)
        )

        results = self.db.execute(stmt).all()
        stats: dict[str, int] = {s.value: 0 for s in ExecutionStatus}
        for status, count in results:
            stats[status.value] = count

        return stats
```

### Celery Task for Async Hooks

```python
# app/tasks/hooks.py
from __future__ import annotations

import logging
import time

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=3600,
)
def execute_async_hook(self, execution_id: str, hook_id: str) -> dict:
    """
    Execute an async service hook (webhook, email, etc.).

    Uses Celery's built-in retry with exponential backoff.
    Records execution results for audit trail.
    """
    from uuid import UUID
    from app.models.platform.service_hook import ServiceHook
    from app.models.platform.service_hook_execution import (
        ExecutionStatus,
        ServiceHookExecution,
    )
    from app.services.hooks.registry import HookEvent, HookRegistry

    logger.info("Executing async hook: execution=%s, hook=%s", execution_id, hook_id)

    with SessionLocal() as db:
        execution = db.get(ServiceHookExecution, UUID(execution_id))
        hook = db.get(ServiceHook, UUID(hook_id))

        if not execution or not hook:
            logger.error("Hook or execution not found: hook=%s, exec=%s", hook_id, execution_id)
            return {"error": "not_found"}

        if not hook.is_active:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = "Hook is disabled"
            db.commit()
            return {"error": "hook_disabled"}

        # Reconstruct event from execution record
        event = HookEvent(
            event_name=execution.event_name,
            organization_id=execution.organization_id,
            entity_type=execution.event_payload.get("entity_type", ""),
            entity_id=UUID(execution.event_payload.get("entity_id", "")),
            actor_user_id=None,
            payload=execution.event_payload,
        )

        start = time.monotonic()
        try:
            registry = HookRegistry(db)
            handler = registry._get_handler(hook.handler_type)
            result = handler.execute(db, hook, event)

            execution.status = ExecutionStatus.SUCCESS
            execution.response_body = str(result)[:1000]
            execution.response_status_code = result.get("status_code")
            execution.duration_ms = int((time.monotonic() - start) * 1000)
            execution.executed_at = func_now()
            execution.retry_count = self.request.retries

            db.commit()
            logger.info("Async hook %s executed successfully", hook_id)
            return {"status": "success", **result}

        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            execution.retry_count = self.request.retries
            execution.duration_ms = elapsed
            execution.error_message = str(e)[:500]

            if self.request.retries >= hook.max_retries:
                execution.status = ExecutionStatus.DEAD
                db.commit()
                logger.error("Async hook %s exhausted retries: %s", hook_id, e)
                return {"status": "dead", "error": str(e)}
            else:
                execution.status = ExecutionStatus.RETRYING
                db.commit()
                raise  # Celery will retry


def func_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
```

### Emitting Events from Domain Services

Example integration — emitting from `InvoiceService`:

```python
# app/services/finance/ar/invoice_service.py — add to create() method

def create(self, data: InvoiceCreate, user_id: UUID) -> Invoice:
    """Create a new invoice."""
    invoice = Invoice(**data.model_dump())
    self.db.add(invoice)
    self.db.flush()

    # Emit hook event (silently skipped if hooks are disabled)
    try:
        from app.services.hooks.registry import HookRegistry, HookEvent
        registry = HookRegistry(self.db)
        registry.emit(HookEvent(
            event_name="ar.invoice.created",
            organization_id=invoice.organization_id,
            entity_type="Invoice",
            entity_id=invoice.invoice_id,
            actor_user_id=user_id,
            payload={
                "invoice_number": invoice.invoice_number,
                "customer_id": str(invoice.customer_id),
                "amount": str(invoice.total_amount),
                "currency": invoice.currency_code,
                "status": invoice.status.value,
            },
        ))
    except Exception as e:
        logger.exception("Failed to emit hook event for invoice %s: %s", invoice.invoice_id, e)
        # Never break main flow

    return invoice
```

### Convenience Decorator (Optional Enhancement)

For cleaner integration, a decorator pattern:

```python
# app/services/hooks/decorator.py
from __future__ import annotations

import functools
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def emits_event(event_name: str, payload_builder: Callable[..., dict[str, Any]]):
    """
    Decorator that emits a hook event after the wrapped method succeeds.

    Usage:
        @emits_event("ar.invoice.created", lambda self, result: {
            "invoice_number": result.invoice_number,
            "amount": str(result.total_amount),
        })
        def create(self, data: InvoiceCreate, user_id: UUID) -> Invoice:
            ...
    """
    def decorator(method: Callable) -> Callable:
        @functools.wraps(method)
        def wrapper(self, *args: Any, **kwargs: Any) -> Any:
            result = method(self, *args, **kwargs)

            try:
                from app.services.hooks.registry import HookRegistry, HookEvent

                payload = payload_builder(self, result)
                registry = HookRegistry(self.db)
                registry.emit(HookEvent(
                    event_name=event_name,
                    organization_id=result.organization_id,
                    entity_type=type(result).__name__,
                    entity_id=getattr(result, "id", None) or getattr(result, f"{type(result).__name__.lower()}_id", None),
                    actor_user_id=kwargs.get("user_id"),
                    payload=payload,
                ))
            except Exception as e:
                logger.exception("Hook emission failed for %s: %s", event_name, e)

            return result
        return wrapper
    return decorator
```

### Migration

```python
# alembic/versions/xxx_add_service_hooks.py

def upgrade() -> None:
    # Create enum types
    op.execute("CREATE TYPE hook_handler_type AS ENUM ('NOTIFICATION', 'WEBHOOK', 'EMAIL', 'INTERNAL_SERVICE', 'EVENT_OUTBOX')")
    op.execute("CREATE TYPE hook_execution_mode AS ENUM ('SYNC', 'ASYNC')")
    op.execute("CREATE TYPE hook_execution_status AS ENUM ('PENDING', 'SUCCESS', 'FAILED', 'RETRYING', 'DEAD')")

    # ServiceHook table
    op.create_table(
        'service_hook',
        sa.Column('hook_id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.organization_id'), nullable=True),
        sa.Column('event_name', sa.String(100), nullable=False),
        sa.Column('handler_type', sa.Enum('NOTIFICATION', 'WEBHOOK', 'EMAIL', 'INTERNAL_SERVICE', 'EVENT_OUTBOX', name='hook_handler_type', create_type=False), nullable=False),
        sa.Column('execution_mode', sa.Enum('SYNC', 'ASYNC', name='hook_execution_mode', create_type=False), nullable=False, server_default='ASYNC'),
        sa.Column('handler_config', sa.dialects.postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('conditions', sa.dialects.postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('priority', sa.Integer, nullable=False, server_default='10'),
        sa.Column('max_retries', sa.Integer, nullable=False, server_default='3'),
        sa.Column('retry_backoff_seconds', sa.Integer, nullable=False, server_default='60'),
        sa.Column('created_by_user_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='platform',
    )

    op.create_index('ix_hook_event_org', 'service_hook', ['event_name', 'organization_id'], schema='platform')
    op.create_index('ix_hook_active', 'service_hook', ['is_active', 'event_name'], schema='platform')

    # ServiceHookExecution table
    op.create_table(
        'service_hook_execution',
        sa.Column('execution_id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hook_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('platform.service_hook.hook_id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_name', sa.String(100), nullable=False),
        sa.Column('event_payload', sa.dialects.postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('status', sa.Enum('PENDING', 'SUCCESS', 'FAILED', 'RETRYING', 'DEAD', name='hook_execution_status', create_type=False), nullable=False, server_default='PENDING'),
        sa.Column('response_body', sa.Text, nullable=True),
        sa.Column('response_status_code', sa.Integer, nullable=True),
        sa.Column('error_message', sa.String(500), nullable=True),
        sa.Column('retry_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        schema='platform',
    )

    op.create_index('ix_execution_hook_status', 'service_hook_execution', ['hook_id', 'status'], schema='platform')
    op.create_index('ix_execution_created', 'service_hook_execution', ['created_at'], schema='platform')


def downgrade() -> None:
    op.drop_table('service_hook_execution', schema='platform')
    op.drop_table('service_hook', schema='platform')
    op.execute("DROP TYPE IF EXISTS hook_execution_status")
    op.execute("DROP TYPE IF EXISTS hook_execution_mode")
    op.execute("DROP TYPE IF EXISTS hook_handler_type")
```

### UI: Hook Management Page

```
Route: /automation/hooks
Template: templates/automation/hooks/list.html
```

Management UI for organization admins to view, create, enable/disable hooks:

```
┌─────────────────────────────────────────────────────┐
│ Service Hooks                        [+ New Hook]   │
├─────────────────────────────────────────────────────┤
│ [Search hooks...          ] [Handler ▾] [Status ▾]  │
├─────────────────────────────────────────────────────┤
│ ☐ Name           │ Event          │ Handler  │ ✓/✗  │
│ ☐ Slack on invoice│ ar.invoice.crt│ WEBHOOK  │ ✓    │
│ ☐ CRM sync       │ ar.customer.cr│ SERVICE  │ ✓    │
│ ☐ Overdue email  │ ar.invoice.ovd│ EMAIL    │ ✗    │
├─────────────────────────────────────────────────────┤
│ Hook Detail (click to expand)                       │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Last 30 days: 142 success, 3 failed, 0 dead    │ │
│ │ Recent executions:                              │ │
│ │   ✓ 24 Feb 14:30 — 120ms — Invoice INV-5241   │ │
│ │   ✓ 24 Feb 14:15 — 95ms  — Invoice INV-5240   │ │
│ │   ✗ 24 Feb 13:50 — 5001ms — Timeout            │ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### Testing Plan

```
tests/services/hooks/test_hook_registry.py
├── test_emit_event_finds_matching_hooks
├── test_emit_event_skips_inactive_hooks
├── test_emit_event_skips_other_org_hooks
├── test_global_hook_fires_for_all_orgs
├── test_condition_evaluation_exact_match
├── test_condition_evaluation_gt_lt
├── test_condition_evaluation_in_operator
├── test_sync_hook_executes_immediately
├── test_async_hook_queues_celery_task
├── test_sync_hook_failure_does_not_break_caller
├── test_feature_flag_disabled_skips_all
└── test_execution_audit_trail

tests/services/hooks/test_handlers.py
├── test_notification_handler_creates_notifications
├── test_webhook_handler_posts_to_url
├── test_webhook_handler_retries_on_failure
├── test_email_handler_sends_email
├── test_internal_service_handler_calls_method
├── test_event_outbox_handler_writes_entry
└── test_handler_template_substitution

tests/services/hooks/test_service_hook_service.py
├── test_create_hook
├── test_update_hook
├── test_delete_hook
├── test_toggle_hook
├── test_list_for_org_includes_global
├── test_list_filters_by_event_name
└── test_execution_stats
```

### Implementation Schedule (8 days)

| Day | Task |
|-----|------|
| 1 | Models (`ServiceHook`, `ServiceHookExecution`) + migration + enums |
| 2 | `HookRegistry` — `emit()`, `_find_matching_hooks()`, `_evaluate_conditions()` |
| 3 | Hook handlers — `NotificationHookHandler`, `WebhookHookHandler`, `EmailHookHandler` |
| 4 | Hook handlers — `InternalServiceHookHandler`, `EventOutboxHookHandler` |
| 5 | `ServiceHookService` (CRUD) + feature flag + Celery async task |
| 6 | Integration — add `emit()` calls to 5 key services (AR invoice, AP payment, SO, GL journal, inventory) |
| 7 | UI — hook management list + detail + create form |
| 8 | Tests — registry, handlers, CRUD, integration (20+ test cases) |

---

## Cross-Feature Integration

### Stock Reservation → Service Hooks

When the hook system is active, stock reservation events are emitted automatically:

```python
# In StockReservationService.reserve() — after successful reservation
from app.services.hooks.events import INVENTORY_STOCK_RESERVED

try:
    registry = HookRegistry(self.db)
    registry.emit(HookEvent(
        event_name=INVENTORY_STOCK_RESERVED,
        organization_id=organization_id,
        entity_type="StockReservation",
        entity_id=reservation.reservation_id,
        actor_user_id=reserved_by_user_id,
        payload={
            "item_id": str(item_id),
            "warehouse_id": str(warehouse_id),
            "quantity_reserved": str(qty_to_reserve),
            "shortfall": str(shortfall),
            "source_type": source_type.value,
            "source_id": str(source_id),
        },
    ))
except Exception as e:
    logger.exception("Hook emission failed: %s", e)
```

### Low Stock Alert Hook

Combine with reservation to trigger alerts:

```python
# Seed data for default hook
ServiceHookService(db).create(
    organization_id=None,  # Global
    event_name="inventory.stock.low",
    handler_type=HookHandlerType.NOTIFICATION,
    handler_config={
        "recipient_role": "inventory_manager",
        "channel": "BOTH",
        "title_template": "Low Stock: ${item_code}",
        "message_template": "Available quantity is ${available} (reorder point: ${reorder_point})",
        "action_url": "/inventory/items/${item_id}",
    },
    name="Low Stock Alert",
    execution_mode=HookExecutionMode.SYNC,
)
```

---

## Summary

| Feature | New Models | New Services | New Files | Lines (est.) |
|---------|-----------|-------------|-----------|-------------|
| Stock Reservation | 1 (`StockReservation`) | 1 (`StockReservationService`) | 4 (model, service, migration, task) | ~800 |
| Service Hook System | 2 (`ServiceHook`, `ServiceHookExecution`) | 4 (`HookRegistry`, handlers, `ServiceHookService`, events) | 8 (models, services, task, migration) | ~1,200 |
| **Total Phase 4** | **3 models** | **5 services** | **12 files** | **~2,000** |

### Dependencies

- **Stock Reservation** requires: Phase 3 inventory valuation (for cost tracking on fulfillment)
- **Service Hooks** is independent but benefits from Phase 2 field-level tracking (emit change events)
- Both features are gated behind feature flags — zero impact when disabled

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Row lock contention on lots | `SELECT ... FOR UPDATE` with short transaction scope; consider `SKIP LOCKED` for high-volume |
| Webhook timeout cascading | Async execution via Celery; configurable timeout per hook; circuit breaker pattern (disable hook after N consecutive failures) |
| Hook execution volume | Execution log table grows fast — add retention policy (delete records > 90 days via scheduled task) |
| SQLite test incompatibility | `with_for_update()` not supported in SQLite — mock or skip in unit tests, test with PostgreSQL in integration |
