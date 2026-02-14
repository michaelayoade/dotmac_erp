"""
Shared import file format definitions.
"""

from __future__ import annotations

SPREADSHEET_EXTENSIONS: tuple[str, ...] = (".csv", ".xls", ".xlsx", ".xlsm")


def spreadsheet_formats_label() -> str:
    """Human-readable list for validation messages."""
    return "CSV, XLS, XLSX, XLSM"
