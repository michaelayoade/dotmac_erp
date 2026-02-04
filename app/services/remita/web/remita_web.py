"""
Remita Web Service.

Provides request/response formatting for Remita web UI routes.
"""

import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.remita import RemitaRRR, RRRStatus
from app.services.remita.rrr_service import RemitaRRRService

logger = logging.getLogger(__name__)

# Common billers for dropdown
# Note: Biller IDs are shorthand codes for the UI. The actual Remita
# serviceTypeId is what gets sent to the API.
COMMON_BILLERS = [
    {"id": "FIRS", "name": "Federal Inland Revenue Service"},
    {"id": "FMBN", "name": "Federal Mortgage Bank of Nigeria"},
    {"id": "BPP", "name": "Bureau of Public Procurement"},
    {"id": "PENCOM", "name": "National Pension Commission"},
    {"id": "NSITF", "name": "Nigeria Social Insurance Trust Fund"},
    {"id": "ITF", "name": "Industrial Training Fund"},
    {"id": "OTHER", "name": "Other"},
]

# Common service types by biller
# Note: These service type IDs are EXAMPLES. Organizations should obtain
# actual serviceTypeId values from Remita when setting up their biller accounts.
# For demo/testing, use serviceTypeId "4430731" with demo credentials.
COMMON_SERVICES = {
    "FIRS": [
        {"id": "4430731", "name": "PAYE Tax (Demo)"},
        {"id": "4430731", "name": "Stamp Duty (Demo)"},
        {"id": "4430731", "name": "Company Income Tax (Demo)"},
        {"id": "4430731", "name": "Withholding Tax (Demo)"},
        {"id": "4430731", "name": "Value Added Tax (Demo)"},
    ],
    "FMBN": [
        {"id": "4430731", "name": "National Housing Fund (Demo)"},
    ],
    "PENCOM": [
        {"id": "4430731", "name": "Pension Contribution (Demo)"},
    ],
    "NSITF": [
        {"id": "4430731", "name": "Employee Compensation Scheme (Demo)"},
    ],
    "ITF": [
        {"id": "4430731", "name": "ITF Levy (Demo)"},
    ],
}


class RemitaWebService:
    """Web service helper for Remita UI routes."""

    def __init__(self, db: Session):
        self.db = db
        self._service: Optional[RemitaRRRService] = None

    @property
    def service(self) -> RemitaRRRService:
        """Lazy-load RRR service."""
        if self._service is None:
            self._service = RemitaRRRService(self.db)
        return self._service

    def list_context(
        self,
        organization_id: UUID,
        status_filter: Optional[str] = None,
        biller_filter: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """
        Build context for RRR list page.

        Args:
            organization_id: Organization to filter by
            status_filter: Optional status filter
            biller_filter: Optional biller ID filter
            page: Current page number
            per_page: Items per page

        Returns:
            Dict with template context
        """
        # Parse status filter
        status = None
        if status_filter:
            try:
                status = RRRStatus(status_filter)
            except ValueError:
                pass

        offset = (page - 1) * per_page
        rrrs = self.service.list_rrrs(
            organization_id=organization_id,
            status=status,
            biller_id=biller_filter if biller_filter else None,
            limit=per_page + 1,  # Fetch one extra to check if there's more
            offset=offset,
        )

        # Determine if there are more pages
        has_next = len(rrrs) > per_page
        if has_next:
            rrrs = rrrs[:per_page]

        # Calculate totals for pending
        pending_count = len([r for r in rrrs if r.status == RRRStatus.pending])
        pending_amount = sum(
            (r.amount for r in rrrs if r.status == RRRStatus.pending),
            Decimal("0"),
        )

        return {
            "rrrs": rrrs,
            "statuses": [s.value for s in RRRStatus],
            "billers": COMMON_BILLERS,
            "status_filter": status_filter,
            "biller_filter": biller_filter,
            "current_page": page,
            "has_next": has_next,
            "has_prev": page > 1,
            "pending_count": pending_count,
            "pending_amount": pending_amount,
            "is_configured": self.service.is_configured(),
        }

    def detail_context(
        self,
        organization_id: UUID,
        rrr_id: UUID,
    ) -> dict:
        """
        Build context for RRR detail page.

        Args:
            organization_id: Organization to verify access
            rrr_id: RRR record ID

        Returns:
            Dict with template context

        Raises:
            ValueError: If RRR not found or access denied
        """
        rrr = self.service.get_by_id(rrr_id)
        if not rrr:
            raise ValueError(f"RRR {rrr_id} not found")

        if rrr.organization_id != organization_id:
            raise ValueError("Access denied")

        context = {
            "rrr": rrr,
            "can_cancel": rrr.status == RRRStatus.pending,
            "can_mark_paid": rrr.status == RRRStatus.pending,
            "can_refresh": rrr.status == RRRStatus.pending,
            "source_options": self.source_options(),
        }
        if rrr.source_type and rrr.source_id:
            context["source_link"] = self._source_link_for(rrr.source_type, rrr.source_id)
        return context

    def generate_form_context(self) -> dict:
        """
        Build context for RRR generation form.

        Returns:
            Dict with form options
        """
        return {
            "billers": COMMON_BILLERS,
            "services_by_biller": COMMON_SERVICES,
            "is_configured": self.service.is_configured(),
        }

    def generate_form_context_with_org(self, organization_id: UUID) -> dict:
        """Build context for RRR generation form with payer defaults."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        payer_name = ""
        payer_email = ""
        payer_phone = ""
        if org:
            payer_name = org.trading_name or org.legal_name or ""
            payer_email = org.contact_email or ""
            payer_phone = org.contact_phone or ""

        context = self.generate_form_context()
        context["payer_defaults"] = {
            "payer_name": payer_name,
            "payer_email": payer_email,
            "payer_phone": payer_phone,
        }
        return context

    def generate_rrr(
        self,
        organization_id: UUID,
        biller_id: str,
        biller_name: str,
        service_type_id: str,
        service_name: str,
        amount: Decimal,
        payer_name: str,
        payer_email: str,
        payer_phone: Optional[str] = None,
        description: str = "",
        created_by_id: Optional[UUID] = None,
    ) -> RemitaRRR:
        """
        Generate a new RRR.

        Args:
            organization_id: Organization making the payment
            biller_id: Biller code
            biller_name: Biller display name
            service_type_id: Remita service type
            service_name: Service display name
            amount: Payment amount
            payer_name: Payer name
            payer_email: Payer email
            payer_phone: Optional payer phone
            description: Payment description
            created_by_id: User who initiated the request

        Returns:
            Generated RemitaRRR record
        """
        return self.service.generate_rrr(
            organization_id=organization_id,
            biller_id=biller_id,
            biller_name=biller_name,
            service_type_id=service_type_id,
            service_name=service_name,
            amount=amount,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            description=description,
            created_by_id=created_by_id,
        )

    def source_options(self) -> list[dict[str, str]]:
        """List available source types for linking."""
        return [
            {"value": "ap_invoice", "label": "AP Invoice (Bill)"},
            {"value": "ap_payment", "label": "AP Payment"},
            {"value": "payroll_run", "label": "Payroll Run"},
            {"value": "expense_claim", "label": "Expense Claim"},
        ]

    def _resolve_source(self, organization_id: UUID, source_type: str, source_id: UUID):
        from app.models.finance.ap.supplier_invoice import SupplierInvoice
        from app.models.finance.ap.supplier_payment import SupplierPayment
        from app.models.people.payroll.payroll_entry import PayrollEntry
        from app.models.expense.expense_claim import ExpenseClaim

        type_map = {
            "ap_invoice": SupplierInvoice,
            "ap_payment": SupplierPayment,
            "payroll_run": PayrollEntry,
            "expense_claim": ExpenseClaim,
        }
        model = type_map.get(source_type)
        if not model:
            raise ValueError("Invalid source type")

        entity = self.db.get(model, source_id)
        if not entity:
            raise ValueError("Source entity not found")

        entity_org_id = getattr(entity, "organization_id", None)
        if entity_org_id and entity_org_id != organization_id:
            raise ValueError("Access denied")

        return entity

    def _source_link_for(self, source_type: str, source_id: UUID) -> dict[str, str]:
        label_map = {
            "ap_invoice": "AP Invoice (Bill)",
            "ap_payment": "AP Payment",
            "payroll_run": "Payroll Run",
            "expense_claim": "Expense Claim",
        }
        url_map = {
            "ap_invoice": f"/finance/ap/invoices/{source_id}",
            "ap_payment": f"/finance/ap/payments/{source_id}",
            "payroll_run": f"/people/payroll/runs/{source_id}",
            "expense_claim": f"/expense/claims/{source_id}",
        }
        return {
            "label": label_map.get(source_type, source_type),
            "url": url_map.get(source_type, ""),
        }

    def link_rrr(
        self,
        organization_id: UUID,
        rrr_id: UUID,
        source_type: str,
        source_id: UUID,
    ) -> RemitaRRR:
        """Link an RRR to a source entity."""
        rrr = self.service.get_by_id(rrr_id)
        if not rrr or rrr.organization_id != organization_id:
            raise ValueError("RRR not found or access denied")

        self._resolve_source(organization_id, source_type, source_id)

        existing = self.service.get_for_source(
            organization_id=organization_id,
            source_type=source_type,
            source_id=source_id,
            status=RRRStatus.pending,
        )
        if existing and existing.id != rrr.id:
            raise ValueError("Another pending RRR is already linked to this source")

        rrr.source_type = source_type
        rrr.source_id = source_id
        self.db.flush()
        return rrr

    def search_sources(
        self,
        organization_id: UUID,
        source_type: str,
        query: str,
        limit: int = 10,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        recent: bool = False,
    ) -> list[dict[str, str]]:
        """Search source entities for linking."""
        from sqlalchemy import select, or_
        from app.models.finance.ap.supplier_invoice import SupplierInvoice
        from app.models.finance.ap.supplier_payment import SupplierPayment
        from app.models.finance.ap.supplier import Supplier
        from app.models.people.payroll.payroll_entry import PayrollEntry
        from app.models.expense.expense_claim import ExpenseClaim
        from datetime import date

        results: list[dict[str, str]] = []
        q = (query or "").strip()
        if not q and not recent:
            return results

        q_like = f"%{q}%"
        date_from_val: Optional[date] = None
        date_to_val: Optional[date] = None
        try:
            if date_from:
                date_from_val = date.fromisoformat(date_from)
            if date_to:
                date_to_val = date.fromisoformat(date_to)
        except ValueError:
            raise ValueError("Invalid date filter")

        stmt: Any
        if source_type == "ap_invoice":
            stmt = (
                select(SupplierInvoice, Supplier)
                .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
                .where(SupplierInvoice.organization_id == organization_id)
            )
            if not recent:
                stmt = stmt.where(
                    or_(
                        SupplierInvoice.invoice_number.ilike(q_like),
                        Supplier.supplier_code.ilike(q_like),
                        Supplier.legal_name.ilike(q_like),
                        Supplier.trading_name.ilike(q_like),
                    )
                )
            if status:
                try:
                    from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
                    stmt = stmt.where(SupplierInvoice.status == SupplierInvoiceStatus(status))
                except ValueError:
                    raise ValueError("Invalid invoice status filter")
            if date_from_val:
                stmt = stmt.where(SupplierInvoice.invoice_date >= date_from_val)
            if date_to_val:
                stmt = stmt.where(SupplierInvoice.invoice_date <= date_to_val)
            stmt = stmt.order_by(SupplierInvoice.invoice_date.desc())
            if not recent:
                stmt = stmt.order_by(SupplierInvoice.invoice_number.desc())
            stmt = stmt.limit(limit)
            for invoice, supplier in self.db.execute(stmt).all():
                supplier_name = supplier.trading_name or supplier.legal_name
                label = f"{invoice.invoice_number} — {supplier_name}"
                results.append(
                    {
                        "id": str(invoice.invoice_id),
                        "label": label,
                        "sub_label": f"Amount: {invoice.total_amount}",
                        "url": f"/finance/ap/invoices/{invoice.invoice_id}",
                    }
                )
            return results

        if source_type == "ap_payment":
            stmt = (
                select(SupplierPayment, Supplier)
                .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
                .where(SupplierPayment.organization_id == organization_id)
            )
            if not recent:
                stmt = stmt.where(
                    or_(
                        SupplierPayment.payment_number.ilike(q_like),
                        SupplierPayment.reference.ilike(q_like),
                        Supplier.supplier_code.ilike(q_like),
                        Supplier.legal_name.ilike(q_like),
                        Supplier.trading_name.ilike(q_like),
                    )
                )
            if status:
                try:
                    from app.models.finance.ap.supplier_payment import APPaymentStatus
                    stmt = stmt.where(SupplierPayment.status == APPaymentStatus(status))
                except ValueError:
                    raise ValueError("Invalid payment status filter")
            if date_from_val:
                stmt = stmt.where(SupplierPayment.payment_date >= date_from_val)
            if date_to_val:
                stmt = stmt.where(SupplierPayment.payment_date <= date_to_val)
            stmt = stmt.order_by(SupplierPayment.payment_date.desc())
            if not recent:
                stmt = stmt.order_by(SupplierPayment.payment_number.desc())
            stmt = stmt.limit(limit)
            for payment, supplier in self.db.execute(stmt).all():
                supplier_name = supplier.trading_name or supplier.legal_name
                label = f"{payment.payment_number} — {supplier_name}"
                results.append(
                    {
                        "id": str(payment.payment_id),
                        "label": label,
                        "sub_label": f"Amount: {payment.amount}",
                        "url": f"/finance/ap/payments/{payment.payment_id}",
                    }
                )
            return results

        if source_type == "payroll_run":
            stmt = (
                select(PayrollEntry)
                .where(PayrollEntry.organization_id == organization_id)
            )
            if not recent:
                stmt = stmt.where(
                    or_(
                        PayrollEntry.entry_number.ilike(q_like),
                        PayrollEntry.entry_name.ilike(q_like),
                    )
                )
            stmt = stmt.order_by(PayrollEntry.created_at.desc())
            stmt = stmt.limit(limit)
            for run in self.db.execute(stmt).scalars().all():
                label = run.entry_number
                if run.entry_name:
                    label = f"{run.entry_number} — {run.entry_name}"
                results.append(
                    {
                        "id": str(run.entry_id),
                        "label": label,
                        "sub_label": f"{run.start_date} to {run.end_date}",
                        "url": f"/people/payroll/runs/{run.entry_id}",
                    }
                )
            return results

        if source_type == "expense_claim":
            stmt = (
                select(ExpenseClaim)
                .where(ExpenseClaim.organization_id == organization_id)
            )
            if not recent:
                stmt = stmt.where(ExpenseClaim.claim_number.ilike(q_like))
            stmt = stmt.order_by(ExpenseClaim.created_at.desc())
            stmt = stmt.limit(limit)
            for claim in self.db.execute(stmt).scalars().all():
                results.append(
                    {
                        "id": str(claim.claim_id),
                        "label": claim.claim_number,
                        "sub_label": claim.status.value,
                        "url": f"/expense/claims/{claim.claim_id}",
                    }
                )
            return results

        return results

    def refresh_status(
        self,
        organization_id: UUID,
        rrr_id: UUID,
    ) -> RemitaRRR:
        """
        Refresh RRR status from Remita API.

        Args:
            organization_id: Organization to verify access
            rrr_id: RRR record ID

        Returns:
            Updated RemitaRRR record
        """
        rrr = self.service.get_by_id(rrr_id)
        if not rrr or rrr.organization_id != organization_id:
            raise ValueError("RRR not found or access denied")

        return self.service.check_status(rrr_id)

    def mark_paid(
        self,
        organization_id: UUID,
        rrr_id: UUID,
        payment_reference: str,
        payment_channel: str = "Bank",
    ) -> RemitaRRR:
        """
        Manually mark RRR as paid.

        Args:
            organization_id: Organization to verify access
            rrr_id: RRR record ID
            payment_reference: External payment reference
            payment_channel: Payment channel

        Returns:
            Updated RemitaRRR record
        """
        rrr = self.service.get_by_id(rrr_id)
        if not rrr or rrr.organization_id != organization_id:
            raise ValueError("RRR not found or access denied")

        return self.service.mark_paid(
            rrr_id=rrr_id,
            payment_reference=payment_reference,
            payment_channel=payment_channel,
        )

    def cancel_rrr(
        self,
        organization_id: UUID,
        rrr_id: UUID,
    ) -> RemitaRRR:
        """
        Cancel a pending RRR.

        Args:
            organization_id: Organization to verify access
            rrr_id: RRR record ID

        Returns:
            Updated RemitaRRR record
        """
        rrr = self.service.get_by_id(rrr_id)
        if not rrr or rrr.organization_id != organization_id:
            raise ValueError("RRR not found or access denied")

        return self.service.cancel(rrr_id)


# Singleton for use in routes
def get_remita_web_service(db: Session) -> RemitaWebService:
    """Get RemitaWebService instance."""
    return RemitaWebService(db)
