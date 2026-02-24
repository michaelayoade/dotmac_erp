"""
Purchase Invoice Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Purchase Invoices → ap.supplier_invoice + ap.supplier_invoice_line.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine

from ..mappings.purchase_invoice import (
    PurchaseInvoiceItemMapping,
    PurchaseInvoiceMapping,
)
from .base import BaseSyncService

logger = logging.getLogger(__name__)

# Map ERPNext status/docstatus → DotMac SupplierInvoiceStatus
_STATUS_MAP: dict[str, SupplierInvoiceStatus] = {
    "Draft": SupplierInvoiceStatus.DRAFT,
    "Unpaid": SupplierInvoiceStatus.APPROVED,
    "Overdue": SupplierInvoiceStatus.APPROVED,
    "Partly Paid": SupplierInvoiceStatus.PARTIALLY_PAID,
    "Paid": SupplierInvoiceStatus.PAID,
    "Return": SupplierInvoiceStatus.VOID,
    "Debit Note Issued": SupplierInvoiceStatus.APPROVED,
    "Cancelled": SupplierInvoiceStatus.VOID,
}


class PurchaseInvoiceSyncService(BaseSyncService[SupplierInvoice]):
    """Sync Purchase Invoices from ERPNext."""

    source_doctype = "Purchase Invoice"
    target_table = "ap.supplier_invoice"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        super().__init__(db, organization_id, user_id)
        self._mapping = PurchaseInvoiceMapping()
        self._item_mapping = PurchaseInvoiceItemMapping()
        self._invoice_cache: dict[str, SupplierInvoice] = {}
        self._supplier_exact_lookup: dict[str, uuid.UUID] | None = None
        self._supplier_norm_lookup: dict[str, uuid.UUID] | None = None
        # Injected by orchestrator
        self.ap_control_account_id: uuid.UUID | None = None

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Purchase Invoices with child items."""
        if since:
            for inv in client.get_modified_since(
                doctype="Purchase Invoice",
                since=since,
            ):
                # Incremental list calls may omit required fields; fetch full doc.
                try:
                    full_doc = client.get_document("Purchase Invoice", inv["name"])
                    # Preserve modified from list payload for update comparisons.
                    if inv.get("modified"):
                        full_doc["modified"] = inv["modified"]
                    yield full_doc
                except Exception:
                    inv["items"] = client.list_documents(
                        doctype="Purchase Invoice Item",
                        filters={"parent": inv["name"]},
                        parent="Purchase Invoice",
                    )
                    yield inv
        else:
            for inv in client.get_purchase_invoices():
                try:
                    full_doc = client.get_document("Purchase Invoice", inv["name"])
                    inv["items"] = full_doc.get("items", [])
                except Exception:
                    inv["items"] = []
                yield inv

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        result["_items"] = [
            self._item_mapping.transform_record(item)
            for item in record.get("items", [])
        ]
        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
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
        return self._resolve_entity_id(account_source_name, "Account")

    @staticmethod
    def _normalize_party_name(value: str | None) -> str:
        if not value:
            return ""
        norm = value.strip().lower()
        norm = norm.replace("&", "and")
        norm = re.sub(r"\s+", " ", norm)
        norm = re.sub(r"\s*-\s*\d+$", "", norm)
        norm = re.sub(r"\s+\d+\)$", ")", norm)
        return norm

    def _ensure_supplier_lookups(self) -> None:
        if (
            self._supplier_exact_lookup is not None
            and self._supplier_norm_lookup is not None
        ):
            return

        rows = self.db.execute(
            select(
                Supplier.supplier_id,
                Supplier.supplier_code,
                Supplier.legal_name,
                Supplier.trading_name,
                Supplier.erpnext_id,
            ).where(Supplier.organization_id == self.organization_id)
        ).all()

        exact: dict[str, uuid.UUID] = {}
        norm_multi: dict[str, set[uuid.UUID]] = {}

        for row in rows:
            supplier_id = row.supplier_id
            for key in (row.erpnext_id, row.supplier_code):
                if key and key not in exact:
                    exact[str(key)] = supplier_id

            for key in (
                row.erpnext_id,
                row.supplier_code,
                row.legal_name,
                row.trading_name,
            ):
                nk = self._normalize_party_name(key)
                if not nk:
                    continue
                norm_multi.setdefault(nk, set()).add(supplier_id)

        norm_unique = {k: next(iter(v)) for k, v in norm_multi.items() if len(v) == 1}
        self._supplier_exact_lookup = exact
        self._supplier_norm_lookup = norm_unique

    def _resolve_supplier_id(self, source_name: str | None) -> uuid.UUID | None:
        if not source_name:
            return None

        candidate = self._resolve_entity_id(source_name, "Supplier")
        if candidate and self.db.get(Supplier, candidate):
            return candidate

        self._ensure_supplier_lookups()
        assert self._supplier_exact_lookup is not None  # noqa: S101  # nosec B101
        assert self._supplier_norm_lookup is not None  # noqa: S101  # nosec B101

        if source_name in self._supplier_exact_lookup:
            return self._supplier_exact_lookup[source_name]

        source_norm = self._normalize_party_name(source_name)
        if source_norm in self._supplier_norm_lookup:
            return self._supplier_norm_lookup[source_norm]

        prefix_candidates: set[uuid.UUID] = set()
        for key, supplier_id in self._supplier_norm_lookup.items():
            if not source_norm:
                break
            if key.startswith(source_norm) or source_norm.startswith(key):
                if abs(len(key) - len(source_norm)) <= 3:
                    prefix_candidates.add(supplier_id)
        if len(prefix_candidates) == 1:
            return next(iter(prefix_candidates))

        supplier = self.db.scalar(
            select(Supplier).where(
                Supplier.organization_id == self.organization_id,
                Supplier.erpnext_id == source_name,
            )
        )
        if supplier:
            return supplier.supplier_id

        supplier = self.db.scalar(
            select(Supplier).where(
                Supplier.organization_id == self.organization_id,
                Supplier.supplier_code == source_name,
            )
        )
        if supplier:
            return supplier.supplier_id

        return None

    def _map_status(self, data: dict[str, Any]) -> SupplierInvoiceStatus:
        docstatus = data.get("_docstatus", 0)
        erpnext_status = data.get("_erpnext_status", "")

        if docstatus == 2:
            return SupplierInvoiceStatus.VOID
        if docstatus == 0:
            return SupplierInvoiceStatus.DRAFT

        return _STATUS_MAP.get(erpnext_status, SupplierInvoiceStatus.APPROVED)

    def _map_invoice_type(self, data: dict[str, Any]) -> SupplierInvoiceType:
        is_return = data.get("_is_return", 0)
        if is_return:
            return SupplierInvoiceType.DEBIT_NOTE
        return SupplierInvoiceType.STANDARD

    def _generate_invoice_number(self, reference_date=None) -> str:
        """Generate AP invoice number.

        Uses a separate sequence or falls back to INVOICE type.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        # AP invoices may use EXPENSE_INVOICE sequence or a dedicated AP one.
        # Fall back to generic invoice numbering with AP prefix.
        try:
            return svc.generate_next_number(
                self.organization_id, SequenceType.EXPENSE_INVOICE, reference_date
            )
        except Exception:
            # Sequence not configured — generate manually
            import time

            return f"PINV-{int(time.time())}"

    @staticmethod
    def _calculate_line_taxes(
        items_data: list[dict[str, Any]],
        total_tax_amount: Decimal,
    ) -> list[Decimal]:
        """Allocate invoice-level tax to lines, preserving explicit line taxes."""
        if not items_data:
            return []

        total_tax = Decimal(total_tax_amount or 0)
        explicit_taxes = [Decimal(item.get("tax_amount") or 0) for item in items_data]
        line_amounts = [Decimal(item.get("line_amount") or 0) for item in items_data]

        remaining = total_tax - sum(explicit_taxes, Decimal("0"))
        allocated = [tax for tax in explicit_taxes]
        total_line_amount = sum(line_amounts, Decimal("0"))

        if remaining != 0:
            if total_line_amount > 0:
                distributed = Decimal("0")
                for idx, line_amount in enumerate(line_amounts):
                    if idx == len(line_amounts) - 1:
                        share = remaining - distributed
                    else:
                        share = (remaining * line_amount / total_line_amount).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        distributed += share
                    allocated[idx] += share
            else:
                allocated[-1] += remaining

        return [
            amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for amount in allocated
        ]

    def _create_invoice_lines(
        self, invoice_id: uuid.UUID, items_data: list[dict[str, Any]]
    ) -> None:
        for seq, item_data in enumerate(items_data, 1):
            item_data.pop("_source_name", None)
            item_data.pop("_source_modified", None)
            item_source = item_data.pop("_item_source_name", None)
            item_name = item_data.pop("_item_name", None)
            expense_acct = item_data.pop("_expense_account_source_name", None)
            item_data.pop("_cost_center_source_name", None)
            item_data.pop("_project_source_name", None)

            item_id = self._resolve_entity_id(item_source, "Item")
            expense_account_id = self._resolve_account_id(expense_acct)

            description = (
                item_data.get("description") or item_name or item_source or "Item"
            )

            line = SupplierInvoiceLine(
                invoice_id=invoice_id,
                line_number=seq,
                item_id=item_id,
                description=str(description)[:1000],
                quantity=item_data.get("quantity", Decimal("1")),
                unit_price=item_data.get("unit_price", Decimal("0")),
                line_amount=item_data.get("line_amount", Decimal("0")),
                tax_amount=Decimal("0"),
                expense_account_id=expense_account_id,
            )
            self.db.add(line)

    def create_entity(self, data: dict[str, Any]) -> SupplierInvoice:
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        supplier_source = data.pop("_supplier_source_name", None)
        supplier_display = data.pop("_supplier_display_name", None)
        items_data = data.pop("_items", [])
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        data.pop("_bill_date", None)

        # Resolve supplier
        supplier_id = self._resolve_supplier_id(supplier_source)
        if not supplier_id and supplier_display:
            supplier_id = self._resolve_supplier_id(supplier_display)
        if not supplier_id:
            raise ValueError(
                f"Supplier '{supplier_source or supplier_display}' not found in sync_entity"
            )

        # Map status and type
        status = self._map_status(data)
        invoice_type = self._map_invoice_type(data)
        data.pop("_docstatus", None)
        data.pop("_erpnext_status", None)
        data.pop("_is_return", None)

        invoice_number = self._generate_invoice_number(data.get("invoice_date"))

        functional_amount = data.get("functional_currency_amount")
        if not functional_amount:
            exchange_rate = data.get("exchange_rate", Decimal("1"))
            total = data.get("total_amount", Decimal("0"))
            functional_amount = total * (exchange_rate or Decimal("1"))

        invoice = SupplierInvoice(
            organization_id=self.organization_id,
            invoice_number=invoice_number[:30],
            supplier_invoice_number=data.get("supplier_invoice_number"),
            invoice_type=invoice_type,
            status=status,
            invoice_date=data["invoice_date"],
            received_date=data["invoice_date"],
            due_date=data.get("due_date") or data["invoice_date"],
            supplier_id=supplier_id,
            currency_code=data.get("currency_code", "NGN")[:3],
            subtotal=data.get("subtotal", Decimal("0")),
            tax_amount=data.get("tax_amount", Decimal("0")),
            total_amount=data.get("total_amount", Decimal("0")),
            amount_paid=data.get("total_amount", Decimal("0"))
            - data.get("outstanding_amount", Decimal("0")),
            functional_currency_amount=functional_amount,
            exchange_rate=data.get("exchange_rate", Decimal("1")),
            ap_control_account_id=self.ap_control_account_id,
            created_by_user_id=self.user_id,
        )
        data.pop("outstanding_amount", None)

        self.db.add(invoice)
        self.db.flush()

        if items_data:
            self._create_invoice_lines(invoice.invoice_id, items_data)
        else:
            self._create_invoice_lines(
                invoice.invoice_id,
                [
                    {
                        "description": "Purchase Invoice",
                        "quantity": Decimal("1"),
                        "unit_price": data.get("total_amount", Decimal("0")),
                        "line_amount": data.get("total_amount", Decimal("0")),
                    }
                ],
            )

        return invoice

    def update_entity(
        self, entity: SupplierInvoice, data: dict[str, Any]
    ) -> SupplierInvoice:
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_supplier_source_name", None)
        data.pop("_supplier_display_name", None)
        data.pop("_items", [])
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        data.pop("_bill_date", None)
        data.pop("_is_return", None)

        entity.status = self._map_status(data)
        data.pop("_docstatus", None)
        data.pop("_erpnext_status", None)

        entity.total_amount = data.get("total_amount", entity.total_amount)
        entity.subtotal = data.get("subtotal", entity.subtotal)
        entity.tax_amount = data.get("tax_amount", entity.tax_amount)
        outstanding = data.get("outstanding_amount", Decimal("0"))
        entity.amount_paid = entity.total_amount - outstanding

        return entity

    def get_entity_id(self, entity: SupplierInvoice) -> uuid.UUID:
        return entity.invoice_id

    def find_existing_entity(self, source_name: str) -> SupplierInvoice | None:
        if source_name in self._invoice_cache:
            return self._invoice_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            invoice = self.db.get(SupplierInvoice, sync_entity.target_id)
            if invoice:
                self._invoice_cache[source_name] = invoice
                return invoice

        return None
