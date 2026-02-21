"""
Expense Management API Router.

Independent expense module API endpoints for:
- Expense Categories
- Expense Claims and workflow
- Cash Advances
- Corporate Cards
- Card Transactions
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    require_organization_id,
    require_tenant_permission,
)
from app.api.idempotency import (
    build_cached_response,
    build_request_hash,
    check_or_reserve_idempotency,
    require_idempotency_key,
)
from app.db import SessionLocal
from app.models.expense import (
    CardTransactionStatus,
    CashAdvanceStatus,
    ExpenseClaimStatus,
)
from app.schemas.expense import (
    # Card Transaction
    CardTransactionCreate,
    CardTransactionListResponse,
    CardTransactionRead,
    CardTransactionUpdate,
    # Cash Advance
    CashAdvanceCreate,
    CashAdvanceDisburseRequest,
    CashAdvanceListResponse,
    CashAdvanceRead,
    CashAdvanceSettleRequest,
    CashAdvanceUpdate,
    # Corporate Card
    CorporateCardCreate,
    CorporateCardListResponse,
    CorporateCardRead,
    CorporateCardUpdate,
    DeactivateCardRequest,
    EmployeeExpenseSummary,
    # Expense Category
    ExpenseCategoryCreate,
    ExpenseCategoryListResponse,
    ExpenseCategoryRead,
    ExpenseCategoryUpdate,
    ExpenseClaimApprovalRequest,
    # Expense Claim
    ExpenseClaimCreate,
    ExpenseClaimItemCreate,
    ExpenseClaimItemRead,
    ExpenseClaimListResponse,
    ExpenseClaimRead,
    ExpenseClaimRejectRequest,
    ExpenseClaimUpdate,
    # Reports
    ExpenseStats,
    LinkAdvanceRequest,
    MarkPaidRequest,
    MatchTransactionRequest,
)
from app.services.common import PaginationParams
from app.services.expense import ExpenseService
from app.services.expense.expense_service import (
    ApproverAuthorityError,
    ExpenseClaimStatusError,
)
from app.services.finance.platform.idempotency import IdempotencyService

router = APIRouter(
    prefix="/expenses",
    tags=["expenses"],
    dependencies=[Depends(require_tenant_permission("expense:access"))],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_enum(value: str | None, enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name}: {value}"
        ) from exc


# =============================================================================
# Expense Categories
# =============================================================================


@router.get("/categories", response_model=ExpenseCategoryListResponse)
def list_expense_categories(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:categories:read")),
    search: str | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List expense categories."""
    svc = ExpenseService(db)
    result = svc.list_categories(
        org_id=organization_id,
        search=search,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ExpenseCategoryListResponse(
        items=[ExpenseCategoryRead.model_validate(c) for c in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/categories",
    response_model=ExpenseCategoryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_expense_category(
    payload: ExpenseCategoryCreate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:categories:manage")),
    db: Session = Depends(get_db),
):
    """Create an expense category."""
    svc = ExpenseService(db)
    category = svc.create_category(
        org_id=organization_id,
        category_code=payload.category_code,
        category_name=payload.category_name,
        description=payload.description,
        expense_account_id=payload.expense_account_id,
        max_amount_per_claim=payload.max_amount_per_claim,
        requires_receipt=payload.requires_receipt,
        is_active=payload.is_active,
    )
    return ExpenseCategoryRead.model_validate(category)


@router.get("/categories/{category_id}", response_model=ExpenseCategoryRead)
def get_expense_category(
    category_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:categories:read")),
    db: Session = Depends(get_db),
):
    """Get an expense category by ID."""
    svc = ExpenseService(db)
    return ExpenseCategoryRead.model_validate(
        svc.get_category(organization_id, category_id)
    )


@router.patch("/categories/{category_id}", response_model=ExpenseCategoryRead)
def update_expense_category(
    category_id: UUID,
    payload: ExpenseCategoryUpdate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:categories:manage")),
    db: Session = Depends(get_db),
):
    """Update an expense category."""
    svc = ExpenseService(db)
    update_data = payload.model_dump(exclude_unset=True)
    category = svc.update_category(organization_id, category_id, **update_data)
    return ExpenseCategoryRead.model_validate(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense_category(
    category_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:categories:manage")),
    db: Session = Depends(get_db),
):
    """Delete an expense category."""
    svc = ExpenseService(db)
    svc.delete_category(organization_id, category_id)


# =============================================================================
# Expense Claims
# =============================================================================


@router.get("/claims", response_model=ExpenseClaimListResponse)
def list_expense_claims(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:read")),
    employee_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List expense claims."""
    svc = ExpenseService(db)
    status_enum = parse_enum(status, ExpenseClaimStatus, "status")
    result = svc.list_claims(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        search=search,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ExpenseClaimListResponse(
        items=[ExpenseClaimRead.model_validate(c) for c in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/claims", response_model=ExpenseClaimRead, status_code=status.HTTP_201_CREATED
)
def create_expense_claim(
    payload: ExpenseClaimCreate,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:create")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Create an expense claim."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        payload,
        {"organization_id": str(organization_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    # Convert items to dicts for service
    items_data = None
    if payload.items:
        items_data = [item.model_dump() for item in payload.items]

    try:
        claim = svc.create_claim(
            org_id=organization_id,
            employee_id=payload.employee_id,
            claim_date=payload.claim_date,
            purpose=payload.purpose,
            expense_period_start=payload.expense_period_start,
            expense_period_end=payload.expense_period_end,
            project_id=payload.project_id,
            task_id=payload.task_id,
            currency_code=payload.currency_code,
            cost_center_id=payload.cost_center_id,
            recipient_bank_code=payload.recipient_bank_code,
            recipient_bank_name=payload.recipient_bank_name,
            recipient_account_number=payload.recipient_account_number,
            recipient_name=payload.recipient_name,
            requested_approver_id=payload.requested_approver_id,
            notes=payload.notes,
            items=items_data,
        )
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_201_CREATED,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


@router.get("/claims/{claim_id}", response_model=ExpenseClaimRead)
def get_expense_claim(
    claim_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:read")),
    db: Session = Depends(get_db),
):
    """Get an expense claim by ID."""
    svc = ExpenseService(db)
    return ExpenseClaimRead.model_validate(svc.get_claim(organization_id, claim_id))


@router.patch("/claims/{claim_id}", response_model=ExpenseClaimRead)
def update_expense_claim(
    claim_id: UUID,
    payload: ExpenseClaimUpdate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
):
    """Update an expense claim (only in draft status)."""
    svc = ExpenseService(db)
    update_data = payload.model_dump(exclude_unset=True)
    claim = svc.update_claim(organization_id, claim_id, **update_data)
    return ExpenseClaimRead.model_validate(claim)


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense_claim(
    claim_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:delete")),
    db: Session = Depends(get_db),
):
    """Delete an expense claim (only in draft status)."""
    svc = ExpenseService(db)
    svc.delete_claim(organization_id, claim_id)


# Claim items
@router.post(
    "/claims/{claim_id}/items",
    response_model=ExpenseClaimItemRead,
    status_code=status.HTTP_201_CREATED,
)
def add_claim_item(
    claim_id: UUID,
    payload: ExpenseClaimItemCreate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
):
    """Add an item to an expense claim."""
    svc = ExpenseService(db)
    item = svc.add_claim_item(
        org_id=organization_id,
        claim_id=claim_id,
        **payload.model_dump(),
    )
    return ExpenseClaimItemRead.model_validate(item)


@router.delete(
    "/claims/{claim_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_claim_item(
    claim_id: UUID,
    item_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
):
    """Remove an item from an expense claim."""
    svc = ExpenseService(db)
    svc.remove_claim_item(organization_id, claim_id, item_id)


# =============================================================================
# Expense Claim Workflow
# =============================================================================


@router.post("/claims/{claim_id}/submit", response_model=ExpenseClaimRead)
def submit_claim(
    claim_id: UUID,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:submit")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Submit an expense claim for approval."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        None,
        {"organization_id": str(organization_id), "claim_id": str(claim_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    try:
        result = svc.submit_claim(organization_id, claim_id)
        claim = result.claim if hasattr(result, "claim") else result
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


@router.post("/claims/{claim_id}/approve", response_model=ExpenseClaimRead)
def approve_claim(
    claim_id: UUID,
    payload: ExpenseClaimApprovalRequest,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:approve:tier1")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Approve an expense claim."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        payload,
        {"organization_id": str(organization_id), "claim_id": str(claim_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    # Convert approved_amounts if provided
    approved_amounts = None
    if payload.approved_amounts:
        approved_amounts = [a.model_dump() for a in payload.approved_amounts]

    try:
        claim = svc.approve_claim(
            org_id=organization_id,
            claim_id=claim_id,
            approver_id=payload.approver_id,
            approved_amounts=approved_amounts,
            notes=payload.notes,
        )
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except ApproverAuthorityError as exc:
        detail = str(exc)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=403,
            response_body={"detail": detail},
        )
        raise HTTPException(status_code=403, detail=detail)
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


@router.post("/claims/{claim_id}/reject", response_model=ExpenseClaimRead)
def reject_claim(
    claim_id: UUID,
    payload: ExpenseClaimRejectRequest,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:reject")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Reject an expense claim."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        payload,
        {"organization_id": str(organization_id), "claim_id": str(claim_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    try:
        claim = svc.reject_claim(
            org_id=organization_id,
            claim_id=claim_id,
            approver_id=payload.approver_id,
            reason=payload.reason,
        )
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


@router.post("/claims/{claim_id}/mark-paid", response_model=ExpenseClaimRead)
def mark_claim_paid(
    claim_id: UUID,
    payload: MarkPaidRequest,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:reimburse")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Mark an expense claim as paid."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        payload,
        {"organization_id": str(organization_id), "claim_id": str(claim_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    try:
        claim = svc.mark_paid(
            org_id=organization_id,
            claim_id=claim_id,
            payment_reference=payload.payment_reference,
            payment_date=payload.payment_date,
        )
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


@router.post("/claims/{claim_id}/cancel", response_model=ExpenseClaimRead)
def cancel_claim(
    claim_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
    reason: str | None = None,
):
    """Cancel an expense claim (DRAFT or SUBMITTED only)."""
    svc = ExpenseService(db)
    try:
        claim = svc.cancel_claim(
            org_id=organization_id,
            claim_id=claim_id,
            reason=reason,
        )
        return ExpenseClaimRead.model_validate(claim)
    except ExpenseClaimStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/claims/{claim_id}/resubmit", response_model=ExpenseClaimRead)
def resubmit_claim(
    claim_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
):
    """Resubmit a rejected expense claim (resets to DRAFT)."""
    svc = ExpenseService(db)
    try:
        claim = svc.resubmit_claim(
            org_id=organization_id,
            claim_id=claim_id,
        )
        return ExpenseClaimRead.model_validate(claim)
    except ExpenseClaimStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/claims/{claim_id}/link-advance", response_model=ExpenseClaimRead)
def link_advance_to_claim(
    claim_id: UUID,
    payload: LinkAdvanceRequest,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:update")),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """Link a cash advance to an expense claim."""
    svc = ExpenseService(db)
    idempotency_key = require_idempotency_key(idempotency_key)
    request_hash = build_request_hash(
        payload,
        {"organization_id": str(organization_id), "claim_id": str(claim_id)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=request.url.path,
        request_hash=request_hash,
    )
    if replay:
        return build_cached_response(replay)

    try:
        claim = svc.link_advance(
            org_id=organization_id,
            claim_id=claim_id,
            advance_id=payload.advance_id,
            amount_to_adjust=payload.amount_to_adjust,
        )
        response = ExpenseClaimRead.model_validate(claim)
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump(mode="json"),
        )
        return response
    except HTTPException as exc:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=exc.status_code,
            response_body={"detail": exc.detail},
        )
        raise
    except Exception:
        IdempotencyService.update_response(
            db=db,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            endpoint=request.url.path,
            response_status=500,
            response_body={"detail": "Internal Server Error"},
        )
        raise


# =============================================================================
# Cash Advances
# =============================================================================


@router.get("/advances", response_model=CashAdvanceListResponse)
def list_cash_advances(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:read")),
    employee_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List cash advances."""
    svc = ExpenseService(db)
    status_enum = parse_enum(status, CashAdvanceStatus, "status")
    result = svc.list_advances(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return CashAdvanceListResponse(
        items=[CashAdvanceRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/advances", response_model=CashAdvanceRead, status_code=status.HTTP_201_CREATED
)
def create_cash_advance(
    payload: CashAdvanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:create")),
    db: Session = Depends(get_db),
):
    """Create a cash advance request."""
    svc = ExpenseService(db)
    advance = svc.create_advance(
        org_id=organization_id,
        employee_id=payload.employee_id,
        request_date=payload.request_date,
        purpose=payload.purpose,
        requested_amount=payload.requested_amount,
        currency_code=payload.currency_code,
        expected_settlement_date=payload.expected_settlement_date,
        cost_center_id=payload.cost_center_id,
        advance_account_id=payload.advance_account_id,
        notes=payload.notes,
    )
    return CashAdvanceRead.model_validate(advance)


@router.get("/advances/{advance_id}", response_model=CashAdvanceRead)
def get_cash_advance(
    advance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:read")),
    db: Session = Depends(get_db),
):
    """Get a cash advance by ID."""
    svc = ExpenseService(db)
    return CashAdvanceRead.model_validate(svc.get_advance(organization_id, advance_id))


@router.patch("/advances/{advance_id}", response_model=CashAdvanceRead)
def update_cash_advance(
    advance_id: UUID,
    payload: CashAdvanceUpdate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:create")),
    db: Session = Depends(get_db),
):
    """Update a cash advance (only in draft status)."""
    svc = ExpenseService(db)
    update_data = payload.model_dump(exclude_unset=True)
    advance = svc.update_advance(organization_id, advance_id, **update_data)
    return CashAdvanceRead.model_validate(advance)


@router.delete("/advances/{advance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cash_advance(
    advance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:create")),
    db: Session = Depends(get_db),
):
    """Delete a cash advance (only in draft status)."""
    svc = ExpenseService(db)
    svc.delete_advance(organization_id, advance_id)


# Advance workflow
@router.post("/advances/{advance_id}/submit", response_model=CashAdvanceRead)
def submit_advance(
    advance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:create")),
    db: Session = Depends(get_db),
):
    """Submit a cash advance for approval."""
    svc = ExpenseService(db)
    advance = svc.submit_advance(organization_id, advance_id)
    return CashAdvanceRead.model_validate(advance)


@router.post("/advances/{advance_id}/approve", response_model=CashAdvanceRead)
def approve_advance(
    advance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:approve:tier1")),
    approver_id: UUID | None = None,
    approved_amount: Decimal | None = None,
    db: Session = Depends(get_db),
):
    """Approve a cash advance."""
    if approver_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="approver_id required"
        )
    svc = ExpenseService(db)
    advance = svc.approve_advance(
        org_id=organization_id,
        advance_id=advance_id,
        approver_id=approver_id,
        approved_amount=approved_amount,
    )
    return CashAdvanceRead.model_validate(advance)


@router.post("/advances/{advance_id}/reject", response_model=CashAdvanceRead)
def reject_advance(
    advance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:approve:tier1")),
    approver_id: UUID | None = None,
    reason: str = Query(...),
    db: Session = Depends(get_db),
):
    """Reject a cash advance."""
    svc = ExpenseService(db)
    advance = svc.reject_advance(
        org_id=organization_id,
        advance_id=advance_id,
        approver_id=approver_id,
        reason=reason,
    )
    return CashAdvanceRead.model_validate(advance)


@router.post("/advances/{advance_id}/disburse", response_model=CashAdvanceRead)
def disburse_advance(
    advance_id: UUID,
    payload: CashAdvanceDisburseRequest,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:disburse")),
    db: Session = Depends(get_db),
):
    """Disburse a cash advance."""
    svc = ExpenseService(db)
    advance = svc.disburse_advance(
        org_id=organization_id,
        advance_id=advance_id,
        disbursed_amount=payload.disbursed_amount,
        disbursement_date=payload.disbursement_date,
        payment_reference=payload.payment_reference,
    )
    return CashAdvanceRead.model_validate(advance)


@router.post("/advances/{advance_id}/settle", response_model=CashAdvanceRead)
def settle_advance(
    advance_id: UUID,
    payload: CashAdvanceSettleRequest,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:advances:settle")),
    db: Session = Depends(get_db),
):
    """Settle a cash advance."""
    svc = ExpenseService(db)
    advance = svc.settle_advance(
        org_id=organization_id,
        advance_id=advance_id,
        settled_amount=payload.settled_amount,
        settlement_date=payload.settlement_date,
        notes=payload.notes,
    )
    return CashAdvanceRead.model_validate(advance)


# =============================================================================
# Corporate Cards
# =============================================================================


@router.get("/cards", response_model=CorporateCardListResponse)
def list_corporate_cards(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:read")),
    employee_id: UUID | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List corporate cards."""
    svc = ExpenseService(db)
    result = svc.list_cards(
        org_id=organization_id,
        employee_id=employee_id,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return CorporateCardListResponse(
        items=[CorporateCardRead.model_validate(c) for c in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/cards", response_model=CorporateCardRead, status_code=status.HTTP_201_CREATED
)
def create_corporate_card(
    payload: CorporateCardCreate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:manage")),
    db: Session = Depends(get_db),
):
    """Create a corporate card record."""
    svc = ExpenseService(db)
    card = svc.create_card(
        org_id=organization_id,
        card_number_last4=payload.card_number_last4,
        card_name=payload.card_name,
        card_type=payload.card_type,
        issuer=payload.issuer,
        employee_id=payload.employee_id,
        assigned_date=payload.assigned_date,
        expiry_date=payload.expiry_date,
        credit_limit=payload.credit_limit,
        single_transaction_limit=payload.single_transaction_limit,
        monthly_limit=payload.monthly_limit,
        currency_code=payload.currency_code,
        liability_account_id=payload.liability_account_id,
    )
    return CorporateCardRead.model_validate(card)


@router.get("/cards/{card_id}", response_model=CorporateCardRead)
def get_corporate_card(
    card_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:read")),
    db: Session = Depends(get_db),
):
    """Get a corporate card by ID."""
    svc = ExpenseService(db)
    return CorporateCardRead.model_validate(svc.get_card(organization_id, card_id))


@router.patch("/cards/{card_id}", response_model=CorporateCardRead)
def update_corporate_card(
    card_id: UUID,
    payload: CorporateCardUpdate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:manage")),
    db: Session = Depends(get_db),
):
    """Update a corporate card."""
    svc = ExpenseService(db)
    update_data = payload.model_dump(exclude_unset=True)
    card = svc.update_card(organization_id, card_id, **update_data)
    return CorporateCardRead.model_validate(card)


@router.post("/cards/{card_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_corporate_card(
    card_id: UUID,
    payload: DeactivateCardRequest,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:manage")),
    db: Session = Depends(get_db),
):
    """Deactivate a corporate card."""
    svc = ExpenseService(db)
    svc.deactivate_card(organization_id, card_id, reason=payload.reason)


# =============================================================================
# Card Transactions
# =============================================================================


@router.get("/transactions", response_model=CardTransactionListResponse)
def list_card_transactions(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:transactions:read")),
    card_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    unmatched_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List card transactions."""
    svc = ExpenseService(db)
    status_enum = parse_enum(status, CardTransactionStatus, "status")
    result = svc.list_transactions(
        org_id=organization_id,
        card_id=card_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        unmatched_only=unmatched_only,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return CardTransactionListResponse(
        items=[CardTransactionRead.model_validate(t) for t in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/transactions",
    response_model=CardTransactionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_card_transaction(
    payload: CardTransactionCreate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(
        require_tenant_permission("expense:cards:transactions:reconcile")
    ),
    db: Session = Depends(get_db),
):
    """Create a card transaction record (typically from bank feed import)."""
    svc = ExpenseService(db)
    transaction = svc.create_transaction(
        org_id=organization_id,
        card_id=payload.card_id,
        transaction_date=payload.transaction_date,
        posting_date=payload.posting_date,
        merchant_name=payload.merchant_name,
        merchant_category=payload.merchant_category,
        amount=payload.amount,
        currency_code=payload.currency_code,
        original_currency=payload.original_currency,
        original_amount=payload.original_amount,
        external_reference=payload.external_reference,
        description=payload.description,
        notes=payload.notes,
    )
    return CardTransactionRead.model_validate(transaction)


@router.get("/transactions/{transaction_id}", response_model=CardTransactionRead)
def get_card_transaction(
    transaction_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:cards:transactions:read")),
    db: Session = Depends(get_db),
):
    """Get a card transaction by ID."""
    svc = ExpenseService(db)
    return CardTransactionRead.model_validate(
        svc.get_transaction(organization_id, transaction_id)
    )


@router.patch("/transactions/{transaction_id}", response_model=CardTransactionRead)
def update_card_transaction(
    transaction_id: UUID,
    payload: CardTransactionUpdate,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(
        require_tenant_permission("expense:cards:transactions:reconcile")
    ),
    db: Session = Depends(get_db),
):
    """Update a card transaction."""
    svc = ExpenseService(db)
    update_data = payload.model_dump(exclude_unset=True)
    transaction = svc.update_transaction(organization_id, transaction_id, **update_data)
    return CardTransactionRead.model_validate(transaction)


@router.post("/transactions/{transaction_id}/match", response_model=CardTransactionRead)
def match_transaction(
    transaction_id: UUID,
    payload: MatchTransactionRequest,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(
        require_tenant_permission("expense:cards:transactions:reconcile")
    ),
    db: Session = Depends(get_db),
):
    """Match a card transaction to an expense claim."""
    svc = ExpenseService(db)
    transaction = svc.match_transaction(
        org_id=organization_id,
        transaction_id=transaction_id,
        expense_claim_id=payload.expense_claim_id,
    )
    return CardTransactionRead.model_validate(transaction)


# =============================================================================
# Reporting
# =============================================================================


@router.get("/stats", response_model=ExpenseStats)
def get_expense_stats(
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:reports:read")),
    db: Session = Depends(get_db),
):
    """Get overall expense statistics for the organization."""
    svc = ExpenseService(db)
    return svc.get_expense_stats(organization_id)


@router.get("/employees/{employee_id}/summary", response_model=EmployeeExpenseSummary)
def get_employee_expense_summary(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:reports:read")),
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
):
    """Get expense summary for an employee."""
    svc = ExpenseService(db)
    return svc.get_employee_expense_summary(
        org_id=organization_id,
        employee_id=employee_id,
        year=year,
        month=month,
    )
