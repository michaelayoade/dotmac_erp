"""
GL API Router.

General Ledger API endpoints for chart of accounts, journal entries,
fiscal periods, and account balances.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.gl import (
    AccountCreate,
    AccountUpdate,
    AccountRead,
    FiscalPeriodCreate,
    FiscalPeriodRead,
    JournalEntryCreate,
    JournalEntryRead,
    AccountBalanceRead,
    TrialBalanceRead,
)
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.services.ifrs.gl import (
    chart_of_accounts_service,
    fiscal_period_service,
    journal_service,
    ledger_posting_service,
    AccountInput,
    JournalInput,
    JournalLineInput,
)


router = APIRouter(prefix="/gl", tags=["general-ledger"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Chart of Accounts
# =============================================================================

@router.post("/accounts", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    organization_id: UUID = Query(...),
    category_id: UUID = Query(..., description="Account category ID"),
    db: Session = Depends(get_db),
):
    """Create a new GL account."""
    input_data = AccountInput(
        account_code=payload.account_code,
        account_name=payload.account_name,
        category_id=category_id,
        account_type=payload.account_type,
        normal_balance=payload.normal_balance,
        description=payload.description,
        is_reconciliation_required=payload.is_reconcilable,
    )
    return chart_of_accounts_service.create_account(db, organization_id, input_data)


@router.get("/accounts/{account_id}", response_model=AccountRead)
def get_account(
    account_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a GL account by ID."""
    return chart_of_accounts_service.get(db, str(account_id))


@router.get("/accounts", response_model=ListResponse[AccountRead])
def list_accounts(
    organization_id: UUID = Query(...),
    account_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    parent_account_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List GL accounts with filters."""
    accounts = chart_of_accounts_service.list(
        db=db,
        organization_id=str(organization_id),
        account_type=account_type,
        is_active=is_active,
        parent_account_id=str(parent_account_id) if parent_account_id else None,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=accounts,
        count=len(accounts),
        limit=limit,
        offset=offset,
    )


@router.patch("/accounts/{account_id}", response_model=AccountRead)
def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Update a GL account."""
    return chart_of_accounts_service.update_account(
        db=db,
        organization_id=organization_id,
        account_id=account_id,
        account_name=payload.account_name,
        description=payload.description,
        is_active=payload.is_active,
    )


@router.post("/accounts/{account_id}/deactivate", response_model=AccountRead)
def deactivate_account(
    account_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Deactivate a GL account."""
    return chart_of_accounts_service.deactivate_account(db, organization_id, account_id)


# =============================================================================
# Fiscal Periods
# =============================================================================

@router.post("/fiscal-periods", response_model=FiscalPeriodRead, status_code=status.HTTP_201_CREATED)
def create_fiscal_period(
    payload: FiscalPeriodCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new fiscal period."""
    return fiscal_period_service.create_period(
        db=db,
        organization_id=organization_id,
        period_name=payload.period_name,
        period_type=payload.period_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        fiscal_year=payload.fiscal_year,
    )


@router.get("/fiscal-periods/{period_id}", response_model=FiscalPeriodRead)
def get_fiscal_period(
    period_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a fiscal period by ID."""
    return fiscal_period_service.get(db, str(period_id))


@router.get("/fiscal-periods", response_model=ListResponse[FiscalPeriodRead])
def list_fiscal_periods(
    organization_id: UUID = Query(...),
    fiscal_year: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List fiscal periods with filters."""
    periods = fiscal_period_service.list(
        db=db,
        organization_id=str(organization_id),
        fiscal_year=fiscal_year,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=periods,
        count=len(periods),
        limit=limit,
        offset=offset,
    )


@router.post("/fiscal-periods/{period_id}/open", response_model=FiscalPeriodRead)
def open_fiscal_period(
    period_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Open a fiscal period for posting."""
    return fiscal_period_service.open_period(db, organization_id, period_id)


@router.post("/fiscal-periods/{period_id}/close", response_model=FiscalPeriodRead)
def close_fiscal_period(
    period_id: UUID,
    organization_id: UUID = Query(...),
    closed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Close a fiscal period."""
    return fiscal_period_service.close_period(db, organization_id, period_id, closed_by_user_id)


# =============================================================================
# Journal Entries
# =============================================================================

@router.post("/journal-entries", response_model=JournalEntryRead, status_code=status.HTTP_201_CREATED)
def create_journal_entry(
    payload: JournalEntryCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new journal entry."""
    lines = [
        JournalLineInput(
            account_id=line.account_id,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
            currency_code=line.currency_code,
            description=line.description,
            cost_center_id=line.cost_center_id,
            project_id=line.project_id,
        )
        for line in payload.lines
    ]

    from app.models.ifrs.gl.journal_entry import JournalType

    input_data = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=payload.journal_date,
        posting_date=payload.journal_date,
        description=payload.description,
        source_module=payload.source_module,
        source_document_type=payload.source_document_type,
        source_document_id=payload.source_document_id,
        reference=payload.reference_number,
        lines=lines,
    )

    return journal_service.create_entry(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/journal-entries/{entry_id}", response_model=JournalEntryRead)
def get_journal_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a journal entry by ID."""
    return journal_service.get(db, str(entry_id))


@router.get("/journal-entries", response_model=ListResponse[JournalEntryRead])
def list_journal_entries(
    organization_id: UUID = Query(...),
    fiscal_period_id: Optional[UUID] = None,
    source_module: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List journal entries with filters."""
    entries = journal_service.list(
        db=db,
        organization_id=str(organization_id),
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        source_module=source_module,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=entries,
        count=len(entries),
        limit=limit,
        offset=offset,
    )


@router.post("/journal-entries/{entry_id}/post", response_model=PostingResultSchema)
def post_journal_entry(
    entry_id: UUID,
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post a journal entry to the ledger."""
    result = ledger_posting_service.post_entry(
        db=db,
        organization_id=organization_id,
        entry_id=entry_id,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/journal-entries/{entry_id}/reverse", response_model=JournalEntryRead)
def reverse_journal_entry(
    entry_id: UUID,
    reversal_date: date = Query(...),
    organization_id: UUID = Query(...),
    reversed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Reverse a posted journal entry."""
    return journal_service.reverse_entry(
        db=db,
        organization_id=organization_id,
        entry_id=entry_id,
        reversal_date=reversal_date,
        reversed_by_user_id=reversed_by_user_id,
    )


# =============================================================================
# Account Balances
# =============================================================================

@router.get("/balances/{account_id}", response_model=AccountBalanceRead)
def get_account_balance(
    account_id: UUID,
    fiscal_period_id: UUID = Query(...),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get account balance for a fiscal period."""
    from app.services.ifrs.gl import balance_service

    balance = balance_service.get_balance(
        db=db,
        organization_id=str(organization_id),
        account_id=str(account_id),
        fiscal_period_id=str(fiscal_period_id),
    )
    if not balance:
        raise HTTPException(status_code=404, detail="Balance not found")

    account = chart_of_accounts_service.get(db, str(account_id))
    return AccountBalanceRead(
        account_id=balance.account_id,
        account_code=account.account_code,
        account_name=account.account_name,
        fiscal_period_id=balance.fiscal_period_id,
        opening_balance=balance.opening_balance,
        period_debit=balance.period_debit,
        period_credit=balance.period_credit,
        closing_balance=balance.closing_balance,
        currency_code=balance.currency_code,
    )


@router.get("/trial-balance", response_model=TrialBalanceRead)
def get_trial_balance(
    organization_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    as_of_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Get trial balance for a fiscal period."""
    from app.services.ifrs.gl import balance_service

    result = balance_service.get_trial_balance(
        db=db,
        organization_id=str(organization_id),
        fiscal_period_id=str(fiscal_period_id),
        as_of_date=as_of_date,
    )
    return result
