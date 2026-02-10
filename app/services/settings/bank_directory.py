"""
Org Bank Directory Service.

Provides per-organization allowed banks for claim reimbursements.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.settings.org_bank_directory import OrgBankDirectory


class OrgBankDirectoryService:
    """Service for managing org-specific allowed banks."""

    def __init__(self, db: Session):
        self.db = db

    def list_active_banks(self, organization_id: UUID) -> list[OrgBankDirectory]:
        stmt = (
            select(OrgBankDirectory)
            .where(
                OrgBankDirectory.organization_id == organization_id,
                OrgBankDirectory.is_active.is_(True),
            )
            .order_by(OrgBankDirectory.bank_name)
        )
        return list(self.db.scalars(stmt).all())

    def seed_defaults(self, organization_id: UUID) -> int:
        """
        Seed default banks for an organization from app/data/bank_names.csv.

        Returns number of rows inserted.
        """
        if self._has_any_for_org(organization_id):
            return 0

        rows = [
            OrgBankDirectory(
                organization_id=organization_id,
                bank_name=bank_name,
                bank_sort_code=bank_sort_code,
            )
            for bank_name, bank_sort_code in _load_bank_rows()
        ]
        self.db.add_all(rows)
        self.db.flush()
        return len(rows)

    def _has_any_for_org(self, organization_id: UUID) -> bool:
        stmt = (
            select(OrgBankDirectory.org_bank_id)
            .where(OrgBankDirectory.organization_id == organization_id)
            .limit(1)
        )
        return self.db.scalar(stmt) is not None


def _load_bank_rows() -> Iterable[tuple[str, str]]:
    csv_path = Path(__file__).resolve().parents[2] / "data" / "bank_names.csv"
    if not csv_path.exists():
        return []

    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            bank_name = (raw.get("Bank Name") or "").strip()
            bank_sort_code = (raw.get("Bank Sort Code") or "").strip()
            if not bank_name or not bank_sort_code:
                continue
            key = (bank_name.lower(), bank_sort_code)
            if key in seen:
                continue
            seen.add(key)
            rows.append((bank_name, bank_sort_code))
    return rows
