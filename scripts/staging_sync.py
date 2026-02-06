#!/usr/bin/env python3
"""
ERPNext Staging Sync Script.

Syncs ERPNext data to staging tables, validates, and displays a report.

Usage:
    python scripts/staging_sync.py --sync          # Sync to staging
    python scripts/staging_sync.py --validate     # Validate existing staging data
    python scripts/staging_sync.py --report       # Show validation report
    python scripts/staging_sync.py --duplicates   # Show duplicate emails report
"""

import argparse
import logging
import sys
import os
import uuid

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.models.sync import IntegrationType
from app.models.sync.staging import (
    StagingDepartment,
    StagingDesignation,
    StagingEmployee,
    StagingEmployeeGrade,
    StagingEmploymentType,
    StagingSyncBatch,
)
from app.services.erpnext.client import ERPNextClient, ERPNextConfig
from app.services.erpnext.sync.staging import StagingSyncOrchestrator
from app.services.erpnext.sync.validation import StagingValidationService
from app.services.erpnext.sync.import_staging import StagingImportService


def get_erpnext_config(db, org):
    """Get ERPNext config from integration settings."""
    from app.services.integration_config import IntegrationConfigService

    service = IntegrationConfigService(db)
    creds = service.get_decrypted_credentials(
        org.organization_id,
        IntegrationType.ERPNEXT,
    )

    if not creds:
        return None

    return ERPNextConfig(
        url=creds["base_url"],
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        company=creds.get("company"),
    )


def get_latest_batch(db, organization_id: uuid.UUID):
    """Get the latest staging batch for an organization."""
    return (
        db.query(StagingSyncBatch)
        .filter(
            StagingSyncBatch.organization_id == organization_id,
        )
        .order_by(StagingSyncBatch.started_at.desc())
        .first()
    )


def sync_to_staging(org_code: str = "DEFAULT"):
    """Sync ERPNext data to staging tables."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        print(f"Organization: {org.legal_name} ({org.organization_code})")

        config = get_erpnext_config(db, org)
        if not config:
            print("ERPNext not configured for this organization")
            return

        print(f"ERPNext URL: {config.url}")
        print(f"Company: {config.company}")
        print("-" * 60)

        with ERPNextClient(config) as client:
            orchestrator = StagingSyncOrchestrator(
                db=db,
                client=client,
                organization_id=org.organization_id,
            )

            print("\nSyncing to staging tables...")
            batch = orchestrator.sync_to_staging()

            print("\n" + "=" * 60)
            print("STAGING SYNC COMPLETE")
            print("=" * 60)
            print(f"Batch ID: {batch.batch_id}")
            print(f"Status: {batch.status}")
            print(f"Total Records: {batch.total_records}")
            print()

            if batch.validation_summary:
                print("Records by type:")
                for entity, stats in batch.validation_summary.items():
                    print(f"  {entity}: {stats.get('synced', 0)} synced")

            print("\nNext step: Run validation with --validate")
            return batch.batch_id


def validate_staging(org_code: str = "DEFAULT", batch_id: str = None):
    """Validate staging data."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        # Get batch
        if batch_id:
            batch = db.get(StagingSyncBatch, uuid.UUID(batch_id))
        else:
            batch = get_latest_batch(db, org.organization_id)

        if not batch:
            print("No staging batch found. Run --sync first.")
            return

        print(f"Validating batch: {batch.batch_id}")
        print(f"Synced at: {batch.synced_at}")
        print("-" * 60)

        validator = StagingValidationService(db, org.organization_id)
        report = validator.validate_batch(batch.batch_id)

        print("\n" + "=" * 60)
        print("VALIDATION COMPLETE")
        print("=" * 60)
        print(f"Total Records: {report.total_records}")
        print(f"Valid: {report.valid_records}")
        print(f"Invalid: {report.invalid_records}")
        print(f"Errors: {len(report.errors)}")
        print(f"Warnings: {len(report.warnings)}")
        print()

        # Summary by entity type
        print("Summary by Entity Type:")
        print("-" * 40)
        for entity, stats in report.summary.items():
            print(f"  {entity}:")
            print(f"    Total: {stats.get('total', 0)}")
            print(f"    Valid: {stats.get('valid', 0)}")
            print(f"    Invalid: {stats.get('invalid', 0)}")
            if "duplicate_emails" in stats:
                print(f"    Duplicate Emails: {stats['duplicate_emails']}")

        # Show errors
        if report.errors:
            print("\n" + "=" * 60)
            print("ERRORS (must fix before import)")
            print("=" * 60)
            # Group by entity type
            by_type: dict[str, list] = {}
            for err in report.errors:
                if err.entity_type not in by_type:
                    by_type[err.entity_type] = []
                by_type[err.entity_type].append(err)

            for entity_type, errors in by_type.items():
                print(f"\n{entity_type.upper()} ({len(errors)} errors):")
                for err in errors[:20]:  # Limit display
                    print(f"  [{err.source_name}] {err.message}")
                if len(errors) > 20:
                    print(f"  ... and {len(errors) - 20} more")

        # Show warnings
        if report.warnings:
            print("\n" + "=" * 60)
            print("WARNINGS (review recommended)")
            print("=" * 60)
            by_type: dict[str, list] = {}
            for warn in report.warnings:
                if warn.entity_type not in by_type:
                    by_type[warn.entity_type] = []
                by_type[warn.entity_type].append(warn)

            for entity_type, warnings in by_type.items():
                print(f"\n{entity_type.upper()} ({len(warnings)} warnings):")
                for warn in warnings[:10]:
                    print(f"  [{warn.source_name}] {warn.message}")
                if len(warnings) > 10:
                    print(f"  ... and {len(warnings) - 10} more")

        if report.invalid_records == 0:
            print("\n✓ All records valid! Ready for import.")
        else:
            print(
                f"\n✗ {report.invalid_records} records have errors that must be fixed."
            )
            print("  Fix the issues in ERPNext, then run --sync again.")


def show_duplicates_report(org_code: str = "DEFAULT", batch_id: str = None):
    """Show detailed duplicate emails report."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        # Get batch
        if batch_id:
            batch = db.get(StagingSyncBatch, uuid.UUID(batch_id))
        else:
            batch = get_latest_batch(db, org.organization_id)

        if not batch:
            print("No staging batch found. Run --sync first.")
            return

        validator = StagingValidationService(db, org.organization_id)
        duplicates = validator.get_duplicate_emails_report(batch.batch_id)

        print("=" * 70)
        print("DUPLICATE EMAILS REPORT")
        print("=" * 70)
        print(f"Batch: {batch.batch_id}")
        print(f"Total duplicate groups: {len(duplicates)}")
        print()

        if not duplicates:
            print("✓ No duplicate emails found!")
            return

        for dup in duplicates:
            print("-" * 70)
            print(f"EMAIL: {dup['email']} ({dup['count']} employees)")
            print("-" * 70)
            for emp in dup["employees"]:
                print(f"  {emp['employee_code']}: {emp['employee_name']}")
                print(f"    Department: {emp['department'] or 'N/A'}")
                print(f"    Status: {emp['status'] or 'N/A'}")
                print(f"    Company Email: {emp['company_email'] or 'N/A'}")
                print(f"    Personal Email: {emp['personal_email'] or 'N/A'}")
            print()

        print("\nACTION REQUIRED:")
        print(
            "  1. Update emails in ERPNext to ensure each employee has a unique email"
        )
        print("  2. Run --sync again to refresh staging data")
        print("  3. Run --validate to confirm fixes")


def show_missing_references(org_code: str = "DEFAULT", batch_id: str = None):
    """Show missing foreign key references."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        # Get batch
        if batch_id:
            batch = db.get(StagingSyncBatch, uuid.UUID(batch_id))
        else:
            batch = get_latest_batch(db, org.organization_id)

        if not batch:
            print("No staging batch found. Run --sync first.")
            return

        validator = StagingValidationService(db, org.organization_id)
        missing = validator.get_missing_references_report(batch.batch_id)

        print("=" * 60)
        print("MISSING REFERENCES REPORT")
        print("=" * 60)

        for ref_type, values in missing.items():
            if values:
                print(f"\n{ref_type.replace('_', ' ').title()}:")
                for v in values:
                    print(f"  - {v}")
            else:
                print(f"\n{ref_type.replace('_', ' ').title()}: None")


def show_staging_summary(org_code: str = "DEFAULT"):
    """Show summary of staging data."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        batch = get_latest_batch(db, org.organization_id)
        if not batch:
            print("No staging batch found. Run --sync first.")
            return

        print("=" * 60)
        print("STAGING DATA SUMMARY")
        print("=" * 60)
        print(f"Organization: {org.legal_name}")
        print(f"Batch ID: {batch.batch_id}")
        print(f"Status: {batch.status}")
        print(f"Started: {batch.started_at}")
        print(f"Synced: {batch.synced_at}")
        print(f"Validated: {batch.validated_at}")
        print()

        # Count records
        dept_count = (
            db.query(StagingDepartment)
            .filter(StagingDepartment.batch_id == batch.batch_id)
            .count()
        )
        desg_count = (
            db.query(StagingDesignation)
            .filter(StagingDesignation.batch_id == batch.batch_id)
            .count()
        )
        emptype_count = (
            db.query(StagingEmploymentType)
            .filter(StagingEmploymentType.batch_id == batch.batch_id)
            .count()
        )
        grade_count = (
            db.query(StagingEmployeeGrade)
            .filter(StagingEmployeeGrade.batch_id == batch.batch_id)
            .count()
        )
        emp_count = (
            db.query(StagingEmployee)
            .filter(StagingEmployee.batch_id == batch.batch_id)
            .count()
        )

        print("Record Counts:")
        print(f"  Departments: {dept_count}")
        print(f"  Designations: {desg_count}")
        print(f"  Employment Types: {emptype_count}")
        print(f"  Employee Grades: {grade_count}")
        print(f"  Employees: {emp_count}")


def import_to_production(
    org_code: str = "DEFAULT",
    batch_id: str = None,
    skip_invalid: bool = False,
    generate_emails: bool = True,
):
    """Import validated staging data to production tables."""
    with SessionLocal() as db:
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        # Get batch
        if batch_id:
            batch = db.get(StagingSyncBatch, uuid.UUID(batch_id))
        else:
            batch = get_latest_batch(db, org.organization_id)

        if not batch:
            print("No staging batch found. Run --sync first.")
            return

        if batch.status not in ("VALIDATED", "SYNCED"):
            print(f"Batch status is '{batch.status}'. Run --validate first.")
            return

        print("=" * 60)
        print("IMPORT TO PRODUCTION")
        print("=" * 60)
        print(f"Organization: {org.legal_name}")
        print(f"Batch ID: {batch.batch_id}")
        print(f"Skip Invalid: {skip_invalid}")
        print(f"Generate Placeholder Emails: {generate_emails}")
        print("-" * 60)

        # Confirm
        confirm = input("\nProceed with import? (yes/no): ")
        if confirm.lower() != "yes":
            print("Import cancelled.")
            return

        print("\nImporting...")

        importer = StagingImportService(
            db=db,
            organization_id=org.organization_id,
        )

        try:
            results = importer.import_batch(
                batch_id=batch.batch_id,
                skip_invalid=skip_invalid,
                generate_placeholder_emails=generate_emails,
            )

            print("\n" + "=" * 60)
            print("IMPORT COMPLETE")
            print("=" * 60)

            total_imported = 0
            total_skipped = 0
            total_errors = 0

            for entity_type, result in results.items():
                print(f"\n{entity_type.upper()}:")
                print(f"  Total: {result.total}")
                print(f"  Imported: {result.imported}")
                print(f"  Skipped: {result.skipped}")
                print(f"  Errors: {len(result.errors)}")

                total_imported += result.imported
                total_skipped += result.skipped
                total_errors += len(result.errors)

                if result.errors:
                    print("  Error details:")
                    for err in result.errors[:5]:
                        print(f"    - {err}")
                    if len(result.errors) > 5:
                        print(f"    ... and {len(result.errors) - 5} more")

            print("\n" + "-" * 60)
            print(
                f"TOTAL: {total_imported} imported, {total_skipped} skipped, {total_errors} errors"
            )

            if total_errors == 0:
                print("\n✓ Import successful!")
            else:
                print(f"\n⚠ Import completed with {total_errors} errors")

        except ValueError as e:
            print(f"\n✗ Import failed: {e}")
        except Exception as e:
            print(f"\n✗ Import error: {e}")
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ERPNext Staging Sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/staging_sync.py --sync              Sync ERPNext data to staging
  python scripts/staging_sync.py --validate          Validate staging data
  python scripts/staging_sync.py --duplicates        Show duplicate emails
  python scripts/staging_sync.py --missing           Show missing references
  python scripts/staging_sync.py --summary           Show staging summary
  python scripts/staging_sync.py --import            Import staging to production
  python scripts/staging_sync.py --import --skip-invalid  Import, skipping invalid records
        """,
    )
    parser.add_argument("--org-code", default="DEFAULT", help="Organization code")
    parser.add_argument("--batch-id", help="Specific batch ID (default: latest)")
    parser.add_argument("--sync", action="store_true", help="Sync to staging")
    parser.add_argument("--validate", action="store_true", help="Validate staging data")
    parser.add_argument(
        "--duplicates", action="store_true", help="Show duplicate emails"
    )
    parser.add_argument(
        "--missing", action="store_true", help="Show missing references"
    )
    parser.add_argument("--summary", action="store_true", help="Show staging summary")
    parser.add_argument(
        "--import", dest="do_import", action="store_true", help="Import to production"
    )
    parser.add_argument(
        "--skip-invalid", action="store_true", help="Skip invalid records during import"
    )
    parser.add_argument(
        "--no-placeholder-emails",
        action="store_true",
        help="Don't generate placeholder emails",
    )

    args = parser.parse_args()

    if args.sync:
        sync_to_staging(args.org_code)
    elif args.validate:
        validate_staging(args.org_code, args.batch_id)
    elif args.duplicates:
        show_duplicates_report(args.org_code, args.batch_id)
    elif args.missing:
        show_missing_references(args.org_code, args.batch_id)
    elif args.summary:
        show_staging_summary(args.org_code)
    elif args.do_import:
        import_to_production(
            org_code=args.org_code,
            batch_id=args.batch_id,
            skip_invalid=args.skip_invalid,
            generate_emails=not args.no_placeholder_emails,
        )
    else:
        parser.print_help()
