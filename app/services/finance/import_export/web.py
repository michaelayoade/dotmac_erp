"""
Import/Export Web Service.

Provides web-specific service methods for data import functionality.
Both API and Web routes can use this service layer.
"""

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.gl.account import Account
from app.services.imports.formats import (
    SPREADSHEET_EXTENSIONS,
    spreadsheet_formats_label,
)
from app.services.upload_utils import get_env_max_bytes, write_upload_to_temp

from . import (
    AccountImporter,
    AssetImporter,
    BankAccountImporter,
    CustomerImporter,
    CustomerPaymentImporter,
    ExpenseImporter,
    ImportConfig,
    ImportResult,
    ImportStatus,
    InvoiceImporter,
    ItemImporter,
    PreviewResult,
    SupplierImporter,
    SupplierPaymentImporter,
    get_ap_control_account,
    get_ar_control_account,
)

logger = logging.getLogger(__name__)


class ImportWebService:
    """Service for handling data imports from the web interface."""

    SUPPORTED_ENTITY_TYPES = {
        "accounts": "Chart of Accounts",
        "customers": "Customers",
        "suppliers": "Suppliers/Vendors",
        "items": "Inventory Items",
        "assets": "Fixed Assets",
        "bank_accounts": "Bank Accounts",
        "invoices": "Customer Invoices",
        "expenses": "Expenses",
        "customer_payments": "Customer Payments",
        "supplier_payments": "Supplier Payments",
    }

    @staticmethod
    def _find_account_by_type(
        db: Session, org_id: UUID, subledger_type: str
    ) -> UUID | None:
        """Find account by subledger type."""
        result = db.execute(
            select(Account).where(
                Account.organization_id == org_id,
                Account.subledger_type == subledger_type,
            )
        ).scalar_one_or_none()
        return result.account_id if result else None

    @staticmethod
    def _find_account_by_name_pattern(
        db: Session, org_id: UUID, pattern: str
    ) -> UUID | None:
        """Find account by name pattern."""
        result = db.execute(
            select(Account).where(
                Account.organization_id == org_id,
                Account.account_name.ilike(f"%{pattern}%"),
            )
        ).first()
        return result[0].account_id if result else None

    @staticmethod
    def _get_importer(entity_type: str, db: Session, config: ImportConfig):
        """Get the appropriate importer for the entity type."""
        org_id = config.organization_id

        if entity_type == "accounts":
            return AccountImporter(db, config)

        elif entity_type == "customers":
            ar_control_id = get_ar_control_account(db, org_id)
            if not ar_control_id:
                ar_control_id = ImportWebService._find_account_by_name_pattern(
                    db, org_id, "receivable"
                )
            if not ar_control_id:
                raise ValueError("No AR control account found. Import accounts first.")
            return CustomerImporter(db, config, ar_control_id)

        elif entity_type == "suppliers":
            ap_control_id = get_ap_control_account(db, org_id)
            if not ap_control_id:
                ap_control_id = ImportWebService._find_account_by_name_pattern(
                    db, org_id, "payable"
                )
            if not ap_control_id:
                raise ValueError("No AP control account found. Import accounts first.")
            return SupplierImporter(db, config, ap_control_id)

        elif entity_type == "items":
            inv_account = ImportWebService._find_account_by_type(
                db, org_id, "INVENTORY"
            )
            if not inv_account:
                inv_account = ImportWebService._find_account_by_name_pattern(
                    db, org_id, "inventory"
                )
            if not inv_account:
                raise ValueError("No inventory account found. Import accounts first.")
            return ItemImporter(
                db, config, inv_account, inv_account, inv_account, inv_account
            )

        elif entity_type == "assets":
            asset_account = ImportWebService._find_account_by_type(db, org_id, "ASSET")
            if not asset_account:
                asset_account = ImportWebService._find_account_by_name_pattern(
                    db, org_id, "fixed asset"
                )
            if not asset_account:
                raise ValueError("No fixed asset account found. Import accounts first.")
            return AssetImporter(
                db, config, asset_account, asset_account, asset_account, asset_account
            )

        elif entity_type == "bank_accounts":
            gl_account = ImportWebService._find_account_by_type(db, org_id, "BANK")
            return BankAccountImporter(db, config, gl_account)

        elif entity_type == "invoices":
            ar_control_id = get_ar_control_account(db, org_id)
            if not ar_control_id:
                raise ValueError("No AR control account found. Import accounts first.")
            revenue_account = (
                ImportWebService._find_account_by_name_pattern(db, org_id, "sales")
                or ar_control_id
            )
            return InvoiceImporter(db, config, ar_control_id, revenue_account)

        elif entity_type == "expenses":
            expense_account = ImportWebService._find_account_by_name_pattern(
                db, org_id, "expense"
            )
            if not expense_account:
                raise ValueError("No expense account found. Import accounts first.")
            payment_account = ImportWebService._find_account_by_type(db, org_id, "BANK")
            return ExpenseImporter(db, config, expense_account, payment_account)

        elif entity_type == "customer_payments":
            result = db.execute(
                select(BankAccount).where(BankAccount.organization_id == org_id)
            ).first()
            bank_account_id = result[0].bank_account_id if result else None
            return CustomerPaymentImporter(db, config, bank_account_id)

        elif entity_type == "supplier_payments":
            result = db.execute(
                select(BankAccount).where(BankAccount.organization_id == org_id)
            ).first()
            if not result:
                raise ValueError("No bank account found. Import bank accounts first.")
            return SupplierPaymentImporter(db, config, result[0].bank_account_id)

        else:
            raise ValueError(f"Unsupported entity type: {entity_type}")

    @staticmethod
    async def preview_import(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        entity_type: str,
        file: UploadFile,
    ) -> dict[str, Any]:
        """
        Preview import with validation and column mapping.

        Returns a dictionary with preview data suitable for the web interface.
        """
        if entity_type not in ImportWebService.SUPPORTED_ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type}")

        if not file.filename or not file.filename.lower().endswith(
            SPREADSHEET_EXTENSIONS
        ):
            raise ValueError(f"Only {spreadsheet_formats_label()} files are supported")

        ext = Path(file.filename).suffix.lower()
        max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
        tmp_path = await write_upload_to_temp(
            file,
            suffix=ext,
            max_bytes=max_bytes,
            error_detail=f"File too large. Maximum size: {max_bytes // 1024 // 1024}MB",
        )

        try:
            config = ImportConfig(
                organization_id=organization_id,
                user_id=user_id,
                skip_duplicates=True,
                dry_run=True,
            )

            # Get the appropriate importer
            try:
                importer = ImportWebService._get_importer(entity_type, db, config)
            except ValueError:
                # If we can't get full importer (missing accounts), use AccountImporter for preview
                importer = AccountImporter(db, config)

            # Use the format-aware preview method
            preview_result: PreviewResult = importer.preview_any_file(
                tmp_path, max_rows=10
            )

            # Convert to dict for template
            return {
                "entity_type": preview_result.entity_type,
                "total_rows": preview_result.total_rows,
                "sample_data": preview_result.sample_data,
                "detected_columns": preview_result.detected_columns,
                "required_columns": preview_result.required_columns,
                "optional_columns": preview_result.optional_columns,
                "missing_required": preview_result.missing_required,
                "column_mappings": [
                    {
                        "source": m.source_column,
                        "target": m.target_field,
                        "confidence": m.confidence,
                        "samples": m.sample_values[:3],
                    }
                    for m in preview_result.column_mappings
                ],
                "validation_errors": preview_result.validation_errors,
                "detected_format": preview_result.detected_format,
                "is_valid": preview_result.is_valid,
            }

        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    async def execute_import(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        entity_type: str,
        file: UploadFile,
        skip_duplicates: bool = True,
        dry_run: bool = False,
        batch_size: int = 100,
        column_mapping: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Execute the import operation.

        Returns a dictionary with import results suitable for the web interface.
        """
        if entity_type not in ImportWebService.SUPPORTED_ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type}")

        if not file.filename or not file.filename.lower().endswith(
            SPREADSHEET_EXTENSIONS
        ):
            raise ValueError(f"Only {spreadsheet_formats_label()} files are supported")

        ext = Path(file.filename).suffix.lower()
        max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
        tmp_path = await write_upload_to_temp(
            file,
            suffix=ext,
            max_bytes=max_bytes,
            error_detail=f"File too large. Maximum size: {max_bytes // 1024 // 1024}MB",
        )

        try:
            config = ImportConfig(
                organization_id=organization_id,
                user_id=user_id,
                skip_duplicates=skip_duplicates,
                dry_run=dry_run,
                batch_size=batch_size,
                column_mapping=column_mapping,
            )

            # Get the appropriate importer
            importer = ImportWebService._get_importer(entity_type, db, config)

            # Run import (format-aware: CSV or XLSX)
            result: ImportResult = importer.import_any_file(tmp_path)

            # Commit if not dry run and successful
            if not dry_run and result.status in (
                ImportStatus.COMPLETED,
                ImportStatus.COMPLETED_WITH_ERRORS,
            ):
                db.commit()
            else:
                db.rollback()

            # Convert to dict for template
            return {
                "entity_type": result.entity_type,
                "status": result.status.value,
                "total_rows": result.total_rows,
                "imported_count": result.imported_count,
                "skipped_count": result.skipped_count,
                "duplicate_count": result.duplicate_count,
                "error_count": result.error_count,
                "success_rate": f"{result.success_rate:.1f}%",
                "duration_seconds": round(result.duration_seconds, 2),
                "errors": [str(e) for e in result.errors[:50]],
                "warnings": [str(w) for w in result.warnings[:50]],
            }

        except Exception:
            db.rollback()
            raise

        finally:
            Path(tmp_path).unlink(missing_ok=True)


# Singleton instance
import_web_service = ImportWebService()
