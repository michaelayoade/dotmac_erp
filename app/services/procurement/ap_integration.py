"""
Procurement → AP Integration Service.

Generates AP supplier invoices from procurement contracts,
bridging the procurement and accounts payable modules.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.common import NotFoundError

if TYPE_CHECKING:
    from app.models.finance.ap.supplier_invoice import SupplierInvoice

logger = logging.getLogger(__name__)


class ProcurementAPIntegrationService:
    """Service for creating AP invoices from procurement contracts."""

    def __init__(self, db: Session):
        self.db = db

    def generate_invoice_from_contract(
        self,
        organization_id: UUID,
        contract_id: UUID,
        *,
        created_by_user_id: UUID,
        ap_control_account_id: UUID,
        expense_account_id: UUID,
        invoice_date: Optional[date] = None,
        payment_terms_days: int = 30,
    ) -> "SupplierInvoice":
        """
        Create a DRAFT supplier invoice from a procurement contract.

        Args:
            organization_id: Tenant organization.
            contract_id: Source procurement contract.
            created_by_user_id: User creating the invoice.
            ap_control_account_id: AP control GL account.
            expense_account_id: Expense GL account for the line.
            invoice_date: Invoice date (defaults to today).
            payment_terms_days: Days until payment is due.

        Returns:
            The created SupplierInvoice in DRAFT status.
        """
        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
            SupplierInvoiceType,
        )
        from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.models.procurement.procurement_contract import ProcurementContract
        from app.services.finance.platform.sequence import SequenceService

        # Load contract with tenant check
        contract = self.db.get(ProcurementContract, contract_id)
        if not contract:
            raise NotFoundError(f"Contract {contract_id} not found")
        if contract.organization_id != organization_id:
            raise NotFoundError(f"Contract {contract_id} not found")

        if invoice_date is None:
            invoice_date = date.today()

        due_date = invoice_date + timedelta(days=payment_terms_days)

        # Generate invoice number
        invoice_number = SequenceService.get_next_number(
            self.db, organization_id, SequenceType.SUPPLIER_INVOICE
        )

        invoice = SupplierInvoice(
            organization_id=organization_id,
            supplier_id=contract.supplier_id,
            invoice_number=invoice_number,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=invoice_date,
            received_date=invoice_date,
            due_date=due_date,
            currency_code=contract.currency_code,
            subtotal=contract.contract_value,
            tax_amount=Decimal("0"),
            total_amount=contract.contract_value,
            functional_currency_amount=contract.contract_value,
            status=SupplierInvoiceStatus.DRAFT,
            ap_control_account_id=ap_control_account_id,
            created_by_user_id=created_by_user_id,
            correlation_id=f"proc-contract:{contract_id}",
        )
        self.db.add(invoice)
        self.db.flush()

        # Create single line item for the full contract value
        line = SupplierInvoiceLine(
            invoice_id=invoice.invoice_id,
            line_number=1,
            description=f"Contract: {contract.title}",
            quantity=Decimal("1"),
            unit_price=contract.contract_value,
            line_amount=contract.contract_value,
            expense_account_id=expense_account_id,
        )
        self.db.add(line)
        self.db.flush()

        logger.info(
            "Generated AP invoice %s from contract %s (value: %s %s)",
            invoice_number,
            contract.contract_number,
            contract.currency_code,
            contract.contract_value,
        )
        return invoice
