#!/usr/bin/env python3
"""
ERPNext Integration Setup Script.

Configure ERPNext credentials for an organization with encryption.
Supports both direct credentials and OpenBao references.

Usage:
    # With direct credentials (encrypted at rest)
    python scripts/setup_erpnext.py --org-code DOTMAC \
        --url https://erp.dotmac.ng \
        --api-key REDACTED_API_KEY \
        --api-secret REDACTED_API_SECRET \
        --company "Dotmac Limited"

    # With OpenBao references
    python scripts/setup_erpnext.py --org-code DOTMAC \
        --url https://erp.dotmac.ng \
        --api-key "bao://secret/erpnext#api_key" \
        --api-secret "bao://secret/erpnext#api_secret" \
        --company "Dotmac Limited"

    # Verify connection
    python scripts/setup_erpnext.py --org-code DOTMAC --verify

    # List configurations
    python scripts/setup_erpnext.py --org-code DOTMAC --list
"""
import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.core_org import Organization
from app.models.sync import IntegrationType
from app.services.integration_config import IntegrationConfigService


def get_organization(db, org_code: str) -> Organization:
    """Get organization by code."""
    org = db.execute(
        select(Organization).where(Organization.organization_code == org_code)
    ).scalar_one_or_none()

    if not org:
        print(f"Error: Organization '{org_code}' not found")
        sys.exit(1)

    return org


def setup_erpnext(args):
    """Set up ERPNext credentials."""
    with SessionLocal() as db:
        org = get_organization(db, args.org_code)
        service = IntegrationConfigService(db)

        print(f"\nConfiguring ERPNext for: {org.legal_name} ({org.organization_code})")
        print(f"URL: {args.url}")
        print(f"Company: {args.company or '(not set)'}")

        # Check if credentials are OpenBao references
        is_vault_key = args.api_key.startswith(("bao://", "vault://", "openbao://"))
        is_vault_secret = args.api_secret.startswith(("bao://", "vault://", "openbao://"))

        if is_vault_key:
            print(f"API Key: OpenBao reference")
        else:
            print(f"API Key: {args.api_key[:8]}... (will be encrypted)")

        if is_vault_secret:
            print(f"API Secret: OpenBao reference")
        else:
            print(f"API Secret: ****** (will be encrypted)")

        # Create or update config
        existing = service.get_config(org.organization_id, IntegrationType.ERPNEXT)
        if existing:
            print("\nUpdating existing configuration...")
            config = service.update_credentials(
                org.organization_id,
                IntegrationType.ERPNEXT,
                api_key=args.api_key,
                api_secret=args.api_secret,
                base_url=args.url,
                company=args.company,
            )
        else:
            print("\nCreating new configuration...")
            config = service.create_config(
                organization_id=org.organization_id,
                integration_type=IntegrationType.ERPNEXT,
                base_url=args.url,
                api_key=args.api_key,
                api_secret=args.api_secret,
                company=args.company,
            )

        db.commit()
        print(f"\n✓ Configuration saved (ID: {config.config_id})")

        # Verify connection
        print("\nVerifying connection...")
        success, error = service.verify_connection(
            org.organization_id,
            IntegrationType.ERPNEXT,
        )

        if success:
            service.mark_verified(org.organization_id, IntegrationType.ERPNEXT)
            db.commit()
            print("✓ Connection verified successfully!")
        else:
            print(f"✗ Connection failed: {error}")
            print("\nCredentials saved but connection could not be verified.")
            print("Check the URL, API key, and API secret.")


def verify_erpnext(args):
    """Verify ERPNext connection."""
    with SessionLocal() as db:
        org = get_organization(db, args.org_code)
        service = IntegrationConfigService(db)

        print(f"\nVerifying ERPNext connection for: {org.legal_name}")

        success, error = service.verify_connection(
            org.organization_id,
            IntegrationType.ERPNEXT,
        )

        if success:
            service.mark_verified(org.organization_id, IntegrationType.ERPNEXT)
            db.commit()
            print("✓ Connection verified successfully!")
        else:
            print(f"✗ Connection failed: {error}")
            sys.exit(1)


def list_configs(args):
    """List integration configurations."""
    with SessionLocal() as db:
        org = get_organization(db, args.org_code)
        service = IntegrationConfigService(db)

        configs = service.list_configs(org.organization_id, active_only=False)

        print(f"\nIntegration configurations for: {org.legal_name}")
        print("=" * 60)

        if not configs:
            print("No configurations found.")
            return

        for config in configs:
            status = "Active" if config.is_active else "Inactive"
            verified = config.last_verified_at.strftime("%Y-%m-%d %H:%M") if config.last_verified_at else "Never"
            print(f"\n{config.integration_type.value}:")
            print(f"  Status: {status}")
            print(f"  URL: {config.base_url}")
            print(f"  Company: {config.company or '(not set)'}")
            print(f"  Last Verified: {verified}")
            print(f"  Config ID: {config.config_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Configure ERPNext integration for an organization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--org-code",
        required=True,
        help="Organization code (e.g., DOTMAC)",
    )

    # Action flags
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing connection",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all integration configurations",
    )

    # Configuration options
    parser.add_argument(
        "--url",
        help="ERPNext URL (e.g., https://erp.dotmac.ng)",
    )
    parser.add_argument(
        "--api-key",
        help="API key or OpenBao reference (bao://...)",
    )
    parser.add_argument(
        "--api-secret",
        help="API secret or OpenBao reference (bao://...)",
    )
    parser.add_argument(
        "--company",
        help="ERPNext company name",
    )

    args = parser.parse_args()

    # Determine action
    if args.list:
        list_configs(args)
    elif args.verify:
        verify_erpnext(args)
    elif args.url and args.api_key and args.api_secret:
        setup_erpnext(args)
    else:
        parser.print_help()
        print("\nError: Specify --verify, --list, or provide --url, --api-key, and --api-secret")
        sys.exit(1)


if __name__ == "__main__":
    main()
