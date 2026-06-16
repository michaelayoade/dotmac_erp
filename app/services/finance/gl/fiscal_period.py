"""
FiscalPeriodService - Fiscal period management.

Manages fiscal periods including creation, status changes, and queries.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class FiscalPeriodInput:
    """Input for creating a fiscal period."""

    fiscal_year_id: UUID
    period_number: int
    period_name: str
    start_date: date
    end_date: date
    is_adjustment_period: bool = False
    is_closing_period: bool = False


class FiscalPeriodService(ListResponseMixin):
    """
    Service for fiscal period management.

    Manages period creation, status transitions, and queries.
    """

    @staticmethod
    def create_period(
        db: Session,
        organization_id: UUID,
        input: FiscalPeriodInput,
    ) -> FiscalPeriod:
        """
        Create a new fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Period input data

        Returns:
            Created FiscalPeriod

        Raises:
            HTTPException(400): If period number already exists for year
        """
        org_id = coerce_uuid(organization_id)
        year_id = coerce_uuid(input.fiscal_year_id)

        if input.start_date > input.end_date:
            raise HTTPException(
                status_code=400,
                detail="Fiscal period start_date cannot be after end_date",
            )

        # Check for duplicate period number
        existing = db.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.fiscal_year_id == year_id,
                FiscalPeriod.period_number == input.period_number,
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Period number {input.period_number} already exists for this fiscal year",
            )

        # Normal posting periods must not overlap each other within an organization.
        # Adjustment/closing periods are excluded from this guard.
        if not input.is_adjustment_period and not input.is_closing_period:
            overlap = db.scalars(
                select(FiscalPeriod).where(
                    and_(
                        FiscalPeriod.organization_id == org_id,
                        FiscalPeriod.is_adjustment_period.is_(False),
                        FiscalPeriod.is_closing_period.is_(False),
                        FiscalPeriod.start_date <= input.end_date,
                        FiscalPeriod.end_date >= input.start_date,
                    )
                )
            ).first()
            if overlap:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Fiscal period date range overlaps an existing period "
                        f"('{overlap.period_name}')"
                    ),
                )

        period = FiscalPeriod(
            organization_id=org_id,
            fiscal_year_id=year_id,
            period_number=input.period_number,
            period_name=input.period_name,
            start_date=input.start_date,
            end_date=input.end_date,
            is_adjustment_period=input.is_adjustment_period,
            is_closing_period=input.is_closing_period,
            status=PeriodStatus.FUTURE,
        )

        db.add(period)
        db.flush()
        db.refresh(period)

        return period

    @staticmethod
    def open_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        opened_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Open a fiscal period for posting.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to open
            opened_by_user_id: User opening the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be opened
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status not in {PeriodStatus.FUTURE, PeriodStatus.SOFT_CLOSED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot open period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.OPEN

        db.flush()
        db.refresh(period)

        return period

    @staticmethod
    def close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Close a fiscal period (alias for soft_close_period).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod
        """
        return FiscalPeriodService.soft_close_period(
            db, organization_id, fiscal_period_id, closed_by_user_id
        )

    @staticmethod
    def soft_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Soft close a fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be soft closed
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status not in {PeriodStatus.OPEN, PeriodStatus.REOPENED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot soft close period with status '{period.status.value}'",
            )

        # Bank reconciliation closing-gate. Bug #13's reproduction exposed
        # that the close service had zero pre-close validation: a period
        # could be soft-closed with no bank reconciliations done for the
        # month, producing GL totals that were never tied back to bank
        # statements. ASCII-only error text (no em dashes etc.) so the
        # message round-trips cleanly through ``RedirectResponse(url=...)``
        # in the web-service catch path.
        missing = FiscalPeriodService._unreconciled_bank_accounts(db, org_id, period)
        if missing:
            account_lines = "; ".join(f"{name} ({reason})" for name, reason in missing)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot close {period.period_name}: "
                    f"{len(missing)} active bank account(s) lack a completed "
                    f"reconciliation covering this period -- "
                    f"{account_lines}. Reconcile each account "
                    f"(or reject/delete stale draft recs) before closing."
                ),
            )

        period.status = PeriodStatus.SOFT_CLOSED
        period.soft_closed_at = datetime.now(UTC)
        period.soft_closed_by_user_id = user_id

        db.flush()
        db.refresh(period)

        # Trigger aging snapshot generation (non-blocking, via Celery)
        try:
            from app.tasks.finance import auto_generate_aging_snapshots

            auto_generate_aging_snapshots.delay(
                str(org_id), str(period_id), str(user_id)
            )
        except Exception:
            logger.exception(
                "Failed to queue aging snapshot generation for period %s",
                period_id,
            )

        return period

    @staticmethod
    def _unreconciled_bank_accounts(
        db: Session,
        organization_id: UUID,
        period: FiscalPeriod,
    ) -> list[tuple[str, str]]:
        """Return active bank accounts that lack a covering reconciliation.

        Returns a list of ``(account_name, reason)`` tuples. An empty list
        means every active account is reconciled for the period and close
        may proceed.

        A reconciliation is considered "covering" iff:
        - ``period_start <= period.start_date``
        - ``period_end >= period.end_date``
        - ``status`` in ``{pending_review, approved}`` (draft/rejected are
          not considered complete)

        Importing inside the function avoids dragging the banking module
        into the GL service's import graph (would create a cycle —
        banking already imports from GL).
        """
        from app.models.finance.banking.bank_account import (
            BankAccount,
            BankAccountStatus,
        )
        from app.models.finance.banking.bank_reconciliation import (
            BankReconciliation,
            ReconciliationStatus,
        )

        active_accounts_stmt = select(BankAccount).where(
            BankAccount.organization_id == organization_id,
            BankAccount.status == BankAccountStatus.active,
        )
        active_accounts = list(db.scalars(active_accounts_stmt).all())

        accepted = {
            ReconciliationStatus.pending_review,
            ReconciliationStatus.approved,
        }
        missing: list[tuple[str, str]] = []

        for account in active_accounts:
            cover_stmt = select(BankReconciliation).where(
                BankReconciliation.organization_id == organization_id,
                BankReconciliation.bank_account_id == account.bank_account_id,
                BankReconciliation.period_start <= period.start_date,
                BankReconciliation.period_end >= period.end_date,
            )
            recs = list(db.scalars(cover_stmt).all())

            if not recs:
                missing.append((account.account_name, "no reconciliation"))
                continue

            statuses = {r.status for r in recs}
            if not (statuses & accepted):
                status_names = sorted(s.value for s in statuses)
                missing.append(
                    (
                        account.account_name,
                        f"only {','.join(status_names)} recs exist",
                    )
                )

        return missing

    @staticmethod
    def hard_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Hard close a fiscal period (permanent).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be hard closed
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status != PeriodStatus.SOFT_CLOSED:
            raise HTTPException(
                status_code=400, detail="Period must be soft closed before hard closing"
            )

        period.status = PeriodStatus.HARD_CLOSED
        period.hard_closed_at = datetime.now(UTC)
        period.hard_closed_by_user_id = user_id

        db.flush()
        db.refresh(period)

        return period

    @staticmethod
    def reopen_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        reopened_by_user_id: UUID,  # noqa: ARG004 — part of public API
        reopen_session_id: UUID,
    ) -> FiscalPeriod:
        """
        Reopen a soft-closed period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to reopen
            reopened_by_user_id: User reopening the period
            reopen_session_id: Session ID for tracking

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be reopened
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        session_id = coerce_uuid(reopen_session_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status == PeriodStatus.HARD_CLOSED:
            raise HTTPException(
                status_code=400, detail="Cannot reopen a hard-closed period"
            )

        if period.status not in {PeriodStatus.SOFT_CLOSED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reopen period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.REOPENED
        period.reopen_count += 1
        period.last_reopen_session_id = session_id

        db.flush()
        db.refresh(period)

        return period

    @staticmethod
    def get(
        db: Session,
        fiscal_period_id: str,
        organization_id: UUID | None = None,
    ) -> FiscalPeriod:
        """
        Get a fiscal period by ID.

        Args:
            db: Database session
            fiscal_period_id: Period ID

        Returns:
            FiscalPeriod

        Raises:
            HTTPException(404): If not found
        """
        period = db.get(FiscalPeriod, coerce_uuid(fiscal_period_id))
        if not period:
            raise HTTPException(status_code=404, detail="Fiscal period not found")
        if organization_id is not None and period.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Fiscal period not found")
        return period

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_year_id: str | None = None,
        status: PeriodStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[FiscalPeriod]:
        """
        List fiscal periods with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            fiscal_year_id: Filter by fiscal year
            status: Filter by status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of FiscalPeriod objects
        """
        query = select(FiscalPeriod)

        if organization_id:
            query = query.where(
                FiscalPeriod.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_year_id:
            query = query.where(
                FiscalPeriod.fiscal_year_id == coerce_uuid(fiscal_year_id)
            )

        if status:
            query = query.where(FiscalPeriod.status == status)

        query = query.order_by(FiscalPeriod.period_number)
        return list(db.scalars(query.limit(limit).offset(offset)).all())


# Module-level singleton instance
fiscal_period_service = FiscalPeriodService()
