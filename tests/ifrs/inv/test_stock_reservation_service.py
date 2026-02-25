"""Tests for StockReservationService."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.inventory.stock_reservation import (
    ReservationSourceType,
    ReservationStatus,
    StockReservation,
)
from app.services.inventory.stock_reservation import StockReservationService


def _reservation(**overrides) -> StockReservation:
    now = datetime.now(UTC)
    data = {
        "organization_id": uuid4(),
        "item_id": uuid4(),
        "quantity_reserved": Decimal("10"),
        "source_type": ReservationSourceType.SALES_ORDER,
        "source_id": uuid4(),
        "source_line_id": uuid4(),
        "reserved_by_user_id": uuid4(),
        "status": ReservationStatus.RESERVED,
        "reserved_at": now,
    }
    data.update(overrides)
    return StockReservation(**data)


class TestReserve:
    """Reserve behavior."""

    @patch("app.services.inventory.stock_reservation.emit_hook_event")
    @patch("app.services.inventory.stock_reservation.InventoryBalanceService")
    @patch("app.services.inventory.stock_reservation.is_feature_enabled")
    def test_reserve_success(
        self,
        mock_feature_enabled,
        mock_balance_service,
        mock_emit_hook_event,
    ):
        db = MagicMock()
        service = StockReservationService(db)
        org_id = uuid4()
        item_id = uuid4()

        mock_feature_enabled.return_value = True
        mock_balance_service.get_available.return_value = Decimal("50")
        mock_balance_service.allocate_inventory.return_value = True

        with patch.object(
            StockReservationService,
            "load_config",
            return_value=MagicMock(
                enabled=True,
                expiry_hours=24,
                allow_partial=True,
                auto_on_confirm=True,
            ),
        ):
            with patch.object(
                StockReservationService,
                "get_reservation_for_line",
                return_value=None,
            ):
                result = service.reserve(
                    organization_id=org_id,
                    item_id=item_id,
                    warehouse_id=uuid4(),
                    quantity=Decimal("20"),
                    source_type=ReservationSourceType.SALES_ORDER,
                    source_id=uuid4(),
                    source_line_id=uuid4(),
                    reserved_by_user_id=uuid4(),
                )

        assert result.success is True
        assert result.quantity_reserved == Decimal("20")
        db.add.assert_called_once()
        db.flush.assert_called_once()
        mock_emit_hook_event.assert_called_once()
        assert (
            mock_emit_hook_event.call_args.kwargs["event_name"]
            == "inventory.stock.reserved"
        )

    @patch("app.services.inventory.stock_reservation.InventoryBalanceService")
    @patch("app.services.inventory.stock_reservation.is_feature_enabled")
    def test_reserve_partial_disabled_fails(
        self,
        mock_feature_enabled,
        mock_balance_service,
    ):
        db = MagicMock()
        service = StockReservationService(db)

        mock_feature_enabled.return_value = True
        mock_balance_service.get_available.return_value = Decimal("5")

        with patch.object(
            StockReservationService,
            "load_config",
            return_value=MagicMock(
                enabled=True,
                expiry_hours=0,
                allow_partial=False,
                auto_on_confirm=True,
            ),
        ):
            with patch.object(
                StockReservationService,
                "get_reservation_for_line",
                return_value=None,
            ):
                result = service.reserve(
                    organization_id=uuid4(),
                    item_id=uuid4(),
                    warehouse_id=uuid4(),
                    quantity=Decimal("20"),
                    source_type=ReservationSourceType.SALES_ORDER,
                    source_id=uuid4(),
                    source_line_id=uuid4(),
                    reserved_by_user_id=uuid4(),
                )

        assert result.success is False
        assert "partial reservation is disabled" in result.message.lower()


class TestLifecycle:
    """Fulfill/cancel/expiry behavior."""

    @patch("app.services.inventory.stock_reservation.emit_hook_event")
    @patch("app.services.inventory.stock_reservation.InventoryBalanceService")
    def test_cancel_releases_remaining(self, mock_balance_service, _mock_emit):
        db = MagicMock()
        reservation = _reservation(
            quantity_reserved=Decimal("10"),
            quantity_fulfilled=Decimal("2"),
        )
        db.get.return_value = reservation
        service = StockReservationService(db)

        updated = service.cancel(reservation.reservation_id, reason="Test")

        assert updated.status == ReservationStatus.CANCELLED
        assert updated.quantity_cancelled == Decimal("8")
        assert updated.cancellation_reason == "Test"
        mock_balance_service.deallocate_inventory.assert_called_once()
        assert _mock_emit.call_count == 1
        assert _mock_emit.call_args.kwargs["event_name"] == "inventory.stock.released"
        assert _mock_emit.call_args.kwargs["payload"]["reason"] == "Test"

    def test_fulfill_marks_fully_fulfilled(self):
        db = MagicMock()
        reservation = _reservation(
            quantity_reserved=Decimal("10"),
            quantity_fulfilled=Decimal("0"),
        )
        db.get.return_value = reservation
        service = StockReservationService(db)

        updated = service.fulfill(reservation.reservation_id, Decimal("10"))

        assert updated.status == ReservationStatus.FULFILLED
        assert updated.quantity_fulfilled == Decimal("10")
        assert updated.fulfilled_at is not None

    @patch("app.services.inventory.stock_reservation.emit_hook_event")
    @patch("app.services.inventory.stock_reservation.InventoryBalanceService")
    def test_release_expired(self, mock_balance_service, _mock_emit):
        db = MagicMock()
        reservation = _reservation(
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            quantity_reserved=Decimal("6"),
        )
        db.scalars.return_value.all.return_value = [reservation]
        service = StockReservationService(db)

        result = service.release_expired(batch_size=50)

        assert result["checked"] == 1
        assert result["released"] == 1
        assert reservation.status == ReservationStatus.EXPIRED
        mock_balance_service.deallocate_inventory.assert_called_once()
        assert _mock_emit.call_count == 1
        assert _mock_emit.call_args.kwargs["event_name"] == "inventory.stock.released"
        assert _mock_emit.call_args.kwargs["payload"]["reason"] == "expired"
