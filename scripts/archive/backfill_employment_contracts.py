#!/usr/bin/env python3
"""Backfill employment contracts for ACTIVE employees that don't have one.

Per Tier-1 HR remediation (2026-05-12):
- All 152 ACTIVE employees lack any hr.employment_contract row.
- For each, generate ONE ACTIVE PERMANENT contract.
- Salary comes from the employee's current payroll.salary_structure_assignment
  (the row whose from_date <= today, picking the most recent). The 5 employees
  with no SSA get a DRAFT contract with salary_amount = NULL — HR fills it in
  manually before activation.

Defaults:
  - contract_type        = PERMANENT
  - status               = ACTIVE (or DRAFT if salary unknown)
  - start_date           = employee.date_of_joining (fallback today)
  - end_date             = NULL (permanent)
  - probation_end_date   = employee.probation_end_date (may be NULL)
  - currency_code        = "NGN"
  - notice_period_days   = 30
  - working_hours_per_week = 40
  - terms                = boilerplate one-liner referencing this backfill
  - contract_number      = CT-YYYY-NNNN (sequential within org)

Dry-run by default. With --execute, contracts are created in one transaction
and the run is idempotent (employees that gain a contract on a previous run
are skipped on the next).

Usage:
    poetry run python scripts/backfill_employment_contracts.py
    poetry run python scripts/backfill_employment_contracts.py --execute
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
from app.models.people.hr.employment_contract import (
    ContractStatus,
    ContractType,
    EmploymentContract,
)
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment

DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_NOTICE_DAYS = 30
DEFAULT_WORKING_HOURS = Decimal("40")


def _backfill_terms(today: date) -> str:
    """Boilerplate contract terms with the actual run date."""
    return (
        f"Backfilled record ({today.isoformat()}) — terms per existing "
        f"employment agreement on file. Generated to close the audit gap "
        f"that left ACTIVE employees without a hr.employment_contract row."
    )


def _set_org_context(db: Session, organization_id: UUID) -> None:
    """Set RLS context. Safe inline f-string: organization_id is a UUID,
    not a user-supplied string — Python type guarantees no injection.
    """
    db.execute(text(f"SET LOCAL app.current_organization_id = '{organization_id}'"))


def _candidates(db: Session, organization_id: UUID) -> list[Employee]:
    """ACTIVE employees with no employment_contract row at all."""
    stmt = (
        select(Employee)
        .where(Employee.organization_id == organization_id)
        .where(Employee.status == EmployeeStatus.ACTIVE)
        .where(
            ~select(EmploymentContract.contract_id)
            .where(EmploymentContract.employee_id == Employee.employee_id)
            .exists()
        )
        .order_by(Employee.employee_code)
    )
    return list(db.scalars(stmt).all())


def _current_ssa(
    db: Session, employee_id: UUID, on_date: date
) -> SalaryStructureAssignment | None:
    """Return the SSA effective on `on_date`, or None if none exists."""
    stmt = (
        select(SalaryStructureAssignment)
        .where(SalaryStructureAssignment.employee_id == employee_id)
        .where(SalaryStructureAssignment.from_date <= on_date)
        .order_by(SalaryStructureAssignment.from_date.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def _starting_contract_seq(db: Session, organization_id: UUID, year: int) -> int:
    """Count existing CT-YYYY-* contracts so we can number from there.

    Race window: if another process creates a contract between this count and
    our inserts, the unique constraint uq_employment_contract_org_number will
    fire as a hard error. Acceptable for a one-off backfill; would need a
    sequence/generator for concurrent use.
    """
    count = (
        db.scalar(
            select(func.count())
            .select_from(EmploymentContract)
            .where(EmploymentContract.organization_id == organization_id)
            .where(EmploymentContract.contract_number.like(f"CT-{year}-%"))
        )
        or 0
    )
    return count + 1


def run(*, organization_id: UUID, execute: bool, limit: int) -> int:
    today = date.today()
    year = today.year

    with SessionLocal() as db:
        _set_org_context(db, organization_id)
        candidates = _candidates(db, organization_id)

        # Categorise
        with_salary: list[tuple[Employee, Decimal]] = []
        without_salary: list[Employee] = []
        for emp in candidates:
            ssa = _current_ssa(db, emp.employee_id, today)
            if ssa and ssa.base and ssa.base > 0:
                with_salary.append((emp, ssa.base))
            else:
                without_salary.append(emp)

        print(f"ACTIVE employees missing a contract: {len(candidates)}")
        print(
            f"  ↳ with current SSA (will create ACTIVE contract):     "
            f"{len(with_salary)}"
        )
        print(
            f"  ↳ without SSA      (will create DRAFT contract, salary NULL): "
            f"{len(without_salary)}"
        )
        print()

        if not candidates:
            print("Nothing to backfill.")
            return 0

        # Sample
        if with_salary:
            print(f"Sample (with salary, first {min(limit, len(with_salary))}):")
            print(f"  {'Code':14s} {'Name':30s} {'Start':12s} {'Salary (NGN)':>15s}")
            for emp, salary in with_salary[:limit]:
                start = emp.date_of_joining or today
                name = (
                    f"{emp.first_name or ''} {emp.last_name or ''}".strip()
                    or "(no name)"
                )
                print(
                    f"  {emp.employee_code or '—':14s} {name[:30]:30s} "
                    f"{str(start):12s} {float(salary):>15,.2f}"
                )
            print()

        if without_salary:
            print(
                f"Without SSA (will create DRAFT) — first {min(5, len(without_salary))}:"
            )
            for emp in without_salary[:5]:
                name = (
                    f"{emp.first_name or ''} {emp.last_name or ''}".strip()
                    or "(no name)"
                )
                print(f"  {emp.employee_code or '—':14s} {name}")
            print()

        if not execute:
            print("Dry run only. Re-run with --execute to create the contracts.")
            return 0

        created_active = 0
        created_draft = 0
        terms = _backfill_terms(today)
        seq = _starting_contract_seq(db, organization_id, year)  # one count, not N

        # Active contracts (with salary)
        for emp, salary in with_salary:
            number = f"CT-{year}-{seq:04d}"
            seq += 1
            contract = EmploymentContract(
                organization_id=organization_id,
                employee_id=emp.employee_id,
                contract_number=number,
                contract_type=ContractType.PERMANENT,
                start_date=emp.date_of_joining or today,
                end_date=None,
                probation_end_date=emp.probation_end_date,
                terms=terms,
                salary_amount=salary,
                currency_code="NGN",
                notice_period_days=DEFAULT_NOTICE_DAYS,
                working_hours_per_week=DEFAULT_WORKING_HOURS,
                notes="T1 HR remediation backfill",
                status=ContractStatus.ACTIVE,
            )
            db.add(contract)
            created_active += 1

        # Draft contracts (no salary)
        for emp in without_salary:
            number = f"CT-{year}-{seq:04d}"
            seq += 1
            contract = EmploymentContract(
                organization_id=organization_id,
                employee_id=emp.employee_id,
                contract_number=number,
                contract_type=ContractType.PERMANENT,
                start_date=emp.date_of_joining or today,
                end_date=None,
                probation_end_date=emp.probation_end_date,
                terms=terms,
                salary_amount=None,
                currency_code="NGN",
                notice_period_days=DEFAULT_NOTICE_DAYS,
                working_hours_per_week=DEFAULT_WORKING_HOURS,
                notes=(
                    "T1 HR remediation backfill — DRAFT because no current "
                    "salary_structure_assignment found. HR must populate "
                    "salary_amount and activate."
                ),
                status=ContractStatus.DRAFT,
            )
            db.add(contract)
            created_draft += 1

        db.commit()
        print(f"Created {created_active} ACTIVE contracts.")
        print(f"Created {created_draft} DRAFT contracts (need HR to fill salary).")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill employment contracts for active employees."
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
