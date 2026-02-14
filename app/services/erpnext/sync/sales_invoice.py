"""
Sales Invoice Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Sales Invoices → ar.invoice + ar.invoice_line.
Handles dedup against Splynx-originated invoices via splynx_id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine

from ..mappings.sales_invoice import SalesInvoiceItemMapping, SalesInvoiceMapping
from .base import BaseSyncService

logger = logging.getLogger(__name__)

# Map ERPNext status/docstatus → DotMac InvoiceStatus
_STATUS_MAP: dict[str, InvoiceStatus] = {
    "Draft": InvoiceStatus.DRAFT,
    "Unpaid": InvoiceStatus.APPROVED,
    "Overdue": InvoiceStatus.OVERDUE,
    "Partly Paid": InvoiceStatus.PARTIALLY_PAID,
    "Paid": InvoiceStatus.PAID,
    "Return": InvoiceStatus.VOID,
    "Credit Note Issued": InvoiceStatus.VOID,
    "Cancelled": InvoiceStatus.VOID,
}


class SalesInvoiceSyncService(BaseSyncService[Invoice]):
    """Sync Sales Invoices from ERPNext with Splynx dedup."""

    source_doctype = "Sales Invoice"
    target_table = "ar.invoice"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        super().__init__(db, organization_id, user_id)
        self._mapping = SalesInvoiceMapping()
        self._item_mapping = SalesInvoiceItemMapping()
        self._invoice_cache: dict[str, Invoice] = {}
        # Injected by orchestrator
        self.ar_control_account_id: uuid.UUID | None = None

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Sales Invoices with child items."""
        if since:
            for inv in client.get_modified_since(
                doctype="Sales Invoice",
                since=since,
            ):
                # Fetch full doc for child tables
                try:
                    full_doc = client.get_document("Sales Invoice", inv["name"])
                    inv["items"] = full_doc.get("items", [])
                    inv["custom_splynx_id"] = full_doc.get("custom_splynx_id")
                except Exception:
                    inv["items"] = []
                yield inv
        else:
            # client.get_sales_invoices() already fetches full doc per invoice
            yield from client.get_sales_invoices()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        # Transform items
        result["_items"] = [
            self._item_mapping.transform_record(item)
            for item in record.get("items", [])
        ]
        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        """Resolve an ERPNext document name to a DotMac UUID via sync_entity."""
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _resolve_account_id(self, account_source_name: str | None) -> uuid.UUID | None:
        """Resolve an ERPNext account name to gl.account UUID."""
        return self._resolve_entity_id(account_source_name, "Account")

    def _map_status(self, data: dict[str, Any]) -> InvoiceStatus:
        """Map ERPNext docstatus + status to DotMac InvoiceStatus."""
        docstatus = data.get("_docstatus", 0)
        erpnext_status = data.get("_erpnext_status", "")

        if docstatus == 2:
            return InvoiceStatus.VOID
        if docstatus == 0:
            return InvoiceStatus.DRAFT

        return _STATUS_MAP.get(erpnext_status, InvoiceStatus.APPROVED)

    def _map_invoice_type(self, data: dict[str, Any]) -> InvoiceType:
        """Determine invoice type from ERPNext data."""
        is_return = data.get("_is_return", 0)
        if is_return:
            return InvoiceType.CREDIT_NOTE
        return InvoiceType.STANDARD

    def _check_splynx_dedup(self, data: dict[str, Any]) -> Invoice | None:
        """Check if a Splynx-originated invoice already exists."""
        splynx_id = data.get("_splynx_id")
        if not splynx_id:
            return None

        return self.db.scalar(
            select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.splynx_id == str(splynx_id),
            )
        )

    def _generate_invoice_number(self, reference_date=None) -> str:
        """Generate sequential invoice number via numbering service."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.INVOICE, reference_date
        )

    def _generate_credit_note_number(self, reference_date=None) -> str:
        """Generate sequential credit note number."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.CREDIT_NOTE, reference_date
        )

    def _create_invoice_lines(
        self, invoice_id: uuid.UUID, items_data: list[dict[str, Any]]
    ) -> None:
        """Create InvoiceLine records from transformed item data."""
        for seq, item_data in enumerate(items_data, 1):
            item_data.pop("_source_name", None)
            item_data.pop("_source_modified", None)
            item_source = item_data.pop("_item_source_name", None)
            item_name = item_data.pop("_item_name", None)
            income_acct = item_data.pop("_income_account_source_name", None)
            item_data.pop("_cost_center_source_name", None)
            item_data.pop("_project_source_name", None)

            # Resolve item FK (optional)
            item_id = self._resolve_entity_id(item_source, "Item")

            # Resolve revenue account (required)
            revenue_account_id = self._resolve_account_id(income_acct)
            if not revenue_account_id and self.ar_control_account_id:
                revenue_account_id = self.ar_control_account_id

            description = (
                item_data.get("description") or item_name or item_source or "Item"
            )

            line = InvoiceLine(
                invoice_id=invoice_id,
                line_number=seq,
                item_id=item_id,
                description=str(description)[:1000],
                quantity=item_data.get("quantity", Decimal("1")),
                unit_price=item_data.get("unit_price", Decimal("0")),
                discount_percentage=item_data.get("discount_percentage", Decimal("0")),
                discount_amount=item_data.get("discount_amount", Decimal("0")),
                line_amount=item_data.get("line_amount", Decimal("0")),
                tax_amount=Decimal("0"),
                revenue_account_id=revenue_account_id,
            )
            self.db.add(line)

    def create_entity(self, data: dict[str, Any]) -> Invoice:
        # Pop internal fields
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        customer_source = data.pop("_customer_source_name", None)
        items_data = data.pop("_items", [])
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        splynx_id = data.pop("_splynx_id", None)
        data.pop("_return_against", None)

        # Check Splynx dedup first
        existing_splynx = self._check_splynx_dedup({"_splynx_id": splynx_id})
        if existing_splynx:
            # Stamp erpnext_id on existing Splynx record, skip creation
            logger.info(
                "Splynx dedup: invoice %s already exists as %s",
                data.get("_source_name"),
                existing_splynx.invoice_number,
            )
            return existing_splynx

        # Resolve customer
        customer_id = self._resolve_entity_id(customer_source, "Customer")
        if not customer_id:
            raise ValueError(f"Customer '{customer_source}' not found in sync_entity")

        # Map status and type
        status = self._map_status(data)
        invoice_type = self._map_invoice_type(data)
        data.pop("_docstatus", None)
        data.pop("_erpnext_status", None)
        data.pop("_is_return", None)

        # Generate number
        invoice_date = data.get("invoice_date")
        if invoice_type == InvoiceType.CREDIT_NOTE:
            invoice_number = self._generate_credit_note_number(invoice_date)
        else:
            invoice_number = self._generate_invoice_number(invoice_date)

        # Functional currency fallback
        functional_amount = data.get("functional_currency_amount")
        if not functional_amount:
            exchange_rate = data.get("exchange_rate", Decimal("1"))
            total = data.get("total_amount", Decimal("0"))
            functional_amount = total * (exchange_rate or Decimal("1"))

        invoice = Invoice(
            organization_id=self.organization_id,
            invoice_number=invoice_number[:30],
            invoice_type=invoice_type,
            status=status,
            invoice_date=data["invoice_date"],
            due_date=data.get("due_date") or data["invoice_date"],
            customer_id=customer_id,
            currency_code=data.get("currency_code", "NGN")[:3],
            subtotal=data.get("subtotal", Decimal("0")),
            tax_amount=data.get("tax_amount", Decimal("0")),
            total_amount=data.get("total_amount", Decimal("0")),
            amount_paid=data.get("total_amount", Decimal("0"))
            - data.get("outstanding_amount", Decimal("0")),
            functional_currency_amount=functional_amount,
            exchange_rate=data.get("exchange_rate", Decimal("1")),
            ar_control_account_id=self.ar_control_account_id,
            created_by_user_id=self.user_id,
            erpnext_id=None,  # Set by base class
            last_synced_at=datetime.now(UTC),
        )
        data.pop("outstanding_amount", None)

        self.db.add(invoice)
        self.db.flush()

        # Create line items
        if items_data:
            self._create_invoice_lines(invoice.invoice_id, items_data)
        else:
            # Single summary line
            self._create_invoice_lines(
                invoice.invoice_id,
                [
                    {
                        "description": f"Sales Invoice {data.get('_source_name', '')}",
                        "quantity": Decimal("1"),
                        "unit_price": data.get("total_amount", Decimal("0")),
                        "line_amount": data.get("total_amount", Decimal("0")),
                        "discount_percentage": Decimal("0"),
                        "discount_amount": Decimal("0"),
                    }
                ],
            )

        return invoice

    def update_entity(self, entity: Invoice, data: dict[str, Any]) -> Invoice:
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_customer_source_name", None)
        data.pop("_items", [])
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        data.pop("_splynx_id", None)
        data.pop("_return_against", None)

        # Update status
        entity.status = self._map_status(data)
        data.pop("_docstatus", None)
        data.pop("_erpnext_status", None)
        data.pop("_is_return", None)

        # Update amounts
        entity.total_amount = data.get("total_amount", entity.total_amount)
        entity.subtotal = data.get("subtotal", entity.subtotal)
        entity.tax_amount = data.get("tax_amount", entity.tax_amount)
        outstanding = data.get("outstanding_amount", Decimal("0"))
        entity.amount_paid = entity.total_amount - outstanding
        entity.last_synced_at = datetime.now(UTC)

        return entity

    def get_entity_id(self, entity: Invoice) -> uuid.UUID:
        return entity.invoice_id

    def find_existing_entity(self, source_name: str) -> Invoice | None:
        if source_name in self._invoice_cache:
            return self._invoice_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            invoice = self.db.get(Invoice, sync_entity.target_id)
            if invoice:
                self._invoice_cache[source_name] = invoice
                return invoice

        return None
