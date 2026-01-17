"""
ARInventoryIntegration - AR to Inventory integration service.

Handles inventory issue transactions and COGS recording when AR invoices
with inventory items are posted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as tz
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.ar.invoice import Invoice, InvoiceType
from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.models.ifrs.inv.item import Item, CostingMethod
from app.models.ifrs.inv.item_category import ItemCategory
from app.models.ifrs.inv.inventory_transaction import TransactionType
from app.services.common import coerce_uuid
from app.services.ifrs.gl.journal import JournalLineInput
from app.services.ifrs.inv.transaction import (
    InventoryTransactionService,
    TransactionInput,
)


@dataclass
class CostingResult:
    """Result of a cost calculation for an inventory item."""

    unit_cost: Decimal
    total_cost: Decimal
    lot_id: Optional[UUID] = None


@dataclass
class InventoryPostingResult:
    """Result of processing inventory for an AR invoice."""

    success: bool
    transaction_ids: list[UUID]
    cogs_journal_lines: list[JournalLineInput]
    total_cogs: Decimal
    errors: list[str]


class ARInventoryIntegration:
    """
    Service for AR → Inventory integration.

    Handles inventory validation, issue transactions, and COGS calculation
    when AR invoices containing inventory items are posted.
    """

    @staticmethod
    def validate_inventory_availability(
        db: Session,
        organization_id: UUID,
        lines: list[InvoiceLine],
    ) -> tuple[bool, list[str]]:
        """
        Validate that sufficient inventory is available for all invoice lines.

        This is the "Block posting if insufficient" strategy - strict inventory
        control that prevents negative inventory.

        Args:
            db: Database session
            organization_id: Organization scope
            lines: Invoice lines to validate

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        org_id = coerce_uuid(organization_id)
        errors: list[str] = []

        for line in lines:
            # Skip non-inventory lines
            if not line.item_id:
                continue

            # Get the item
            item = db.get(Item, line.item_id)
            if not item or item.organization_id != org_id:
                errors.append(f"Line {line.line_number}: Item not found")
                continue

            # Skip non-tracked items
            if not item.track_inventory:
                continue

            # Get warehouse - required for inventory items
            if not line.warehouse_id:
                errors.append(
                    f"Line {line.line_number}: Warehouse required for inventory item '{item.item_code}'"
                )
                continue

            # Check available quantity
            available_qty = InventoryTransactionService.get_current_balance(
                db, org_id, item.item_id, line.warehouse_id
            )

            if available_qty < line.quantity:
                errors.append(
                    f"Line {line.line_number}: Insufficient inventory for '{item.item_code}' "
                    f"(available: {available_qty}, required: {line.quantity})"
                )

        return (len(errors) == 0, errors)

    @staticmethod
    def get_item_cost(
        db: Session,
        organization_id: UUID,
        item: Item,
        quantity: Decimal,
        warehouse_id: UUID,
        lot_id: Optional[UUID] = None,
    ) -> CostingResult:
        """
        Get the cost for an inventory item based on its costing method.

        Args:
            db: Database session
            organization_id: Organization scope
            item: The inventory item
            quantity: Quantity being issued
            warehouse_id: Warehouse to issue from
            lot_id: Optional specific lot (required for lot-tracked items)

        Returns:
            CostingResult with unit cost and total cost
        """
        from app.models.ifrs.inv.inventory_lot import InventoryLot

        org_id = coerce_uuid(organization_id)

        # Determine cost based on costing method
        if item.costing_method == CostingMethod.STANDARD_COST:
            unit_cost = item.standard_cost or Decimal("0")
            total_cost = quantity * unit_cost
            return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=lot_id)

        elif item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
            unit_cost = item.average_cost or Decimal("0")
            total_cost = quantity * unit_cost
            return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=lot_id)

        elif item.costing_method == CostingMethod.FIFO:
            # For FIFO, we need to calculate from lots
            lots = db.query(InventoryLot).filter(
                InventoryLot.item_id == item.item_id,
                InventoryLot.quantity_on_hand > 0,
                InventoryLot.is_active == True,
                InventoryLot.is_quarantined == False,
            ).order_by(InventoryLot.received_date.asc()).all()

            remaining = quantity
            total_cost = Decimal("0")
            first_lot_id = None

            for lot in lots:
                if remaining <= 0:
                    break

                consume_qty = min(lot.quantity_on_hand, remaining)
                total_cost += consume_qty * lot.unit_cost
                remaining -= consume_qty

                if first_lot_id is None:
                    first_lot_id = lot.lot_id

            if remaining > 0:
                # Not enough inventory - use last known cost for remainder
                last_cost = lots[-1].unit_cost if lots else (item.last_purchase_cost or Decimal("0"))
                total_cost += remaining * last_cost

            unit_cost = (total_cost / quantity).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=first_lot_id)

        elif item.costing_method == CostingMethod.SPECIFIC_IDENTIFICATION:
            # Must have specific lot
            if lot_id:
                lot = db.get(InventoryLot, lot_id)
                if lot:
                    unit_cost = lot.unit_cost
                    total_cost = quantity * unit_cost
                    return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=lot_id)

            # Fallback to average cost
            unit_cost = item.average_cost or Decimal("0")
            total_cost = quantity * unit_cost
            return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=lot_id)

        else:
            # Default to average cost
            unit_cost = item.average_cost or item.last_purchase_cost or Decimal("0")
            total_cost = quantity * unit_cost
            return CostingResult(unit_cost=unit_cost, total_cost=total_cost, lot_id=lot_id)

    @staticmethod
    def process_invoice_inventory(
        db: Session,
        organization_id: UUID,
        invoice: Invoice,
        lines: list[InvoiceLine],
        fiscal_period_id: UUID,
        user_id: UUID,
        is_credit_note: bool = False,
    ) -> InventoryPostingResult:
        """
        Process inventory for an AR invoice posting.

        For standard invoices: Creates SALE inventory transactions and COGS entries
        For credit notes: Creates RETURN inventory transactions and reverses COGS

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The AR invoice being posted
            lines: Invoice lines to process
            fiscal_period_id: Fiscal period for transactions
            user_id: User posting the invoice
            is_credit_note: Whether this is a credit note

        Returns:
            InventoryPostingResult with transaction IDs, COGS lines, and any errors
        """
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)
        fp_id = coerce_uuid(fiscal_period_id)

        transaction_ids: list[UUID] = []
        cogs_journal_lines: list[JournalLineInput] = []
        total_cogs = Decimal("0")
        errors: list[str] = []

        for line in lines:
            # Skip non-inventory lines
            if not line.item_id:
                continue

            # Get the item
            item = db.get(Item, line.item_id)
            if not item or item.organization_id != org_id:
                errors.append(f"Line {line.line_number}: Item not found")
                continue

            # Skip non-tracked items
            if not item.track_inventory:
                continue

            # Get warehouse
            warehouse_id = line.warehouse_id
            if not warehouse_id:
                errors.append(f"Line {line.line_number}: No warehouse specified")
                continue

            # Get cost for this item
            cost_result = ARInventoryIntegration.get_item_cost(
                db=db,
                organization_id=org_id,
                item=item,
                quantity=line.quantity,
                warehouse_id=warehouse_id,
                lot_id=line.lot_id,
            )

            # Determine transaction type
            if is_credit_note:
                txn_type = TransactionType.RETURN
            else:
                txn_type = TransactionType.SALE

            # Create transaction datetime
            transaction_datetime = datetime.combine(
                invoice.invoice_date,
                datetime.min.time(),
                tzinfo=tz.utc,
            )

            try:
                # Create inventory transaction
                txn_input = TransactionInput(
                    transaction_type=txn_type,
                    transaction_date=transaction_datetime,
                    fiscal_period_id=fp_id,
                    item_id=item.item_id,
                    warehouse_id=warehouse_id,
                    quantity=line.quantity,
                    unit_cost=cost_result.unit_cost,
                    uom=item.base_uom,
                    currency_code=item.currency_code,
                    lot_id=cost_result.lot_id or line.lot_id,
                    source_document_type="AR_INVOICE",
                    source_document_id=invoice.invoice_id,
                    source_document_line_id=line.line_id,
                    reference=invoice.invoice_number,
                )

                if is_credit_note:
                    # Return increases inventory
                    transaction = InventoryTransactionService.create_receipt(
                        db=db,
                        organization_id=org_id,
                        input=txn_input,
                        created_by_user_id=uid,
                    )
                else:
                    # Sale decreases inventory
                    transaction = InventoryTransactionService.create_issue(
                        db=db,
                        organization_id=org_id,
                        input=txn_input,
                        created_by_user_id=uid,
                    )

                transaction_ids.append(transaction.transaction_id)

                # Update line with transaction ID for traceability
                line.inventory_transaction_id = transaction.transaction_id

                # Get accounts for COGS
                cogs_account_id = item.cogs_account_id
                inventory_account_id = item.inventory_account_id

                # Fall back to category accounts
                if (not cogs_account_id or not inventory_account_id) and item.category_id:
                    category = db.get(ItemCategory, item.category_id)
                    if category:
                        if not cogs_account_id:
                            cogs_account_id = category.cogs_account_id
                        if not inventory_account_id:
                            inventory_account_id = category.inventory_account_id

                if cogs_account_id and inventory_account_id:
                    # Create COGS journal lines
                    exchange_rate = invoice.exchange_rate or Decimal("1.0")
                    functional_cogs = cost_result.total_cost * exchange_rate

                    if is_credit_note:
                        # Credit note: reverse COGS
                        # Dr Inventory, Cr COGS
                        cogs_journal_lines.append(
                            JournalLineInput(
                                account_id=inventory_account_id,
                                debit_amount=cost_result.total_cost,
                                credit_amount=Decimal("0"),
                                debit_amount_functional=functional_cogs,
                                credit_amount_functional=Decimal("0"),
                                description=f"Inventory return: {item.item_code}",
                                cost_center_id=line.cost_center_id,
                                project_id=line.project_id,
                            )
                        )
                        cogs_journal_lines.append(
                            JournalLineInput(
                                account_id=cogs_account_id,
                                debit_amount=Decimal("0"),
                                credit_amount=cost_result.total_cost,
                                debit_amount_functional=Decimal("0"),
                                credit_amount_functional=functional_cogs,
                                description=f"COGS reversal: {item.item_code}",
                                cost_center_id=line.cost_center_id,
                                project_id=line.project_id,
                            )
                        )
                        total_cogs -= cost_result.total_cost
                    else:
                        # Standard invoice: record COGS
                        # Dr COGS, Cr Inventory
                        cogs_journal_lines.append(
                            JournalLineInput(
                                account_id=cogs_account_id,
                                debit_amount=cost_result.total_cost,
                                credit_amount=Decimal("0"),
                                debit_amount_functional=functional_cogs,
                                credit_amount_functional=Decimal("0"),
                                description=f"Cost of goods sold: {item.item_code}",
                                cost_center_id=line.cost_center_id,
                                project_id=line.project_id,
                            )
                        )
                        cogs_journal_lines.append(
                            JournalLineInput(
                                account_id=inventory_account_id,
                                debit_amount=Decimal("0"),
                                credit_amount=cost_result.total_cost,
                                debit_amount_functional=Decimal("0"),
                                credit_amount_functional=functional_cogs,
                                description=f"Inventory issued: {item.item_code}",
                                cost_center_id=line.cost_center_id,
                                project_id=line.project_id,
                            )
                        )
                        total_cogs += cost_result.total_cost

            except HTTPException as e:
                errors.append(f"Line {line.line_number}: {e.detail}")
            except Exception as e:
                errors.append(f"Line {line.line_number}: Inventory transaction failed - {str(e)}")

        # Determine success
        success = len(errors) == 0

        return InventoryPostingResult(
            success=success,
            transaction_ids=transaction_ids,
            cogs_journal_lines=cogs_journal_lines,
            total_cogs=total_cogs,
            errors=errors,
        )


# Module-level singleton instance
ar_inventory_integration = ARInventoryIntegration()
