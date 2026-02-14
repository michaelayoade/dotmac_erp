"""
Fleet Import Web Service.

Provides web-facing helpers for fleet CSV import workflows.
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
from app.services.fleet.import_export import (
    FuelLogImporter,
    MaintenanceImporter,
    VehicleAssignmentImporter,
    VehicleDocumentImporter,
    VehicleImporter,
)
from app.services.imports.formats import (
    SPREADSHEET_EXTENSIONS,
    spreadsheet_formats_label,
)
from app.services.upload_utils import get_env_max_bytes, write_upload_to_temp

logger = logging.getLogger(__name__)


class FleetImportWebService:
    """Service for handling fleet data imports from the web interface."""

    ENTITY_TYPES = {
        "vehicles": "Vehicles",
        "assignments": "Vehicle Assignments",
        "fuel_logs": "Fuel Logs",
        "maintenance": "Maintenance Records",
        "documents": "Vehicle Documents",
    }

    @staticmethod
    def get_dashboard_entities() -> list[dict[str, Any]]:
        return [
            {
                "id": "vehicles",
                "name": "Vehicles",
                "description": "Import fleet vehicles and identifiers",
                "icon": "truck",
                "order": 1,
                "prereqs": [],
            },
            {
                "id": "assignments",
                "name": "Assignments",
                "description": "Import vehicle assignment history",
                "icon": "users",
                "order": 2,
                "prereqs": ["vehicles"],
            },
            {
                "id": "fuel_logs",
                "name": "Fuel Logs",
                "description": "Import fuel purchase entries",
                "icon": "receipt-percent",
                "order": 3,
                "prereqs": ["vehicles"],
            },
            {
                "id": "maintenance",
                "name": "Maintenance",
                "description": "Import maintenance records and schedules",
                "icon": "wrench",
                "order": 4,
                "prereqs": ["vehicles"],
            },
            {
                "id": "documents",
                "name": "Documents",
                "description": "Import registration and insurance documents",
                "icon": "document-text",
                "order": 5,
                "prereqs": ["vehicles"],
            },
        ]

    @staticmethod
    def get_entity_columns(entity_type: str) -> dict[str, list[str]]:
        columns = {
            "vehicles": {
                "required": [
                    "Vehicle Code",
                    "Registration Number",
                    "Make",
                    "Model",
                    "Year",
                ],
                "optional": [
                    "Vehicle Type",
                    "Fuel Type",
                    "Ownership Type",
                    "Status",
                    "VIN",
                    "Chassis Number",
                    "Engine Number",
                    "Purchase Date",
                    "Purchase Price",
                    "License Expiry Date",
                    "Location Code",
                    "Location Name",
                ],
            },
            "assignments": {
                "required": ["Vehicle Code", "Assignment Type", "Start Date"],
                "optional": ["Employee Code", "Department Code", "End Date"],
            },
            "fuel_logs": {
                "required": [
                    "Vehicle Code",
                    "Log Date",
                    "Fuel Type",
                    "Quantity Liters",
                    "Price Per Liter",
                    "Odometer Reading",
                ],
                "optional": ["Total Cost", "Employee Code", "Station Name"],
            },
            "maintenance": {
                "required": [
                    "Vehicle Code",
                    "Maintenance Type",
                    "Description",
                    "Scheduled Date",
                ],
                "optional": ["Status", "Completed Date", "Supplier Code"],
            },
            "documents": {
                "required": ["Vehicle Code", "Document Type", "Description"],
                "optional": ["Document Number", "Issue Date", "Expiry Date"],
            },
        }
        return columns.get(entity_type, {"required": [], "optional": []})

    @staticmethod
    def _get_importer(entity_type: str, db: Session, config: ImportConfig):
        if entity_type == "vehicles":
            return VehicleImporter(db, config)
        if entity_type == "assignments":
            return VehicleAssignmentImporter(db, config)
        if entity_type == "fuel_logs":
            return FuelLogImporter(db, config)
        if entity_type == "maintenance":
            return MaintenanceImporter(db, config)
        if entity_type == "documents":
            return VehicleDocumentImporter(db, config)
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
                column_mapping=column_mapping,
            )
            importer = self._get_importer(entity_type, db, config)
            result = ImportService.run_import(importer, tmp_path)
            payload = result.to_dict()
            payload["status"] = result.status.value
            if result.status == ImportStatus.FAILED:
                logger.warning("Fleet import failed: %s", payload.get("errors"))
            return payload
        finally:
            Path(tmp_path).unlink(missing_ok=True)


fleet_import_web_service = FleetImportWebService()
