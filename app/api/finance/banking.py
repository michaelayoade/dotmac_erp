"""
Banking API Router.

Bank accounts, statements, and reconciliation API endpoints.
All endpoints are tenant-scoped via require_tenant_auth.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.api.deps import require_tenant_auth
from app.services.auth_dependencies import require_tenant_permission
from app.models.finance.banking.bank_account import BankAccountStatus, BankAccountType
from app.models.finance.banking.bank_statement import BankStatementStatus
from app.models.finance.banking.bank_reconciliation import ReconciliationStatus
from app.schemas.finance.banking import (
    # Bank Account
    BankAccountCreate,
    BankAccountRead,
    BankAccountStatusUpdate,
    BankAccountUpdate,
    # Bank Statement
    BankStatementImport,
    BankStatementRead,
    BankStatementWithLines,
    StatementImportResult,
    StatementLineRead,
    StatementSummary,
    # Bank Reconciliation
    AutoMatchRequest,
    AutoMatchResult,
    BankReconciliationRead,
    BankReconciliationWithLines,
    ReconciliationAdjustmentCreate,
    ReconciliationApproval,
    ReconciliationCreate,
    ReconciliationLineRead,
    ReconciliationMatchCreate,
    ReconciliationOutstandingCreate,
    ReconciliationRejection,
    ReconciliationReport,
)
from app.schemas.finance.common import ListResponse
from app.services.finance.banking import (
    BankAccountInput,
    ReconciliationInput,
    ReconciliationMatchInput,
    StatementLineInput,
    bank_account_service,
    bank_reconciliation_service,
    bank_statement_service,
)


router = APIRouter(prefix="/banking", tags=["banking"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_org_id(auth: dict) -> UUID:
    """Extract organization_id from auth dict."""
    org_id = auth.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=403,
            detail="User is not associated with an organization"
        )
    return UUID(org_id)


def _get_user_id(auth: dict) -> UUID:
    """Extract person_id from auth dict."""
    return UUID(auth["person_id"])


# =============================================================================
# Bank Accounts
# =============================================================================


@router.post(
    "/accounts",
    response_model=BankAccountRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bank_account(
    payload: BankAccountCreate,
    auth: dict = Depends(require_tenant_permission("banking:accounts:create")),
    db: Session = Depends(get_db),
):
    """Create a new bank account."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

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
    db.commit()
    return result


@router.get("/accounts/{bank_account_id}", response_model=BankAccountRead)
def get_bank_account(
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db),
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
    status: Optional[BankAccountStatus] = None,
    currency_code: Optional[str] = None,
    account_type: Optional[BankAccountType] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db),
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


@router.put("/accounts/{bank_account_id}", response_model=BankAccountRead)
def update_bank_account(
    bank_account_id: UUID,
    payload: BankAccountUpdate,
    auth: dict = Depends(require_tenant_permission("banking:accounts:update")),
    db: Session = Depends(get_db),
):
    """Update a bank account."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    existing = bank_account_service.get(db, organization_id, bank_account_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Bank account not found")
    # Verify tenant ownership
    if existing.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

    input_data = BankAccountInput(
        bank_name=payload.bank_name or existing.bank_name,
        account_number=existing.account_number,  # Cannot change
        account_name=payload.account_name or existing.account_name,
        gl_account_id=payload.gl_account_id or existing.gl_account_id,
        currency_code=existing.currency_code,  # Cannot change
        account_type=payload.account_type or existing.account_type,
        bank_code=payload.bank_code if payload.bank_code is not None else existing.bank_code,
        branch_code=payload.branch_code if payload.branch_code is not None else existing.branch_code,
        branch_name=payload.branch_name if payload.branch_name is not None else existing.branch_name,
        iban=payload.iban if payload.iban is not None else existing.iban,
        contact_name=payload.contact_name if payload.contact_name is not None else existing.contact_name,
        contact_phone=payload.contact_phone if payload.contact_phone is not None else existing.contact_phone,
        contact_email=payload.contact_email if payload.contact_email is not None else existing.contact_email,
        notes=payload.notes if payload.notes is not None else existing.notes,
        is_primary=payload.is_primary if payload.is_primary is not None else existing.is_primary,
        allow_overdraft=payload.allow_overdraft if payload.allow_overdraft is not None else existing.allow_overdraft,
        overdraft_limit=payload.overdraft_limit if payload.overdraft_limit is not None else existing.overdraft_limit,
    )
    result = bank_account_service.update(db, organization_id, bank_account_id, input_data, user_id)
    db.commit()
    return result


@router.patch("/accounts/{bank_account_id}/status", response_model=BankAccountRead)
def update_bank_account_status(
    bank_account_id: UUID,
    payload: BankAccountStatusUpdate,
    auth: dict = Depends(require_tenant_permission("banking:accounts:manage")),
    db: Session = Depends(get_db),
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
    db.commit()
    return result


@router.get("/accounts/{bank_account_id}/balance")
def get_bank_account_gl_balance(
    bank_account_id: UUID,
    as_of_date: Optional[date] = None,
    auth: dict = Depends(require_tenant_permission("banking:accounts:read")),
    db: Session = Depends(get_db),
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
    return {"bank_account_id": bank_account_id, "as_of_date": as_of_date, "balance": balance}


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
    db: Session = Depends(get_db),
):
    """Import a bank statement with lines."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    # Verify bank account belongs to org
    account = bank_account_service.get(db, _get_org_id(auth), payload.bank_account_id)
    if not account or account.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

    lines = [
        StatementLineInput(
            line_number=line.line_number,
            transaction_date=line.transaction_date,
            transaction_type=line.transaction_type,
            amount=line.amount,
            description=line.description,
            reference=line.reference,
            payee_payer=line.payee_payer,
            bank_reference=line.bank_reference,
            check_number=line.check_number,
            bank_category=line.bank_category,
            bank_code=line.bank_code,
            value_date=line.value_date,
            running_balance=line.running_balance,
            transaction_id=line.transaction_id,
            raw_data=line.raw_data,
        )
        for line in payload.lines
    ]

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
    db.commit()
    return result


@router.get("/statements/{statement_id}", response_model=BankStatementRead)
def get_bank_statement(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Get a bank statement with all lines."""
    organization_id = _get_org_id(auth)

    result = bank_statement_service.get_with_lines(db, organization_id, statement_id)
    if not result:
        raise HTTPException(status_code=404, detail="Statement not found")
    return result


@router.get("/statements", response_model=ListResponse[BankStatementRead])
def list_bank_statements(
    bank_account_id: Optional[UUID] = None,
    status: Optional[BankStatementStatus] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db),
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
    response_model=List[StatementLineRead],
)
def get_unmatched_statement_lines(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db),
):
    """Get all unmatched lines for a statement."""
    organization_id = _get_org_id(auth)

    statement = bank_statement_service.get(db, organization_id, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    return bank_statement_service.get_unmatched_lines(db, statement_id)


@router.delete("/statements/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bank_statement(
    statement_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:delete")),
    db: Session = Depends(get_db),
):
    """Delete a bank statement."""
    organization_id = _get_org_id(auth)

    statement = bank_statement_service.get(db, organization_id, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    if not bank_statement_service.delete(db, statement_id):
        raise HTTPException(status_code=404, detail="Statement not found")
    db.commit()


@router.get("/accounts/{bank_account_id}/statements/summary", response_model=StatementSummary)
def get_statement_summary(
    bank_account_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:statements:read")),
    db: Session = Depends(get_db),
):
    """Get statement summary statistics for a bank account."""
    organization_id = _get_org_id(auth)

    account = bank_account_service.get(db, _get_org_id(auth), bank_account_id)
    if not account or account.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

    return bank_statement_service.get_statement_summary(db, bank_account_id)


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
    db: Session = Depends(get_db),
):
    """Create a new bank reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    # Verify bank account belongs to org
    account = bank_account_service.get(db, _get_org_id(auth), payload.bank_account_id)
    if not account or account.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Bank account not found")

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
    db.commit()
    return result


@router.get("/reconciliations/{reconciliation_id}", response_model=BankReconciliationRead)
def get_reconciliation(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db),
):
    """Get a reconciliation by ID."""
    organization_id = _get_org_id(auth)

    result = bank_reconciliation_service.get(db, reconciliation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if result.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return result


@router.get(
    "/reconciliations/{reconciliation_id}/lines",
    response_model=BankReconciliationWithLines,
)
def get_reconciliation_with_lines(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db),
):
    """Get a reconciliation with all lines."""
    organization_id = _get_org_id(auth)

    result = bank_reconciliation_service.get_with_lines(db, reconciliation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if result.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return result


@router.get("/reconciliations", response_model=ListResponse[BankReconciliationRead])
def list_reconciliations(
    bank_account_id: Optional[UUID] = None,
    status: Optional[ReconciliationStatus] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db),
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
    return ListResponse(items=reconciliations, count=total_count, limit=limit, offset=offset)


@router.post(
    "/reconciliations/{reconciliation_id}/matches",
    response_model=ReconciliationLineRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reconciliation_match(
    reconciliation_id: UUID,
    payload: ReconciliationMatchCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db),
):
    """Add a match between statement line and GL entry."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    input_data = ReconciliationMatchInput(
        statement_line_id=payload.statement_line_id,
        journal_line_id=payload.journal_line_id,
        match_type=payload.match_type,
        notes=payload.notes,
    )
    result = bank_reconciliation_service.add_match(
        db=db,
        reconciliation_id=reconciliation_id,
        input=input_data,
        created_by=user_id,
    )
    db.commit()
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/adjustments",
    response_model=ReconciliationLineRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reconciliation_adjustment(
    reconciliation_id: UUID,
    payload: ReconciliationAdjustmentCreate,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db),
):
    """Add a reconciling adjustment."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.add_adjustment(
        db=db,
        reconciliation_id=reconciliation_id,
        transaction_date=payload.transaction_date,
        amount=payload.amount,
        description=payload.description,
        adjustment_type=payload.adjustment_type,
        adjustment_account_id=payload.adjustment_account_id,
        created_by=user_id,
    )
    db.commit()
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
    db: Session = Depends(get_db),
):
    """Add an outstanding item (deposit in transit or outstanding check)."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.add_outstanding_item(
        db=db,
        reconciliation_id=reconciliation_id,
        transaction_date=payload.transaction_date,
        amount=payload.amount,
        description=payload.description,
        outstanding_type=payload.outstanding_type,
        reference=payload.reference,
        journal_line_id=payload.journal_line_id,
        created_by=user_id,
    )
    db.commit()
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/auto-match",
    response_model=AutoMatchResult,
)
def auto_match_reconciliation(
    reconciliation_id: UUID,
    payload: AutoMatchRequest = AutoMatchRequest(),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:update")),
    db: Session = Depends(get_db),
):
    """Automatically match statement lines to GL entries."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.auto_match(
        db=db,
        reconciliation_id=reconciliation_id,
        tolerance=payload.tolerance,
        created_by=user_id,
    )
    db.commit()
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/submit",
    response_model=BankReconciliationRead,
)
def submit_reconciliation_for_review(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:submit")),
    db: Session = Depends(get_db),
):
    """Submit reconciliation for review."""
    organization_id = _get_org_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.submit_for_review(db, reconciliation_id)
    db.commit()
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/approve",
    response_model=BankReconciliationRead,
)
def approve_reconciliation(
    reconciliation_id: UUID,
    payload: ReconciliationApproval = ReconciliationApproval(),
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:approve")),
    db: Session = Depends(get_db),
):
    """Approve a reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.approve(
        db=db,
        reconciliation_id=reconciliation_id,
        approved_by=user_id,
        notes=payload.notes,
    )
    db.commit()
    return result


@router.post(
    "/reconciliations/{reconciliation_id}/reject",
    response_model=BankReconciliationRead,
)
def reject_reconciliation(
    reconciliation_id: UUID,
    payload: ReconciliationRejection,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:approve")),
    db: Session = Depends(get_db),
):
    """Reject a reconciliation."""
    organization_id = _get_org_id(auth)
    user_id = _get_user_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    result = bank_reconciliation_service.reject(
        db=db,
        reconciliation_id=reconciliation_id,
        rejected_by=user_id,
        notes=payload.notes,
    )
    db.commit()
    return result


@router.get(
    "/reconciliations/{reconciliation_id}/report",
    response_model=ReconciliationReport,
)
def get_reconciliation_report(
    reconciliation_id: UUID,
    auth: dict = Depends(require_tenant_permission("banking:reconciliation:read")),
    db: Session = Depends(get_db),
):
    """Get full reconciliation report."""
    organization_id = _get_org_id(auth)

    recon = bank_reconciliation_service.get(db, reconciliation_id)
    if not recon or recon.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    report_data = bank_reconciliation_service.get_reconciliation_report(db, reconciliation_id)

    # Convert to schema format
    return ReconciliationReport(
        reconciliation=report_data["reconciliation"],
        summary=report_data["summary"],
        matched_items=report_data["matched_items"],
        adjustments=report_data["adjustments"],
        outstanding_deposits=report_data["outstanding_deposits"],
        outstanding_payments=report_data["outstanding_payments"],
    )
