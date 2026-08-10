"""
Sync Salary Assignments from Excel - January 2026.

Creates salary assignments for employees that exist in the database
but don't have assignments yet, based on data from the Excel file.

Usage:
    poetry run python scripts/sync_salary_assignments.py

    # To see what would happen without making changes:
    poetry run python scripts/sync_salary_assignments.py --dry-run
"""

import argparse
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import UUID

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_structure import SalaryStructure

# Excel file path
EXCEL_PATH = Path("/root/.dotmac/jan paye (2) (1).xlsx")


def get_org_id(db: Session) -> UUID:
    """Get the first organization ID."""
    result = db.execute(
        text("SELECT organization_id FROM core_org.organization LIMIT 1")
    )
    row = result.fetchone()
    if not row:
        raise ValueError("No organization found.")
    return row[0]


def get_admin_user_id(db: Session) -> UUID:
    """Get admin user ID."""
    result = db.execute(
        text(
            "SELECT person_id FROM public.user_credentials WHERE username = 'admin' LIMIT 1"
        )
    )
    row = result.fetchone()
    if row:
        return row[0]
    result = db.execute(text("SELECT person_id FROM public.user_credentials LIMIT 1"))
    row = result.fetchone()
    if not row:
        raise ValueError("No users found.")
    return row[0]


def get_structures(
    db: Session, org_id: UUID
) -> tuple[SalaryStructure, SalaryStructure]:
    """Get permanent and contract salary structures."""
    perm = (
        db.query(SalaryStructure)
        .filter(
            SalaryStructure.organization_id == org_id,
            SalaryStructure.structure_code == "PERM-STAFF",
        )
        .first()
    )

    contract = (
        db.query(SalaryStructure)
        .filter(
            SalaryStructure.organization_id == org_id,
            SalaryStructure.structure_code == "CONTRACT-STAFF",
        )
        .first()
    )

    if not perm or not contract:
        raise ValueError(
            "Salary structures not found. Run seed_payroll_from_excel.py first."
        )

    return perm, contract


def excel_to_decimal(value) -> Decimal:
    """Convert Excel value to Decimal."""
    if value is None or pd.isna(value):
        return Decimal("0")
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return Decimal("0")
    rounded = round(float(value), 2)
    return Decimal(str(rounded)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_excel_data(excel_path: Path) -> dict[str, dict]:
    """Parse Excel file and extract employee salary data by code."""
    print(f"Reading Excel file: {excel_path}")

    employees = {}

    # Parse Permanent Staff sheet
    perm = pd.read_excel(excel_path, sheet_name="Payroll(January 2026)", header=2)
    perm.columns = perm.iloc[0].tolist()
    perm = perm.iloc[1:]
    perm = perm[pd.to_numeric(perm["S/N"], errors="coerce").notna()]

    for _, row in perm.iterrows():
        code = row.get("Employee Code")
        if not code or pd.isna(code):
            continue
        code = str(code).strip()
        if not code.startswith("EMP"):
            continue

        # Get monthly gross
        gross = row.get("Monthly Gross") or row.get("Gross Salary") or 0
        employees[code] = {
            "type": "permanent",
            "gross": excel_to_decimal(gross),
            "name": str(row.get("Name", "")).strip(),
        }

    # Parse Contract Staff sheet
    contract = pd.read_excel(
        excel_path, sheet_name="Payroll (January Contract)", header=2
    )
    contract.columns = contract.iloc[0].tolist()
    contract = contract.iloc[1:]
    contract = contract[pd.to_numeric(contract["S/N"], errors="coerce").notna()]

    for _, row in contract.iterrows():
        code = row.get("Employee Code")
        if not code or pd.isna(code):
            continue
        code = str(code).strip()
        if not code.startswith("EMP"):
            continue

        # Get salary - try multiple columns, use first non-null value
        net = None
        for col in ["Net Pay", "Current Take-Home", "Monthly Gross"]:
            val = row.get(col)
            if val is not None and not pd.isna(val):
                net = val
                break
        if net is None:
            net = 0

        employees[code] = {
            "type": "contract",
            "gross": excel_to_decimal(net),
            "name": str(row.get("Name", "")).strip(),
        }

    print(f"  Found {len(employees)} employees with codes in Excel")
    return employees


def main():
    parser = argparse.ArgumentParser(description="Sync salary assignments from Excel")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Sync Salary Assignments - January 2026")
    if args.dry_run:
        print("  Mode: DRY RUN (no changes will be made)")
    print("=" * 60)

    if not EXCEL_PATH.exists():
        print(f"ERROR: Excel file not found: {EXCEL_PATH}")
        sys.exit(1)

    db = SessionLocal()

    try:
        org_id = get_org_id(db)
        user_id = get_admin_user_id(db)
        print(f"Organization ID: {org_id}")

        # Get salary structures
        perm_struct, contract_struct = get_structures(db, org_id)
        print(f"Permanent Structure: {perm_struct.structure_name}")
        print(f"Contract Structure: {contract_struct.structure_name}")

        # Parse Excel data
        excel_data = parse_excel_data(EXCEL_PATH)

        # Find employees without assignments
        print("\nFinding employees without salary assignments...")
        result = db.execute(
            text("""
            SELECT e.employee_id, e.employee_code
            FROM hr.employee e
            WHERE e.organization_id = :org_id
            AND e.status = 'ACTIVE' AND e.is_deleted = false
            AND NOT EXISTS (
                SELECT 1 FROM payroll.salary_structure_assignment ssa
                WHERE ssa.employee_id = e.employee_id
                AND ssa.from_date <= '2026-01-31'
                AND (ssa.to_date IS NULL OR ssa.to_date >= '2026-01-01')
            )
        """),
            {"org_id": org_id},
        )

        employees_without_assignment = list(result)
        print(
            f"  Found {len(employees_without_assignment)} employees without assignments"
        )

        # Match and create assignments
        print("\nCreating salary assignments...")
        created = 0
        skipped = 0

        for emp_id, emp_code in employees_without_assignment:
            if emp_code in excel_data:
                data = excel_data[emp_code]
                structure = (
                    perm_struct if data["type"] == "permanent" else contract_struct
                )

                if args.dry_run:
                    print(
                        f"  [DRY RUN] Would assign {emp_code} ({data['name']}) -> {structure.structure_code}, Base: {data['gross']}"
                    )
                else:
                    assignment = SalaryStructureAssignment(
                        organization_id=org_id,
                        employee_id=emp_id,
                        structure_id=structure.structure_id,
                        from_date=date(2026, 1, 1),
                        base=data["gross"],
                        created_by_id=user_id,
                    )
                    db.add(assignment)
                    print(
                        f"  + Assigned {emp_code} ({data['name']}) -> {structure.structure_code}, Base: {data['gross']}"
                    )
                created += 1
            else:
                print(f"  ⚠ Skipped {emp_code} - not found in Excel")
                skipped += 1

        if args.dry_run:
            print("\n[DRY RUN - No changes committed]")
            db.rollback()
        else:
            db.commit()
            print("\n" + "=" * 60)
            print("SUMMARY")
            print("=" * 60)
            print(f"  Created: {created} salary assignments")
            print(f"  Skipped: {skipped} (not in Excel)")
            print("=" * 60)
            print("\nSUCCESS: Salary assignments synced!")

    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
