"""
Contract Service.

Business logic for procurement contract management.
"""

import logging
from typing import List, Optional, Tuple
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
    ) -> Optional[ProcurementContract]:
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
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[ProcurementContract], int]:
        """List contracts with filters."""
        base = select(ProcurementContract).where(
            ProcurementContract.organization_id == organization_id,
        )
        if status:
            base = base.where(
                ProcurementContract.status == ContractStatus(status),
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
    ) -> ProcurementContract:
        """Activate a contract."""
        contract = self.get_by_id(organization_id, contract_id)
        if not contract:
            raise NotFoundError("Contract not found")
        if contract.status != ContractStatus.DRAFT:
            raise ValidationError("Only draft contracts can be activated")

        contract.status = ContractStatus.ACTIVE
        self.db.flush()
        logger.info("Activated contract %s", contract.contract_number)
        return contract

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
