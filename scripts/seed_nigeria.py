#!/usr/bin/env python3
"""
Seed Nigeria tax data for one or more organizations.

Usage:
  poetry run python scripts/seed_nigeria.py --org-id <uuid>
  poetry run python scripts/seed_nigeria.py --org-code <code>
  poetry run python scripts/seed_nigeria.py
"""

import argparse
import os
import sys
from uuid import UUID

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session_context import cross_org_session, session_for_org
from app.models.finance.core_org.organization import Organization
from app.services.finance.tax.seed import seed_nigeria_tax_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Nigeria tax data.")
    parser.add_argument("--org-id", help="Organization ID to seed")
    parser.add_argument("--org-code", help="Organization code to seed")
    return parser.parse_args()


def resolve_orgs(db, args: argparse.Namespace) -> list[Organization]:
    if args.org_id and args.org_code:
        raise SystemExit("Use only one of --org-id or --org-code.")

    if args.org_id:
        try:
            org_id = UUID(args.org_id)
        except ValueError as exc:
            raise SystemExit(f"Invalid organization ID: {args.org_id}") from exc
        org = db.get(Organization, org_id)
        return [org] if org else []

    if args.org_code:
        return (
            db.query(Organization)
            .filter(Organization.organization_code == args.org_code)
            .all()
        )

    return (
        db.query(Organization)
        .filter(Organization.jurisdiction_country_code == "NG")
        .all()
    )


def main() -> None:
    load_dotenv()
    args = parse_args()
    # Selecting which organizations to seed is cross-org work; seeding each
    # one is not. Resolve under the bypass, then drop into that organization's
    # own scope to write — the pattern `cross_org_session` documents.
    with cross_org_session() as cross_db:
        targets = [
            (org.organization_id, org.organization_code)
            for org in resolve_orgs(cross_db, args)
        ]
    if not targets:
        raise SystemExit("No organizations matched for Nigeria seed data.")

    for org_id, org_code in targets:
        with session_for_org(org_id) as db:
            summary = seed_nigeria_tax_data(db, org_id)
            # `seed_nigeria_tax_data` neither commits nor flushes, and this
            # script only ever closed the session. So every run built the whole
            # Nigeria tax configuration in memory, printed the summary below as
            # though it had landed, and discarded it. The seed has never
            # actually persisted; this commit is the fix.
            db.commit()
            print(
                "Seeded Nigeria data for org "
                f"{org_code} ({org_id}): "
                f"currency={summary.currency_created}, "
                f"categories={summary.categories_created}, "
                f"accounts={summary.accounts_created}, "
                f"jurisdictions={summary.jurisdictions_created}, "
                f"tax_codes={summary.tax_codes_created}"
            )


if __name__ == "__main__":
    main()
