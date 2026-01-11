"""
Import/Export API Endpoints.

Provides REST API endpoints for importing data from CSV files.
"""

import csv
import io
import tempfile
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.services.auth_dependencies import get_current_user_id, get_current_org_id
from app.models.ifrs.gl.account import Account
from app.services.ifrs.import_export import (
    AccountImporter,
    CustomerImporter,
    SupplierImporter,
    ItemImporter,
    AssetImporter,
    BankAccountImporter,
    InvoiceImporter,
    ExpenseImporter,
    CustomerPaymentImporter,
    SupplierPaymentImporter,
    ImportConfig,
    ImportResult,
    ImportStatus,
    PreviewResult,
    COLUMN_ALIASES,
    detect_csv_format,
    get_ar_control_account,
    get_ap_control_account,
)


router = APIRouter(prefix="/import", tags=["Import/Export"])


class EntityType(str, Enum):
    """Supported entity types for import."""
    ACCOUNTS = "accounts"
    CUSTOMERS = "customers"
    SUPPLIERS = "suppliers"
    ITEMS = "items"
    ASSETS = "assets"
    BANK_ACCOUNTS = "bank_accounts"
    INVOICES = "invoices"
    EXPENSES = "expenses"
    CUSTOMER_PAYMENTS = "customer_payments"
    SUPPLIER_PAYMENTS = "supplier_payments"


class ImportOptions(BaseModel):
    """Options for import operation."""
    skip_duplicates: bool = Field(default=True, description="Skip duplicate entries")
    dry_run: bool = Field(default=False, description="Validate without saving")
    batch_size: int = Field(default=100, ge=1, le=1000, description="Records per batch")


class ImportResultResponse(BaseModel):
    """Response model for import results."""
    entity_type: str
    status: str
    total_rows: int
    imported_count: int
    skipped_count: int
    duplicate_count: int
    error_count: int
    success_rate: str
    duration_seconds: float
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @classmethod
    def from_import_result(cls, result: ImportResult) -> "ImportResultResponse":
        return cls(
            entity_type=result.entity_type,
            status=result.status.value,
            total_rows=result.total_rows,
            imported_count=result.imported_count,
            skipped_count=result.skipped_count,
            duplicate_count=result.duplicate_count,
            error_count=result.error_count,
            success_rate=f"{result.success_rate:.1f}%",
            duration_seconds=round(result.duration_seconds, 2),
            errors=[str(e) for e in result.errors[:50]],
            warnings=[str(w) for w in result.warnings[:50]],
        )


class ColumnMappingResponse(BaseModel):
    """Column mapping with confidence score."""
    source: str
    target: str
    confidence: float
    samples: List[str] = Field(default_factory=list)


class ImportPreviewResponse(BaseModel):
    """Enhanced response for import preview with visual data."""
    entity_type: str
    total_rows: int
    sample_data: List[Dict[str, Any]]
    detected_columns: List[str]
    required_columns: List[str]
    optional_columns: List[str]
    missing_required: List[str]
    column_mappings: List[ColumnMappingResponse]
    validation_errors: List[str]
    detected_format: str  # zoho, quickbooks, xero, sage, wave, freshbooks, generic
    is_valid: bool

    @classmethod
    def from_preview_result(cls, result: PreviewResult) -> "ImportPreviewResponse":
        return cls(
            entity_type=result.entity_type,
            total_rows=result.total_rows,
            sample_data=result.sample_data,
            detected_columns=result.detected_columns,
            required_columns=result.required_columns,
            optional_columns=result.optional_columns,
            missing_required=result.missing_required,
            column_mappings=[
                ColumnMappingResponse(
                    source=m.source_column,
                    target=m.target_field,
                    confidence=m.confidence,
                    samples=m.sample_values[:3],
                )
                for m in result.column_mappings
            ],
            validation_errors=result.validation_errors,
            detected_format=result.detected_format,
            is_valid=result.is_valid,
        )


def _find_account_by_type(db: Session, org_id: UUID, subledger_type: str) -> Optional[UUID]:
    """Find account by subledger type."""
    result = db.execute(
        select(Account).where(
            Account.organization_id == org_id,
            Account.subledger_type == subledger_type,
        )
    ).scalar_one_or_none()
    return result.account_id if result else None


def _find_account_by_name_pattern(db: Session, org_id: UUID, pattern: str) -> Optional[UUID]:
    """Find account by name pattern."""
    result = db.execute(
        select(Account).where(
            Account.organization_id == org_id,
            Account.account_name.ilike(f"%{pattern}%"),
        )
    ).first()
    return result[0].account_id if result else None


@router.get("/supported-types")
async def get_supported_types() -> Dict[str, Any]:
    """Get list of supported entity types and their required columns."""
    return {
        "entity_types": [
            {
                "type": "accounts",
                "name": "Chart of Accounts",
                "description": "Import account categories and accounts",
                "required_columns": ["Account Name", "Account Type"],
                "optional_columns": ["Account Code", "Description", "Currency", "Status"],
                "import_order": 1,
            },
            {
                "type": "customers",
                "name": "Customers",
                "description": "Import customer contacts",
                "required_columns": ["Display Name OR Company Name"],
                "optional_columns": ["Phone", "Email", "Currency Code", "Credit Limit", "Billing Address"],
                "import_order": 2,
            },
            {
                "type": "suppliers",
                "name": "Suppliers/Vendors",
                "description": "Import supplier/vendor contacts",
                "required_columns": ["Display Name OR Contact Name"],
                "optional_columns": ["Phone", "Email", "Currency Code", "Payment Terms"],
                "import_order": 3,
            },
            {
                "type": "items",
                "name": "Inventory Items",
                "description": "Import inventory products and services",
                "required_columns": ["Item Name OR Name"],
                "optional_columns": ["Item Code", "SKU", "Description", "Unit Price", "Category"],
                "import_order": 4,
            },
            {
                "type": "assets",
                "name": "Fixed Assets",
                "description": "Import fixed assets",
                "required_columns": ["Asset Name"],
                "optional_columns": ["Asset Number", "Acquisition Date", "Cost", "Category"],
                "import_order": 5,
            },
            {
                "type": "bank_accounts",
                "name": "Bank Accounts",
                "description": "Import bank accounts",
                "required_columns": ["Bank Name", "Account Number"],
                "optional_columns": ["Account Type", "Currency", "IBAN", "Branch"],
                "import_order": 6,
            },
            {
                "type": "invoices",
                "name": "Customer Invoices",
                "description": "Import customer invoices",
                "required_columns": ["Customer Name", "Total Amount"],
                "optional_columns": ["Invoice Number", "Invoice Date", "Due Date", "Status"],
                "import_order": 7,
                "prerequisites": ["customers"],
            },
            {
                "type": "expenses",
                "name": "Expenses",
                "description": "Import expense entries",
                "required_columns": ["Amount"],
                "optional_columns": ["Date", "Description", "Category", "Payment Method"],
                "import_order": 8,
                "prerequisites": ["accounts"],
            },
            {
                "type": "customer_payments",
                "name": "Customer Payments",
                "description": "Import customer payment receipts",
                "required_columns": ["Customer Name", "Amount"],
                "optional_columns": ["Payment Date", "Reference", "Payment Method"],
                "import_order": 9,
                "prerequisites": ["customers"],
            },
            {
                "type": "supplier_payments",
                "name": "Supplier Payments",
                "description": "Import supplier/vendor payments",
                "required_columns": ["Vendor Name", "Amount"],
                "optional_columns": ["Payment Date", "Reference", "Payment Method"],
                "import_order": 10,
                "prerequisites": ["suppliers"],
            },
        ],
        "recommended_order": [
            "accounts", "customers", "suppliers", "items", "assets",
            "bank_accounts", "invoices", "expenses", "customer_payments", "supplier_payments"
        ]
    }


@router.post("/preview/{entity_type}")
async def preview_import(
    entity_type: EntityType,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
    org_id: UUID = Depends(get_current_org_id),
    user_id: UUID = Depends(get_current_user_id),
) -> ImportPreviewResponse:
    """
    Preview import with enhanced visual data and validation.

    Analyzes the CSV file and returns:
    - Sample data (first 10 rows) with data table format
    - Detected columns with auto-mapping suggestions
    - Column confidence scores
    - Detected source format (Zoho, QuickBooks, Xero, Sage, etc.)
    - Validation errors and warnings
    - Required vs optional columns
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported"
        )

    content = await file.read()

    # Save to temp file for processing
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Create config for preview
        config = ImportConfig(
            organization_id=org_id,
            user_id=user_id,
            skip_duplicates=True,
            dry_run=True,
        )

        # Get the appropriate importer and use its preview method
        try:
            importer = _get_importer(entity_type, db, config)
        except ValueError:
            # If we can't get full importer (missing accounts), use AccountImporter for preview
            if entity_type == EntityType.ACCOUNTS:
                importer = AccountImporter(db, config)
            else:
                # Use a dummy preview for other types
                importer = AccountImporter(db, config)

        # Use the enhanced preview method
        preview_result = importer.preview_file(tmp_path, max_rows=10)

        return ImportPreviewResponse.from_preview_result(preview_result)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preview failed: {str(e)}"
        )
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/{entity_type}")
async def import_data(
    entity_type: EntityType,
    file: UploadFile = File(...),
    skip_duplicates: bool = Form(default=True),
    dry_run: bool = Form(default=False),
    batch_size: int = Form(default=100),
    db: Session = Depends(get_db_session),
    org_id: UUID = Depends(get_current_org_id),
    user_id: UUID = Depends(get_current_user_id),
) -> ImportResultResponse:
    """
    Import data from a CSV file.

    Supports various entity types including accounts, customers, suppliers,
    items, assets, bank accounts, invoices, expenses, and payments.
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported"
        )

    # Read file content
    content = await file.read()

    # Save to temp file for processing
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = ImportConfig(
            organization_id=org_id,
            user_id=user_id,
            skip_duplicates=skip_duplicates,
            dry_run=dry_run,
            batch_size=batch_size,
        )

        # Get the appropriate importer
        importer = _get_importer(entity_type, db, config)

        # Run import
        result = importer.import_file(tmp_path)

        # Commit if not dry run and successful
        if not dry_run and result.status in (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_ERRORS):
            db.commit()
        else:
            db.rollback()

        return ImportResultResponse.from_import_result(result)

    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


def _get_importer(entity_type: EntityType, db: Session, config: ImportConfig):
    """Get the appropriate importer for the entity type."""
    org_id = config.organization_id

    if entity_type == EntityType.ACCOUNTS:
        return AccountImporter(db, config)

    elif entity_type == EntityType.CUSTOMERS:
        ar_control_id = get_ar_control_account(db, org_id)
        if not ar_control_id:
            ar_control_id = _find_account_by_name_pattern(db, org_id, "receivable")
        if not ar_control_id:
            raise ValueError("No AR control account found. Import accounts first.")
        return CustomerImporter(db, config, ar_control_id)

    elif entity_type == EntityType.SUPPLIERS:
        ap_control_id = get_ap_control_account(db, org_id)
        if not ap_control_id:
            ap_control_id = _find_account_by_name_pattern(db, org_id, "payable")
        if not ap_control_id:
            raise ValueError("No AP control account found. Import accounts first.")
        return SupplierImporter(db, config, ap_control_id)

    elif entity_type == EntityType.ITEMS:
        inv_account = _find_account_by_type(db, org_id, "INVENTORY")
        if not inv_account:
            inv_account = _find_account_by_name_pattern(db, org_id, "inventory")
        if not inv_account:
            raise ValueError("No inventory account found. Import accounts first.")
        return ItemImporter(db, config, inv_account, inv_account, inv_account, inv_account)

    elif entity_type == EntityType.ASSETS:
        asset_account = _find_account_by_type(db, org_id, "ASSET")
        if not asset_account:
            asset_account = _find_account_by_name_pattern(db, org_id, "fixed asset")
        if not asset_account:
            raise ValueError("No fixed asset account found. Import accounts first.")
        return AssetImporter(db, config, asset_account, asset_account, asset_account, asset_account)

    elif entity_type == EntityType.BANK_ACCOUNTS:
        gl_account = _find_account_by_type(db, org_id, "BANK")
        return BankAccountImporter(db, config, gl_account)

    elif entity_type == EntityType.INVOICES:
        ar_control_id = get_ar_control_account(db, org_id)
        if not ar_control_id:
            raise ValueError("No AR control account found. Import accounts first.")
        revenue_account = _find_account_by_name_pattern(db, org_id, "sales") or ar_control_id
        return InvoiceImporter(db, config, ar_control_id, revenue_account)

    elif entity_type == EntityType.EXPENSES:
        expense_account = _find_account_by_name_pattern(db, org_id, "expense")
        if not expense_account:
            raise ValueError("No expense account found. Import accounts first.")
        payment_account = _find_account_by_type(db, org_id, "BANK")
        return ExpenseImporter(db, config, expense_account, payment_account)

    elif entity_type == EntityType.CUSTOMER_PAYMENTS:
        from app.models.ifrs.banking.bank_account import BankAccount
        result = db.execute(
            select(BankAccount).where(BankAccount.organization_id == org_id)
        ).first()
        bank_account_id = result[0].bank_account_id if result else None
        return CustomerPaymentImporter(db, config, bank_account_id)

    elif entity_type == EntityType.SUPPLIER_PAYMENTS:
        from app.models.ifrs.banking.bank_account import BankAccount
        result = db.execute(
            select(BankAccount).where(BankAccount.organization_id == org_id)
        ).first()
        if not result:
            raise ValueError("No bank account found. Import bank accounts first.")
        return SupplierPaymentImporter(db, config, result[0].bank_account_id)

    else:
        raise ValueError(f"Unsupported entity type: {entity_type}")


def _get_column_mappings(entity_type: EntityType, columns: List[str]) -> Dict[str, str]:
    """Get column mappings based on entity type and detected columns."""
    mappings = {}

    # Common column name variations
    column_aliases = {
        # Accounts
        "Account Name": ["Account Name", "Name", "Account"],
        "Account Code": ["Account Code", "Code", "Account Number"],
        "Account Type": ["Account Type", "Type", "Category"],
        # Contacts
        "Display Name": ["Display Name", "Name", "Customer Name", "Vendor Name"],
        "Company Name": ["Company Name", "Company", "Organization"],
        "Phone": ["Phone", "Phone Number", "Telephone", "Contact Phone"],
        "Email": ["Email", "EmailID", "Email Address"],
        # Items
        "Item Name": ["Item Name", "Name", "Product Name", "Description"],
        "Item Code": ["Item Code", "SKU", "Product Code", "Code"],
        # Common
        "Amount": ["Amount", "Total", "Total Amount", "Value"],
        "Date": ["Date", "Invoice Date", "Payment Date", "Expense Date"],
        "Currency": ["Currency", "Currency Code"],
        "Status": ["Status", "State"],
    }

    for target_col, aliases in column_aliases.items():
        for alias in aliases:
            if alias in columns:
                mappings[alias] = target_col
                break

    return mappings


def _validate_preview(entity_type: EntityType, rows: List[Dict], mappings: Dict) -> List[str]:
    """Validate preview data and return errors."""
    errors = []

    if not rows:
        errors.append("No data rows found in file")
        return errors

    # Check for required columns based on entity type
    required_checks = {
        EntityType.ACCOUNTS: ["Account Name", "Account Type"],
        EntityType.CUSTOMERS: ["Display Name", "Company Name", "Customer Name"],
        EntityType.SUPPLIERS: ["Display Name", "Vendor Name", "Contact Name"],
        EntityType.ITEMS: ["Item Name", "Name", "Product Name"],
        EntityType.BANK_ACCOUNTS: ["Bank Name", "Account Number"],
        EntityType.INVOICES: ["Customer Name", "Customer", "Amount", "Total"],
    }

    if entity_type in required_checks:
        required = required_checks[entity_type]
        found = False
        for req in required:
            if any(req in row for row in rows[:1]):
                found = True
                break
        if not found:
            errors.append(f"Missing required column. Expected one of: {', '.join(required)}")

    # Check for empty required values
    sample_errors = 0
    for i, row in enumerate(rows[:10]):
        if all(not v or v.strip() == '' for v in row.values()):
            sample_errors += 1

    if sample_errors > 0:
        errors.append(f"Found {sample_errors} empty rows in sample data")

    return errors
