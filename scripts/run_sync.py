"""Run ERPNext sync for projects and tickets with forced re-sync."""

from __future__ import annotations

import os
import traceback
import uuid

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("f147b14c-7e22-4175-aa93-5c577ae30d87")  # admin user


def make_client():
    """Create ERPNext client from environment variables."""
    from app.services.erpnext.client import ERPNextClient, ERPNextConfig

    config = ERPNextConfig(
        url=os.environ["ERPNEXT_URL"],
        api_key=os.environ["ERPNEXT_API_KEY"],
        api_secret=os.environ["ERPNEXT_API_SECRET"],
    )
    return ERPNextClient(config)


def run_project_sync() -> None:
    """Run project sync against live ERPNext."""
    from app.db import SessionLocal
    from app.services.erpnext.sync.projects import ProjectSyncService

    print("=" * 60)
    print("PROJECT SYNC")
    print("=" * 60)

    client = make_client()

    with SessionLocal() as db:
        # Quick check: what does ERPNext return?
        print("Fetching sample project from ERPNext...")
        projects = client.get_projects()
        print(f"Fetched {len(projects)} projects from ERPNext API")

        if projects:
            sample = projects[0]
            print(
                f"Sample record fields: priority={sample.get('priority')}, "
                f"project_type={sample.get('project_type')}, "
                f"cost_center={sample.get('cost_center')}"
            )

        # Run sync (sync() calls fetch_records internally)
        service = ProjectSyncService(
            db=db,
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        result = service.sync(client)
        db.commit()

        print("\nResults:")
        print(f"  Total:   {result.total_records}")
        print(f"  Synced:  {result.synced_count}")
        print(f"  Skipped: {result.skipped_count}")
        print(f"  Errors:  {result.error_count}")
        if result.errors:
            print("  Error details (first 10):")
            for err in result.errors[:10]:
                print(f"    - {err}")


def run_ticket_sync() -> None:
    """Run ticket sync against live ERPNext."""
    from app.db import SessionLocal
    from app.services.erpnext.sync.support import TicketSyncService

    print("\n" + "=" * 60)
    print("TICKET SYNC")
    print("=" * 60)

    client = make_client()

    with SessionLocal() as db:
        # Quick check: what does ERPNext return?
        print("Fetching sample tickets from ERPNext...")
        tickets = client.get_issues()
        print(f"Fetched {len(tickets)} tickets from ERPNext API")

        if tickets:
            sample = tickets[0]
            print(
                f"Sample record fields: ticket_type={sample.get('ticket_type')}, "
                f"issue_type={sample.get('issue_type')}, "
                f"name={sample.get('name')}"
            )
            # Show ticket_type distribution from fetched data
            types = {}
            for t in tickets:
                tt = t.get("ticket_type") or t.get("issue_type") or "(none)"
                types[tt] = types.get(tt, 0) + 1
            print("Ticket type distribution from ERPNext:")
            for tt, cnt in sorted(types.items(), key=lambda x: -x[1]):
                print(f"    {tt}: {cnt}")

        # Run sync - use_hd_ticket=False because existing sync entities
        # are stored under "Issue" doctype
        service = TicketSyncService(
            db=db,
            organization_id=ORG_ID,
            user_id=USER_ID,
            use_hd_ticket=False,
        )
        result = service.sync(client)
        db.commit()

        print("\nResults:")
        print(f"  Total:   {result.total_records}")
        print(f"  Synced:  {result.synced_count}")
        print(f"  Skipped: {result.skipped_count}")
        print(f"  Errors:  {result.error_count}")
        if result.errors:
            print("  Error details (first 10):")
            for err in result.errors[:10]:
                print(f"    - {err}")

        # Show category results
        from sqlalchemy import select

        from app.models.support.category import TicketCategory

        cats = list(
            db.scalars(
                select(TicketCategory).where(TicketCategory.organization_id == ORG_ID)
            ).all()
        )
        print(f"\n  Ticket categories in DB: {len(cats)}")
        for cat in cats:
            print(f"    - {cat.category_code}: {cat.category_name}")


def verify_results() -> None:
    """Verify sync results in database."""
    from sqlalchemy import text

    from app.db import SessionLocal

    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    with SessionLocal() as db:
        # Project priority distribution
        rows = db.execute(
            text("""
            SELECT project_priority, COUNT(*) as cnt
            FROM core_org.project
            GROUP BY project_priority
            ORDER BY cnt DESC
        """)
        ).fetchall()
        print("\nProject Priority Distribution:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}")

        # Project type distribution
        rows = db.execute(
            text("""
            SELECT project_type, COUNT(*) as cnt
            FROM core_org.project
            GROUP BY project_type
            ORDER BY cnt DESC
        """)
        ).fetchall()
        print("\nProject Type Distribution:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}")

        # Cost center assignments
        rows = db.execute(
            text("""
            SELECT COUNT(*) as total,
                   COUNT(cost_center_id) as with_cc
            FROM core_org.project
        """)
        ).fetchone()
        print(f"\nCost Centers: {rows[1]}/{rows[0]} projects have cost_center_id")

        # Ticket category assignments
        rows = db.execute(
            text("""
            SELECT COUNT(*) as total,
                   COUNT(category_id) as with_cat
            FROM support.ticket
        """)
        ).fetchone()
        print(f"\nTicket Categories: {rows[1]}/{rows[0]} tickets have category_id")

        # Category detail
        rows = db.execute(
            text("""
            SELECT tc.category_name, COUNT(t.ticket_id) as cnt
            FROM support.ticket_category tc
            LEFT JOIN support.ticket t ON t.category_id = tc.category_id
            WHERE tc.organization_id = '00000000-0000-0000-0000-000000000001'
            GROUP BY tc.category_name
            ORDER BY cnt DESC
        """)
        ).fetchall()
        if rows:
            print("\nTicket Category Distribution:")
            for row in rows:
                print(f"  {row[0]}: {row[1]}")


if __name__ == "__main__":
    try:
        run_project_sync()
    except Exception as e:
        print(f"PROJECT SYNC ERROR: {e}")
        traceback.print_exc()

    try:
        run_ticket_sync()
    except Exception as e:
        print(f"TICKET SYNC ERROR: {e}")
        traceback.print_exc()

    try:
        verify_results()
    except Exception as e:
        print(f"VERIFICATION ERROR: {e}")
        traceback.print_exc()
