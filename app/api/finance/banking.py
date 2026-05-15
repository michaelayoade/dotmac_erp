"""
Banking API Router.

Bank accounts, statements, and reconciliation API endpoints.
All endpoints are tenant-scoped via require_tenant_auth.
"""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org
from app.db import SessionLocal
from app.models.finance.banking.bank_account import BankAccountStatus, BankAccountType
from app.models.finance.banking.bank_reconciliation import ReconciliationStatus
from app.models.finance.banking.bank_statement import BankStatementStatus
from app.schemas.finance.banking import (
    # Bank Reconciliation
    AutoMatchRequest,
    AutoMatchResult,
    # Bank Account
    BankAccountCreate,
    BankAccountRead,
    BankAccountStatusUpdate,
    BankAccountUpdate,
    BankReconciliationRead,
    BankReconciliationWithLines,
    # Bank Statement
    BankStatementImport,
    BankStatementRead,
    BankStatementWithLines,
    MonoLinkRequest,
    ReconciliationAdjustmentCreate,
    ReconciliationApproval,
    ReconciliationCreate,
    ReconciliationLineRead,
    ReconciliationMatchCreate,
    ReconciliationMultiMatchCreate,
    ReconciliationOutstandingCreate,
    ReconciliationRejection,
    ReconciliationReport,
    StatementImportResult,
    StatementLineRead,
    StatementSummary,
)
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.banking import (
    BankAccountInput,
    ReconciliationInput,
    ReconciliationMatchInput,
    bank_account_service,
    bank_reconciliation_service,
    bank_statement_service,
)

router = APIRouter(prefix="/banking", tags=["banking"])


def get_db():
    """Unprimed DB session for unauthenticated routes (Mono webhook).

    Tenant-scoped banking routes use ``get_db_with_org`` instead; this
    yielder exists only for the webhook handler at the bottom of this
    module, which has no auth context to derive an org from.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _get_org_id(auth: dict) -> UUID:
    """Extract organization_id from auth dict."""
    org_id = auth.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=403, detail="User is not associated with an organization"
        )
    return UUID(org_id)


def _get_user_id(auth: dict) -> UUID:
    """Extract person_id from auth dict."""
    return UUID(auth["person_id"])


def _raise_mono_http_error(exc: Exception) -> None:
    """Translate Mono service-layer exceptions to HTTP responses."""
    detail = str(exc)
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=detail) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=detail) from exc
    if isinstance(exc, ValueError):
        status_code = 409 if "already linked" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if isinstance(exc, RuntimeError):
        status_code = 503 if "not configured" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    raise exc


def _normalize_form_payload(data: dict, *, include_booleans: bool) -> dict:
    normalized: dict = {}
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                value = None
        normalized[key] = value

    if include_booleans:
        for flag in ("allow_overdraft", "is_primary"):
            if flag in data:
                raw = data.get(flag)
                normalized[flag] = str(raw).lower() in ("1", "true", "on", "yes")
    return normalized


async def _bank_account_payload_from_request(request: Request, model_cls):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        raw = await request.json()
    else:
        form = await request.form()
        raw = dict(form)

    normalized = _normalize_form_payload(raw, include_booleans=True)
    if hasattr(model_cls, "model_fields"):
        allowed = set(model_cls.model_fields.keys())
        normalized = {key: value for key, value in normalized.items() if key in allowed}
    try:
        return model_cls.model_validate(normalized)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


# =============================================================================
# Bank Accounts
# =============================================================================


@router.post(
    "/accounts",
    response_model=BankAccountRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bank_account(
    request: Request,
    auth: dict = Depends(require_tenant_permission("banking:accounts:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new bank account."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)
    payload = await _bank_account_payload_from_request(request, BankAccountCreate)

    input_data = BankAccountInput(
        bank_name=payload.bank_name,
        account_number=payload.account_number,
        account_name=payload.account_name,
        gl_account_id=payload.gl_account_id,
        currency_code=payload.currency_code,
        account_type=payload.account_type,
        bank_code=payload.bank_code,
        branch_code=payload.branch_code,
        branch_name=payload.branch_name,
        iban=payload.iban,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        notes=payload.notes,
        is_primary=payload.is_primary,
        allow_overdraft=payload.allow_overdraft,
        overdraft_limit=payload.overdraft_limit,
    )
    result = bank_account_service.create(db, organization_id, input_data, user_id)
    return result


@router.get("/accounts/{bank_account_id}", response_model=BankAccountRead)
def get_bank_account(
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a bank account by ID."""
    result = bank_account_service.get(db, _get_org_id(auth), bank_account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Bank account not found")
    # Verify tenant ownership
    if result.organization_id != _get_org_id(auth):
        raise HTTPException(status_code=404, detail="Bank account not found")
    return result


@router.get("/accounts", response_model=ListResponse[BankAccountRead])
def list_bank_accounts(
    status: BankAccountStatus | None = None,
    currency_code: str | None = None,
    account_type: BankAccountType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db_with_org),
):
    """List bank accounts with filters."""
    organization_id = _get_org_id(auth)

    accounts = bank_account_service.list(
        db=db,
        organization_id=organization_id,
        status=status,
        currency_code=currency_code,
        account_type=account_type,
        limit=limit,
        offset=offset,
    )
    total_count = bank_account_service.count(
        db=db,
        organization_id=organization_id,
        status=status,
        currency_code=currency_code,
        account_type=account_type,
    )
    return ListResponse(items=accounts, count=total_count, limit=limit, offset=offset)


async def _update_bank_account(
    request: Request,
    bank_account_id: UUID,
    auth: dict,
    db: Session,
) -> BankAccountRead:
    """Parse partial update payload and delegate to service."""
    payload = await _bank_account_payload_from_request(request, BankAccountUpdate)
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)
    result = bank_account_service.update(
        db, organization_id, bank_account_id, payload, user_id
    )
    return BankAccountRead.model_validate(result)


@router.put("/accounts/{bank_account_id}", response_model=BankAccountRead)
async def update_bank_account(
    request: Request,
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:accounts:update")),
    db: Session = Depends(get_db_with_org),
):
    """Update a bank account."""
    return await _update_bank_account(request, bank_account_id, auth, db)


@router.post("/accounts/{bank_account_id}", response_model=BankAccountRead)
async def update_bank_account_post(
    request: Request,
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:accounts:update")),
    db: Session = Depends(get_db_with_org),
):
    """Update a bank account via form submission."""
    return await _update_bank_account(request, bank_account_id, auth, db)


@router.patch("/accounts/{bank_account_id}/status", response_model=BankAccountRead)
def update_bank_account_status(
    bank_account_id: UUID,
    payload: BankAccountStatusUpdate,
    auth: dict = Depends(require_tenant_permission("banking:accounts:manage")),
    db: Session = Depends(get_db_with_org),
):
    """Update bank account status."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    existing = bank_account_service.get(db, organization_id, bank_account_id)
    if not existing or existing.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

    result = bank_account_service.update_status(
        db,
        organization_id,
        bank_account_id,
        payload.status,
        user_id,
    )
    return result


@router.get("/accounts/{bank_account_id}/balance")
def get_bank_account_gl_balance(
    bank_account_id: UUID,
    as_of_date: date | None = None,
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get GL balance for a bank account."""
    organization_id = _get_org_id(auth)

    existing = bank_account_service.get(db, organization_id, bank_account_id)
    if not existing or existing.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

    as_of_datetime = (
        datetime.combine(as_of_date, datetime.min.time()) if as_of_date else None
    )
    balance = bank_account_service.get_gl_balance(
        db, organization_id, bank_account_id, as_of_datetime
    )
    return {
        "bank_account_id": bank_account_id,
        "as_of_date": as_of_date,
        "balance": balance,
    }


# =============================================================================
# Bank Statements
# =============================================================================


@router.post(
    "/statements/import",
    response_model=StatementImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_bank_statement(
    payload: BankStatementImport,
    auth: dict = Depends(require_tenant_permission("banking:statements:import")),
    db: Session = Depends(get_db_with_org),
):
    """Import a bank statement with lines."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    lines, errors = bank_statement_service.build_line_inputs(payload.lines)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    result = bank_statement_service.import_statement(
        db=db,
        organization_id=organization_id,
        bank_account_id=payload.bank_account_id,
        statement_number=payload.statement_number,
        statement_date=payload.statement_date,
        period_start=payload.period_start,
        period_end=payload.period_end,
        opening_balance=payload.opening_balance,
        closing_balance=payload.closing_balance,
        lines=lines,
        import_source=payload.import_source,
        import_filename=payload.import_filename,
        imported_by=user_id,
    )
    return result


@router.get("/statements/{statement_id}", response_model=BankStatementRead)
def get_bank_statement(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a bank statement by ID."""
    organization_id = _get_org_id(auth)

    result = bank_statement_service.get(db, organization_id, statement_id)
    if not result:
        raise HTTPException(status_code=404, detail="Statement not found")
    return result


@router.get("/statements/{statement_id}/lines", response_model=BankStatementWithLines)
def get_bank_statement_with_lines(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a bank statement with all lines."""
    organization_id = _get_org_id(auth)

    result = bank_statement_service.get_with_lines(db, organization_id, statement_id)
    if not result:
        raise HTTPException(status_code=404, detail="Statement not found")
    return result


@router.get("/statements", response_model=ListResponse[BankStatementRead])
def list_bank_statements(
    bank_account_id: UUID | None = None,
    status: BankStatementStatus | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db_with_org),
):
    """List bank statements with filters."""
    organization_id = _get_org_id(auth)

    statements = bank_statement_service.list(
        db=db,
        organization_id=organization_id,
        bank_account_id=bank_account_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    total_count = bank_statement_service.count(
        db=db,
        organization_id=organization_id,
        bank_account_id=bank_account_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return ListResponse(items=statements, count=total_count, limit=limit, offset=offset)


@router.get(
    "/statements/{statement_id}/unmatched",
    response_model=list[StatementLineRead],
)
def get_unmatched_statement_lines(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get all unmatched lines for a statement."""
    organization_id = _get_org_id(auth)

    statement = bank_statement_service.get(db, organization_id, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    return bank_statement_service.get_unmatched_lines(db, organization_id, statement_id)


@router.delete("/statements/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bank_statement(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:delete")),
    db: Session = Depends(get_db_with_org),
):
    """Delete a bank statement."""
    organization_id = _get_org_id(auth)

    statement = bank_statement_service.get(db, organization_id, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    if not bank_statement_service.delete(db, organization_id, statement_id):
        raise HTTPException(status_code=404, detail="Statement not found")


@router.get(
    "/accounts/{bank_account_id}/statements/summary", response_model=StatementSummary
)
def get_statement_summary(
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get statement summary statistics for a bank account."""
    organization_id = _get_org_id(auth)
    return bank_statement_service.get_statement_summary(
        db, organization_id, bank_account_id
    )


# =============================================================================
# Bank Reconciliation
# =============================================================================


@router.post(
    "/reconciliations",
    response_model=BankReconciliationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_reconciliation(
    payload: ReconciliationCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new bank reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    input_data = ReconciliationInput(
        reconciliation_date=payload.reconciliation_date,
        period_start=payload.period_start,
        period_end=payload.period_end,
        statement_opening_balance=payload.statement_opening_balance,
        statement_closing_balance=payload.statement_closing_balance,
        notes=payload.notes,
    )
    result = bank_reconciliation_service.create_reconciliation(
        db=db,
        organization_id=organization_id,
        bank_account_id=payload.bank_account_id,
        input=input_data,
        prepared_by=user_id,
    )
    return result


@router.get(
    "/reconciliations/{reconciliation_id}", response_model=BankReconciliationRead
)
def get_reconciliation(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a reconciliation by ID."""
    organization_id = _get_org_id(auth)
    result = bank_reconciliation_service.get(db, organization_id, reconciliation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return result


@router.get(
    "/reconciliations/{reconciliation_id}/lines",
    response_model=BankReconciliationWithLines,
)
def get_reconciliation_with_lines(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a reconciliation with all lines."""
    organization_id = _get_org_id(auth)
    result = bank_reconciliation_service.get_with_lines(
        db, organization_id, reconciliation_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return result


@router.get("/reconciliations", response_model=ListResponse[BankReconciliationRead])
def list_reconciliations(
    bank_account_id: UUID | None = None,
    status: ReconciliationStatus | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db_with_org),
):
    """List reconciliations with filters."""
    organization_id = _get_org_id(auth)

    reconciliations = bank_reconciliation_service.list(
        db=db,
        organization_id=organization_id,
        bank_account_id=bank_account_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    total_count = bank_reconciliation_service.count(
        db=db,
        organization_id=organization_id,
        bank_account_id=bank_account_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return ListResponse(
        items=reconciliations, count=total_count, limit=limit, offset=offset
    )


@router.post(
    "/reconciliations/{reconciliation_id}/matches",
    response_model=ReconciliationLineRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reconciliation_match(
    reconciliation_id: UUID,
    payload: ReconciliationMatchCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db_with_org),
):
    """Add a match between statement line and GL entry."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    input_data = ReconciliationMatchInput(
        statement_line_id=payload.statement_line_id,
        journal_line_id=payload.journal_line_id,
        match_type=payload.match_type,
        notes=payload.notes,
    )
    result = bank_reconciliation_service.add_match(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        input=input_data,
        created_by=user_id,
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/multi-match",
    response_model=list[ReconciliationLineRead],
    status_code=status.HTTP_201_CREATED,
)
def add_reconciliation_multi_match(
    reconciliation_id: UUID,
    payload: ReconciliationMultiMatchCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db_with_org),
):
    """Match multiple statement lines to multiple GL entries."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    lines = bank_reconciliation_service.add_multi_match(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        statement_line_ids=payload.statement_line_ids,
        journal_line_ids=payload.journal_line_ids,
        tolerance=payload.tolerance,
        notes=payload.notes,
        created_by=user_id,
    )
    return lines


@router.post(
    "/reconciliations/{reconciliation_id}/adjustments",
    response_model=ReconciliationLineRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reconciliation_adjustment(
    reconciliation_id: UUID,
    payload: ReconciliationAdjustmentCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db_with_org),
):
    """Add a reconciling adjustment."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    result = bank_reconciliation_service.add_adjustment(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        transaction_date=payload.transaction_date,
        amount=payload.amount,
        description=payload.description,
        adjustment_type=payload.adjustment_type,
        adjustment_account_id=payload.adjustment_account_id,
        created_by=user_id,
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/outstanding",
    response_model=ReconciliationLineRead,
    status_code=status.HTTP_201_CREATED,
)
def add_outstanding_item(
    reconciliation_id: UUID,
    payload: ReconciliationOutstandingCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db_with_org),
):
    """Add an outstanding item (deposit in transit or outstanding check)."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    result = bank_reconciliation_service.add_outstanding_item(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        transaction_date=payload.transaction_date,
        amount=payload.amount,
        description=payload.description,
        outstanding_type=payload.outstanding_type,
        reference=payload.reference,
        journal_line_id=payload.journal_line_id,
        created_by=user_id,
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/auto-match",
    response_model=AutoMatchResult,
)
def auto_match_reconciliation(
    reconciliation_id: UUID,
    payload: AutoMatchRequest = AutoMatchRequest(),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db_with_org),
):
    """Automatically match statement lines to GL entries."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    result = bank_reconciliation_service.auto_match(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        tolerance=payload.tolerance,
        created_by=user_id,
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/submit",
    response_model=BankReconciliationRead,
)
def submit_reconciliation_for_review(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:submit")),
    db: Session = Depends(get_db_with_org),
):
    """Submit reconciliation for review."""
    organization_id = _get_org_id(auth)
    result = bank_reconciliation_service.submit_for_review(
        db, organization_id, reconciliation_id
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/approve",
    response_model=BankReconciliationRead,
)
def approve_reconciliation(
    reconciliation_id: UUID,
    payload: ReconciliationApproval = ReconciliationApproval(),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Approve a reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)
    result = bank_reconciliation_service.approve(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        approved_by=user_id,
        notes=payload.notes,
    )
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/reject",
    response_model=BankReconciliationRead,
)
def reject_reconciliation(
    reconciliation_id: UUID,
    payload: ReconciliationRejection,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Reject a reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)
    result = bank_reconciliation_service.reject(
        db=db,
        organization_id=organization_id,
        reconciliation_id=reconciliation_id,
        rejected_by=user_id,
        notes=payload.notes,
    )
    return result


@router.get(
    "/reconciliations/{reconciliation_id}/report",
    response_model=ReconciliationReport,
)
def get_reconciliation_report(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get full reconciliation report."""
    organization_id = _get_org_id(auth)
    report_data = bank_reconciliation_service.get_reconciliation_report(
        db, organization_id, reconciliation_id
    )

    # Convert to schema format
    return ReconciliationReport(
        reconciliation=report_data["reconciliation"],
        summary=report_data["summary"],
        matched_items=report_data["matched_items"],
        adjustments=report_data["adjustments"],
        outstanding_deposits=report_data["outstanding_deposits"],
        outstanding_payments=report_data["outstanding_payments"],
    )


# ------------------------------------------------------------------
# Mono Connect — link / unlink / sync / webhook
# ------------------------------------------------------------------


@router.post("/accounts/{account_id}/link-mono")
def link_mono_account(
    account_id: UUID,
    payload: MonoLinkRequest,
    auth: dict = Depends(require_tenant_permission("banking:account:update")),
    db: Session = Depends(get_db_with_org),
):
    """
    Link a bank account to Mono Connect.

    Receives the authorization code from the Mono Connect widget and
    exchanges it for a permanent account ID.

    Request body: ``{"code": "<mono_widget_auth_code>"}``
    """
    from app.services.finance.banking.mono_sync import MonoSyncService

    try:
        return MonoSyncService(db).link_account(
            _get_org_id(auth),
            account_id,
            payload.code,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _raise_mono_http_error(exc)


@router.post("/accounts/{account_id}/unlink-mono")
def unlink_mono_account(
    account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:account:update")),
    db: Session = Depends(get_db_with_org),
):
    """Remove Mono Connect link from a bank account."""
    organization_id = _get_org_id(auth)
    try:
        account = bank_account_service.unlink_mono(
            db,
            organization_id,
            account_id,
            require_linked=True,
            updated_by=_get_user_id(auth),
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _raise_mono_http_error(exc)

    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    return {"status": "success", "message": "Mono link removed"}


@router.post("/accounts/{account_id}/sync-mono")
def sync_mono_account(
    account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statement:create")),
    db: Session = Depends(get_db_with_org),
):
    """Trigger an incremental Mono sync for a bank account.

    Stateful — the sync computes its own window from the account's
    watermark, so clicking this repeatedly is safe and idempotent.
    """
    from app.services.finance.banking.mono_sync import MonoSyncService

    try:
        return MonoSyncService(db).sync_account_by_id(
            _get_org_id(auth),
            account_id,
            user_id=_get_user_id(auth),
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _raise_mono_http_error(exc)


@router.post("/accounts/{account_id}/refresh-mono")
def refresh_mono_account(
    account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statement:create")),
    db: Session = Depends(get_db_with_org),
):
    """Trigger a real-time data refresh from the upstream bank via Mono.

    Asks Mono's indexer to re-pull transactions from the bank instead of
    serving cached data. When the refresh completes, Mono fires a webhook
    that the existing handler picks up automatically.

    Rate-limited by Mono to one call per account every 5 minutes.
    """
    from app.services.finance.banking.mono_sync import MonoSyncService

    try:
        return MonoSyncService(db).trigger_data_refresh(
            _get_org_id(auth),
            account_id,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _raise_mono_http_error(exc)


# Webhook endpoint — no auth required, uses secret verification
mono_webhook_router = APIRouter(tags=["mono-webhooks"])


@mono_webhook_router.post("/banking/webhook/mono")
async def mono_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Mono Connect webhook events.

    Verifies the request using the mono-webhook-secret header and processes
    account connection and data update events.
    """
    from app.services.finance.banking.mono_sync import MonoSyncService

    try:
        result = MonoSyncService(db).process_webhook(
            request.headers.get("mono-webhook-secret", ""),
            await request.body(),
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _raise_mono_http_error(exc)
        return None  # unreachable — _raise_mono_http_error always raises
    db.commit()
    return result
