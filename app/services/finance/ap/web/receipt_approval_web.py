"""
AP inventory receipt approval web service.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.invoice_inventory_receipt_approval import (
    InvoiceInventoryReceiptApproval,
    InvoiceInventoryReceiptApprovalStatus,
)
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.inventory.item import Item
from app.models.inventory.warehouse import Warehouse
from app.models.person import Person
from app.services.common import ValidationError, coerce_uuid
from app.services.finance.ap.inventory_receipt_approval import (
    ap_inventory_receipt_approval_service,
)
from app.services.finance.ap.web.base import format_date, supplier_display_name
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None or value.strip() == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValidationError("Approved quantity must be a valid number") from exc


def _serials_from_text(value: str | None) -> list[str] | None:
    if not value:
        return None
    serials = [
        part.strip()
        for raw in value.replace(",", "\n").splitlines()
        for part in [raw.strip()]
        if part
    ]
    return serials or None


class ReceiptApprovalWebService:
    """Web responses for store manager AP receipt approvals."""

    def receipt_approvals_context(
        self,
        db: Session,
        organization_id: str,
        *,
        status: str | None = "PENDING",
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        status_value: InvoiceInventoryReceiptApprovalStatus | None = None
        if status and status.upper() != "ALL":
            status_value = InvoiceInventoryReceiptApprovalStatus(status.upper())

        approvals = ap_inventory_receipt_approval_service.list_pending(
            db, org_id, status=status_value
        )
        rows = self._approval_rows(db, org_id, approvals)
        return {
            "approvals": rows,
            "status": status or "PENDING",
            "pending_count": sum(1 for row in rows if row["status"] == "PENDING"),
        }

    def receipt_approval_detail_context(
        self,
        db: Session,
        organization_id: str,
        approval_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        approval = ap_inventory_receipt_approval_service.get(
            db, org_id, coerce_uuid(approval_id)
        )
        row = self._approval_rows(db, org_id, [approval])[0]
        history = list(
            db.scalars(
                select(InvoiceInventoryReceiptApproval)
                .where(
                    InvoiceInventoryReceiptApproval.organization_id == org_id,
                    InvoiceInventoryReceiptApproval.supplier_invoice_line_id
                    == approval.supplier_invoice_line_id,
                )
                .order_by(InvoiceInventoryReceiptApproval.created_at)
            ).all()
        )
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                    Warehouse.is_receiving.is_(True),
                )
                .order_by(Warehouse.warehouse_code)
            ).all()
        )
        row["serial_text"] = "\n".join(row["receipt_serial_numbers"] or [])
        return {
            "approval": row,
            "history": self._approval_rows(db, org_id, history),
            "warehouses": [
                {
                    "warehouse_id": str(warehouse.warehouse_id),
                    "warehouse_code": warehouse.warehouse_code,
                    "warehouse_name": warehouse.warehouse_name,
                }
                for warehouse in warehouses
            ],
        }

    def _approval_rows(
        self,
        db: Session,
        organization_id,
        approvals: list[InvoiceInventoryReceiptApproval],
    ) -> list[dict]:
        org_id = coerce_uuid(organization_id)
        invoice_ids = {approval.supplier_invoice_id for approval in approvals}
        line_ids = {approval.supplier_invoice_line_id for approval in approvals}
        item_ids = {approval.item_id for approval in approvals}
        warehouse_ids = {
            approval.warehouse_id for approval in approvals if approval.warehouse_id
        }
        person_ids = {
            person_id
            for approval in approvals
            for person_id in (
                approval.submitted_by_user_id,
                approval.approved_by_user_id,
                approval.rejected_by_user_id,
            )
            if person_id
        }

        invoices = (
            {
                invoice.invoice_id: invoice
                for invoice in db.scalars(
                    select(SupplierInvoice).where(
                        SupplierInvoice.organization_id == org_id,
                        SupplierInvoice.invoice_id.in_(invoice_ids),
                    )
                ).all()
            }
            if invoice_ids
            else {}
        )
        supplier_ids = {invoice.supplier_id for invoice in invoices.values()}
        suppliers = (
            {
                supplier.supplier_id: supplier
                for supplier in db.scalars(
                    select(Supplier).where(
                        Supplier.organization_id == org_id,
                        Supplier.supplier_id.in_(supplier_ids),
                    )
                ).all()
            }
            if supplier_ids
            else {}
        )
        lines = (
            {
                line.line_id: line
                for line in db.scalars(
                    select(SupplierInvoiceLine).where(
                        SupplierInvoiceLine.line_id.in_(line_ids)
                    )
                ).all()
            }
            if line_ids
            else {}
        )
        items = (
            {
                item.item_id: item
                for item in db.scalars(
                    select(Item).where(
                        Item.organization_id == org_id,
                        Item.item_id.in_(item_ids),
                    )
                ).all()
            }
            if item_ids
            else {}
        )
        warehouses = (
            {
                warehouse.warehouse_id: warehouse
                for warehouse in db.scalars(
                    select(Warehouse).where(
                        Warehouse.organization_id == org_id,
                        Warehouse.warehouse_id.in_(warehouse_ids),
                    )
                ).all()
            }
            if warehouse_ids
            else {}
        )
        people = (
            {
                person.id: person
                for person in db.scalars(
                    select(Person).where(
                        Person.organization_id == org_id,
                        Person.id.in_(person_ids),
                    )
                ).all()
            }
            if person_ids
            else {}
        )

        rows: list[dict] = []
        for approval in approvals:
            invoice = invoices.get(approval.supplier_invoice_id)
            supplier = suppliers.get(invoice.supplier_id) if invoice else None
            line = lines.get(approval.supplier_invoice_line_id)
            item = items.get(approval.item_id)
            warehouse = warehouses.get(approval.warehouse_id)
            submitted_by = people.get(approval.submitted_by_user_id)
            approved_by = people.get(approval.approved_by_user_id)
            rejected_by = people.get(approval.rejected_by_user_id)
            remaining_quantity = None
            if approval.approved_quantity is not None:
                remaining_quantity = (
                    approval.requested_quantity - approval.approved_quantity
                )
            rows.append(
                {
                    "approval_id": str(approval.approval_id),
                    "invoice_id": str(approval.supplier_invoice_id),
                    "invoice_number": invoice.invoice_number if invoice else "",
                    "supplier_name": supplier_display_name(supplier)
                    if supplier
                    else "",
                    "line_number": line.line_number if line else None,
                    "line_description": line.description if line else "",
                    "item_code": item.item_code if item else "",
                    "item_name": item.item_name if item else "",
                    "track_serial_numbers": bool(item.track_serial_numbers)
                    if item
                    else False,
                    "warehouse_id": str(approval.warehouse_id)
                    if approval.warehouse_id
                    else "",
                    "warehouse_name": warehouse.warehouse_name if warehouse else "",
                    "requested_quantity": approval.requested_quantity,
                    "approved_quantity": approval.approved_quantity,
                    "remaining_quantity": remaining_quantity,
                    "receipt_reference": approval.receipt_reference or "",
                    "receipt_serial_numbers": approval.receipt_serial_numbers or [],
                    "serial_count": len(approval.receipt_serial_numbers or []),
                    "status": approval.status.value,
                    "created_at": format_date(approval.created_at),
                    "submitted_by_name": submitted_by.name if submitted_by else "",
                    "approved_at": format_date(approval.approved_at)
                    if approval.approved_at
                    else "",
                    "approved_by_name": approved_by.name if approved_by else "",
                    "rejected_at": format_date(approval.rejected_at)
                    if approval.rejected_at
                    else "",
                    "rejected_by_name": rejected_by.name if rejected_by else "",
                    "rejection_reason": approval.rejection_reason or "",
                    "inventory_transaction_id": str(approval.inventory_transaction_id)
                    if approval.inventory_transaction_id
                    else "",
                }
            )
        return rows

    def receipt_approvals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
    ):
        context = base_context(
            request, auth, "Receipt Approvals", "receipt-approvals", db=db
        )
        context.update(
            self.receipt_approvals_context(
                db, str(auth.organization_id), status=status or "PENDING"
            )
        )
        return templates.TemplateResponse(
            request, "inventory/receipt_approvals.html", context
        )

    def receipt_approval_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        approval_id: str,
    ):
        context = base_context(
            request, auth, "Receipt Approval", "receipt-approvals", db=db
        )
        context.update(
            self.receipt_approval_detail_context(
                db, str(auth.organization_id), approval_id
            )
        )
        return templates.TemplateResponse(
            request, "inventory/receipt_approval_detail.html", context
        )

    async def approve_receipt_approval_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        approval_id: str,
    ) -> RedirectResponse:
        form = await request.form()
        try:
            ap_inventory_receipt_approval_service.approve(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                approval_id=coerce_uuid(approval_id),
                approved_by_user_id=coerce_uuid(auth.user_id),
                approved_quantity=_decimal_or_none(form.get("approved_quantity")),
                warehouse_id=coerce_uuid(form.get("warehouse_id"))
                if form.get("warehouse_id")
                else None,
                serial_numbers=_serials_from_text(form.get("receipt_serial_numbers")),
            )
            db.commit()
            return RedirectResponse(
                url=f"/inventory/receipt-approvals/{approval_id}?success=Receipt+approved",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                url=f"/inventory/receipt-approvals/{approval_id}?error={str(exc)}",
                status_code=303,
            )

    async def reject_receipt_approval_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        approval_id: str,
    ) -> RedirectResponse:
        form = await request.form()
        try:
            ap_inventory_receipt_approval_service.reject(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                approval_id=coerce_uuid(approval_id),
                rejected_by_user_id=coerce_uuid(auth.user_id),
                rejection_reason=str(form.get("rejection_reason") or ""),
            )
            db.commit()
            return RedirectResponse(
                url="/inventory/receipt-approvals?success=Receipt+approval+rejected",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                url=f"/inventory/receipt-approvals/{approval_id}?error={str(exc)}",
                status_code=303,
            )
