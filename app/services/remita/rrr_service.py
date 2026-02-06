"""
Remita RRR Service.

Business logic for generating, tracking, and managing RRRs (Remita Retrieval References).
Used by all modules needing to make government payments: Payroll, Finance, Procurement.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.remita import RemitaRRR, RRRStatus
from app.services.remita.client import RemitaClient, RemitaError

logger = logging.getLogger(__name__)


class RemitaRRRService:
    """
    Service for managing Remita RRRs.

    This is a generic service used by various modules to generate RRRs
    for government payments. Each module provides its own biller/service
    details when calling generate_rrr().
    """

    def __init__(self, db: Session):
        self.db = db
        self._client: Optional[RemitaClient] = None

    @property
    def client(self) -> RemitaClient:
        """Lazy-load Remita client."""
        if self._client is None:
            # Validate configuration
            if not settings.remita_merchant_id or not settings.remita_api_key:
                raise ValueError(
                    "Remita is not configured. Please set REMITA_MERCHANT_ID and "
                    "REMITA_API_KEY environment variables."
                )
            self._client = RemitaClient(
                merchant_id=settings.remita_merchant_id,
                api_key=settings.remita_api_key,
                is_live=settings.remita_is_live,
            )
        return self._client

    def is_configured(self) -> bool:
        """Check if Remita credentials are configured."""
        return bool(settings.remita_merchant_id and settings.remita_api_key)

    def get_by_id(self, rrr_id: UUID) -> Optional[RemitaRRR]:
        """Get a RRR record by ID."""
        return self.db.get(RemitaRRR, rrr_id)

    def get_by_rrr(self, rrr: str) -> Optional[RemitaRRR]:
        """Get a RRR record by the RRR number."""
        stmt = select(RemitaRRR).where(RemitaRRR.rrr == rrr)
        return self.db.scalar(stmt)

    def get_for_source(
        self,
        organization_id: UUID,
        source_type: str,
        source_id: UUID,
        status: Optional[RRRStatus] = None,
    ) -> Optional[RemitaRRR]:
        """Get the most recent RRR linked to a source entity."""
        stmt = select(RemitaRRR).where(
            RemitaRRR.organization_id == organization_id,
            RemitaRRR.source_type == source_type,
            RemitaRRR.source_id == source_id,
        )
        if status:
            stmt = stmt.where(RemitaRRR.status == status)
        stmt = stmt.order_by(RemitaRRR.created_at.desc())
        return self.db.scalar(stmt)

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
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        created_by_id: Optional[UUID] = None,
    ) -> RemitaRRR:
        """
        Generate RRR via Remita API and save to database.

        Args:
            organization_id: Organization making the payment
            biller_id: Biller code (e.g., "FIRS", "FMBN", "BPP")
            biller_name: Full biller name (e.g., "Federal Inland Revenue Service")
            service_type_id: Remita service type code
            service_name: Human-readable service name (e.g., "PAYE Tax")
            amount: Payment amount
            payer_name: Name of payer (company or individual)
            payer_email: Email for payment notifications
            payer_phone: Optional phone number
            description: Payment description
            source_type: Optional source type for linking (e.g., "payroll_paye")
            source_id: Optional source entity ID
            created_by_id: User ID who initiated the RRR generation

        Returns:
            RemitaRRR record with generated RRR

        Raises:
            ValueError: If validation fails
            RemitaError: If API call fails
        """
        # Validate amount
        if amount <= 0:
            raise ValueError("Amount must be positive")

        # Generate unique order ID
        order_id = f"{organization_id}-{uuid4()}"

        logger.info(
            f"Generating RRR: biller={biller_id}, service={service_type_id}, "
            f"amount={amount}, order={order_id}"
        )

        # Call Remita API
        response = self.client.generate_rrr(
            service_type_id=service_type_id,
            amount=amount,
            order_id=order_id,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            description=description,
        )

        # Save to database
        rrr_record = RemitaRRR(
            organization_id=organization_id,
            rrr=response.rrr,
            order_id=order_id,
            amount=amount,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            biller_id=biller_id,
            biller_name=biller_name,
            service_type_id=service_type_id,
            service_name=service_name,
            description=description,
            source_type=source_type,
            source_id=source_id,
            status=RRRStatus.pending,
            api_response=response.raw_response,
            created_by_id=created_by_id,
        )

        self.db.add(rrr_record)
        self.db.flush()

        logger.info(f"RRR generated and saved: {response.rrr} (ID: {rrr_record.id})")
        return rrr_record

    def check_status(self, rrr_id: UUID) -> RemitaRRR:
        """
        Check and update RRR payment status.

        Args:
            rrr_id: ID of the RRR record to check

        Returns:
            Updated RemitaRRR record

        Raises:
            ValueError: If RRR not found
            RemitaError: If API call fails
        """
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        if not rrr_record:
            raise ValueError(f"RRR {rrr_id} not found")

        logger.info(f"Checking status for RRR: {rrr_record.rrr}")

        response = self.client.check_status(rrr_record.rrr)

        # Update record with response
        rrr_record.last_status_check = datetime.now(timezone.utc)
        rrr_record.last_status_response = response.raw_response

        # Map Remita status codes to our status
        # 00 = Successful payment, 01 = Pending, 02 = Failed
        status_code = response.status
        was_pending = rrr_record.status == RRRStatus.pending

        if status_code == "00":
            rrr_record.status = RRRStatus.paid
            rrr_record.paid_at = datetime.now(timezone.utc)
            if response.payment_date:
                try:
                    # Try to parse Remita's date format
                    rrr_record.paid_at = datetime.fromisoformat(
                        response.payment_date.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass
            if response.transaction_id:
                rrr_record.payment_reference = response.transaction_id
            if response.debitted_account:
                rrr_record.payment_channel = f"Bank: {response.debitted_account}"
            logger.info(f"RRR {rrr_record.rrr} marked as paid")

            # Trigger source update if status changed to paid
            if was_pending:
                self._handle_paid(rrr_record)
        elif status_code == "01":
            # Still pending
            logger.info(f"RRR {rrr_record.rrr} still pending")
        elif status_code == "02":
            rrr_record.status = RRRStatus.failed
            logger.warning(f"RRR {rrr_record.rrr} payment failed")

        self.db.flush()
        return rrr_record

    def mark_paid(
        self,
        rrr_id: UUID,
        payment_reference: str,
        payment_channel: str = "Bank",
        paid_at: Optional[datetime] = None,
    ) -> RemitaRRR:
        """
        Manually mark RRR as paid.

        Used when payment is confirmed outside the API (e.g., from bank statement).

        Args:
            rrr_id: ID of the RRR record
            payment_reference: External payment reference
            payment_channel: How payment was made (e.g., "Bank", "Card")
            paid_at: When payment was made (defaults to now)

        Returns:
            Updated RemitaRRR record

        Raises:
            ValueError: If RRR not found or already paid
        """
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        if not rrr_record:
            raise ValueError(f"RRR {rrr_id} not found")

        if rrr_record.status == RRRStatus.paid:
            raise ValueError(f"RRR {rrr_record.rrr} is already marked as paid")

        rrr_record.status = RRRStatus.paid
        rrr_record.paid_at = paid_at or datetime.now(timezone.utc)
        rrr_record.payment_reference = payment_reference
        rrr_record.payment_channel = payment_channel

        self.db.flush()
        logger.info(
            f"RRR {rrr_record.rrr} manually marked as paid: {payment_reference}"
        )

        # Trigger source update
        self._handle_paid(rrr_record)

        return rrr_record

    def _handle_paid(self, rrr_record: RemitaRRR) -> None:
        """
        Handle source entity update when RRR is paid.

        Called after RRR status changes to paid (via API check or manual marking).
        """
        if not rrr_record.source_type or not rrr_record.source_id:
            return

        try:
            from app.services.remita.source_handler import get_source_handler

            handler = get_source_handler(self.db)
            result = handler.handle_rrr_paid(rrr_record)
            if result:
                logger.info(
                    f"Source update for RRR {rrr_record.rrr}: {result.get('action', 'completed')}"
                )
        except Exception as e:
            # Log but don't fail the RRR update
            logger.exception(f"Failed to update source for RRR {rrr_record.rrr}: {e}")

    def mark_expired(self, rrr_id: UUID) -> RemitaRRR:
        """
        Mark RRR as expired.

        Args:
            rrr_id: ID of the RRR record

        Returns:
            Updated RemitaRRR record
        """
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        if not rrr_record:
            raise ValueError(f"RRR {rrr_id} not found")

        if rrr_record.status != RRRStatus.pending:
            raise ValueError(f"Can only expire pending RRRs, got {rrr_record.status}")

        rrr_record.status = RRRStatus.expired
        self.db.flush()

        logger.info(f"RRR {rrr_record.rrr} marked as expired")
        return rrr_record

    def cancel(self, rrr_id: UUID) -> RemitaRRR:
        """
        Cancel a pending RRR.

        Args:
            rrr_id: ID of the RRR record

        Returns:
            Updated RemitaRRR record
        """
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        if not rrr_record:
            raise ValueError(f"RRR {rrr_id} not found")

        if rrr_record.status != RRRStatus.pending:
            raise ValueError(f"Can only cancel pending RRRs, got {rrr_record.status}")

        rrr_record.status = RRRStatus.cancelled
        self.db.flush()

        logger.info(f"RRR {rrr_record.rrr} cancelled")
        return rrr_record

    def list_rrrs(
        self,
        organization_id: UUID,
        status: Optional[RRRStatus] = None,
        source_type: Optional[str] = None,
        biller_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RemitaRRR]:
        """
        List RRRs for an organization with optional filters.

        Args:
            organization_id: Organization to filter by
            status: Optional status filter
            source_type: Optional source type filter (e.g., "payroll_paye")
            biller_id: Optional biller filter (e.g., "FIRS")
            limit: Maximum records to return
            offset: Number of records to skip

        Returns:
            List of RemitaRRR records
        """
        stmt = select(RemitaRRR).where(RemitaRRR.organization_id == organization_id)

        if status:
            stmt = stmt.where(RemitaRRR.status == status)
        if source_type:
            stmt = stmt.where(RemitaRRR.source_type == source_type)
        if biller_id:
            stmt = stmt.where(RemitaRRR.biller_id == biller_id)

        stmt = stmt.order_by(RemitaRRR.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)

        return list(self.db.scalars(stmt).all())

    def list_pending(self, organization_id: UUID) -> list[RemitaRRR]:
        """Get all pending RRRs for an organization."""
        return self.list_rrrs(organization_id, status=RRRStatus.pending)

    def get_by_source(
        self,
        organization_id: UUID,
        source_type: str,
        source_id: UUID,
    ) -> list[RemitaRRR]:
        """
        Get all RRRs linked to a specific source entity.

        Args:
            organization_id: Organization to filter by
            source_type: Type of source (e.g., "payroll_paye")
            source_id: ID of the source entity

        Returns:
            List of RemitaRRR records linked to the source
        """
        stmt = (
            select(RemitaRRR)
            .where(
                RemitaRRR.organization_id == organization_id,
                RemitaRRR.source_type == source_type,
                RemitaRRR.source_id == source_id,
            )
            .order_by(RemitaRRR.created_at.desc())
        )

        return list(self.db.scalars(stmt).all())

    def get_total_pending_amount(self, organization_id: UUID) -> Decimal:
        """Get total amount of pending RRRs for an organization."""
        rrrs = self.list_pending(organization_id)
        return sum((r.amount for r in rrrs), Decimal("0"))

    def refresh_pending_statuses(self, organization_id: UUID) -> dict:
        """
        Check status of all pending RRRs for an organization.

        Returns:
            Dict with counts of updated statuses
        """
        pending = self.list_pending(organization_id)
        results = {"checked": 0, "paid": 0, "failed": 0, "errors": 0}

        for rrr in pending:
            try:
                updated = self.check_status(rrr.id)
                results["checked"] += 1
                if updated.status == RRRStatus.paid:
                    results["paid"] += 1
                elif updated.status == RRRStatus.failed:
                    results["failed"] += 1
            except RemitaError as e:
                logger.error(f"Failed to check status for RRR {rrr.rrr}: {e}")
                results["errors"] += 1

        logger.info(
            f"Refreshed {results['checked']} pending RRRs: "
            f"{results['paid']} paid, {results['failed']} failed, {results['errors']} errors"
        )
        return results
