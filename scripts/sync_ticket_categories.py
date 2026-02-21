"""
Quick category sync: fetch ticket_type from ERPNext HD Tickets,
create categories, and assign to existing tickets in DotMac DB.

Skips the full ticket sync (which fetches comments/communications per ticket).
"""

from __future__ import annotations

import os
import sys
import uuid

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def main() -> None:
    print("ERPNext API sync is disabled. Use SQL-based sync tooling.")
    raise SystemExit(2)

    from sqlalchemy import select, text

    from app.db import SessionLocal
    from app.services.erpnext.client import ERPNextClient, ERPNextConfig

    config = ERPNextConfig(
        url=os.environ["ERPNEXT_URL"],
        api_key=os.environ["ERPNEXT_API_KEY"],
        api_secret=os.environ["ERPNEXT_API_SECRET"],
    )
    client = ERPNextClient(config)

    # Step 1: Fetch just name + ticket_type from ERPNext HD Tickets
    print("Fetching ticket_type from ERPNext HD Tickets...")
    sys.stdout.flush()
    ticket_types: dict[str, str] = {}
    try:
        records = client.get_all_documents(
            doctype="HD Ticket",
            fields=["name", "ticket_type"],
            filters={},
        )
        for r in records:
            name = str(r.get("name", ""))
            tt = r.get("ticket_type")
            if name and tt:
                ticket_types[name] = str(tt)
    except Exception as e:
        print(f"HD Ticket fetch failed ({e}), trying Issue...")
        sys.stdout.flush()
        records = client.get_all_documents(
            doctype="Issue",
            fields=["name", "issue_type"],
            filters={},
        )
        for r in records:
            name = str(r.get("name", ""))
            tt = r.get("issue_type")
            if name and tt:
                ticket_types[name] = str(tt)

    print(f"Got ticket_type for {len(ticket_types)} tickets")
    sys.stdout.flush()

    # Show distribution
    type_counts: dict[str, int] = {}
    for tt in ticket_types.values():
        type_counts[tt] = type_counts.get(tt, 0) + 1
    print("Ticket type distribution:")
    for tt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {tt}: {cnt}")
    sys.stdout.flush()

    # Step 2: Create categories and assign to tickets
    print("\nAssigning categories to tickets...")
    sys.stdout.flush()

    with SessionLocal() as db:
        from app.models.support.category import TicketCategory

        # Get existing sync entities to map ERPNext name → DB ticket_id
        se_rows = db.execute(
            text("""
            SELECT source_name, target_id
            FROM sync.sync_entity
            WHERE organization_id = :org_id
              AND source_system = 'erpnext'
              AND source_doctype = 'Issue'
              AND target_id IS NOT NULL
        """),
            {"org_id": str(ORG_ID)},
        ).fetchall()

        erpnext_to_ticket: dict[str, uuid.UUID] = {}
        for row in se_rows:
            erpnext_to_ticket[row[0]] = row[1]
        print(f"Found {len(erpnext_to_ticket)} synced tickets in DB")
        sys.stdout.flush()

        # Build category cache
        category_cache: dict[str, uuid.UUID] = {}

        existing_cats = db.scalars(
            select(TicketCategory).where(
                TicketCategory.organization_id == ORG_ID,
            )
        ).all()
        for cat in existing_cats:
            category_cache[cat.category_name] = cat.category_id

        created_count = 0
        assigned_count = 0
        skipped_count = 0

        for erpnext_name, ticket_type in ticket_types.items():
            ticket_id = erpnext_to_ticket.get(erpnext_name)
            if not ticket_id:
                skipped_count += 1
                continue

            # Resolve or create category
            if ticket_type not in category_cache:
                code = ticket_type.strip().upper().replace(" ", "-")[:20]
                # Check if code already exists
                existing = db.execute(
                    select(TicketCategory).where(
                        TicketCategory.organization_id == ORG_ID,
                        TicketCategory.category_code == code,
                    )
                ).scalar_one_or_none()
                if existing:
                    category_cache[ticket_type] = existing.category_id
                else:
                    cat = TicketCategory(
                        organization_id=ORG_ID,
                        category_code=code,
                        category_name=ticket_type.strip(),
                    )
                    db.add(cat)
                    db.flush()
                    category_cache[ticket_type] = cat.category_id
                    created_count += 1
                    print(f"  Created category: {code} ({ticket_type})")
                    sys.stdout.flush()

            # Assign category to ticket
            cat_id = category_cache[ticket_type]
            db.execute(
                text("""
                UPDATE support.ticket
                SET category_id = :cat_id
                WHERE ticket_id = :ticket_id
                  AND (category_id IS NULL OR category_id != :cat_id)
            """),
                {"cat_id": str(cat_id), "ticket_id": str(ticket_id)},
            )
            assigned_count += 1

        db.commit()

        print("\nResults:")
        print(f"  Categories created: {created_count}")
        print(f"  Tickets assigned: {assigned_count}")
        print(f"  Skipped (no DB match): {skipped_count}")
        sys.stdout.flush()

        # Verify
        r = db.execute(
            text("SELECT COUNT(*), COUNT(category_id) FROM support.ticket")
        ).fetchone()
        print(f"\nVerification: {r[1]}/{r[0]} tickets have category_id")

        # Category breakdown
        rows = db.execute(
            text("""
            SELECT tc.category_name, COUNT(t.ticket_id) as cnt
            FROM support.ticket_category tc
            LEFT JOIN support.ticket t ON t.category_id = tc.category_id
            WHERE tc.organization_id = :org_id
            GROUP BY tc.category_name
            ORDER BY cnt DESC
        """),
            {"org_id": str(ORG_ID)},
        ).fetchall()
        print("\nCategory distribution:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}")


if __name__ == "__main__":
    main()
