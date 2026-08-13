#!/usr/bin/env python3
"""Create a new organization with auto-seeded tax configuration.

Two sessions, deliberately. Creating the organization is cross-tenant work —
there is no organization to be scoped to yet — but seeding its tax data is
per-organization work on the row that was just created. Reusing the
cross-org session for the seed would run the whole seed with RLS bypassed,
which is the mistake `cross_org_session`'s own docstring warns about.
"""

import argparse
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.db.session_context import cross_org_session, session_for_org
from app.models.finance.core_org.organization import Organization
from app.services.finance.tax.seed import get_country_config, seed_default_tax_data
from app.services.tenant_projection import reconcile_organization_tenant


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

    # Cross-org: the organization does not exist yet, so there is nothing to
    # scope to. The uniqueness check must also see every organization, not
    # just one — a code collision in another tenant is still a collision.
    with cross_org_session() as db:
        existing = (
            db.query(Organization)
            .filter(Organization.organization_code == args.code)
            .first()
        )
        if existing:
            reconcile_organization_tenant(db, existing)
            db.commit()
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
        reconcile_organization_tenant(db, org)
        db.commit()
        org_id = org.organization_id
        print(f"Created organization: {args.name}")
        print(f"  Organization ID: {org_id}")
        print(f"  Code: {org.organization_code}")
        print(f"  Country: {org.jurisdiction_country_code}")

    if args.skip_tax_seed:
        return

    config = get_country_config(args.country)
    if not config:
        print(f"\n  No tax configuration available for country '{args.country}'")
        print("  Use 'seed_nigeria.py' or create jurisdictions manually")
        return

    # Per-org: everything below writes rows that belong to the organization
    # just created, so it runs under that organization's scope rather than
    # continuing with the bypass above.
    print(f"\nSeeding tax configuration for {config.country_name}...")
    with session_for_org(org_id) as db:
        summary = seed_default_tax_data(db, org_id, country_code=args.country)
        # `seed_default_tax_data` flushes but does not commit, and neither
        # `session_for_org` nor the old `try/finally: db.close()` ever did.
        # So every organization created with tax seeding enabled had its whole
        # tax configuration discarded at close — the organization committed,
        # the seed did not. Committing here is the fix, not a tidy-up.
        db.commit()
        print(f"  Categories created: {summary.categories_created}")
        print(f"  Accounts created: {summary.accounts_created}")
        print(f"  Jurisdictions created: {summary.jurisdictions_created}")
        print(f"  Tax codes created: {summary.tax_codes_created}")
        if summary.default_jurisdiction_id:
            print(f"  Default jurisdiction ID: {summary.default_jurisdiction_id}")


if __name__ == "__main__":
    main()
