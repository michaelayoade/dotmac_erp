"""
Contract Service.

Business logic for procurement contract management.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.enums import ContractStatus
from app.models.procurement.procurement_contract import ProcurementContract
from app.schemas.procurement.contract import ContractCreate, ContractUpdate
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class ContractService:
    """Service for procurement contract management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        contract_id: UUID,
    ) -> ProcurementContract | None:
        """Get a contract by ID."""
        stmt = select(ProcurementContract).where(
            ProcurementContract.organization_id == organization_id,
            ProcurementContract.contract_id == contract_id,
        )
        return self.db.scalar(stmt)

    def list_contracts(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[ProcurementContract], int]:
        """List contracts with filters."""
        base = select(ProcurementContract).where(
            ProcurementContract.organization_id == organization_id,
        )
        if status:
            try:
                status_enum = ContractStatus(status)
            except ValueError:
                status_enum = None
            if status_enum:
                base = base.where(
                    ProcurementContract.status == status_enum,
                )
        if search:
            from sqlalchemy import or_

            term = f"%{search}%"
            base = base.where(
                or_(
                    ProcurementContract.contract_number.ilike(term),
                    ProcurementContract.title.ilike(term),
                )
            )

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            self.db.scalars(
                base.order_by(ProcurementContract.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return items, total or 0

    def create(
        self,
        organization_id: UUID,
        data: ContractCreate,
        created_by_user_id: UUID,
    ) -> ProcurementContract:
        """Create a new contract."""
        contract = ProcurementContract(
            organization_id=organization_id,
            contract_number=data.contract_number,
            title=data.title,
            supplier_id=data.supplier_id,
            rfq_id=data.rfq_id,
            evaluation_id=data.evaluation_id,
            contract_date=data.contract_date,
            start_date=data.start_date,
            end_date=data.end_date,
            contract_value=data.contract_value,
            currency_code=data.currency_code,
            bpp_clearance_number=data.bpp_clearance_number,
            bpp_clearance_date=data.bpp_clearance_date,
            payment_terms=data.payment_terms,
            terms_and_conditions=data.terms_and_conditions,
            performance_bond_required=data.performance_bond_required,
            performance_bond_amount=data.performance_bond_amount,
            retention_percentage=data.retention_percentage,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(contract)
        self.db.flush()
        logger.info("Created contract %s", contract.contract_number)
        return contract

    def update(
        self,
        organization_id: UUID,
        contract_id: UUID,
        data: ContractUpdate,
    ) -> ProcurementContract:
        """Update a contract."""
        contract = self.get_by_id(organization_id, contract_id)
        if not contract:
            raise NotFoundError("Contract not found")
        if contract.status not in (ContractStatus.DRAFT, ContractStatus.ACTIVE):
            raise ValidationError("Contract cannot be updated in current status")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(contract, field, value)

        self.db.flush()
        return contract

    def activate(
        self,
        organization_id: UUID,
        contract_id: UUID,
        *,
        user_id: UUID | None = None,
        fund_id: UUID | None = None,
        account_id: UUID | None = None,
        fiscal_year_id: UUID | None = None,
        fiscal_period_id: UUID | None = None,
        appropriation_id: UUID | None = None,
    ) -> ProcurementContract:
        """Activate a contract.

        When IPSAS params (fund_id, account_id, etc.) are provided and
        the org has commitment_control_enabled, a commitment is auto-created.
        """
        contract = self.get_by_id(organization_id, contract_id)
        if not contract:
            raise NotFoundError("Contract not found")
        if contract.status != ContractStatus.DRAFT:
            raise ValidationError("Only draft contracts can be activated")

        contract.status = ContractStatus.ACTIVE
        self.db.flush()
        logger.info("Activated contract %s", contract.contract_number)

        # Side effect: create IPSAS commitment if enabled
        if fund_id and account_id and fiscal_year_id and fiscal_period_id and user_id:
            try:
                self._create_commitment_if_enabled(
                    organization_id=organization_id,
                    contract=contract,
                    user_id=user_id,
                    fund_id=fund_id,
                    account_id=account_id,
                    fiscal_year_id=fiscal_year_id,
                    fiscal_period_id=fiscal_period_id,
                    appropriation_id=appropriation_id,
                )
            except Exception as e:
                logger.exception(
                    "Failed to create commitment for contract %s: %s",
                    contract.contract_number,
                    e,
                )

        return contract

    def _create_commitment_if_enabled(
        self,
        *,
        organization_id: UUID,
        contract: ProcurementContract,
        user_id: UUID,
        fund_id: UUID,
        account_id: UUID,
        fiscal_year_id: UUID,
        fiscal_period_id: UUID,
        appropriation_id: UUID | None = None,
    ) -> None:
        """Create an IPSAS commitment if the org has commitment control enabled."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if not org or not org.commitment_control_enabled:
            return

        # Import inside function to avoid circular imports
        from app.services.finance.ipsas.commitment_service import CommitmentService

        commitment_number = f"CMT-{contract.contract_number}"
        svc = CommitmentService(self.db)
        svc.create_commitment_from_contract(
            organization_id=organization_id,
            contract_id=contract.contract_id,
            fund_id=fund_id,
            account_id=account_id,
            fiscal_year_id=fiscal_year_id,
            fiscal_period_id=fiscal_period_id,
            amount=contract.contract_value,
            currency_code=contract.currency_code,
            created_by_user_id=user_id,
            commitment_number=commitment_number,
            appropriation_id=appropriation_id,
        )

    def complete(
        self,
        organization_id: UUID,
        contract_id: UUID,
    ) -> ProcurementContract:
        """Mark a contract as completed."""
        contract = self.get_by_id(organization_id, contract_id)
        if not contract:
            raise NotFoundError("Contract not found")
        if contract.status != ContractStatus.ACTIVE:
            raise ValidationError("Only active contracts can be completed")

        contract.status = ContractStatus.COMPLETED
        self.db.flush()
        logger.info("Completed contract %s", contract.contract_number)
        return contract

    def terminate(
        self,
        organization_id: UUID,
        contract_id: UUID,
    ) -> ProcurementContract:
        """Terminate a contract."""
        contract = self.get_by_id(organization_id, contract_id)
        if not contract:
            raise NotFoundError("Contract not found")
        if contract.status != ContractStatus.ACTIVE:
            raise ValidationError("Only active contracts can be terminated")

        contract.status = ContractStatus.TERMINATED
        self.db.flush()
        logger.info("Terminated contract %s", contract.contract_number)
        return contract
