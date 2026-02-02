#!/usr/bin/env python
"""
One-time sync of AR customers to Paystack.

This creates/updates customer records in Paystack with our customer_id and
customer_code in metadata, so incoming payments can be better matched.

Usage:
    docker compose exec app-dev python scripts/sync_customers_to_paystack.py
    docker compose exec app-dev python scripts/sync_customers_to_paystack.py --dry-run
"""
import argparse
import logging
import sys
import time
from uuid import UUID

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.models.finance.ar.customer import Customer
from app.services.settings_spec import resolve_value
from app.services.finance.payments.paystack_client import PaystackClient, PaystackConfig, PaystackError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def get_customer_email(customer: Customer) -> str | None:
    """Extract email from customer primary_contact."""
    if customer.primary_contact and isinstance(customer.primary_contact, dict):
        return customer.primary_contact.get("email")
    return None


def get_customer_phone(customer: Customer) -> str | None:
    """Extract phone from customer primary_contact."""
    if customer.primary_contact and isinstance(customer.primary_contact, dict):
        return customer.primary_contact.get("phone")
    return None


def split_name(legal_name: str) -> tuple[str, str]:
    """Split legal name into first and last name."""
    parts = legal_name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""
    return first_name, last_name


def sync_customers(dry_run: bool = False) -> dict:
    """
    Sync all active customers to Paystack.

    Returns:
        Dict with sync statistics
    """
    results = {
        "total": 0,
        "created": 0,
        "updated": 0,
        "skipped_no_email": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Get Paystack config
        secret_key = resolve_value(db, SettingDomain.payments, "paystack_secret_key")
        public_key = resolve_value(db, SettingDomain.payments, "paystack_public_key")

        if not secret_key:
            logger.error("Paystack secret key not configured")
            return results

        config = PaystackConfig(
            secret_key=str(secret_key),
            public_key=str(public_key or ""),
            webhook_secret=str(secret_key),
        )

        # Get all active customers
        customers = db.scalars(
            select(Customer)
            .where(
                Customer.organization_id == ORG_ID,
                Customer.is_active == True,
            )
            .order_by(Customer.customer_code)
        ).all()

        results["total"] = len(customers)
        logger.info(f"Found {len(customers)} active customers to sync")

        if dry_run:
            logger.info("DRY RUN - No changes will be made to Paystack")

        with PaystackClient(config) as client:
            for i, customer in enumerate(customers, 1):
                email = get_customer_email(customer)

                if not email:
                    logger.warning(f"[{i}/{len(customers)}] {customer.customer_code}: No email, skipping")
                    results["skipped_no_email"] += 1
                    continue

                phone = get_customer_phone(customer)
                first_name, last_name = split_name(customer.legal_name)

                metadata = {
                    "customer_id": str(customer.customer_id),
                    "customer_code": customer.customer_code,
                    "legal_name": customer.legal_name,
                    "customer_type": customer.customer_type.value if customer.customer_type else None,
                }

                if customer.trading_name:
                    metadata["trading_name"] = customer.trading_name

                try:
                    if dry_run:
                        logger.info(
                            f"[{i}/{len(customers)}] {customer.customer_code}: "
                            f"Would sync {email} ({first_name} {last_name})"
                        )
                        results["created"] += 1
                        continue

                    # Check if customer exists in Paystack
                    existing = client.get_customer(email)

                    if existing:
                        # Update existing customer
                        client.update_customer(
                            customer_code=existing.customer_code,
                            first_name=first_name,
                            last_name=last_name,
                            phone=phone,
                            metadata=metadata,
                        )
                        logger.info(
                            f"[{i}/{len(customers)}] {customer.customer_code}: "
                            f"Updated {email} (Paystack: {existing.customer_code})"
                        )
                        results["updated"] += 1
                    else:
                        # Create new customer
                        new_customer = client.create_customer(
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            phone=phone,
                            metadata=metadata,
                        )
                        logger.info(
                            f"[{i}/{len(customers)}] {customer.customer_code}: "
                            f"Created {email} (Paystack: {new_customer.customer_code})"
                        )
                        results["created"] += 1

                    # Rate limit: Paystack allows ~100 requests/second, but be conservative
                    time.sleep(0.1)

                except PaystackError as e:
                    logger.error(f"[{i}/{len(customers)}] {customer.customer_code}: Error - {e.message}")
                    results["errors"].append({
                        "customer_code": customer.customer_code,
                        "email": email,
                        "error": str(e.message),
                    })
                except Exception as e:
                    logger.error(f"[{i}/{len(customers)}] {customer.customer_code}: Unexpected error - {e}")
                    results["errors"].append({
                        "customer_code": customer.customer_code,
                        "email": email,
                        "error": str(e),
                    })

    return results


def main():
    parser = argparse.ArgumentParser(description="Sync AR customers to Paystack")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("CUSTOMER SYNC TO PAYSTACK")
    logger.info("=" * 80)

    results = sync_customers(dry_run=args.dry_run)

    logger.info("")
    logger.info("=" * 80)
    logger.info("SYNC COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total customers:     {results['total']}")
    logger.info(f"Created in Paystack: {results['created']}")
    logger.info(f"Updated in Paystack: {results['updated']}")
    logger.info(f"Skipped (no email):  {results['skipped_no_email']}")
    logger.info(f"Errors:              {len(results['errors'])}")

    if results["errors"]:
        logger.info("")
        logger.info("Errors:")
        for err in results["errors"][:10]:
            logger.info(f"  - {err['customer_code']} ({err['email']}): {err['error']}")
        if len(results["errors"]) > 10:
            logger.info(f"  ... and {len(results['errors']) - 10} more")


if __name__ == "__main__":
    main()
