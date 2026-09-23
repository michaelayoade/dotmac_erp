"""Issue available material-request lines without closing outstanding lines."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.inventory import (
    InventoryTransaction,
    Item,
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestStatus,
    MaterialRequestType,
    TransactionType,
)
from app.services.finance.gl.period_guard import PeriodGuardService
from app.services.inventory.serial import InventorySerialService
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)


def _serials_for_issue(
    line: MaterialRequestItem, quantity: Decimal
) -> list[str] | None:
    """Use only the next units from the immutable saved serial selection.

    Missing selections still reach the inventory owner's mandatory validation.
    Never pick substitute hardware or reuse already-issued saved serials.
    """
    serials = InventorySerialService.normalize_serial_numbers(
        getattr(line, "serial_numbers", None)
    )
    if not serials:
        return None
    already_issued = line.ordered_qty or Decimal("0")
    if any(
        value != value.to_integral_value()
        for value in (line.requested_qty, already_issued, quantity)
    ) or len(serials) != int(line.requested_qty):
        raise ValueError(
            f"Item #{line.sequence}: saved serial selections must match the "
            "requested quantity and serialized issues must be whole units"
        )
    start = int(already_issued)
    return serials[start : start + int(quantity)]


def validate_sub_issue_history(
    db: Session,
    organization_id: UUID,
    request_id: UUID,
    lines: list[MaterialRequestItem],
) -> None:
    """Refuse ambiguous historical counters without repairing or replaying stock.

    Both MATERIAL_REQUEST and older Sub_MATERIAL_REQUEST transactions bind the
    same request/line UUIDs. Returns do not reopen an original material request.
    """
    posted = dict(
        db.execute(
            select(
                InventoryTransaction.source_document_line_id,
                func.sum(InventoryTransaction.quantity),
            )
            .where(
                InventoryTransaction.organization_id == organization_id,
                InventoryTransaction.source_document_id == request_id,
                InventoryTransaction.transaction_type == TransactionType.ISSUE,
            )
            .group_by(InventoryTransaction.source_document_line_id)
        ).all()
    )
    if set(posted) - {line.item_id for line in lines}:
        raise ValueError(
            "Historical issue entries cannot be matched to request lines; "
            "reconcile this request before issuing more stock"
        )
    for line in lines:
        issued = line.ordered_qty or Decimal("0")
        if (
            issued < 0
            or issued > line.requested_qty
            or posted.get(line.item_id, Decimal("0")) != issued
        ):
            raise ValueError(
                f"Item #{line.sequence}: issued quantity differs from stock history; "
                "reconcile this request before issuing more stock"
            )


class MaterialRequestIssueService:
    @staticmethod
    def issue_available(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_id: UUID,
        quantities: dict[UUID, Decimal],
        expected_issued: dict[UUID, Decimal],
        out_of_stock_ids: set[UUID],
    ) -> MaterialRequest:
        """Post selected quantities and the Sub outcome in one caller transaction.

        Creation date and source do not exclude eligible ISSUE requests. The
        request lock is shared with legacy Sub resends and automatic issuance.
        """
        request = db.scalars(
            select(MaterialRequest)
            .where(
                MaterialRequest.request_id == request_id,
                MaterialRequest.organization_id == organization_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if request is None:
            raise ValueError("Material request not found")
        if request.request_type != MaterialRequestType.ISSUE:
            raise ValueError("Only material issue requests support line-by-line issue")
        if request.status not in {
            MaterialRequestStatus.SUBMITTED,
            MaterialRequestStatus.PENDING_STOCK,
            MaterialRequestStatus.PARTIALLY_ISSUED,
        }:
            raise ValueError("Request is not ready to issue")

        lines = list(
            db.scalars(
                select(MaterialRequestItem)
                .where(
                    MaterialRequestItem.request_id == request_id,
                    MaterialRequestItem.organization_id == organization_id,
                )
                .order_by(MaterialRequestItem.sequence)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        pending = {
            line.item_id: line
            for line in lines
            if line.requested_qty > (line.ordered_qty or Decimal("0"))
        }
        if (
            not pending
            or set(quantities) != set(pending)
            or set(expected_issued) != set(pending)
        ):
            raise ValueError(
                "Request lines changed; reload and review the issue quantities"
            )
        if not out_of_stock_ids <= set(pending):
            raise ValueError("Unknown out-of-stock line")
        if request.source_system == "sub":
            validate_sub_issue_history(db, organization_id, request_id, lines)

        # Serialize shared stock buckets in stable order across requests.
        db.execute(
            select(Item.item_id)
            .where(
                Item.organization_id == organization_id,
                Item.item_id.in_({line.inventory_item_id for line in pending.values()}),
            )
            .order_by(Item.item_id)
            .with_for_update()
        ).all()

        balances: dict[tuple[UUID, UUID], Decimal] = {}
        selected: list[tuple[MaterialRequestItem, Decimal, UUID]] = []
        for line_id, line in pending.items():
            already_issued = line.ordered_qty or Decimal("0")
            quantity = quantities[line_id]
            outstanding = line.requested_qty - already_issued
            if expected_issued[line_id] != already_issued:
                raise ValueError(
                    "Request lines changed; reload and review the issue quantities"
                )
            if not quantity.is_finite() or quantity < 0 or quantity > outstanding:
                raise ValueError(f"Item #{line.sequence}: invalid issue quantity")
            exponent = quantity.as_tuple().exponent
            if isinstance(exponent, int) and exponent < -6:
                raise ValueError(
                    f"Item #{line.sequence}: quantity has too many decimal places"
                )
            warehouse_id = line.warehouse_id or request.default_warehouse_id
            if warehouse_id is None:
                raise ValueError(f"Item #{line.sequence}: select a warehouse first")
            key = (line.inventory_item_id, warehouse_id)
            if key not in balances:
                balances[key] = InventoryTransactionService.get_current_balance(
                    db, organization_id, line.inventory_item_id, warehouse_id
                )
            available = balances[key]
            if line_id in out_of_stock_ids:
                if available > 0 or quantity != 0:
                    raise ValueError(
                        f"Item #{line.sequence}: stock is available; remove the out-of-stock mark"
                    )
            elif available <= 0:
                raise ValueError(f"Item #{line.sequence}: mark this line out of stock")
            if quantity > max(available, Decimal("0")):
                raise ValueError(f"Item #{line.sequence}: only {available} available")
            balances[key] -= quantity
            if quantity > 0:
                selected.append((line, quantity, warehouse_id))

        if not selected:
            raise ValueError("Enter a quantity to issue on at least one line")
        now = datetime.now(timezone.utc)
        fiscal_period = PeriodGuardService.get_period_for_date(
            db, organization_id, now.date()
        )
        if fiscal_period is None:
            raise ValueError("No open fiscal period exists for today")

        old_status = request.status
        for line, quantity, warehouse_id in selected:
            item = db.get(Item, line.inventory_item_id)
            if item is None or item.organization_id != organization_id:
                raise ValueError(f"Item #{line.sequence}: inventory item not found")
            transaction = TransactionInput(
                transaction_type=TransactionType.ISSUE,
                transaction_date=now,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                item_id=line.inventory_item_id,
                warehouse_id=warehouse_id,
                quantity=quantity,
                unit_cost=item.average_cost or Decimal("0"),
                uom=line.uom or item.base_uom or "",
                currency_code=item.currency_code
                or settings.default_presentation_currency_code,
                source_document_type="MATERIAL_REQUEST",
                source_document_id=request.request_id,
                source_document_line_id=line.item_id,
                reference=request.request_number,
                serial_numbers=_serials_for_issue(line, quantity),
            )
            InventoryTransactionService.create_issue(
                db, organization_id, transaction, user_id, auto_commit=False
            )
            line.ordered_qty = (line.ordered_qty or Decimal("0")) + quantity

        for line_id, line in pending.items():
            line.out_of_stock = line_id in out_of_stock_ids
        request.status = (
            MaterialRequestStatus.ISSUED
            if all(line.ordered_qty >= line.requested_qty for line in lines)
            else MaterialRequestStatus.PARTIALLY_ISSUED
        )
        request.updated_by_id = user_id
        request.updated_at = now
        db.flush()
        if request.source_system == "sub":
            from app.services.sync.sub.procurement import _ProcurementMixin

            _ProcurementMixin(db)._emit_sub_material_request_status_changed(
                org_id=organization_id,
                request=request,
                old_status=old_status,
                new_status=request.status,
                actor_person_id=user_id,
            )
        return request
