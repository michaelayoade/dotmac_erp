"""
AP Invoice Posting Saga - Saga pattern implementation for AP posting.

Provides transactional guarantees for the multi-step AP invoice posting
process with automatic compensation on failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import JournalInput, JournalLineInput, JournalService
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.services.finance.platform.saga_factory import register_saga
from app.services.finance.platform.saga_orchestrator import (
    SagaOrchestrator,
    SagaResult,
    SagaStepDefinition,
    StepResult,
)
from app.services.finance.tax.tax_transaction import tax_transaction_service

logger = logging.getLogger(__name__)


@dataclass
class APPostingSagaResult:
    """Result from AP invoice posting saga."""
    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""
    saga_id: Optional[UUID] = None


class APInvoicePostingSaga(SagaOrchestrator):
    """
    Saga for posting AP invoices to the general ledger.

    Steps:
    1. validate_invoice - Validate invoice is ready for posting
    2. create_journal - Create and approve GL journal entry
    3. post_to_ledger - Post journal to ledger
    4. create_tax_transactions - Create tax records
    5. update_invoice_status - Mark invoice as posted

    Compensation:
    - On failure after create_journal: void the journal
    - On failure after post_to_ledger: create reversal entry
    - On failure after create_tax_transactions: void tax transactions
    """

    @property
    def saga_type(self) -> str:
        return "AP_INVOICE_POST"

    @property
    def steps(self) -> list[SagaStepDefinition]:
        return [
            SagaStepDefinition(
                name="validate_invoice",
                execute=self._step_validate_invoice,
                compensate=None,  # Read-only step
                description="Validate invoice is ready for posting",
            ),
            SagaStepDefinition(
                name="create_journal",
                execute=self._step_create_journal,
                compensate=self._compensate_create_journal,
                description="Create and approve GL journal entry",
            ),
            SagaStepDefinition(
                name="post_to_ledger",
                execute=self._step_post_to_ledger,
                compensate=self._compensate_post_to_ledger,
                description="Post journal entry to ledger",
            ),
            SagaStepDefinition(
                name="create_tax_transactions",
                execute=self._step_create_tax_transactions,
                compensate=self._compensate_tax_transactions,
                description="Create tax transaction records",
            ),
            SagaStepDefinition(
                name="update_invoice_status",
                execute=self._step_update_invoice_status,
                compensate=self._compensate_invoice_status,
                description="Update invoice status to POSTED",
            ),
        ]

    def _build_result(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build final result from saga context."""
        return {
            "journal_entry_id": context.get("journal_entry_id"),
            "posting_batch_id": context.get("posting_batch_id"),
            "posted_lines": context.get("posted_lines"),
            "total_debit": context.get("total_debit"),
            "total_credit": context.get("total_credit"),
            "tax_transaction_ids": context.get("tax_transaction_ids", []),
        }

    # -------------------------------------------------------------------------
    # Step Implementations
    # -------------------------------------------------------------------------

    def _step_validate_invoice(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """Validate invoice is ready for posting."""
        org_id = coerce_uuid(payload["organization_id"])
        invoice_id = coerce_uuid(payload["invoice_id"])

        invoice = db.get(SupplierInvoice, invoice_id)
        if not invoice:
            return StepResult(success=False, error="Supplier invoice not found")
        if not invoice or invoice.organization_id != org_id:
            return StepResult(
                success=False,
                error="Invoice not found",
            )

        if invoice.status != SupplierInvoiceStatus.APPROVED:
            return StepResult(
                success=False,
                error=f"Invoice must be APPROVED to post (current: {invoice.status.value})",
            )

        # Load supplier
        supplier = db.get(Supplier, invoice.supplier_id)
        if not supplier:
            return StepResult(
                success=False,
                error="Supplier not found",
            )

        # Load invoice lines
        lines = (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == invoice_id)
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

        if not lines:
            return StepResult(
                success=False,
                error="Invoice has no lines",
            )

        # Store validated data in context
        return StepResult(
            success=True,
            output_data={
                "invoice_number": invoice.invoice_number,
                "supplier_name": supplier.legal_name,
                "supplier_id": str(supplier.supplier_id),
                "ap_control_account_id": str(invoice.ap_control_account_id),
                "invoice_type": invoice.invoice_type.value,
                "currency_code": invoice.currency_code,
                "exchange_rate": str(invoice.exchange_rate or "1.0"),
                "total_amount": str(invoice.total_amount),
                "functional_currency_amount": str(invoice.functional_currency_amount),
                "line_count": len(lines),
            },
        )

    def _step_create_journal(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """Create and approve the GL journal entry."""
        from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

        org_id = coerce_uuid(payload["organization_id"])
        invoice_id = coerce_uuid(payload["invoice_id"])
        user_id = coerce_uuid(payload["posted_by_user_id"])
        posting_date = date.fromisoformat(payload["posting_date"])

        # Load invoice and lines again (ensure fresh data)
        invoice = db.get(SupplierInvoice, invoice_id)
        if not invoice:
            return StepResult(success=False, error="Supplier invoice not found")

        supplier = db.get(Supplier, invoice.supplier_id)
        if not supplier:
            return StepResult(success=False, error="Supplier not found")
        lines = (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == invoice_id)
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

        exchange_rate = invoice.exchange_rate or Decimal("1.0")

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []

        for inv_line in lines:
            # Determine debit account
            account_id = APPostingAdapter._determine_debit_account(
                db, org_id, inv_line, supplier
            )
            if not account_id:
                return StepResult(
                    success=False,
                    error=f"No expense account for line {inv_line.line_number}",
                )

            line_total = inv_line.line_amount + inv_line.tax_amount
            functional_amount = line_total * exchange_rate

            if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(line_total),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(functional_amount),
                        description=f"AP Credit Note: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=line_total,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=functional_amount,
                        credit_amount_functional=Decimal("0"),
                        description=f"AP Invoice: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )

        # Credit line (AP Control account)
        total_functional = invoice.functional_currency_amount

        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ap_control_account_id,
                    debit_amount=abs(invoice.total_amount),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(total_functional),
                    credit_amount_functional=Decimal("0"),
                    description=f"AP Credit Note: {supplier.legal_name}",
                )
            )
        else:
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ap_control_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=invoice.total_amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=total_functional,
                    description=f"AP Invoice: {supplier.legal_name}",
                )
            )

        # Create journal
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=invoice.invoice_date,
            posting_date=posting_date,
            description=f"AP Invoice {invoice.invoice_number} - {supplier.legal_name}",
            reference=invoice.supplier_invoice_number or invoice.invoice_number,
            currency_code=invoice.currency_code,
            exchange_rate=exchange_rate,
            exchange_rate_type_id=invoice.exchange_rate_type_id,
            lines=journal_lines,
            source_module="AP",
            source_document_type="SUPPLIER_INVOICE",
            source_document_id=invoice_id,
            correlation_id=invoice.correlation_id,
        )

        try:
            journal = JournalService.create_journal(
                db, org_id, journal_input, user_id
            )

            # Submit and approve
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

            # Commit the journal creation
            db.commit()

            logger.info(
                "Created journal %s for invoice %s",
                journal.journal_number, invoice.invoice_number
            )

            return StepResult(
                success=True,
                output_data={
                    "journal_entry_id": str(journal.journal_entry_id),
                    "journal_number": journal.journal_number,
                },
                compensation_data={
                    "journal_entry_id": str(journal.journal_entry_id),
                },
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to create journal for invoice %s", invoice_id)
            return StepResult(
                success=False,
                error=f"Journal creation failed: {str(e)}",
            )

    def _compensate_create_journal(
        self,
        db: Session,
        payload: dict[str, Any],
        compensation_data: dict[str, Any],
    ) -> bool:
        """Compensate by voiding the journal entry."""
        journal_id = compensation_data.get("journal_entry_id")
        if not journal_id:
            return True  # Nothing to compensate

        journal = db.get(JournalEntry, coerce_uuid(journal_id))
        if not journal:
            return True  # Already gone

        if journal.status == JournalStatus.VOID:
            return True  # Already voided

        if journal.status == JournalStatus.POSTED:
            # Cannot void a posted journal - will need reversal
            # This will be handled by post_to_ledger compensation
            return True

        try:
            journal.status = JournalStatus.VOID
            db.commit()
            logger.info("Voided journal %s during saga compensation", journal_id)
            return True
        except Exception as e:
            logger.exception("Failed to void journal %s", journal_id)
            return False

    def _step_post_to_ledger(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """Post the journal entry to the ledger."""
        org_id = coerce_uuid(payload["organization_id"])
        invoice_id = coerce_uuid(payload["invoice_id"])
        user_id = coerce_uuid(payload["posted_by_user_id"])
        posting_date = date.fromisoformat(payload["posting_date"])
        journal_entry_id = coerce_uuid(context["journal_entry_id"])

        invoice = db.get(SupplierInvoice, invoice_id)
        if not invoice:
            return StepResult(success=False, error="Supplier invoice not found")
        idempotency_key = payload.get("idempotency_key") or f"{org_id}:AP:{invoice_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="AP",
            correlation_id=invoice.correlation_id,
            posted_by_user_id=user_id,
        )

        try:
            result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not result.success:
                return StepResult(
                    success=False,
                    error=f"Ledger posting failed: {result.message}",
                )

            logger.info(
                "Posted journal %s to ledger, batch %s",
                context["journal_number"], result.batch_id
            )

            return StepResult(
                success=True,
                output_data={
                    "posting_batch_id": str(result.batch_id),
                    "posted_lines": result.posted_lines,
                    "total_debit": str(result.total_debit),
                    "total_credit": str(result.total_credit),
                },
                compensation_data={
                    "journal_entry_id": str(journal_entry_id),
                    "posting_batch_id": str(result.batch_id),
                },
            )

        except Exception as e:
            logger.exception("Ledger posting failed for invoice %s", invoice_id)
            return StepResult(
                success=False,
                error=f"Ledger posting error: {str(e)}",
            )

    def _compensate_post_to_ledger(
        self,
        db: Session,
        payload: dict[str, Any],
        compensation_data: dict[str, Any],
    ) -> bool:
        """Compensate by creating a reversal journal entry."""
        from app.services.finance.gl.reversal import ReversalService

        journal_id = compensation_data.get("journal_entry_id")
        if not journal_id:
            return True

        org_id = coerce_uuid(payload["organization_id"])
        user_id = coerce_uuid(payload["posted_by_user_id"])
        posting_date = date.fromisoformat(payload["posting_date"])

        try:
            result = ReversalService.create_reversal(
                db=db,
                organization_id=org_id,
                original_journal_id=coerce_uuid(journal_id),
                reversal_date=posting_date,
                created_by_user_id=user_id,
                reason="Saga compensation - posting failed",
                auto_post=True,
            )

            if result.success:
                logger.info(
                    "Created reversal journal %s for saga compensation",
                    result.reversal_journal_id
                )
                return True
            else:
                logger.error(
                    "Failed to create reversal for journal %s: %s",
                    journal_id, result.message
                )
                return False

        except Exception as e:
            logger.exception("Reversal failed for journal %s", journal_id)
            return False

    def _step_create_tax_transactions(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """Create tax transaction records for input tax."""
        from app.models.finance.gl.fiscal_period import FiscalPeriod

        org_id = coerce_uuid(payload["organization_id"])
        invoice_id = coerce_uuid(payload["invoice_id"])

        invoice = db.get(SupplierInvoice, invoice_id)
        if not invoice:
            return StepResult(success=False, error="Supplier invoice not found")

        supplier = db.get(Supplier, invoice.supplier_id)
        if not supplier:
            return StepResult(success=False, error="Supplier not found")
        lines = (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == invoice_id)
            .all()
        )

        exchange_rate = invoice.exchange_rate or Decimal("1.0")
        is_credit_note = invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE

        # Get fiscal period
        fiscal_period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= invoice.invoice_date,
                FiscalPeriod.end_date >= invoice.invoice_date,
            )
            .first()
        )

        if not fiscal_period:
            # No fiscal period - skip tax transactions (non-fatal)
            return StepResult(
                success=True,
                output_data={"tax_transaction_ids": []},
            )

        tax_transaction_ids = []

        for line in lines:
            if not line.tax_code_id or line.tax_amount == Decimal("0"):
                continue

            base_amount = line.line_amount if not is_credit_note else -line.line_amount

            try:
                tax_txn = tax_transaction_service.create_from_invoice_line(
                    db=db,
                    organization_id=org_id,
                    fiscal_period_id=fiscal_period.fiscal_period_id,
                    tax_code_id=line.tax_code_id,
                    invoice_id=invoice.invoice_id,
                    invoice_line_id=line.line_id,
                    invoice_number=invoice.invoice_number,
                    transaction_date=invoice.invoice_date,
                    is_purchase=True,  # AP = INPUT tax
                    base_amount=base_amount,
                    currency_code=invoice.currency_code,
                    counterparty_name=supplier.legal_name,
                    counterparty_tax_id=supplier.tax_identification_number,
                    exchange_rate=exchange_rate,
                )
                tax_transaction_ids.append(str(tax_txn.transaction_id))
            except Exception as e:
                logger.warning(
                    "Failed to create tax transaction for line %s: %s",
                    line.line_id, e
                )
                # Continue - tax transaction failures are non-fatal

        db.commit()

        return StepResult(
            success=True,
            output_data={"tax_transaction_ids": tax_transaction_ids},
            compensation_data={"tax_transaction_ids": tax_transaction_ids},
        )

    def _compensate_tax_transactions(
        self,
        db: Session,
        payload: dict[str, Any],
        compensation_data: dict[str, Any],
    ) -> bool:
        """Compensate by voiding tax transactions."""
        from app.models.finance.tax.tax_transaction import TaxTransaction

        tax_ids = compensation_data.get("tax_transaction_ids", [])
        if not tax_ids:
            return True

        try:
            for tax_id in tax_ids:
                tax_txn = db.get(TaxTransaction, coerce_uuid(tax_id))
                if tax_txn:
                    db.delete(tax_txn)

            db.commit()
            logger.info("Voided %d tax transactions during saga compensation", len(tax_ids))
            return True

        except Exception as e:
            logger.exception("Failed to void tax transactions")
            return False

    def _step_update_invoice_status(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """Update invoice status to POSTED."""
        invoice_id = coerce_uuid(payload["invoice_id"])
        user_id = coerce_uuid(payload["posted_by_user_id"])
        journal_entry_id = coerce_uuid(context["journal_entry_id"])
        posting_batch_id = coerce_uuid(context["posting_batch_id"])

        invoice = db.get(SupplierInvoice, invoice_id)
        if not invoice:
            return StepResult(success=False, error="Supplier invoice not found")

        original_status = invoice.status.value

        invoice.status = SupplierInvoiceStatus.POSTED
        invoice.posting_status = "POSTED"
        invoice.journal_entry_id = journal_entry_id
        invoice.posting_batch_id = posting_batch_id
        invoice.posted_by_user_id = user_id
        invoice.posted_at = datetime.now(timezone.utc)

        db.commit()

        logger.info(
            "Updated invoice %s status to POSTED",
            invoice.invoice_number
        )

        return StepResult(
            success=True,
            output_data={"invoice_status": "POSTED"},
            compensation_data={
                "original_status": original_status,
            },
        )

    def _compensate_invoice_status(
        self,
        db: Session,
        payload: dict[str, Any],
        compensation_data: dict[str, Any],
    ) -> bool:
        """Compensate by reverting invoice status."""
        invoice_id = coerce_uuid(payload["invoice_id"])
        original_status = compensation_data.get("original_status", "APPROVED")

        try:
            invoice = db.get(SupplierInvoice, invoice_id)
            if invoice:
                invoice.status = SupplierInvoiceStatus(original_status)
                invoice.posting_status = "NOT_POSTED"
                invoice.journal_entry_id = None
                invoice.posting_batch_id = None
                invoice.posted_by_user_id = None
                invoice.posted_at = None

                db.commit()
                logger.info(
                    "Reverted invoice %s status to %s",
                    invoice.invoice_number, original_status
                )

            return True

        except Exception as e:
            logger.exception("Failed to revert invoice status")
            return False


# Register with the saga factory
ap_invoice_posting_saga = register_saga(APInvoicePostingSaga())


def post_invoice_with_saga(
    db: Session,
    organization_id: UUID,
    invoice_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> APPostingSagaResult:
    """
    Post a supplier invoice using the saga pattern.

    This is the recommended way to post AP invoices as it provides
    automatic compensation on failure.

    Args:
        db: Database session
        organization_id: Organization scope
        invoice_id: Invoice to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key
        correlation_id: Optional correlation ID

    Returns:
        APPostingSagaResult with outcome
    """
    org_id = coerce_uuid(organization_id)
    inv_id = coerce_uuid(invoice_id)
    user_id = coerce_uuid(posted_by_user_id)

    if not idempotency_key:
        idempotency_key = f"{org_id}:AP:{inv_id}:post:saga:v1"

    payload = {
        "organization_id": str(org_id),
        "invoice_id": str(inv_id),
        "posting_date": posting_date.isoformat(),
        "posted_by_user_id": str(user_id),
        "idempotency_key": idempotency_key,
    }

    result = ap_invoice_posting_saga.execute(
        db=db,
        organization_id=org_id,
        payload=payload,
        idempotency_key=idempotency_key,
        created_by_user_id=user_id,
        correlation_id=correlation_id,
    )

    if result.success:
        return APPostingSagaResult(
            success=True,
            journal_entry_id=UUID(result.result.get("journal_entry_id")) if result.result.get("journal_entry_id") else None,
            posting_batch_id=UUID(result.result.get("posting_batch_id")) if result.result.get("posting_batch_id") else None,
            message="Invoice posted successfully",
            saga_id=result.saga_id,
        )
    else:
        return APPostingSagaResult(
            success=False,
            message=result.error or "Posting failed",
            saga_id=result.saga_id,
        )
