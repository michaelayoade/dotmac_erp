"""
Contract API Endpoints.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.procurement.contract import (
    ContractCreate,
    ContractResponse,
    ContractUpdate,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.contract import ContractService

router = APIRouter(prefix="/contracts", tags=["procurement-contracts"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: str | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List procurement contracts."""
    service = ContractService(db)
    contracts, _ = service.list_contracts(
        organization_id,
        status=status_filter,
        offset=offset,
        limit=limit,
    )
    return [ContractResponse.model_validate(c) for c in contracts]


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a contract by ID."""
    service = ContractService(db)
    contract = service.get_by_id(organization_id, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return ContractResponse.model_validate(contract)


@router.post("", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
def create_contract(
    data: ContractCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Create a new procurement contract."""
    service = ContractService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        contract = service.create(organization_id, data, user_id)
        db.commit()
        return ContractResponse.model_validate(contract)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{contract_id}", response_model=ContractResponse)
def update_contract(
    contract_id: UUID,
    data: ContractUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a contract."""
    service = ContractService(db)
    try:
        contract = service.update(organization_id, contract_id, data)
        db.commit()
        return ContractResponse.model_validate(contract)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{contract_id}/activate", response_model=ContractResponse)
def activate_contract(
    contract_id: UUID,
    fund_id: UUID | None = Query(None, description="IPSAS fund for commitment"),
    account_id: UUID | None = Query(None, description="GL account for commitment"),
    fiscal_year_id: UUID | None = Query(None, description="Fiscal year for commitment"),
    fiscal_period_id: UUID | None = Query(
        None, description="Fiscal period for commitment"
    ),
    appropriation_id: UUID | None = Query(
        None, description="Appropriation for commitment"
    ),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Activate a contract. Optionally creates an IPSAS commitment."""
    service = ContractService(db)
    person_id = auth.get("person_id")
    user_id = UUID(person_id) if person_id else None
    try:
        contract = service.activate(
            organization_id,
            contract_id,
            user_id=user_id,
            fund_id=fund_id,
            account_id=account_id,
            fiscal_year_id=fiscal_year_id,
            fiscal_period_id=fiscal_period_id,
            appropriation_id=appropriation_id,
        )
        db.commit()
        return ContractResponse.model_validate(contract)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{contract_id}/complete", response_model=ContractResponse)
def complete_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Mark a contract as completed."""
    service = ContractService(db)
    try:
        contract = service.complete(organization_id, contract_id)
        db.commit()
        return ContractResponse.model_validate(contract)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{contract_id}/generate-invoice")
def generate_invoice_from_contract(
    contract_id: UUID,
    ap_control_account_id: UUID = Query(..., description="AP control GL account"),
    expense_account_id: UUID = Query(..., description="Expense GL account"),
    payment_terms_days: int = Query(30, ge=1, le=365),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Generate an AP supplier invoice from a procurement contract."""
    from app.services.procurement.ap_integration import (
        ProcurementAPIntegrationService,
    )

    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        svc = ProcurementAPIntegrationService(db)
        invoice = svc.generate_invoice_from_contract(
            organization_id,
            contract_id,
            created_by_user_id=user_id,
            ap_control_account_id=ap_control_account_id,
            expense_account_id=expense_account_id,
            payment_terms_days=payment_terms_days,
        )
        db.commit()
        return {
            "invoice_id": str(invoice.invoice_id),
            "invoice_number": invoice.invoice_number,
        }
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{contract_id}/terminate", response_model=ContractResponse)
def terminate_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Terminate a contract."""
    service = ContractService(db)
    try:
        contract = service.terminate(organization_id, contract_id)
        db.commit()
        return ContractResponse.model_validate(contract)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
