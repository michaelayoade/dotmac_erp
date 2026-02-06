#!/usr/bin/env python3
"""Create a new organization with auto-seeded tax configuration."""

import argparse
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.services.finance.tax.seed import seed_default_tax_data, get_country_config


def main():
    parser = argparse.ArgumentParser(description="Create a new organization.")
    parser.add_argument("--code", required=True, help="Organization code")
    parser.add_argument("--name", required=True, help="Legal name")
    parser.add_argument("--country", default="NG", help="Country code (default: NG)")
    parser.add_argument(
        "--skip-tax-seed",
        action="store_true",
        help="Skip auto-seeding tax configuration",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Check if organization with this code already exists
        existing = (
            db.query(Organization)
            .filter(Organization.organization_code == args.code)
            .first()
        )
        if existing:
            print(
                f"Organization '{args.code}' already exists with ID: {existing.organization_id}"
            )
            return

        org = Organization(
            organization_id=uuid4(),
            organization_code=args.code,
            legal_name=args.name,
            functional_currency_code=settings.default_functional_currency_code,
            presentation_currency_code=settings.default_presentation_currency_code,
            fiscal_year_end_month=12,
            fiscal_year_end_day=31,
            jurisdiction_country_code=args.country,
            is_active=True,
        )
        db.add(org)
        db.commit()
        print(f"Created organization: {args.name}")
        print(f"  Organization ID: {org.organization_id}")
        print(f"  Code: {org.organization_code}")
        print(f"  Country: {org.jurisdiction_country_code}")

        # Auto-seed tax configuration
        if not args.skip_tax_seed:
            config = get_country_config(args.country)
            if config:
                print(f"\nSeeding tax configuration for {config.country_name}...")
                summary = seed_default_tax_data(
                    db, org.organization_id, country_code=args.country
                )
                print(f"  Categories created: {summary.categories_created}")
                print(f"  Accounts created: {summary.accounts_created}")
                print(f"  Jurisdictions created: {summary.jurisdictions_created}")
                print(f"  Tax codes created: {summary.tax_codes_created}")
                if summary.default_jurisdiction_id:
                    print(
                        f"  Default jurisdiction ID: {summary.default_jurisdiction_id}"
                    )
            else:
                print(
                    f"\n  No tax configuration available for country '{args.country}'"
                )
                print("  Use 'seed_nigeria.py' or create jurisdictions manually")
    finally:
        db.close()


if __name__ == "__main__":
    main()
