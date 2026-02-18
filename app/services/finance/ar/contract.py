"""
ContractService - IFRS 15 Revenue Recognition Contract Management.

Manages contracts, performance obligations, and revenue recognition events
in accordance with IFRS 15.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ar.contract import Contract, ContractStatus, ContractType
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.performance_obligation import (
    PerformanceObligation,
    SatisfactionPattern,
)
from app.models.finance.ar.revenue_recognition_event import RevenueRecognitionEvent
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class PerformanceObligationInput:
    """Input for creating a performance obligation."""

    description: str
    satisfaction_pattern: SatisfactionPattern
    standalone_selling_price: Decimal
    ssp_determination_method: str
    revenue_account_id: UUID
    is_distinct: bool = True
    over_time_method: str | None = None
    progress_measure: str | None = None
    expected_completion_date: date | None = None
    contract_asset_account_id: UUID | None = None
    contract_liability_account_id: UUID | None = None


@dataclass
class ContractInput:
    """Input for creating an IFRS 15 contract."""

    customer_id: UUID
    contract_name: str
    contract_type: ContractType
    start_date: date
    currency_code: str
    obligations: list[PerformanceObligationInput] = field(default_factory=list)
    end_date: date | None = None
    total_contract_value: Decimal | None = None
    is_enforceable: bool = True
    has_commercial_substance: bool = True
    collectability_assessment: str = "PROBABLE"
    significant_financing: bool = False
    financing_rate: Decimal | None = None
    variable_consideration: dict[str, Any] | None = None
    noncash_consideration: dict[str, Any] | None = None


@dataclass
class ProgressUpdateInput:
    """Input for updating progress on a performance obligation."""

    obligation_id: UUID
    event_date: date
    progress_percentage: Decimal
    measurement_details: dict[str, Any] | None = None


class ContractService(ListResponseMixin):
    """
    Service for IFRS 15 contract and revenue recognition management.

    Implements the five-step revenue recognition model:
    1. Identify the contract
    2. Identify performance obligations
    3. Determine the transaction price
    4. Allocate transaction price
    5. Recognize revenue when obligations are satisfied
    """

    @staticmethod
    def create_contract(
        db: Session,
        organization_id: UUID,
        input: ContractInput,
        created_by_user_id: UUID,
    ) -> Contract:
        """
        Create a new IFRS 15 contract with performance obligations.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Contract input data
            created_by_user_id: User creating the contract

        Returns:
            Created Contract

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If customer not found
        """
        org_id = coerce_uuid(organization_id)
        customer_id = coerce_uuid(input.customer_id)

        # Validate customer exists
        customer = db.scalars(
            select(Customer).where(
                Customer.customer_id == customer_id,
                Customer.organization_id == org_id,
            )
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Validate IFRS 15 criteria
        if not input.is_enforceable:
            raise HTTPException(
                status_code=400,
                detail="Contract must be enforceable for IFRS 15 recognition",
            )

        if not input.has_commercial_substance:
            raise HTTPException(
                status_code=400,
                detail="Contract must have commercial substance for IFRS 15 recognition",
            )

        if input.collectability_assessment not in ["PROBABLE", "HIGHLY_PROBABLE"]:
            raise HTTPException(
                status_code=400,
                detail="Collection must be probable for IFRS 15 recognition",
            )

        # Generate contract number
        contract_count = (
            db.scalar(
                select(func.count(Contract.contract_id)).where(
                    Contract.organization_id == org_id
                )
            )
            or 0
        )
        contract_number = f"CTR-{contract_count + 1:06d}"

        # Create contract
        contract = Contract(
            organization_id=org_id,
            customer_id=customer_id,
            contract_number=contract_number,
            contract_name=input.contract_name,
            contract_type=input.contract_type,
            start_date=input.start_date,
            end_date=input.end_date,
            total_contract_value=input.total_contract_value,
            currency_code=input.currency_code,
            status=ContractStatus.DRAFT,
            is_enforceable=input.is_enforceable,
            has_commercial_substance=input.has_commercial_substance,
            collectability_assessment=input.collectability_assessment,
            significant_financing=input.significant_financing,
            financing_rate=input.financing_rate,
            variable_consideration=input.variable_consideration,
            noncash_consideration=input.noncash_consideration,
        )
        db.add(contract)
        db.flush()

        # Create performance obligations
        if input.obligations:
            total_ssp = sum(o.standalone_selling_price for o in input.obligations)
            transaction_price = input.total_contract_value or total_ssp

            for idx, ob_input in enumerate(input.obligations, start=1):
                # Allocate transaction price based on relative SSP
                allocation_ratio = ob_input.standalone_selling_price / total_ssp
                allocated_price = transaction_price * allocation_ratio

                obligation = PerformanceObligation(
                    contract_id=contract.contract_id,
                    organization_id=org_id,
                    obligation_number=idx,
                    description=ob_input.description,
                    is_distinct=ob_input.is_distinct,
                    satisfaction_pattern=ob_input.satisfaction_pattern,
                    over_time_method=ob_input.over_time_method,
                    progress_measure=ob_input.progress_measure,
                    standalone_selling_price=ob_input.standalone_selling_price,
                    ssp_determination_method=ob_input.ssp_determination_method,
                    allocated_transaction_price=allocated_price,
                    expected_completion_date=ob_input.expected_completion_date,
                    revenue_account_id=ob_input.revenue_account_id,
                    contract_asset_account_id=ob_input.contract_asset_account_id,
                    contract_liability_account_id=ob_input.contract_liability_account_id,
                    status="NOT_STARTED",
                )
                db.add(obligation)

        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def activate_contract(
        db: Session,
        organization_id: UUID,
        contract_id: UUID,
        approved_by_user_id: UUID,
    ) -> Contract:
        """
        Activate a contract for revenue recognition.

        Args:
            db: Database session
            organization_id: Organization scope
            contract_id: Contract to activate
            approved_by_user_id: User approving

        Returns:
            Updated Contract
        """
        org_id = coerce_uuid(organization_id)
        contract_id = coerce_uuid(contract_id)

        contract = db.scalars(
            select(Contract).where(
                Contract.contract_id == contract_id,
                Contract.organization_id == org_id,
            )
        ).first()

        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        if contract.status != ContractStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot activate contract in {contract.status.value} status",
            )

        # Verify contract has performance obligations
        obligation_count = (
            db.scalar(
                select(func.count(PerformanceObligation.obligation_id)).where(
                    PerformanceObligation.contract_id == contract_id
                )
            )
            or 0
        )

        if obligation_count == 0:
            raise HTTPException(
                status_code=400,
                detail="Contract must have at least one performance obligation",
            )

        contract.status = ContractStatus.ACTIVE
        contract.approval_status = "APPROVED"

        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def add_performance_obligation(
        db: Session,
        organization_id: UUID,
        contract_id: UUID,
        input: PerformanceObligationInput,
    ) -> PerformanceObligation:
        """
        Add a performance obligation to an existing contract.

        Args:
            db: Database session
            organization_id: Organization scope
            contract_id: Contract to add obligation to
            input: Obligation input data

        Returns:
            Created PerformanceObligation
        """
        org_id = coerce_uuid(organization_id)
        contract_id = coerce_uuid(contract_id)

        contract = db.scalars(
            select(Contract).where(
                Contract.contract_id == contract_id,
                Contract.organization_id == org_id,
            )
        ).first()

        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        if contract.status not in [ContractStatus.DRAFT, ContractStatus.ACTIVE]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot add obligations to contract in {contract.status.value} status",
            )

        # Get next obligation number
        max_number = (
            db.scalar(
                select(func.count(PerformanceObligation.obligation_id)).where(
                    PerformanceObligation.contract_id == contract_id
                )
            )
            or 0
        )

        obligation = PerformanceObligation(
            contract_id=contract_id,
            organization_id=org_id,
            obligation_number=max_number + 1,
            description=input.description,
            is_distinct=input.is_distinct,
            satisfaction_pattern=input.satisfaction_pattern,
            over_time_method=input.over_time_method,
            progress_measure=input.progress_measure,
            standalone_selling_price=input.standalone_selling_price,
            ssp_determination_method=input.ssp_determination_method,
            allocated_transaction_price=input.standalone_selling_price,  # Initial allocation
            expected_completion_date=input.expected_completion_date,
            revenue_account_id=input.revenue_account_id,
            contract_asset_account_id=input.contract_asset_account_id,
            contract_liability_account_id=input.contract_liability_account_id,
            status="NOT_STARTED",
        )
        db.add(obligation)
        db.commit()
        db.refresh(obligation)

        # Reallocate transaction price
        ContractService.reallocate_transaction_price(db, org_id, contract_id)

        return obligation

    @staticmethod
    def reallocate_transaction_price(
        db: Session,
        organization_id: UUID,
        contract_id: UUID,
    ) -> None:
        """
        Reallocate transaction price across all performance obligations.

        Uses relative standalone selling price method.

        Args:
            db: Database session
            organization_id: Organization scope
            contract_id: Contract to reallocate
        """
        org_id = coerce_uuid(organization_id)
        contract_id = coerce_uuid(contract_id)

        contract = db.scalars(
            select(Contract).where(
                Contract.contract_id == contract_id,
                Contract.organization_id == org_id,
            )
        ).first()

        if not contract:
            return

        obligations = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.contract_id == contract_id
            )
        ).all()

        if not obligations:
            return

        total_ssp = sum(o.standalone_selling_price for o in obligations)
        transaction_price = contract.total_contract_value or total_ssp

        for obligation in obligations:
            allocation_ratio = obligation.standalone_selling_price / total_ssp
            obligation.allocated_transaction_price = (
                transaction_price * allocation_ratio
            )

        db.commit()

    @staticmethod
    def update_progress(
        db: Session,
        organization_id: UUID,
        input: ProgressUpdateInput,
        posted_by_user_id: UUID,
    ) -> RevenueRecognitionEvent:
        """
        Update progress on an over-time performance obligation.

        Records a revenue recognition event and calculates revenue to recognize.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Progress update input
            posted_by_user_id: User recording the update

        Returns:
            Created RevenueRecognitionEvent
        """
        org_id = coerce_uuid(organization_id)
        obligation_id = coerce_uuid(input.obligation_id)

        obligation = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.obligation_id == obligation_id,
                PerformanceObligation.organization_id == org_id,
            )
        ).first()

        if not obligation:
            raise HTTPException(
                status_code=404, detail="Performance obligation not found"
            )

        if obligation.satisfaction_pattern != SatisfactionPattern.OVER_TIME:
            raise HTTPException(
                status_code=400,
                detail="Progress updates only apply to over-time obligations",
            )

        if obligation.status == "SATISFIED":
            raise HTTPException(
                status_code=400, detail="Obligation is already satisfied"
            )

        # Calculate revenue to recognize
        cumulative_revenue = obligation.allocated_transaction_price * (
            input.progress_percentage / Decimal("100")
        )
        amount_to_recognize = cumulative_revenue - obligation.total_satisfied_amount

        if amount_to_recognize < 0:
            raise HTTPException(
                status_code=400, detail="Progress percentage cannot decrease"
            )

        # Create recognition event
        event = RevenueRecognitionEvent(
            obligation_id=obligation_id,
            organization_id=org_id,
            event_date=input.event_date,
            event_type="PROGRESS_UPDATE",
            progress_percentage=input.progress_percentage,
            amount_recognized=amount_to_recognize,
            cumulative_recognized=cumulative_revenue,
            measurement_details=input.measurement_details,
        )
        db.add(event)

        # Update obligation
        obligation.satisfaction_percentage = input.progress_percentage
        obligation.total_satisfied_amount = cumulative_revenue
        obligation.status = "IN_PROGRESS"

        if input.progress_percentage >= Decimal("100"):
            obligation.status = "SATISFIED"
            obligation.actual_completion_date = input.event_date

        db.commit()
        db.refresh(event)

        return event

    @staticmethod
    def satisfy_point_in_time(
        db: Session,
        organization_id: UUID,
        obligation_id: UUID,
        satisfaction_date: date,
        posted_by_user_id: UUID,
    ) -> RevenueRecognitionEvent:
        """
        Satisfy a point-in-time performance obligation.

        Recognizes the full allocated transaction price.

        Args:
            db: Database session
            organization_id: Organization scope
            obligation_id: Obligation to satisfy
            satisfaction_date: Date of satisfaction
            posted_by_user_id: User recording

        Returns:
            Created RevenueRecognitionEvent
        """
        org_id = coerce_uuid(organization_id)
        obligation_id = coerce_uuid(obligation_id)

        obligation = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.obligation_id == obligation_id,
                PerformanceObligation.organization_id == org_id,
            )
        ).first()

        if not obligation:
            raise HTTPException(
                status_code=404, detail="Performance obligation not found"
            )

        if obligation.satisfaction_pattern != SatisfactionPattern.POINT_IN_TIME:
            raise HTTPException(
                status_code=400, detail="Use update_progress for over-time obligations"
            )

        if obligation.status == "SATISFIED":
            raise HTTPException(
                status_code=400, detail="Obligation is already satisfied"
            )

        # Create recognition event for full amount
        amount_to_recognize = (
            obligation.allocated_transaction_price - obligation.total_satisfied_amount
        )

        event = RevenueRecognitionEvent(
            obligation_id=obligation_id,
            organization_id=org_id,
            event_date=satisfaction_date,
            event_type="SATISFACTION",
            progress_percentage=Decimal("100"),
            amount_recognized=amount_to_recognize,
            cumulative_recognized=obligation.allocated_transaction_price,
        )
        db.add(event)

        # Update obligation
        obligation.satisfaction_percentage = Decimal("100")
        obligation.total_satisfied_amount = obligation.allocated_transaction_price
        obligation.status = "SATISFIED"
        obligation.actual_completion_date = satisfaction_date

        db.commit()
        db.refresh(event)

        return event

    @staticmethod
    def modify_contract(
        db: Session,
        organization_id: UUID,
        contract_id: UUID,
        modification_date: date,
        new_transaction_price: Decimal | None = None,
        modification_type: str = "PROSPECTIVE",
        modification_details: dict[str, Any] | None = None,
    ) -> Contract:
        """
        Record a contract modification per IFRS 15.

        Args:
            db: Database session
            organization_id: Organization scope
            contract_id: Contract to modify
            modification_date: Date of modification
            new_transaction_price: New total transaction price
            modification_type: PROSPECTIVE, CUMULATIVE_CATCHUP, or SEPARATE_CONTRACT
            modification_details: Additional modification data

        Returns:
            Updated Contract
        """
        org_id = coerce_uuid(organization_id)
        contract_id = coerce_uuid(contract_id)

        contract = db.scalars(
            select(Contract).where(
                Contract.contract_id == contract_id,
                Contract.organization_id == org_id,
            )
        ).first()

        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        if contract.status not in [ContractStatus.ACTIVE]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot modify contract in {contract.status.value} status",
            )

        # Record modification history
        history = contract.modification_history or {"modifications": []}
        history["modifications"].append(
            {
                "date": modification_date.isoformat(),
                "type": modification_type,
                "previous_value": str(contract.total_contract_value),
                "new_value": str(new_transaction_price)
                if new_transaction_price
                else None,
                "details": modification_details,
            }
        )
        contract.modification_history = history

        # Update transaction price
        if new_transaction_price:
            contract.total_contract_value = new_transaction_price

            if modification_type == "PROSPECTIVE":
                # Reallocate remaining consideration to unsatisfied obligations
                ContractService._reallocate_prospectively(db, contract)
            elif modification_type == "CUMULATIVE_CATCHUP":
                # Adjust cumulative revenue and reallocate
                ContractService._reallocate_cumulative_catchup(db, contract)

        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def _reallocate_prospectively(db: Session, contract: Contract) -> None:
        """Reallocate remaining consideration prospectively."""
        obligations = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.contract_id == contract.contract_id,
                PerformanceObligation.status != "SATISFIED",
            )
        ).all()

        if not obligations:
            return

        # Calculate remaining consideration
        satisfied = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.contract_id == contract.contract_id,
                PerformanceObligation.status == "SATISFIED",
            )
        ).all()

        satisfied_amount = sum(
            (o.allocated_transaction_price or Decimal("0") for o in satisfied),
            Decimal("0"),
        )
        total_value = contract.total_contract_value or Decimal("0")
        remaining_price = total_value - satisfied_amount

        # Reallocate to unsatisfied obligations
        total_ssp = sum((o.standalone_selling_price for o in obligations), Decimal("0"))
        for obligation in obligations:
            allocation_ratio = obligation.standalone_selling_price / total_ssp
            obligation.allocated_transaction_price = remaining_price * allocation_ratio

    @staticmethod
    def _reallocate_cumulative_catchup(db: Session, contract: Contract) -> None:
        """Reallocate with cumulative catch-up adjustment."""
        # Get all obligations
        obligations = db.scalars(
            select(PerformanceObligation).where(
                PerformanceObligation.contract_id == contract.contract_id
            )
        ).all()

        total_ssp = sum((o.standalone_selling_price for o in obligations), Decimal("0"))

        for obligation in obligations:
            allocation_ratio = obligation.standalone_selling_price / total_ssp
            total_value = contract.total_contract_value or Decimal("0")
            new_allocated = total_value * allocation_ratio

            # Calculate cumulative catch-up
            expected_recognized = new_allocated * (
                obligation.satisfaction_percentage / Decimal("100")
            )
            adjustment = expected_recognized - obligation.total_satisfied_amount

            if adjustment != 0:
                # Create catch-up event
                event = RevenueRecognitionEvent(
                    obligation_id=obligation.obligation_id,
                    organization_id=contract.organization_id,
                    event_date=date.today(),
                    event_type="MODIFICATION",
                    progress_percentage=obligation.satisfaction_percentage,
                    amount_recognized=adjustment,
                    cumulative_recognized=expected_recognized,
                    measurement_details={"modification": "cumulative_catchup"},
                )
                db.add(event)
                obligation.total_satisfied_amount = expected_recognized

            obligation.allocated_transaction_price = new_allocated

    @staticmethod
    def complete_contract(
        db: Session,
        organization_id: UUID,
        contract_id: UUID,
    ) -> Contract:
        """
        Mark a contract as completed.

        Args:
            db: Database session
            organization_id: Organization scope
            contract_id: Contract to complete

        Returns:
            Updated Contract
        """
        org_id = coerce_uuid(organization_id)
        contract_id = coerce_uuid(contract_id)

        contract = db.scalars(
            select(Contract).where(
                Contract.contract_id == contract_id,
                Contract.organization_id == org_id,
            )
        ).first()

        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        # Check all obligations are satisfied
        unsatisfied = (
            db.scalar(
                select(func.count(PerformanceObligation.obligation_id)).where(
                    PerformanceObligation.contract_id == contract_id,
                    PerformanceObligation.status != "SATISFIED",
                )
            )
            or 0
        )

        if unsatisfied > 0:
            raise HTTPException(
                status_code=400,
                detail=f"{unsatisfied} performance obligations are not yet satisfied",
            )

        contract.status = ContractStatus.COMPLETED
        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def get(
        db: Session,
        contract_id: str,
        organization_id: UUID | None = None,
    ) -> Contract | None:
        """Get a contract by ID."""
        contract = db.scalars(
            select(Contract).where(Contract.contract_id == coerce_uuid(contract_id))
        ).first()
        if not contract:
            return None
        if organization_id is not None and contract.organization_id != coerce_uuid(
            organization_id
        ):
            return None
        return contract

    @staticmethod
    def get_by_number(
        db: Session,
        organization_id: UUID,
        contract_number: str,
    ) -> Contract | None:
        """Get a contract by number."""
        return db.scalars(
            select(Contract).where(
                Contract.organization_id == coerce_uuid(organization_id),
                Contract.contract_number == contract_number,
            )
        ).first()

    @staticmethod
    def get_obligations(
        db: Session,
        contract_id: str,
    ) -> builtins.list[PerformanceObligation]:
        """Get all performance obligations for a contract."""
        return db.scalars(
            select(PerformanceObligation)
            .where(PerformanceObligation.contract_id == coerce_uuid(contract_id))
            .order_by(PerformanceObligation.obligation_number)
        ).all()

    @staticmethod
    def get_recognition_events(
        db: Session,
        obligation_id: str,
    ) -> builtins.list[RevenueRecognitionEvent]:
        """Get all recognition events for an obligation."""
        return db.scalars(
            select(RevenueRecognitionEvent)
            .where(RevenueRecognitionEvent.obligation_id == coerce_uuid(obligation_id))
            .order_by(RevenueRecognitionEvent.event_date)
        ).all()

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        customer_id: str | None = None,
        status: ContractStatus | None = None,
        contract_type: ContractType | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[Contract]:
        """
        List contracts with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            customer_id: Filter by customer
            status: Filter by status
            contract_type: Filter by type
            from_date: Filter by start date from
            to_date: Filter by start date to
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of Contract objects
        """
        stmt = select(Contract)

        if organization_id:
            stmt = stmt.where(Contract.organization_id == coerce_uuid(organization_id))

        if customer_id:
            stmt = stmt.where(Contract.customer_id == coerce_uuid(customer_id))

        if status:
            stmt = stmt.where(Contract.status == status)

        if contract_type:
            stmt = stmt.where(Contract.contract_type == contract_type)

        if from_date:
            stmt = stmt.where(Contract.start_date >= from_date)

        if to_date:
            stmt = stmt.where(Contract.start_date <= to_date)

        return db.scalars(
            stmt.order_by(Contract.start_date.desc()).offset(offset).limit(limit)
        ).all()


# Module-level instance
contract_service = ContractService()
