"""Stock reservation service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory.stock_reservation import (
    ReservationSourceType,
    ReservationStatus,
    StockReservation,
)
from app.services.feature_flags import FEATURE_STOCK_RESERVATION, is_feature_enabled
from app.services.hooks import emit_hook_event
from app.services.hooks.events import INVENTORY_STOCK_RELEASED, INVENTORY_STOCK_RESERVED
from app.services.inventory.balance import InventoryBalanceService
from app.services.settings_cache import settings_cache

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = [
    ReservationStatus.RESERVED,
    ReservationStatus.PARTIALLY_FULFILLED,
]


@dataclass(frozen=True)
class ReservationResult:
    """Result of a reservation attempt."""

    success: bool
    reservation_id: UUID | None
    quantity_reserved: Decimal
    quantity_requested: Decimal
    shortfall: Decimal
    message: str


@dataclass(frozen=True)
class ReservationConfig:
    """Org-scoped stock reservation configuration."""

    enabled: bool
    expiry_hours: int
    allow_partial: bool
    auto_on_confirm: bool


class StockReservationService:
    """Manage stock reservation lifecycle and inventory allocation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def load_config(db: Session, organization_id: UUID) -> ReservationConfig:
        """Load reservation config from settings cache."""

        def _bool(key: str, default: bool) -> bool:
            value = settings_cache.get_setting_value(
                db,
                SettingDomain.inventory,
                key,
                default=default,
            )
            return bool(value)

        def _int(key: str, default: int) -> int:
            value = settings_cache.get_setting_value(
                db,
                SettingDomain.inventory,
                key,
                default=default,
            )
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        return ReservationConfig(
            enabled=_bool("stock_reservation_enabled", False),
            expiry_hours=max(0, _int("stock_reservation_expiry_hours", 0)),
            allow_partial=_bool("stock_reservation_allow_partial", True),
            auto_on_confirm=_bool("stock_reservation_auto_on_confirm", True),
        )

    def reserve(
        self,
        organization_id: UUID,
        item_id: UUID,
        quantity: Decimal,
        source_type: ReservationSourceType,
        source_id: UUID,
        source_line_id: UUID,
        reserved_by_user_id: UUID,
        *,
        warehouse_id: UUID | None = None,
        lot_id: UUID | None = None,
        priority: int = 10,
        config: ReservationConfig | None = None,
    ) -> ReservationResult:
        """Reserve stock for a demand line."""
        qty_requested = Decimal(str(quantity))
        if qty_requested <= 0:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=max(Decimal("0"), qty_requested),
                message="Quantity must be greater than zero.",
            )

        if not is_feature_enabled(self.db, FEATURE_STOCK_RESERVATION):
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=qty_requested,
                message="Stock reservation feature flag is disabled.",
            )

        cfg = config or self.load_config(self.db, organization_id)
        if not cfg.enabled:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=qty_requested,
                message="Stock reservation is not enabled in inventory settings.",
            )

        existing = self.get_reservation_for_line(source_type, source_line_id)
        if existing:
            return ReservationResult(
                success=True,
                reservation_id=existing.reservation_id,
                quantity_reserved=existing.quantity_remaining,
                quantity_requested=qty_requested,
                shortfall=max(
                    Decimal("0"), qty_requested - existing.quantity_remaining
                ),
                message="Reservation already exists.",
            )

        available = InventoryBalanceService.get_available(
            self.db,
            organization_id,
            item_id,
            warehouse_id,
        )
        quantity_to_reserve = min(available, qty_requested)

        if quantity_to_reserve <= 0:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=qty_requested,
                message="No stock available for reservation.",
            )

        if quantity_to_reserve < qty_requested and not cfg.allow_partial:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=qty_requested,
                message="Insufficient stock and partial reservation is disabled.",
            )

        allocated = InventoryBalanceService.allocate_inventory(
            self.db,
            organization_id,
            item_id,
            quantity_to_reserve,
            source_type.value,
            source_id,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
        )
        if not allocated:
            return ReservationResult(
                success=False,
                reservation_id=None,
                quantity_reserved=Decimal("0"),
                quantity_requested=qty_requested,
                shortfall=qty_requested,
                message="Unable to allocate inventory for reservation.",
            )

        expires_at = None
        if cfg.expiry_hours > 0:
            expires_at = datetime.now(UTC) + timedelta(hours=cfg.expiry_hours)

        reservation = StockReservation(
            organization_id=organization_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            quantity_reserved=quantity_to_reserve,
            source_type=source_type,
            source_id=source_id,
            source_line_id=source_line_id,
            status=ReservationStatus.RESERVED,
            expires_at=expires_at,
            priority=priority,
            reserved_by_user_id=reserved_by_user_id,
        )
        self.db.add(reservation)
        self.db.flush()

        shortfall = qty_requested - quantity_to_reserve
        logger.info(
            "Reserved %s of %s for %s/%s (reservation=%s, shortfall=%s)",
            quantity_to_reserve,
            qty_requested,
            source_type.value,
            source_line_id,
            reservation.reservation_id,
            shortfall,
        )
        emit_hook_event(
            self.db,
            event_name=INVENTORY_STOCK_RESERVED,
            organization_id=organization_id,
            entity_type="StockReservation",
            entity_id=reservation.reservation_id,
            actor_user_id=reserved_by_user_id,
            payload={
                "reservation_id": str(reservation.reservation_id),
                "source_type": source_type.value,
                "source_id": str(source_id),
                "source_line_id": str(source_line_id),
                "item_id": str(item_id),
                "warehouse_id": str(warehouse_id) if warehouse_id else None,
                "quantity_reserved": str(quantity_to_reserve),
                "quantity_requested": str(qty_requested),
                "shortfall": str(shortfall),
            },
        )
        return ReservationResult(
            success=True,
            reservation_id=reservation.reservation_id,
            quantity_reserved=quantity_to_reserve,
            quantity_requested=qty_requested,
            shortfall=shortfall,
            message="Reserved successfully."
            if shortfall == 0
            else "Partially reserved.",
        )

    def fulfill(self, reservation_id: UUID, quantity: Decimal) -> StockReservation:
        """Consume reserved quantity when shipment is created."""
        reservation = self.db.get(StockReservation, reservation_id)
        if not reservation:
            raise ValueError(f"Reservation {reservation_id} not found.")

        if reservation.status in {
            ReservationStatus.FULFILLED,
            ReservationStatus.CANCELLED,
            ReservationStatus.EXPIRED,
        }:
            raise ValueError(
                f"Cannot fulfill reservation in status {reservation.status.value}."
            )

        qty = Decimal(str(quantity))
        if qty <= 0:
            raise ValueError("Fulfillment quantity must be greater than zero.")

        remaining = reservation.quantity_remaining
        if qty > remaining:
            raise ValueError(
                f"Cannot fulfill {qty} - only {remaining} remaining on reservation."
            )

        reservation.quantity_fulfilled = (
            reservation.quantity_fulfilled or Decimal("0")
        ) + qty

        if reservation.is_fully_fulfilled:
            reservation.status = ReservationStatus.FULFILLED
            reservation.fulfilled_at = datetime.now(UTC)
        else:
            reservation.status = ReservationStatus.PARTIALLY_FULFILLED

        logger.info(
            "Fulfilled %s on reservation %s (status=%s)",
            qty,
            reservation.reservation_id,
            reservation.status.value,
        )
        self.db.flush()
        return reservation

    def cancel(
        self, reservation_id: UUID, reason: str | None = None
    ) -> StockReservation:
        """Cancel reservation and release any remaining held quantity."""
        reservation = self.db.get(StockReservation, reservation_id)
        if not reservation:
            raise ValueError(f"Reservation {reservation_id} not found.")

        if reservation.status in {
            ReservationStatus.FULFILLED,
            ReservationStatus.CANCELLED,
            ReservationStatus.EXPIRED,
        }:
            raise ValueError(
                f"Cannot cancel reservation in status {reservation.status.value}."
            )

        remaining = reservation.quantity_remaining
        if remaining > 0:
            InventoryBalanceService.deallocate_inventory(
                self.db,
                reservation.organization_id,
                reservation.item_id,
                remaining,
                lot_id=reservation.lot_id,
                warehouse_id=reservation.warehouse_id,
            )

        reservation.quantity_cancelled = (
            reservation.quantity_cancelled or Decimal("0")
        ) + remaining
        reservation.status = ReservationStatus.CANCELLED
        reservation.cancelled_at = datetime.now(UTC)
        reservation.cancellation_reason = reason

        logger.info(
            "Cancelled reservation %s (released=%s)",
            reservation.reservation_id,
            remaining,
        )
        emit_hook_event(
            self.db,
            event_name=INVENTORY_STOCK_RELEASED,
            organization_id=reservation.organization_id,
            entity_type="StockReservation",
            entity_id=reservation.reservation_id,
            actor_user_id=None,
            payload={
                "reservation_id": str(reservation.reservation_id),
                "item_id": str(reservation.item_id),
                "warehouse_id": str(reservation.warehouse_id)
                if reservation.warehouse_id
                else None,
                "released_quantity": str(remaining),
                "reason": reason or "cancelled",
            },
        )
        self.db.flush()
        return reservation

    def release_expired(self, batch_size: int = 200) -> dict[str, int]:
        """Release reservations that passed `expires_at`."""
        now = datetime.now(UTC)
        stmt = (
            select(StockReservation)
            .where(
                StockReservation.status.in_(_ACTIVE_STATUSES),
                StockReservation.expires_at.isnot(None),
                StockReservation.expires_at <= now,
            )
            .order_by(StockReservation.expires_at.asc())
            .limit(batch_size)
        )
        expired = list(self.db.scalars(stmt).all())

        results = {"checked": len(expired), "released": 0, "errors": 0}

        for reservation in expired:
            try:
                remaining = reservation.quantity_remaining
                if remaining > 0:
                    InventoryBalanceService.deallocate_inventory(
                        self.db,
                        reservation.organization_id,
                        reservation.item_id,
                        remaining,
                        lot_id=reservation.lot_id,
                        warehouse_id=reservation.warehouse_id,
                    )

                reservation.quantity_cancelled = (
                    reservation.quantity_cancelled or Decimal("0")
                ) + remaining
                reservation.status = ReservationStatus.EXPIRED
                reservation.cancelled_at = now
                reservation.cancellation_reason = "Reservation expired"
                emit_hook_event(
                    self.db,
                    event_name=INVENTORY_STOCK_RELEASED,
                    organization_id=reservation.organization_id,
                    entity_type="StockReservation",
                    entity_id=reservation.reservation_id,
                    actor_user_id=None,
                    payload={
                        "reservation_id": str(reservation.reservation_id),
                        "item_id": str(reservation.item_id),
                        "warehouse_id": str(reservation.warehouse_id)
                        if reservation.warehouse_id
                        else None,
                        "released_quantity": str(remaining),
                        "reason": "expired",
                    },
                )
                results["released"] += 1
            except Exception:
                results["errors"] += 1
                logger.exception(
                    "Failed to release expired reservation %s",
                    reservation.reservation_id,
                )

        self.db.flush()
        return results

    def get_reservations_for_source(
        self,
        source_type: ReservationSourceType,
        source_id: UUID,
    ) -> list[StockReservation]:
        """Return active reservations for a source document."""
        stmt = select(StockReservation).where(
            StockReservation.source_type == source_type,
            StockReservation.source_id == source_id,
            StockReservation.status.in_(_ACTIVE_STATUSES),
        )
        return list(self.db.scalars(stmt).all())

    def get_reservation_for_line(
        self,
        source_type: ReservationSourceType,
        source_line_id: UUID,
    ) -> StockReservation | None:
        """Return active reservation for a source line."""
        stmt = select(StockReservation).where(
            StockReservation.source_type == source_type,
            StockReservation.source_line_id == source_line_id,
            StockReservation.status.in_(_ACTIVE_STATUSES),
        )
        return self.db.scalar(stmt)

    def get_reserved_quantity(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> Decimal:
        """Return remaining reserved quantity for an item."""
        remaining_expr = (
            StockReservation.quantity_reserved
            - StockReservation.quantity_fulfilled
            - StockReservation.quantity_cancelled
        )
        stmt = select(func.coalesce(func.sum(remaining_expr), 0)).where(
            and_(
                StockReservation.organization_id == organization_id,
                StockReservation.item_id == item_id,
                StockReservation.status.in_(_ACTIVE_STATUSES),
            )
        )
        if warehouse_id:
            stmt = stmt.where(StockReservation.warehouse_id == warehouse_id)
        result = self.db.scalar(stmt)
        return Decimal(str(result or "0"))
