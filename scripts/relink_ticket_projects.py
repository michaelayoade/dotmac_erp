#!/usr/bin/env python3
"""
Re-link tickets to projects after project sync.

This script is useful when tickets were synced before projects,
resulting in missing project_id references.

Usage:
    python scripts/relink_ticket_projects.py [--dry-run]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.db import SessionLocal
from app.models.support.ticket import Ticket
from app.models.sync import SyncEntity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def relink_tickets_to_projects(dry_run: bool = False):
    """
    Re-link tickets to projects using ERPNext ticket number -> project mapping.

    Since the original project reference from ERPNext isn't stored after sync,
    this queries ERPNext Issue records to get project mappings. However, if we
    don't have access to ERPNext, we can try to match based on naming patterns
    or other heuristics.
    """
    db = SessionLocal()

    try:
        # Get all organizations with tickets
        org_ids = db.execute(select(Ticket.organization_id).distinct()).scalars().all()

        total_updated = 0

        for org_id in org_ids:
            logger.info(f"Processing organization: {org_id}")

            # Get tickets without project_id
            tickets_without_project = (
                db.execute(
                    select(Ticket).where(
                        Ticket.organization_id == org_id,
                        Ticket.project_id.is_(None),
                    )
                )
                .scalars()
                .all()
            )

            logger.info(
                f"  Found {len(tickets_without_project)} tickets without project link"
            )

            # Get project mapping (source_name -> project_id)
            project_sync = (
                db.execute(
                    select(SyncEntity).where(
                        SyncEntity.organization_id == org_id,
                        SyncEntity.source_system == "erpnext",
                        SyncEntity.source_doctype == "Project",
                    )
                )
                .scalars()
                .all()
            )

            project_map = {se.source_name: se.target_id for se in project_sync}
            logger.info(f"  Found {len(project_map)} synced projects")

            if not project_map:
                continue

            # For each ticket, try to find project reference
            # Unfortunately, we don't store the original project reference from ERPNext
            # We'd need to query ERPNext again or have stored it during sync

            # Alternative: Check if ticket_number follows a project pattern
            # ERPNext Issues might have project in their naming or custom fields

            # For now, just report the gap
            logger.info("  Note: Re-linking requires re-syncing tickets from ERPNext")
            logger.info("  Run: python scripts/erpnext_sync.py --phase 5 --full")

        # Summary
        logger.info("\n=== Summary ===")
        logger.info(f"Organizations processed: {len(org_ids)}")
        logger.info(f"Tickets updated: {total_updated}")

        if not dry_run:
            db.commit()
            logger.info("Changes committed")
        else:
            logger.info("DRY RUN - no changes made")

    finally:
        db.close()


def check_project_status():
    """Check current project linking status."""
    db = SessionLocal()

    try:
        # Get stats
        result = db.execute("""
            SELECT
                COUNT(*) as total_tickets,
                COUNT(project_id) as with_project,
                (SELECT COUNT(*) FROM core_org.project) as total_projects
            FROM support.ticket
        """).first()

        if result:
            total, with_proj, total_proj = result
            logger.info(f"Tickets: {total:,}")
            logger.info(f"With project: {with_proj:,} ({100 * with_proj / total:.1f}%)")
            logger.info(f"Projects available: {total_proj:,}")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Re-link tickets to projects")
    parser.add_argument("--dry-run", action="store_true", help="Don't commit changes")
    parser.add_argument("--status", action="store_true", help="Just check status")
    args = parser.parse_args()

    if args.status:
        check_project_status()
    else:
        relink_tickets_to_projects(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
