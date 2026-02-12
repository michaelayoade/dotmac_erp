"""
HR Import Web Service.

Provides web-facing helpers for HR CSV import workflows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.services.finance.import_export.base import ImportConfig, ImportStatus
from app.services.finance.import_export.import_service import ImportService
from app.services.people.hr.import_export import (
    DepartmentImporter,
    DesignationImporter,
    EmployeeImporter,
    EmploymentTypeImporter,
)
from app.services.upload_utils import get_env_max_bytes, write_upload_to_temp

logger = logging.getLogger(__name__)


class HrImportWebService:
    """Service for handling HR data imports from the web interface."""

    ENTITY_TYPES = {
        "departments": "Departments",
        "designations": "Designations",
        "employment_types": "Employment Types",
        "employees": "Employees",
    }

    @staticmethod
    def get_dashboard_entities() -> list[dict[str, Any]]:
        return [
            {
                "id": "departments",
                "name": "Departments",
                "description": "Import department records",
                "icon": "building-office",
                "order": 1,
                "prereqs": [],
            },
            {
                "id": "designations",
                "name": "Designations",
                "description": "Import job titles and positions",
                "icon": "briefcase",
                "order": 2,
                "prereqs": [],
            },
            {
                "id": "employment_types",
                "name": "Employment Types",
                "description": "Import employment types and codes",
                "icon": "document-text",
                "order": 3,
                "prereqs": [],
            },
            {
                "id": "employees",
                "name": "Employees",
                "description": "Import employee master records",
                "icon": "users",
                "order": 4,
                "prereqs": ["departments", "designations", "employment_types"],
            },
        ]

    @staticmethod
    def get_entity_columns(entity_type: str) -> dict[str, list[str]]:
        columns = {
            "departments": {
                "required": ["Department Code", "Department Name"],
                "optional": ["Parent Department Code", "Cost Center Code"],
            },
            "designations": {
                "required": ["Designation Code", "Designation Name"],
                "optional": ["Description"],
            },
            "employment_types": {
                "required": ["Employment Type Code", "Employment Type Name"],
                "optional": ["Description"],
            },
            "employees": {
                "required": [
                    "Employee Code",
                    "First Name",
                    "Last Name",
                    "Work Email",
                    "Date of Joining",
                ],
                "optional": [
                    "Department Code",
                    "Designation Code",
                    "Employment Type Code",
                ],
            },
        }
        return columns.get(entity_type, {"required": [], "optional": []})

    @staticmethod
    def _get_importer(entity_type: str, db: Session, config: ImportConfig):
        if entity_type == "departments":
            return DepartmentImporter(db, config)
        if entity_type == "designations":
            return DesignationImporter(db, config)
        if entity_type == "employment_types":
            return EmploymentTypeImporter(db, config)
        if entity_type == "employees":
            return EmployeeImporter(db, config)
        raise ValueError(f"Unsupported entity type: {entity_type}")

    async def preview_import(
        self,
        *,
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        entity_type: str,
        file: UploadFile,
    ) -> dict[str, Any]:
        if entity_type not in self.ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type}")
        _ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xlsm")
        if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSIONS):
            raise ValueError("Only CSV, XLSX, or XLSM files are supported")

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
            importer = self._get_importer(entity_type, db, config)
            preview_result = importer.preview_any_file(tmp_path, max_rows=10)
            return cast(dict[str, Any], preview_result.to_dict())
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def execute_import(
        self,
        *,
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        entity_type: str,
        file: UploadFile,
        skip_duplicates: bool = True,
        dry_run: bool = False,
        column_mapping: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if entity_type not in self.ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type}")
        _ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xlsm")
        if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSIONS):
            raise ValueError("Only CSV, XLSX, or XLSM files are supported")

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
                column_mapping=column_mapping,
            )
            importer = self._get_importer(entity_type, db, config)
            result = ImportService.run_import(importer, tmp_path)
            payload = result.to_dict()
            payload["status"] = result.status.value
            if result.status == ImportStatus.FAILED:
                logger.warning("HR import failed: %s", payload.get("errors"))
            return payload
        finally:
            Path(tmp_path).unlink(missing_ok=True)


hr_import_web_service = HrImportWebService()
