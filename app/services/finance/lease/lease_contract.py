"""
LeaseContractService - IFRS 16 lease contract management.

Manages lease contract lifecycle, classification, and initial recognition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.finance.lease.lease_asset import LeaseAsset
from app.models.finance.lease.lease_contract import (
    LeaseClassification,
    LeaseContract,
    LeaseStatus,
)
from app.models.finance.lease.lease_liability import LeaseLiability
from app.services.common import coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class LeaseContractInput:
    """Input for creating a lease contract."""

    lease_name: str
    lessor_name: str
    classification: LeaseClassification
    commencement_date: date
    end_date: date
    currency_code: str
    payment_frequency: str
    base_payment_amount: Decimal
    incremental_borrowing_rate: Decimal
    asset_description: str
    lease_liability_account_id: UUID
    interest_expense_account_id: UUID
    rou_asset_account_id: UUID
    depreciation_expense_account_id: UUID
    description: str | None = None
    lessor_supplier_id: UUID | None = None
    external_reference: str | None = None
    is_lessee: bool = True
    payment_timing: str = "ADVANCE"
    has_renewal_option: bool = False
    renewal_option_term_months: int | None = None
    renewal_reasonably_certain: bool = False
    has_purchase_option: bool = False
    purchase_option_price: Decimal | None = None
    purchase_reasonably_certain: bool = False
    has_termination_option: bool = False
    termination_penalty: Decimal | None = None
    has_variable_payments: bool = False
    variable_payment_basis: str | None = None
    is_index_linked: bool = False
    index_type: str | None = None
    index_base_value: Decimal | None = None
    residual_value_guarantee: Decimal = Decimal("0")
    implicit_rate_known: bool = False
    implicit_rate: Decimal | None = None
    initial_direct_costs: Decimal = Decimal("0")
    lease_incentives_received: Decimal = Decimal("0")
    restoration_obligation: Decimal = Decimal("0")
    asset_category_id: UUID | None = None
    location_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None


class LeaseContractService(ListResponseMixin):
    """
    Service for IFRS 16 lease contract management.

    Handles contract creation, classification, approval, and activation.
    """

    @staticmethod
    def calculate_lease_term_months(
        commencement_date: date,
        end_date: date,
        renewal_months: int = 0,
        renewal_certain: bool = False,
    ) -> int:
        """
        Calculate lease term in months including reasonably certain renewals.

        Args:
            commencement_date: Lease start date
            end_date: Lease end date (before renewals)
            renewal_months: Renewal option period
            renewal_certain: Whether renewal is reasonably certain

        Returns:
            Total lease term in months
        """
        base_months = (end_date.year - commencement_date.year) * 12 + (
            end_date.month - commencement_date.month
        )

        if renewal_certain and renewal_months:
            base_months += renewal_months

        return max(1, base_months)

    @staticmethod
    def determine_discount_rate(
        ibr: Decimal,
        implicit_rate: Decimal | None,
        implicit_known: bool,
    ) -> Decimal:
        """
        Determine the discount rate per IFRS 16.

        Uses implicit rate if known, otherwise incremental borrowing rate.

        Args:
            ibr: Incremental borrowing rate
            implicit_rate: Implicit rate in the lease (if known)
            implicit_known: Whether implicit rate is known

        Returns:
            Discount rate to use
        """
        if implicit_known and implicit_rate is not None:
            return implicit_rate
        return ibr

    @staticmethod
    def create_contract(
        db: Session,
        organization_id: UUID,
        input: LeaseContractInput,
        created_by_user_id: UUID,
    ) -> LeaseContract:
        """
        Create a new lease contract in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Contract input data
            created_by_user_id: User creating the contract

        Returns:
            Created LeaseContract
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Generate lease number
        lease_number = SequenceService.get_next_number(db, org_id, SequenceType.LEASE)

        # Calculate lease term
        lease_term_months = LeaseContractService.calculate_lease_term_months(
            commencement_date=input.commencement_date,
            end_date=input.end_date,
            renewal_months=input.renewal_option_term_months or 0,
            renewal_certain=input.renewal_reasonably_certain,
        )

        # Determine discount rate
        discount_rate = LeaseContractService.determine_discount_rate(
            ibr=input.incremental_borrowing_rate,
            implicit_rate=input.implicit_rate,
            implicit_known=input.implicit_rate_known,
        )

        contract = LeaseContract(
            organization_id=org_id,
            lease_number=lease_number,
            lease_name=input.lease_name,
            description=input.description,
            lessor_supplier_id=input.lessor_supplier_id,
            lessor_name=input.lessor_name,
            external_reference=input.external_reference,
            classification=input.classification,
            is_lessee=input.is_lessee,
            commencement_date=input.commencement_date,
            end_date=input.end_date,
            lease_term_months=lease_term_months,
            has_renewal_option=input.has_renewal_option,
            renewal_option_term_months=input.renewal_option_term_months,
            renewal_reasonably_certain=input.renewal_reasonably_certain,
            has_purchase_option=input.has_purchase_option,
            purchase_option_price=input.purchase_option_price,
            purchase_reasonably_certain=input.purchase_reasonably_certain,
            has_termination_option=input.has_termination_option,
            termination_penalty=input.termination_penalty,
            currency_code=input.currency_code,
            payment_frequency=input.payment_frequency,
            payment_timing=input.payment_timing,
            base_payment_amount=input.base_payment_amount,
            has_variable_payments=input.has_variable_payments,
            variable_payment_basis=input.variable_payment_basis,
            is_index_linked=input.is_index_linked,
            index_type=input.index_type,
            index_base_value=input.index_base_value,
            residual_value_guarantee=input.residual_value_guarantee,
            incremental_borrowing_rate=input.incremental_borrowing_rate,
            implicit_rate_known=input.implicit_rate_known,
            implicit_rate=input.implicit_rate,
            discount_rate_used=discount_rate,
            initial_direct_costs=input.initial_direct_costs,
            lease_incentives_received=input.lease_incentives_received,
            restoration_obligation=input.restoration_obligation,
            asset_description=input.asset_description,
            asset_category_id=input.asset_category_id,
            location_id=input.location_id,
            status=LeaseStatus.DRAFT,
            cost_center_id=input.cost_center_id,
            project_id=input.project_id,
            created_by_user_id=user_id,
        )

        db.add(contract)
        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def approve_contract(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        approved_by_user_id: UUID,
    ) -> LeaseContract:
        """
        Approve a lease contract.

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease to approve
            approved_by_user_id: User approving

        Returns:
            Updated LeaseContract
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(approved_by_user_id)

        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Lease contract not found")

        if contract.status != LeaseStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve contract with status '{contract.status.value}'",
            )

        # SoD check
        if contract.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve",
            )

        contract.approved_by_user_id = user_id
        contract.approved_at = datetime.now(UTC)

        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def activate_contract(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        lease_liability_account_id: UUID,
        interest_expense_account_id: UUID,
        rou_asset_account_id: UUID,
        depreciation_expense_account_id: UUID,
    ) -> tuple[LeaseContract, LeaseLiability | None, LeaseAsset | None]:
        """
        Activate a lease and create initial recognition entries.

        Creates the lease liability and ROU asset records.

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease to activate
            lease_liability_account_id: GL account for lease liability
            interest_expense_account_id: GL account for interest expense
            rou_asset_account_id: GL account for ROU asset
            depreciation_expense_account_id: GL account for depreciation

        Returns:
            Tuple of (contract, liability, asset)
        """
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)

        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Lease contract not found")

        if not contract.approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Contract must be approved before activation",
            )

        if contract.status == LeaseStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail="Contract is already active",
            )

        # Skip recognition for short-term and low-value leases
        if contract.classification in [
            LeaseClassification.SHORT_TERM,
            LeaseClassification.LOW_VALUE,
        ]:
            contract.status = LeaseStatus.ACTIVE
            db.commit()
            db.refresh(contract)
            return (contract, None, None)

        # Calculate initial liability
        pv_result = LeaseCalculationService.calculate_initial_liability(
            db=db,
            contract=contract,
        )

        # Create lease liability
        liability = LeaseLiability(
            lease_id=ls_id,
            initial_measurement_date=contract.commencement_date,
            initial_liability_amount=pv_result.total_liability,
            pv_fixed_payments=pv_result.pv_fixed_payments,
            pv_variable_payments=pv_result.pv_variable_payments,
            pv_residual_guarantee=pv_result.pv_residual_guarantee,
            pv_purchase_option=pv_result.pv_purchase_option,
            pv_termination_penalties=Decimal("0"),
            discount_rate=contract.discount_rate_used,
            current_liability_balance=pv_result.total_liability,
            current_portion=pv_result.current_portion,
            non_current_portion=pv_result.non_current_portion,
            lease_liability_account_id=lease_liability_account_id,
            interest_expense_account_id=interest_expense_account_id,
        )
        db.add(liability)

        # Calculate ROU asset
        rou_amount = (
            pv_result.total_liability
            + contract.initial_direct_costs
            - contract.lease_incentives_received
            + contract.restoration_obligation
        )

        # Create ROU asset
        asset = LeaseAsset(
            lease_id=ls_id,
            initial_measurement_date=contract.commencement_date,
            lease_liability_at_commencement=pv_result.total_liability,
            lease_payments_at_commencement=Decimal("0"),
            initial_direct_costs=contract.initial_direct_costs,
            restoration_obligation=contract.restoration_obligation,
            lease_incentives_deducted=contract.lease_incentives_received,
            initial_rou_asset_value=rou_amount,
            depreciation_method="STRAIGHT_LINE",
            useful_life_months=contract.lease_term_months,
            residual_value=Decimal("0"),
            accumulated_depreciation=Decimal("0"),
            impairment_losses=Decimal("0"),
            revaluation_adjustments=Decimal("0"),
            modification_adjustments=Decimal("0"),
            carrying_amount=rou_amount,
            rou_asset_account_id=rou_asset_account_id,
            accumulated_depreciation_account_id=rou_asset_account_id,
            depreciation_expense_account_id=depreciation_expense_account_id,
        )
        db.add(asset)

        # Update contract status
        contract.status = LeaseStatus.ACTIVE

        db.commit()
        db.refresh(contract)
        db.refresh(liability)
        db.refresh(asset)

        return (contract, liability, asset)

    @staticmethod
    def terminate_contract(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        termination_date: date,
        termination_reason: str | None = None,
    ) -> LeaseContract:
        """
        Terminate a lease contract early.

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease to terminate
            termination_date: Date of termination
            termination_reason: Reason for termination

        Returns:
            Updated LeaseContract
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)

        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Lease contract not found")

        if contract.status not in [LeaseStatus.ACTIVE, LeaseStatus.MODIFIED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot terminate contract with status '{contract.status.value}'",
            )

        contract.status = LeaseStatus.TERMINATED
        contract.end_date = termination_date

        db.commit()
        db.refresh(contract)

        return contract

    @staticmethod
    def get(
        db: Session,
        lease_id: str,
        organization_id: UUID | None = None,
    ) -> LeaseContract:
        """Get a lease contract by ID."""
        contract = db.get(LeaseContract, coerce_uuid(lease_id))
        if not contract:
            raise HTTPException(status_code=404, detail="Lease contract not found")
        if organization_id is not None and contract.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Lease contract not found")
        return contract

    @staticmethod
    def get_liability(
        db: Session,
        lease_id: str,
    ) -> LeaseLiability | None:
        """Get the lease liability for a contract."""
        ls_id = coerce_uuid(lease_id)
        return db.scalar(select(LeaseLiability).where(LeaseLiability.lease_id == ls_id))

    @staticmethod
    def get_asset(
        db: Session,
        lease_id: str,
    ) -> LeaseAsset | None:
        """Get the ROU asset for a contract."""
        ls_id = coerce_uuid(lease_id)
        return db.scalar(select(LeaseAsset).where(LeaseAsset.lease_id == ls_id))

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        classification: LeaseClassification | None = None,
        status: LeaseStatus | None = None,
        lessor_supplier_id: str | None = None,
        is_lessee: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LeaseContract]:
        """List lease contracts with optional filters."""
        query = select(LeaseContract)

        if organization_id:
            query = query.where(
                LeaseContract.organization_id == coerce_uuid(organization_id)
            )

        if classification:
            query = query.where(LeaseContract.classification == classification)

        if status:
            query = query.where(LeaseContract.status == status)

        if lessor_supplier_id:
            query = query.where(
                LeaseContract.lessor_supplier_id == coerce_uuid(lessor_supplier_id)
            )

        if is_lessee is not None:
            query = query.where(LeaseContract.is_lessee == is_lessee)

        query = query.order_by(LeaseContract.commencement_date.desc())
        return list(db.scalars(query.limit(limit).offset(offset)).all())


# Module-level singleton instance
lease_contract_service = LeaseContractService()
