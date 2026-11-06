"""Financial actions selected on a new fleet maintenance record."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.models.expense import ExpenseCategory, ExpenseClaim, ExpenseClaimStatus
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.fleet.maintenance import MaintenanceRecord
from app.services.common import ValidationError
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
    if form.get("create_draft_invoice") != "1":
        return
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

    items: list[dict] = []
    category_ids: set[UUID] = set()
    try:
        expense_date = date.fromisoformat(str(form.get("claim_date", "")))
        for category, description, amount in zip(
            form.getlist("claim_category"),
            form.getlist("claim_description"),
            form.getlist("claim_amount"),
            strict=True,
        ):
            category_id = UUID(str(category))
            item_description = str(description).strip()
            claimed_amount = Decimal(str(amount))
            if (
                not item_description
                or not claimed_amount.is_finite()
                or claimed_amount <= 0
            ):
                raise ValueError(
                    "Enter a description and positive amount for every expense item."
                )
            category_ids.add(category_id)
            items.append(
                {
                    "expense_date": expense_date,
                    "category_id": category_id,
                    "description": item_description,
                    "claimed_amount": claimed_amount,
                }
            )
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(
            "Enter a valid expense date, category, description and amount."
        ) from exc

    if expense_date > date.today():
        raise ValueError("The reimbursement date must be today or earlier.")
    if not items:
        raise ValueError("Add at least one expense item.")
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
        recipient_name=payout["recipient_name"],
        recipient_bank_name=payout["recipient_bank_name"],
        recipient_bank_code=payout["recipient_bank_code"],
        recipient_account_number=payout["recipient_account_number"],
    )
    claim.source_system = "fleet_maintenance"
    claim.source_reference = str(record.maintenance_id)
    result = service.submit_claim(
        org_id, claim.claim_id, actor_id=actor_id, defer_approver_email=True
    )
    if not result.success:
        raise ValueError(
            result.warning_message or "The expense could not be submitted for approval."
        )


def finance_handled_maintenance_ids(
    db: Session,
    org_id: UUID,
    records: list[MaintenanceRecord],
) -> set[UUID]:
    """Return maintenance IDs that already created a financial record."""
    record_ids = {record.maintenance_id for record in records}
    handled_ids = {
        record.maintenance_id
        for record in records
        if getattr(record, "invoice_id", None) is not None
    }
    pending_ids = record_ids - handled_ids
    if not pending_ids:
        return handled_ids

    pending_records = [
        record for record in records if record.maintenance_id in pending_ids
    ]
    handled_ids.update(
        expense_claim_ids_by_maintenance(db, org_id, pending_records).keys()
    )
    return handled_ids


def expense_claim_ids_by_maintenance(
    db: Session,
    org_id: UUID,
    records: list[MaintenanceRecord],
) -> dict[UUID, UUID]:
    """Resolve tenant-scoped reimbursement claim IDs for maintenance records."""
    record_ids = {record.maintenance_id for record in records}
    if not record_ids:
        return {}

    references = {str(record_id) for record_id in record_ids}
    legacy_notes = {f"Fleet maintenance {record_id}" for record_id in record_ids}
    rows = db.execute(
        select(
            ExpenseClaim.claim_id,
            ExpenseClaim.source_reference,
            ExpenseClaim.notes,
        ).where(
            ExpenseClaim.organization_id == org_id,
            (
                (
                    (ExpenseClaim.source_system == "fleet_maintenance")
                    & ExpenseClaim.source_reference.in_(references)
                )
                | ExpenseClaim.notes.in_(legacy_notes)
            ),
        )
    ).all()
    claim_ids: dict[UUID, UUID] = {}
    for claim_id, source_reference, notes in rows:
        candidate = source_reference
        if candidate is None and notes and notes.startswith("Fleet maintenance "):
            candidate = notes.removeprefix("Fleet maintenance ")
        if candidate:
            try:
                maintenance_id = UUID(candidate)
            except ValueError:
                continue
            if maintenance_id in record_ids:
                claim_ids.setdefault(maintenance_id, claim_id)
    return claim_ids


def cancel_finance_handling(
    db: Session,
    org_id: UUID,
    record: MaintenanceRecord,
    user_id: UUID | None,
    person_id: UUID | None,
) -> None:
    """Cancel reversible financial records linked to cancelled maintenance.

    Posted or paid supplier invoices and approved or paid reimbursements require
    a Finance-owned reversal, so they block cancellation. All mutations share
    the caller's transaction with the maintenance status change.
    """
    reason = f"Fleet maintenance {record.maintenance_id} was cancelled."

    if record.invoice_id is not None:
        invoice = db.scalar(
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.invoice_id == record.invoice_id,
            )
            .with_for_update()
        )
        if invoice is None:
            raise ValidationError("The linked supplier invoice was not found.")
        if invoice.status not in {
            SupplierInvoiceStatus.VOID,
            SupplierInvoiceStatus.REJECTED,
        }:
            if invoice.status in SupplierInvoiceStatus.gl_impacting():
                raise ValidationError(
                    "This maintenance cannot be cancelled because its supplier "
                    "invoice is posted or paid. Finance must reverse it first."
                )
            invoice_actor_id = user_id or person_id
            if invoice_actor_id is None:
                raise ValidationError(
                    "An authenticated user is required to void the supplier invoice."
                )
            SupplierInvoiceService.void_invoice(
                db,
                org_id,
                invoice.invoice_id,
                invoice_actor_id,
                reason,
            )

    claims = list(
        db.scalars(
            select(ExpenseClaim)
            .where(
                ExpenseClaim.organization_id == org_id,
                (
                    (
                        (ExpenseClaim.source_system == "fleet_maintenance")
                        & (ExpenseClaim.source_reference == str(record.maintenance_id))
                    )
                    | (
                        ExpenseClaim.notes
                        == f"Fleet maintenance {record.maintenance_id}"
                    )
                ),
            )
            .with_for_update()
        ).all()
    )
    cancellable_claim_statuses = {
        ExpenseClaimStatus.DRAFT,
        ExpenseClaimStatus.SUBMITTED,
        ExpenseClaimStatus.PENDING_APPROVAL,
    }
    safe_terminal_claim_statuses = {
        ExpenseClaimStatus.CANCELLED,
        ExpenseClaimStatus.REJECTED,
        ExpenseClaimStatus.APPROVAL_WITHDRAWN,
        ExpenseClaimStatus.EXPIRED,
    }
    for claim in claims:
        if claim.status in cancellable_claim_statuses:
            from app.services.expense import ExpenseService

            claim_actor_id = person_id or user_id
            ExpenseService(db).cancel_claim(
                org_id,
                claim.claim_id,
                reason=reason,
                actor_id=claim_actor_id,
            )
        elif claim.status not in safe_terminal_claim_statuses:
            raise ValidationError(
                "This maintenance cannot be cancelled because its employee "
                "reimbursement is approved or paid. Finance must reverse it first."
            )
