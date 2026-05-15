"""Customer routes for the AR API."""

from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.config import settings
from app.models.finance.ar.customer import CustomerType
from app.schemas.finance.ar import CustomerCreate, CustomerRead, CustomerUpdate
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import CustomerInput, customer_service


@router.post(
    "/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED
)
def create_customer(
    payload: CustomerCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new customer."""
    input_data = CustomerInput(
        customer_code=payload.customer_code,
        customer_type=CustomerType(payload.customer_type.upper()),
        customer_name=payload.customer_name,
        trading_name=payload.trading_name,
        tax_id=payload.tax_id,
        vat_category=payload.vat_category,
        payment_terms_days=payload.payment_terms_days,
        credit_limit=payload.credit_limit,
        currency_code=settings.default_functional_currency_code,
        default_revenue_account_id=payload.default_revenue_account_id,
        default_receivable_account_id=payload.default_receivable_account_id,
        default_tax_code_id=payload.default_tax_code_id,
    )
    return customer_service.create_customer(db, organization_id, input_data)


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a customer by ID."""
    return customer_service.get(db, organization_id, str(customer_id))


@router.get("/customers", response_model=ListResponse[CustomerRead])
def list_customers(
    organization_id: UUID = Depends(require_organization_id),
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ar:customers:read")),
    db: Session = Depends(get_db_with_org),
):
    """List customers with filters."""
    customers = customer_service.list(
        db=db,
        organization_id=str(organization_id),
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=customers,
        count=len(customers),
        limit=limit,
        offset=offset,
    )


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:update")),
    db: Session = Depends(get_db_with_org),
):
    """Update a customer (partial update)."""
    update_data = payload.model_dump(exclude_unset=True)
    return customer_service.partial_update_customer(
        db=db,
        organization_id=organization_id,
        customer_id=customer_id,
        update_data=update_data,
    )
