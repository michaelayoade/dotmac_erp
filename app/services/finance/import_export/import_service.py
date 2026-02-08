"""
Import Service.

Orchestrates import runs and owns DB commit/rollback behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from app.services.finance.import_export.base import (
    BaseImporter,
    ImportResult,
    ImportStatus,
)


class ImportService:
    """Service wrapper for running imports with consistent transaction handling."""

    @staticmethod
    def run_import(
        importer: BaseImporter,
        file_path: Union[str, Path],
    ) -> ImportResult:
        try:
            result = importer.import_file(file_path)

            if not importer.config.dry_run and result.status in (
                ImportStatus.COMPLETED,
                ImportStatus.COMPLETED_WITH_ERRORS,
            ):
                importer.db.commit()
            else:
                importer.db.rollback()

            return result
        except Exception:
            importer.db.rollback()
            raise
