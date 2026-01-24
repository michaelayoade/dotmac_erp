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

from app.db import SessionLocal
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
    db = SessionLocal()
    try:
        orgs = resolve_orgs(db, args)
        if not orgs:
            raise SystemExit("No organizations matched for Nigeria seed data.")

        for org in orgs:
            summary = seed_nigeria_tax_data(db, org.organization_id)
            print(
                "Seeded Nigeria data for org "
                f"{org.organization_code} ({org.organization_id}): "
                f"currency={summary.currency_created}, "
                f"categories={summary.categories_created}, "
                f"accounts={summary.accounts_created}, "
                f"jurisdictions={summary.jurisdictions_created}, "
                f"tax_codes={summary.tax_codes_created}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
