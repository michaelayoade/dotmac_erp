"""
Project Import Web Service.

Provides web-facing helpers for project CSV import workflows.
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
from app.services.pm.import_export import ProjectImporter
from app.services.upload_utils import get_env_max_bytes, write_upload_to_temp

logger = logging.getLogger(__name__)


class ProjectImportWebService:
    """Service for handling project data imports from the web interface."""

    ENTITY_TYPES = {"projects": "Projects"}

    @staticmethod
    def get_dashboard_entities() -> list[dict[str, Any]]:
        return [
            {
                "id": "projects",
                "name": "Projects",
                "description": "Import project master data",
                "icon": "folder",
                "order": 1,
                "prereqs": [],
            }
        ]

    @staticmethod
    def get_entity_columns(entity_type: str) -> dict[str, list[str]]:
        if entity_type != "projects":
            return {"required": [], "optional": []}
        return {
            "required": ["Project Code", "Project Name"],
            "optional": [
                "Project Status",
                "Project Type",
                "Project Priority",
                "Start Date",
                "End Date",
                "Budget Amount",
            ],
        }

    @staticmethod
    def _get_importer(entity_type: str, db: Session, config: ImportConfig):
        if entity_type == "projects":
            return ProjectImporter(db, config)
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
        if not file.filename or not file.filename.endswith(".csv"):
            raise ValueError("Only CSV files are supported")

        max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
        tmp_path = await write_upload_to_temp(
            file,
            suffix=".csv",
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
            preview_result = importer.preview_file(tmp_path, max_rows=10)
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
    ) -> dict[str, Any]:
        if entity_type not in self.ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type}")
        if not file.filename or not file.filename.endswith(".csv"):
            raise ValueError("Only CSV files are supported")

        max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
        tmp_path = await write_upload_to_temp(
            file,
            suffix=".csv",
            max_bytes=max_bytes,
            error_detail=f"File too large. Maximum size: {max_bytes // 1024 // 1024}MB",
        )

        try:
            config = ImportConfig(
                organization_id=organization_id,
                user_id=user_id,
                skip_duplicates=skip_duplicates,
                dry_run=dry_run,
            )
            importer = self._get_importer(entity_type, db, config)
            result = ImportService.run_import(importer, tmp_path)
            payload = result.to_dict()
            payload["status"] = result.status.value
            if result.status == ImportStatus.FAILED:
                logger.warning("Project import failed: %s", payload.get("errors"))
            return payload
        finally:
            Path(tmp_path).unlink(missing_ok=True)


project_import_web_service = ProjectImportWebService()
