"""Backfill Mailcow offboarding for already exited employees."""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.db.session_context import cross_org_session, session_for_org
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.people.hr.offboarding import EmployeeOffboardingService

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Mailcow offboarding for resigned/terminated employees."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply changes.")
    mode.add_argument("--dry-run", action="store_true", help="Preview only.")
    parser.add_argument("--organization-id", help="Limit to one organization UUID.")
    parser.add_argument("--employee-id", help="Limit to one employee UUID.")
    return parser.parse_args()


def _list_org_ids(limit_org_id: str | None) -> list:
    if limit_org_id:
        from uuid import UUID

        return [UUID(limit_org_id)]
    with cross_org_session() as db:
        return list(db.scalars(select(Employee.organization_id).distinct()).all())


def main() -> int:
    args = parse_args()
    dry_run = not args.apply
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    processed = 0
    for org_id in _list_org_ids(args.organization_id):
        with session_for_org(org_id) as db:
            stmt = select(Employee).where(
                Employee.organization_id == org_id,
                Employee.status.in_(
                    [EmployeeStatus.RESIGNED, EmployeeStatus.TERMINATED]
                ),
            )
            if args.employee_id:
                from uuid import UUID

                stmt = stmt.where(Employee.employee_id == UUID(args.employee_id))
            employees = list(db.scalars(stmt).all())
            logger.info("Found %d exited employees in org %s", len(employees), org_id)
            if dry_run:
                for employee in employees:
                    logger.info(
                        "Would offboard employee_id=%s status=%s",
                        employee.employee_id,
                        employee.status.value,
                    )
                continue

            service = EmployeeOffboardingService(db)
            for employee in employees:
                result = service.offboard_employee(org_id, employee.employee_id)
                logger.info("Offboarding result: %s", result)
                processed += 1
            db.commit()

    if dry_run:
        logger.info("Dry run complete. Re-run with --apply to make changes.")
    else:
        logger.info("Applied offboarding to %d employees", processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
