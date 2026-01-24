"""
Expense → AP Integration Adapter.

Creates AP supplier invoices from approved expense claims.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.exp import ExpenseClaim, ExpenseClaimItem, ExpenseClaimStatus
from app.models.people.hr import Employee

logger = logging.getLogger(__name__)


@dataclass
class APPostingResult:
    """Result of posting expense claim to AP."""
    success: bool
    supplier_invoice_id: Optional[uuid.UUID] = None
    error_message: Optional[str] = None


class ExpenseAPAdapter:
    """
    Adapter for creating AP supplier invoices from expense claims.

    When an expense claim is approved, this adapter:
    1. Gets/creates the employee as an internal supplier
    2. Creates a supplier invoice in AP with expense line items
    3. Posts the invoice to GL via AP's existing posting adapter
    4. Updates the expense claim with the invoice reference

    This follows the same pattern as APPostingAdapter in the Finance module.
    """

    @staticmethod
    def create_payable_from_claim(
        db: Session,
        org_id: uuid.UUID,
        claim_id: uuid.UUID,
        posting_date: date,
        user_id: uuid.UUID,
    ) -> APPostingResult:
        """
        Create an AP supplier invoice from an approved expense claim.

        Args:
            db: Database session
            org_id: Organization ID
            claim_id: Expense claim ID
            posting_date: Date to post the invoice
            user_id: User creating the invoice

        Returns:
            APPostingResult with invoice ID or error
        """
        try:
            # Get the expense claim
            claim = db.get(ExpenseClaim, claim_id)
            if not claim:
                return APPostingResult(
                    success=False,
                    error_message=f"Expense claim {claim_id} not found"
                )

            if claim.organization_id != org_id:
                return APPostingResult(
                    success=False,
                    error_message="Expense claim does not belong to this organization"
                )

            if claim.status != ExpenseClaimStatus.APPROVED:
                return APPostingResult(
                    success=False,
                    error_message=f"Expense claim must be APPROVED, current status: {claim.status}"
                )

            if claim.supplier_invoice_id is not None:
                return APPostingResult(
                    success=False,
                    error_message="Expense claim already has an associated invoice"
                )

            # Get the employee
            employee = db.get(Employee, claim.employee_id)
            if not employee:
                return APPostingResult(
                    success=False,
                    error_message="Employee not found"
                )

            # Import AP services (deferred to avoid circular imports)
            from app.services.finance.ap.supplier import SupplierService
            from app.services.finance.ap.supplier_invoice import SupplierInvoiceService
            from app.services.finance.ap import (
                SupplierInput,
                SupplierInvoiceInput,
                SupplierInvoiceLineInput,
            )

            # Get or create employee as internal supplier
            supplier = ExpenseAPAdapter._get_or_create_internal_supplier(
                db, org_id, employee, user_id
            )

            # Build invoice lines from expense claim items
            lines = []
            for item in claim.items:
                # Use item-level account override or category default
                expense_account_id = (
                    item.expense_account_id
                    or (item.category.expense_account_id if item.category else None)
                )

                if expense_account_id is None:
                    logger.warning(
                        f"No expense account for item {item.item_id}, "
                        f"category {item.category_id}"
                    )
                    continue

                lines.append(SupplierInvoiceLineInput(
                    expense_account_id=expense_account_id,
                    description=item.description,
                    quantity=Decimal("1"),
                    unit_price=item.approved_amount or item.claimed_amount,
                    amount=item.approved_amount or item.claimed_amount,
                    cost_center_id=item.cost_center_id or claim.cost_center_id,
                ))

            if not lines:
                return APPostingResult(
                    success=False,
                    error_message="No valid expense items with GL accounts"
                )

            # Create the supplier invoice
            invoice_input = SupplierInvoiceInput(
                supplier_id=supplier.supplier_id,
                invoice_date=posting_date,
                due_date=posting_date,  # Expense reimbursements typically due immediately
                currency_code=claim.currency_code,
                reference_number=claim.claim_number,
                description=f"Expense Reimbursement: {claim.purpose}",
                lines=lines,
            )

            invoice = SupplierInvoiceService.create_invoice(
                db, org_id, invoice_input, user_id
            )

            # Link the invoice to the claim
            claim.supplier_invoice_id = invoice.invoice_id

            # Commit changes
            db.commit()

            logger.info(
                f"Created AP invoice {invoice.invoice_id} for expense claim {claim_id}"
            )

            return APPostingResult(
                success=True,
                supplier_invoice_id=invoice.invoice_id,
            )

        except Exception as e:
            logger.exception(f"Error creating AP invoice for claim {claim_id}")
            db.rollback()
            return APPostingResult(
                success=False,
                error_message=str(e),
            )

    @staticmethod
    def _get_or_create_internal_supplier(
        db: Session,
        org_id: uuid.UUID,
        employee: Employee,
        user_id: uuid.UUID,
    ):
        """
        Get or create an internal supplier for an employee.

        Internal suppliers are used for expense reimbursements.
        """
        from app.models.finance.ap.supplier import Supplier
        from app.services.finance.ap.supplier import SupplierService
        from app.services.finance.ap import SupplierInput

        # Try to find existing internal supplier for this employee
        # Convention: internal supplier code = EMP-{employee_code}
        supplier_code = f"EMP-{employee.employee_code}"

        stmt = select(Supplier).where(
            Supplier.organization_id == org_id,
            Supplier.supplier_code == supplier_code,
        )
        supplier = db.execute(stmt).scalar_one_or_none()

        if supplier:
            return supplier

        # Create new internal supplier
        # Get employee's name from related person
        person = employee.person
        supplier_name = f"{person.first_name} {person.last_name}" if person else employee.employee_code

        supplier_input = SupplierInput(
            supplier_code=supplier_code,
            supplier_name=supplier_name,
            supplier_type="INTERNAL",
            is_internal=True,
            email=person.email if person else None,
            payment_terms_days=0,  # Immediate payment
            notes=f"Internal supplier for employee {employee.employee_code}",
        )

        supplier = SupplierService.create_supplier(
            db, org_id, supplier_input, user_id
        )

        logger.info(
            f"Created internal supplier {supplier.supplier_id} for employee {employee.employee_id}"
        )

        return supplier

    @staticmethod
    def post_claim_to_gl(
        db: Session,
        org_id: uuid.UUID,
        claim_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> APPostingResult:
        """
        Post an expense claim's AP invoice to GL.

        This should be called after create_payable_from_claim.
        Uses the Finance module's APPostingAdapter.
        """
        try:
            claim = db.get(ExpenseClaim, claim_id)
            if not claim or not claim.supplier_invoice_id:
                return APPostingResult(
                    success=False,
                    error_message="Claim has no associated AP invoice"
                )

            # Import AP posting adapter
            from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

            # Post the invoice
            result = APPostingAdapter.post_invoice(
                db,
                org_id,
                claim.supplier_invoice_id,
                user_id,
            )

            if result.success:
                logger.info(
                    f"Posted expense claim {claim_id} to GL via invoice {claim.supplier_invoice_id}"
                )

            return APPostingResult(
                success=result.success,
                supplier_invoice_id=claim.supplier_invoice_id,
                error_message=result.error_message if not result.success else None,
            )

        except Exception as e:
            logger.exception(f"Error posting claim {claim_id} to GL")
            return APPostingResult(
                success=False,
                error_message=str(e),
            )
