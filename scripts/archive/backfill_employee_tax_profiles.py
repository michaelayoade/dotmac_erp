#!/usr/bin/env python3
"""Backfill missing employee_tax_profile rows for ACTIVE employees.

Creates a default Nigerian PAYE profile (NTA 2025 defaults) for every ACTIVE
employee that has no profile. Defaults:
  - effective_from = date_of_joining (if set), else today
  - pension_rate    = 0.08   (8%, statutory employee contribution)
  - nhf_rate        = 0.025  (2.5%, statutory)
  - nhis_rate       = 0      (off by default; HR can opt-in per employee)
  - annual_rent     = 0      (no rent relief until employee declares + verifies)
  - is_tax_exempt   = False
  - tin / rsa_pin / pfa_code / nhf_number / tax_state = NULL (HR fills in)

Dry-run by default. With --execute, the rows are created in a single
transaction. Idempotent: re-running skips employees that already have a
profile.

Usage:
    poetry run python scripts/backfill_employee_tax_profiles.py
    poetry run python scripts/backfill_employee_tax_profiles.py --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile

DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")

# NTA 2025 statutory defaults
DEFAULT_PENSION_RATE = Decimal("0.08")
DEFAULT_NHF_RATE = Decimal("0.025")
DEFAULT_NHIS_RATE = Decimal("0")


def _set_org_context(db: Session, organization_id: UUID) -> None:
    """Set RLS context. Safe inline f-string: organization_id is a UUID,
    not a user-supplied string — Python type guarantees no injection.
    """
    db.execute(text(f"SET LOCAL app.current_organization_id = '{organization_id}'"))


def _candidates(db: Session, organization_id: UUID) -> list[Employee]:
    """Active employees with no existing tax profile."""
    stmt = (
        select(Employee)
        .where(Employee.organization_id == organization_id)
        .where(Employee.status == EmployeeStatus.ACTIVE)
        .where(
            ~select(EmployeeTaxProfile.profile_id)
            .where(EmployeeTaxProfile.employee_id == Employee.employee_id)
            .exists()
        )
        .order_by(Employee.employee_code)
    )
    return list(db.scalars(stmt).all())


def run(*, organization_id: UUID, execute: bool, limit: int) -> int:
    with SessionLocal() as db:
        _set_org_context(db, organization_id)
        candidates = _candidates(db, organization_id)
        total_active = (
            db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(Employee.organization_id == organization_id)
                .where(Employee.status == EmployeeStatus.ACTIVE)
            )
            or 0
        )

        print(f"ACTIVE employees in scope:           {total_active}")
        print(f"ACTIVE employees missing tax profile: {len(candidates)}")
        print()

        if not candidates:
            print("Nothing to backfill.")
            return 0

        print(f"Sample (first {min(limit, len(candidates))} of {len(candidates)}):")
        print(f"  {'Code':10s} {'Name':35s} {'Effective from':14s} {'Joined':12s}")
        for emp in candidates[:limit]:
            joined = emp.date_of_joining
            eff = joined or date.today()
            name = (
                f"{emp.first_name or ''} {emp.last_name or ''}".strip() or "(no name)"
            )
            print(
                f"  {emp.employee_code or '—':10s} {name[:35]:35s} "
                f"{str(eff):14s} {str(joined) if joined else '—':12s}"
            )
        print()

        if not execute:
            print("Dry run only. Re-run with --execute to create the profiles.")
            return 0

        created = 0
        for emp in candidates:
            effective_from = emp.date_of_joining or date.today()
            profile = EmployeeTaxProfile(
                organization_id=organization_id,
                employee_id=emp.employee_id,
                effective_from=effective_from,
                pension_rate=DEFAULT_PENSION_RATE,
                nhf_rate=DEFAULT_NHF_RATE,
                nhis_rate=DEFAULT_NHIS_RATE,
                annual_rent=Decimal("0"),
                is_tax_exempt=False,
            )
            # No-op with annual_rent=0, but call it for symmetry with HR-edited
            # profiles where rent is later populated.
            profile.update_rent_relief()
            db.add(profile)
            created += 1

        db.commit()
        print(f"Created {created} employee_tax_profile rows.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill default tax profiles for active employees."
    )
    parser.add_argument(
        "--org",
        type=str,
        default=str(DEFAULT_ORG),
        help="Organization UUID (default: production org).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. Without this flag, only reports what would change.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of sample rows to print in dry-run output.",
    )
    args = parser.parse_args()
    return run(organization_id=UUID(args.org), execute=args.execute, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
