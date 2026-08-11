#!/usr/bin/env python3
"""Create the 'Unallocated Payroll' cost centre and assign all ACTIVE employees.

Per Tier-1 HR remediation (2026-05-12):
- core_org.cost_center is empty (0 rows). Departmental P&L is impossible
  because every payroll line posts without a cost-centre dimension.
- Quick-win: create ONE cost centre called 'Unallocated Payroll', assign
  every ACTIVE employee to it. Future tiers will subdivide into per-department
  cost centres.

Side effects:
- Creates 1 row in core_org.business_unit (CC FKs to BU and BU is also empty).
  Unit name: 'Default'. Type: COST_CENTER.
- Creates 1 row in core_org.cost_center: code=UNALLOC-PAY, name=Unallocated
  Payroll, business_unit_id=above, is_active=True.
- Sets hr.employee.cost_center_id = <new CC id> for every ACTIVE employee
  whose cost_center_id is currently NULL. Does NOT overwrite existing values.

Idempotent: re-running detects existing rows by code and skips creation.

Dry-run by default. Use --execute to apply.

Usage:
    poetry run python scripts/create_unallocated_payroll_cc.py
    poetry run python scripts/create_unallocated_payroll_cc.py --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.finance.core_org.business_unit import BusinessUnit, BusinessUnitType
from app.models.finance.core_org.cost_center import CostCenter
from app.models.people.hr.employee import Employee, EmployeeStatus

DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")

CC_CODE = "UNALLOC-PAY"
CC_NAME = "Unallocated Payroll"
BU_CODE = "DEFAULT-BU"
BU_NAME = "Default"


def _get_or_create_business_unit(
    db: Session, organization_id: UUID, *, dry_run: bool
) -> BusinessUnit | None:
    existing = db.scalar(
        select(BusinessUnit)
        .where(BusinessUnit.organization_id == organization_id)
        .where(BusinessUnit.unit_code == BU_CODE)
    )
    if existing:
        print(
            f"  business_unit '{BU_CODE}' already exists ({existing.business_unit_id})"
        )
        return existing
    if dry_run:
        print(f"  WOULD create business_unit code={BU_CODE} name={BU_NAME}")
        return None
    bu = BusinessUnit(
        organization_id=organization_id,
        unit_code=BU_CODE,
        unit_name=BU_NAME,
        unit_type=BusinessUnitType.COST_CENTER,
        is_active=True,
        hierarchy_level=1,
    )
    db.add(bu)
    db.flush()
    print(f"  CREATED business_unit {bu.business_unit_id}")
    return bu


def _get_or_create_cost_center(
    db: Session,
    organization_id: UUID,
    business_unit_id: UUID | None,
    *,
    dry_run: bool,
) -> CostCenter | None:
    existing = db.scalar(
        select(CostCenter)
        .where(CostCenter.organization_id == organization_id)
        .where(CostCenter.cost_center_code == CC_CODE)
    )
    if existing:
        print(f"  cost_center '{CC_CODE}' already exists ({existing.cost_center_id})")
        return existing
    if dry_run:
        print(f"  WOULD create cost_center code={CC_CODE} name={CC_NAME}")
        return None
    cc = CostCenter(
        organization_id=organization_id,
        cost_center_code=CC_CODE,
        cost_center_name=CC_NAME,
        business_unit_id=business_unit_id,
        is_active=True,
    )
    db.add(cc)
    db.flush()
    print(f"  CREATED cost_center {cc.cost_center_id}")
    return cc


def run(*, organization_id: UUID, execute: bool) -> int:
    dry_run = not execute
    with session_for_org(organization_id) as db:
        print("Step 1: business_unit")
        bu = _get_or_create_business_unit(db, organization_id, dry_run=dry_run)
        bu_id = bu.business_unit_id if bu else None

        print()
        print("Step 2: cost_center")
        cc = _get_or_create_cost_center(db, organization_id, bu_id, dry_run=dry_run)
        cc_id = cc.cost_center_id if cc else None

        print()
        print("Step 3: assign ACTIVE employees with NULL cost_center_id")
        n_to_assign = (
            db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(Employee.organization_id == organization_id)
                .where(Employee.status == EmployeeStatus.ACTIVE)
                .where(Employee.cost_center_id.is_(None))
            )
            or 0
        )
        print(f"  ACTIVE employees with NULL cost_center_id: {n_to_assign}")

        n_already_set = (
            db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(Employee.organization_id == organization_id)
                .where(Employee.status == EmployeeStatus.ACTIVE)
                .where(Employee.cost_center_id.is_not(None))
            )
            or 0
        )
        print(
            f"  ACTIVE employees already assigned a CC (will not touch): "
            f"{n_already_set}"
        )

        if dry_run:
            print()
            print("Dry run only. Re-run with --execute to apply.")
            return 0

        if cc_id and n_to_assign:
            db.execute(
                update(Employee)
                .where(Employee.organization_id == organization_id)
                .where(Employee.status == EmployeeStatus.ACTIVE)
                .where(Employee.cost_center_id.is_(None))
                .values(cost_center_id=cc_id)
            )

        db.commit()
        print()
        print(f"DONE. Assigned {n_to_assign} employees to cost_center {cc_id}.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create Unallocated Payroll CC and backfill employees."
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
    args = parser.parse_args()
    return run(organization_id=UUID(args.org), execute=args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
