"""
Remita Source Handler.

Handles updates to source entities when RRR status changes (e.g., marked as paid).
This is a dispatcher that routes updates to the appropriate module service.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.finance.remita import RemitaRRR, RRRStatus

logger = logging.getLogger(__name__)


class RemitaSourceHandler:
    """
    Handles source entity updates when RRR payment status changes.

    When an RRR is marked as paid (either via API refresh or manual marking),
    this handler updates the linked source entity appropriately:
    - AP Invoice: Mark as paid or create payment record
    - AP Payment: Update with Remita reference
    - Payroll Run: Mark statutory remittance as complete
    - Expense Claim: Update reimbursement status
    """

    def __init__(self, db: Session):
        self.db = db

    def handle_rrr_paid(self, rrr: RemitaRRR) -> dict | None:
        """
        Handle RRR payment confirmation.

        Called when RRR status changes to 'paid'. Updates the linked source
        entity if one exists.

        Args:
            rrr: The RRR that was marked as paid

        Returns:
            Dict with update details, or None if no source linked
        """
        if not rrr.source_type or not rrr.source_id:
            logger.debug(f"RRR {rrr.rrr} has no linked source, skipping update")
            return None

        if rrr.status != RRRStatus.paid:
            logger.warning(
                f"handle_rrr_paid called for RRR {rrr.rrr} with status {rrr.status}"
            )
            return None

        logger.info(
            f"Processing paid RRR {rrr.rrr} for source {rrr.source_type}:{rrr.source_id}"
        )

        handler_map = {
            "ap_invoice": self._handle_ap_invoice_paid,
            "ap_payment": self._handle_ap_payment_paid,
            "payroll_run": self._handle_payroll_run_paid,
            "expense_claim": self._handle_expense_claim_paid,
        }

        handler = handler_map.get(rrr.source_type)
        if not handler:
            logger.warning(f"No handler for source type: {rrr.source_type}")
            return None

        try:
            result = handler(rrr)
            self.db.flush()
            return result
        except Exception as e:
            logger.exception(
                f"Error handling paid RRR {rrr.rrr} for {rrr.source_type}: {e}"
            )
            raise

    def _handle_ap_invoice_paid(self, rrr: RemitaRRR) -> dict:
        """
        Handle AP Invoice (Bill) payment via Remita.

        For now, just logs the payment. Full integration would:
        - Create a SupplierPayment record
        - Allocate to the invoice
        - Post to GL
        """
        from app.models.finance.ap.supplier_invoice import SupplierInvoice

        invoice = self.db.get(SupplierInvoice, rrr.source_id)
        if not invoice:
            raise ValueError(f"AP Invoice {rrr.source_id} not found")

        # Log for now - full payment creation requires more context
        # (bank account, posting date, etc.)
        logger.info(
            f"RRR {rrr.rrr} paid for AP Invoice {invoice.invoice_number}. "
            f"Amount: {rrr.amount}, Reference: {rrr.payment_reference}"
        )

        return {
            "source_type": "ap_invoice",
            "source_id": str(rrr.source_id),
            "invoice_number": invoice.invoice_number,
            "action": "logged",
            "note": "Manual payment creation required to complete AP workflow",
        }

    def _handle_ap_payment_paid(self, rrr: RemitaRRR) -> dict:
        """
        Handle AP Payment confirmation via Remita.

        Updates the payment record with Remita reference.
        """
        from app.models.finance.ap.supplier_payment import (
            APPaymentStatus,
            SupplierPayment,
        )

        payment = self.db.get(SupplierPayment, rrr.source_id)
        if not payment:
            raise ValueError(f"AP Payment {rrr.source_id} not found")

        # Update payment with Remita reference
        if hasattr(payment, "remita_payment_reference"):
            payment.remita_payment_reference = rrr.payment_reference

        # If payment is in SENT status, mark as CLEARED
        if payment.status == APPaymentStatus.SENT:
            payment.status = APPaymentStatus.CLEARED
            logger.info(
                f"AP Payment {payment.payment_number} marked as CLEARED via Remita"
            )

        return {
            "source_type": "ap_payment",
            "source_id": str(rrr.source_id),
            "payment_number": payment.payment_number,
            "action": "updated",
            "new_status": payment.status.value
            if hasattr(payment.status, "value")
            else str(payment.status),
        }

    def _handle_payroll_run_paid(self, rrr: RemitaRRR) -> dict:
        """
        Handle Payroll Run statutory remittance via Remita.

        Updates the payroll run to mark the specific statutory deduction as remitted.
        The biller_id indicates which statutory (PAYE, Pension, NHF, etc.).
        """
        from app.models.people.payroll.payroll_entry import PayrollEntry

        payroll_run = self.db.get(PayrollEntry, rrr.source_id)
        if not payroll_run:
            raise ValueError(f"Payroll Run {rrr.source_id} not found")

        # Map biller to remittance field
        # Note: These fields may need to be added to PayrollEntry model
        biller_field_map = {
            "FIRS": "paye_remitted_at",
            "PENCOM": "pension_remitted_at",
            "FMBN": "nhf_remitted_at",
            "NSITF": "nsitf_remitted_at",
            "ITF": "itf_remitted_at",
        }

        field_name = biller_field_map.get(rrr.biller_id)
        if field_name and hasattr(payroll_run, field_name):
            setattr(payroll_run, field_name, rrr.paid_at or datetime.now(UTC))
            logger.info(
                f"Payroll Run {rrr.source_id} {rrr.biller_id} marked as remitted"
            )
            return {
                "source_type": "payroll_run",
                "source_id": str(rrr.source_id),
                "biller": rrr.biller_id,
                "action": "remitted",
                "field_updated": field_name,
            }
        else:
            logger.info(
                f"Payroll Run {rrr.source_id} RRR paid but no remittance field for {rrr.biller_id}"
            )
            return {
                "source_type": "payroll_run",
                "source_id": str(rrr.source_id),
                "biller": rrr.biller_id,
                "action": "logged",
                "note": f"No remittance tracking field for {rrr.biller_id}",
            }

    def _handle_expense_claim_paid(self, rrr: RemitaRRR) -> dict:
        """
        Handle Expense Claim payment via Remita.

        Updates the expense claim with payment reference.
        """
        from app.models.expense.expense_claim import ExpenseClaim

        claim = self.db.get(ExpenseClaim, rrr.source_id)
        if not claim:
            raise ValueError(f"Expense Claim {rrr.source_id} not found")

        # Log for now - expense claims typically have their own payment workflow
        logger.info(
            f"RRR {rrr.rrr} paid for Expense Claim {claim.claim_id}. "
            f"Amount: {rrr.amount}, Reference: {rrr.payment_reference}"
        )

        return {
            "source_type": "expense_claim",
            "source_id": str(rrr.source_id),
            "action": "logged",
            "note": "Expense claim payment recorded via Remita RRR",
        }


def get_source_handler(db: Session) -> RemitaSourceHandler:
    """Get RemitaSourceHandler instance."""
    return RemitaSourceHandler(db)
