"""Financial actions selected on a new fleet maintenance record."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.models.expense import ExpenseCategory
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoiceType
from app.models.fleet.maintenance import MaintenanceRecord
from app.services.finance.ap.supplier_invoice import (
    InvoiceLineInput,
    SupplierInvoiceInput,
    SupplierInvoiceService,
)


if TYPE_CHECKING:
    from app.web.deps import WebAuthContext


def create_draft_invoice(
    db: Session,
    org_id: UUID,
    record: MaintenanceRecord,
    form: FormData,
    auth: WebAuthContext,
    actor_id: UUID | None,
) -> None:
    supplier = None
    if record.supplier_id:
        supplier = db.scalar(
            select(Supplier).where(
                Supplier.organization_id == org_id,
                Supplier.supplier_id == record.supplier_id,
                Supplier.is_active.is_(True),
            )
        )
        if supplier is None:
            raise ValueError("Select an active supplier.")
    if form.get("create_draft_invoice") != "1":
        return
    if not auth.has_permission("ap:invoices:create") or actor_id is None:
        raise ValueError("You do not have permission to create supplier invoices.")
    if supplier is None or record.estimated_cost is None or record.estimated_cost <= 0:
        raise ValueError(
            "A supplier and positive estimated cost are required for the draft invoice."
        )
    vehicle = record.vehicle
    invoice = SupplierInvoiceService.create_invoice(
        db,
        org_id,
        SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=record.scheduled_date,
            due_date=record.scheduled_date,
            received_date=record.scheduled_date,
            currency_code=supplier.currency_code,
            vehicle_id=record.vehicle_id,
            purpose=record.notes,
            lines=[
                InvoiceLineInput(
                    description="Car maintenance",
                    quantity=Decimal("1"),
                    unit_price=record.estimated_cost,
                    cost_center_id=vehicle.assigned_cost_center_id,
                )
            ],
        ),
        actor_id,
    )
    record.invoice_id = invoice.invoice_id


def reimbursement_form_options(db: Session, org_id: UUID) -> dict:
    from app.services.expense import ExpenseService

    return {
        "expense_categories": ExpenseService(db)
        .list_categories(org_id, is_active=True)
        .items,
    }


async def submit_reimbursement(
    db: Session,
    org_id: UUID,
    record: MaintenanceRecord,
    form: FormData,
    employee_id: UUID,
    actor_id: UUID | None,
    uploads: list[UploadFile],
) -> None:
    """Create an employee claim and use the ordinary approval workflow."""
    from app.services.expense import ExpenseService

    try:
        expense_date = date.fromisoformat(str(form.get("claim_date", "")))
        items = [
            {
                "expense_date": expense_date,
                "category_id": UUID(str(category)),
                "description": str(description).strip(),
                "claimed_amount": Decimal(str(amount)),
            }
            for category, description, amount in zip(
                form.getlist("claim_category"),
                form.getlist("claim_description"),
                form.getlist("claim_amount"),
                strict=True,
            )
        ]
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("Enter a valid expense date, category and amount.") from exc

    if expense_date > date.today():
        raise ValueError("The reimbursement date must be today or earlier.")
    if not items or any(
        not item["description"]
        or not item["claimed_amount"].is_finite()
        or item["claimed_amount"] <= 0
        for item in items
    ):
        raise ValueError(
            "Enter a description and positive amount for every expense item."
        )
    category_ids = {item["category_id"] for item in items}
    active_categories = set(
        db.scalars(
            select(ExpenseCategory.category_id).where(
                ExpenseCategory.organization_id == org_id,
                ExpenseCategory.category_id.in_(category_ids),
                ExpenseCategory.is_active.is_(True),
            )
        )
    )
    if active_categories != category_ids:
        raise ValueError("Select an active category for every expense item.")

    payout = {
        field: str(form.get(field, "")).strip()
        for field in (
            "recipient_name",
            "recipient_bank_name",
            "recipient_bank_code",
            "recipient_account_number",
        )
    }
    if not all(payout.values()):
        raise ValueError("Complete the reimbursement bank details.")

    vehicle = record.vehicle
    service = ExpenseService(db)
    receipt_url = None
    if form.get("use_attachments_as_receipts") == "1" and uploads:
        from app.services.expense.receipt_files import stage_receipt

        for upload in uploads:
            await upload.seek(0)
            receipt = stage_receipt(
                db,
                org_id,
                file_data=await upload.read(),
                content_type=upload.content_type,
                original_filename=upload.filename,
            )
            receipt_url = service.append_receipt_url(receipt_url, str(receipt.id))

    for item in items:
        item["cost_center_id"] = vehicle.assigned_cost_center_id
        item["receipt_url"] = receipt_url

    claim = service.create_claim(
        org_id,
        employee_id=employee_id,
        claim_date=expense_date,
        purpose=(record.description or "Car maintenance")[:500],
        vehicle_id=record.vehicle_id,
        cost_center_id=vehicle.assigned_cost_center_id,
        notes=f"Fleet maintenance {record.maintenance_id}",
        items=items,
        created_by_id=actor_id,
        **payout,
    )
    result = service.submit_claim(
        org_id, claim.claim_id, actor_id=actor_id, defer_approver_email=True
    )
    if not result.success:
        raise ValueError(
            result.warning_message or "The expense could not be submitted for approval."
        )
