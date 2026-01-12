"""
Opening Balance API Endpoints.

Provides REST API endpoints for importing opening balances.
"""

import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.services.auth_dependencies import get_current_user_id, get_current_org_id
from app.services.ifrs.import_export import ImportConfig
from app.services.ifrs.import_export.opening_balance import (
    OpeningBalanceImporter,
    OpeningBalancePreview,
    OpeningBalanceResult,
    get_opening_balance_template,
)


router = APIRouter(prefix="/opening-balance", tags=["Opening Balance"])


# ═══════════════════════════════════════════════════════════════════════════════
# Response Models
# ═══════════════════════════════════════════════════════════════════════════════


class OpeningBalanceLineResponse(BaseModel):
    """Response model for a single opening balance line."""
    account_name: str
    account_type: str
    debit: float
    credit: float
    normal_balance: str
    notes: Optional[str]
    matched: bool
    matched_account: Optional[str]


class OpeningBalancePreviewResponse(BaseModel):
    """Response model for opening balance preview."""
    total_rows: int
    total_debit: float
    total_credit: float
    is_balanced: bool
    difference: float
    matched_count: int
    unmatched_count: int
    unmatched_accounts: List[str]
    validation_errors: List[str]
    entry_date: str
    detected_format: str
    lines: List[OpeningBalanceLineResponse]

    @classmethod
    def from_preview(cls, preview: OpeningBalancePreview) -> "OpeningBalancePreviewResponse":
        return cls(
            total_rows=preview.total_rows,
            total_debit=float(preview.total_debit),
            total_credit=float(preview.total_credit),
            is_balanced=preview.is_balanced,
            difference=float(preview.difference),
            matched_count=preview.matched_count,
            unmatched_count=preview.unmatched_count,
            unmatched_accounts=preview.unmatched_accounts,
            validation_errors=preview.validation_errors[:20],
            entry_date=preview.entry_date.isoformat(),
            detected_format=preview.detected_format,
            lines=[
                OpeningBalanceLineResponse(
                    account_name=line.account_name,
                    account_type=line.account_type,
                    debit=float(line.debit),
                    credit=float(line.credit),
                    normal_balance=line.normal_balance.value,
                    notes=line.notes,
                    matched=line.account_id is not None,
                    matched_account=line.matched_account_name,
                )
                for line in preview.lines
            ],
        )


class OpeningBalanceImportResponse(BaseModel):
    """Response model for opening balance import result."""
    success: bool
    journal_entry_id: Optional[str]
    journal_number: Optional[str]
    total_debit: float
    total_credit: float
    lines_created: int
    errors: List[str]
    warnings: List[str]

    @classmethod
    def from_result(cls, result: OpeningBalanceResult) -> "OpeningBalanceImportResponse":
        return cls(
            success=result.success,
            journal_entry_id=str(result.journal_entry_id) if result.journal_entry_id else None,
            journal_number=result.journal_number,
            total_debit=float(result.total_debit),
            total_credit=float(result.total_credit),
            lines_created=result.lines_created,
            errors=result.errors[:50],
            warnings=result.warnings[:50],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/template")
async def get_template() -> Dict[str, Any]:
    """
    Get CSV template for opening balances.

    Returns a sample CSV template with the expected format.
    """
    return {
        "template": get_opening_balance_template(),
        "columns": [
            {"name": "Account Name", "required": True, "description": "Name of the account"},
            {"name": "Account Type", "required": False, "description": "Type of account (Asset, Liability, etc.)"},
            {"name": "Debit", "required": True, "description": "Debit amount (0 if credit)"},
            {"name": "Credit", "required": True, "description": "Credit amount (0 if debit)"},
            {"name": "Normal Balance", "required": False, "description": "DEBIT or CREDIT"},
            {"name": "Notes", "required": False, "description": "Additional notes"},
            {"name": "COA Match", "required": False, "description": "Exact account name to match in Chart of Accounts"},
        ],
        "example_rows": [
            {
                "Account Name": "Cash and Cash Equivalent",
                "Account Type": "Bank",
                "Debit": 100000.00,
                "Credit": 0,
                "Normal Balance": "DEBIT",
                "Notes": "Bank balances",
                "COA Match": "Zenith Bank",
            },
            {
                "Account Name": "Retained Earnings",
                "Account Type": "Equity",
                "Debit": 0,
                "Credit": 100000.00,
                "Normal Balance": "CREDIT",
                "Notes": "Opening retained earnings",
                "COA Match": "Retained Earnings",
            },
        ],
    }


@router.post("/preview")
async def preview_opening_balance(
    file: UploadFile = File(...),
    entry_date: str = Form(..., description="Opening balance date (YYYY-MM-DD)"),
    db: Session = Depends(get_db_session),
    org_id: UUID = Depends(get_current_org_id),
    user_id: UUID = Depends(get_current_user_id),
) -> OpeningBalancePreviewResponse:
    """
    Preview opening balance file before importing.

    Analyzes the CSV file and returns:
    - Total debits and credits
    - Whether the entry is balanced
    - Account matching results
    - Validation errors

    This does not create any records.
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported"
        )

    # Parse entry date
    try:
        parsed_date = date.fromisoformat(entry_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    content = await file.read()

    # Save to temp file
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = ImportConfig(
            organization_id=org_id,
            user_id=user_id,
        )

        importer = OpeningBalanceImporter(db, config)
        preview = importer.preview_file(tmp_path, parsed_date)

        return OpeningBalancePreviewResponse.from_preview(preview)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preview failed: {str(e)}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/import")
async def import_opening_balance(
    file: UploadFile = File(...),
    entry_date: str = Form(..., description="Opening balance date (YYYY-MM-DD)"),
    description: str = Form(default="Opening Balance Entry", description="Journal entry description"),
    auto_create_accounts: bool = Form(default=False, description="Auto-create missing accounts"),
    db: Session = Depends(get_db_session),
    org_id: UUID = Depends(get_current_org_id),
    user_id: UUID = Depends(get_current_user_id),
) -> OpeningBalanceImportResponse:
    """
    Import opening balances and create journal entry.

    Creates an OPENING type journal entry with all balance lines.
    The journal is created in DRAFT status for review before posting.

    Requirements:
    - CSV file with Account Name, Debit, and Credit columns
    - Total debits must equal total credits
    - All accounts must exist in Chart of Accounts (unless auto_create_accounts=True)
    - A fiscal period must exist for the entry date
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported"
        )

    # Parse entry date
    try:
        parsed_date = date.fromisoformat(entry_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    content = await file.read()

    # Save to temp file
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = ImportConfig(
            organization_id=org_id,
            user_id=user_id,
        )

        importer = OpeningBalanceImporter(db, config)
        result = importer.import_file(
            tmp_path,
            parsed_date,
            description=description,
            auto_create_accounts=auto_create_accounts,
        )

        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Import failed",
                    "errors": result.errors,
                    "warnings": result.warnings,
                }
            )

        return OpeningBalanceImportResponse.from_result(result)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/status/{journal_entry_id}")
async def get_import_status(
    journal_entry_id: UUID,
    db: Session = Depends(get_db_session),
    org_id: UUID = Depends(get_current_org_id),
) -> Dict[str, Any]:
    """
    Get status of an imported opening balance journal entry.

    Returns journal entry details and line count.
    """
    from sqlalchemy import select, func
    from app.models.ifrs.gl.journal_entry import JournalEntry
    from app.models.ifrs.gl.journal_entry_line import JournalEntryLine

    journal = db.execute(
        select(JournalEntry).where(
            JournalEntry.journal_entry_id == journal_entry_id,
            JournalEntry.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not journal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Journal entry not found"
        )

    line_count = db.execute(
        select(func.count(JournalEntryLine.line_id)).where(
            JournalEntryLine.journal_entry_id == journal_entry_id
        )
    ).scalar()

    return {
        "journal_entry_id": str(journal.journal_entry_id),
        "journal_number": journal.journal_number,
        "journal_type": journal.journal_type.value,
        "entry_date": journal.entry_date.isoformat(),
        "description": journal.description,
        "status": journal.status.value,
        "total_debit": float(journal.total_debit),
        "total_credit": float(journal.total_credit),
        "line_count": line_count,
        "created_at": journal.created_at.isoformat(),
    }
