"""Supplier routes for the AP API."""

from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.api.finance.utils import parse_enum
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.schemas.finance.ap import SupplierCreate, SupplierRead, SupplierUpdate
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import SupplierInput, supplier_service
from app.services.finance.ap.supplier_bank_details import build_supplier_bank_details


@router.post(
    "/suppliers", response_model=SupplierRead, status_code=status.HTTP_201_CREATED
)
def create_supplier(
    payload: SupplierCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new supplier."""
    bank_details = None
    if payload.bank_details is not None:
        bank_details = build_supplier_bank_details(
            db,
            organization_id,
            bank_code=payload.bank_details.bank_code,
            account_name=payload.bank_details.account_name,
            account_number=payload.bank_details.account_number.get_secret_value(),
        )

    input_data = SupplierInput(
        supplier_code=payload.supplier_code,
        supplier_type=parse_enum(SupplierType, payload.supplier_type)
        or SupplierType.VENDOR,
        supplier_name=payload.supplier_name,
        trading_name=payload.trading_name,
        tax_id=payload.tax_id,
        payment_terms_days=payload.payment_terms_days,
        currency_code=payload.currency_code,
        default_expense_account_id=payload.default_expense_account_id,
        default_payable_account_id=payload.default_payable_account_id,
        bank_details=bank_details,
    )
    return supplier_service.create_supplier(db, organization_id, input_data)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a supplier by ID."""
    return supplier_service.get(db, organization_id, str(supplier_id))


@router.get("/suppliers", response_model=ListResponse[SupplierRead])
def list_suppliers(
    organization_id: UUID = Depends(require_organization_id),
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:read")),
    db: Session = Depends(get_db_with_org),
):
    """List suppliers with filters."""
    suppliers = supplier_service.list(
        db=db,
        organization_id=str(organization_id),
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(Supplier.supplier_id)).where(
        Supplier.organization_id == organization_id
    )
    if is_active is not None:
        count_stmt = count_stmt.where(Supplier.is_active == is_active)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=suppliers,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:update")),
    db: Session = Depends(get_db_with_org),
):
    """Update a supplier (partial update)."""
    update_data = payload.model_dump(exclude_unset=True)
    if "bank_details" in update_data and update_data["bank_details"] is not None:
        bank_details = payload.bank_details
        if bank_details is not None:
            update_data["bank_details"] = build_supplier_bank_details(
                db,
                organization_id,
                bank_code=bank_details.bank_code,
                account_name=bank_details.account_name,
                account_number=bank_details.account_number.get_secret_value(),
            )

    return supplier_service.partial_update_supplier(
        db=db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        update_data=update_data,
    )
