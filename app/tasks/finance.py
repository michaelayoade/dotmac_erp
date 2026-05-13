"""
Finance Module Background Tasks - Celery tasks for finance workflows.

Handles:
- Fiscal period close reminders
- Tax filing due date reminders
- Bank reconciliation overdue alerts
- AR collection reminders for overdue invoices
- Subledger reconciliation discrepancy alerts
"""

import asyncio
import html
import logging
from datetime import date
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.email_profile import EmailModule
from app.models.finance.core_org.organization import Organization
from app.models.finance.rpt.report_instance import ReportInstance, ReportStatus
from app.models.notification import EntityType, NotificationType
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.rls import bypass_rls_sync, set_current_organization_sync
from app.services.email import send_email
from app.services.finance.rpt.report_instance import ReportInstanceService
from app.services.notification import NotificationService
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


def _get_export_instance(db: Session, instance_id: str) -> ReportInstance | None:
    """Load a queued report instance and set tenant context for generation."""
    with bypass_rls_sync(db):
        instance = db.get(ReportInstance, UUID(instance_id))
    if instance:
        set_current_organization_sync(db, instance.organization_id)
    return instance


def _notify_export_result(
    db: Session,
    instance: ReportInstance,
    *,
    title: str,
    message: str,
    action_url: str | None = None,
) -> None:
    """Create an in-app notification and send a direct email for an export."""
    NotificationService().create(
        db,
        organization_id=instance.organization_id,
        recipient_id=instance.generated_by_user_id,
        entity_type=EntityType.SYSTEM,
        entity_id=instance.instance_id,
        notification_type=NotificationType.INFO,
        title=title,
        message=message,
        action_url=action_url,
    )
    db.commit()
    set_current_organization_sync(db, instance.organization_id)

    recipient = db.get(Person, instance.generated_by_user_id)
    if not recipient or not recipient.email:
        return

    safe_message = html.escape(message)
    body_html = f"<p>{safe_message}</p>"
    body_text = message
    if action_url:
        download_url = f"{settings.app_url.rstrip('/')}{action_url}"
        safe_url = html.escape(download_url)
        body_html += f'<p><a href="{safe_url}">Download export</a></p>'
        body_text = f"{message}\n\nDownload export: {download_url}"

    send_email(
        db=db,
        to_email=recipient.email,
        subject=title,
        body_html=body_html,
        body_text=body_text,
        module=EmailModule.FINANCE,
        organization_id=instance.organization_id,
    )


@shared_task
def process_general_ledger_export(
    instance_id: str,
) -> dict[str, Any]:
    """Generate a queued General Ledger export and notify the requester."""
    with SessionLocal() as db:
        instance = _get_export_instance(db, instance_id)
        if not instance:
            return {"success": False, "error": "Report instance not found"}

        if instance.status not in {ReportStatus.QUEUED, ReportStatus.FAILED}:
            return {"success": False, "status": instance.status.value}

        try:
            instance = ReportInstanceService.start_generation(db, instance.instance_id)
            params = instance.parameters_used or {}
            fmt = (instance.output_format or "CSV").upper()

            from app.services.finance.rpt.web import reports_web_service

            data: bytes
            if fmt == "PDF":
                data = reports_web_service.export_general_ledger_pdf(
                    str(instance.organization_id),
                    db,
                    params.get("account_id"),
                    params.get("start_date"),
                    params.get("end_date"),
                )
                suffix = "pdf"
            else:
                csv_content = reports_web_service.export_general_ledger_csv(
                    str(instance.organization_id),
                    db,
                    params.get("account_id"),
                    params.get("start_date"),
                    params.get("end_date"),
                )
                suffix = "csv"
                data = csv_content.encode("utf-8")

            storage_key = (
                f"generated_reports/{instance.organization_id}/"
                f"general_ledger_{instance.instance_id}.{suffix}"
            )
            media_type = "application/pdf" if fmt == "PDF" else "text/csv"
            get_storage().upload(storage_key, data, media_type)

            instance = ReportInstanceService.complete_generation(
                db=db,
                instance_id=instance.instance_id,
                output_file_path=f"s3://{storage_key}",
                output_size_bytes=len(data),
            )

            action_url = (
                f"/finance/reports/general-ledger/exports/"
                f"{instance.instance_id}/download"
            )
            _notify_export_result(
                db,
                instance,
                title="General Ledger export ready",
                message="Your General Ledger export is ready to download.",
                action_url=action_url,
            )
            return {
                "success": True,
                "instance_id": str(instance.instance_id),
                "output_size_bytes": len(data),
            }
        except Exception as exc:
            logger.exception("General Ledger export failed for %s", instance_id)
            try:
                instance = ReportInstanceService.fail_generation(
                    db=db,
                    instance_id=instance.instance_id,
                    error_message=str(exc),
                )
                _notify_export_result(
                    db,
                    instance,
                    title="General Ledger export failed",
                    message=(
                        "Your General Ledger export could not be generated. "
                        "Please narrow the filters and try again."
                    ),
                )
            except Exception:
                logger.exception("Failed to record General Ledger export failure")
            return {"success": False, "instance_id": instance_id, "error": str(exc)}


def _response_body_bytes(response: Any) -> bytes:
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def _export_action_url(report_code: str, instance_id: UUID) -> str:
    bases = {
        "GL_JOURNALS": "/finance/gl/journals/exports",
        "AR_INVOICES": "/finance/ar/invoices/exports",
        "AR_RECEIPTS": "/finance/ar/receipts/exports",
    }
    return f"{bases[report_code]}/{instance_id}/download"


def _export_label(report_code: str) -> str:
    return {
        "GL_JOURNALS": "GL Journals",
        "AR_INVOICES": "AR Invoices",
        "AR_RECEIPTS": "AR Receipts",
    }[report_code]


def _export_filename_prefix(report_code: str) -> str:
    return {
        "GL_JOURNALS": "gl_journals",
        "AR_INVOICES": "ar_invoices",
        "AR_RECEIPTS": "ar_receipts",
    }[report_code]


async def _build_list_export_response(
    db: Session,
    instance: ReportInstance,
    report_code: str,
) -> Any:
    params = instance.parameters_used or {}
    search = str(params.get("search") or "")
    status = str(params.get("status") or "")
    start_date = str(params.get("start_date") or "")
    end_date = str(params.get("end_date") or "")

    if report_code == "GL_JOURNALS":
        from app.services.finance.gl.bulk import get_journal_bulk_service

        journal_service = get_journal_bulk_service(
            db,
            instance.organization_id,
            instance.generated_by_user_id,
        )
        return await journal_service.export_all(search, status, start_date, end_date)

    if report_code == "AR_INVOICES":
        from app.services.finance.ar.invoice_bulk import get_ar_invoice_bulk_service

        invoice_service = get_ar_invoice_bulk_service(
            db,
            instance.organization_id,
            instance.generated_by_user_id,
        )
        return await invoice_service.export_all(
            search,
            status,
            start_date,
            end_date,
            {"customer_id": params.get("customer_id") or ""},
        )

    if report_code == "AR_RECEIPTS":
        from app.services.finance.ar.receipt_bulk import get_ar_receipt_bulk_service

        receipt_service = get_ar_receipt_bulk_service(
            db,
            instance.organization_id,
            instance.generated_by_user_id,
        )
        return await receipt_service.export_all(
            search,
            status,
            start_date,
            end_date,
            {"customer_id": params.get("customer_id") or ""},
        )

    raise ValueError(f"Unsupported export report code: {report_code}")


def _process_list_export(instance_id: str, report_code: str) -> dict[str, Any]:
    label = _export_label(report_code)
    with SessionLocal() as db:
        instance = _get_export_instance(db, instance_id)
        if not instance:
            return {"success": False, "error": "Report instance not found"}

        if instance.status not in {ReportStatus.QUEUED, ReportStatus.FAILED}:
            return {"success": False, "status": instance.status.value}

        try:
            instance = ReportInstanceService.start_generation(db, instance.instance_id)
            response = asyncio.run(
                _build_list_export_response(db, instance, report_code)
            )
            data = _response_body_bytes(response)
            storage_key = (
                f"generated_reports/{instance.organization_id}/"
                f"{_export_filename_prefix(report_code)}_{instance.instance_id}.csv"
            )
            get_storage().upload(storage_key, data, "text/csv")

            instance = ReportInstanceService.complete_generation(
                db=db,
                instance_id=instance.instance_id,
                output_file_path=f"s3://{storage_key}",
                output_size_bytes=len(data),
            )

            action_url = _export_action_url(report_code, instance.instance_id)
            _notify_export_result(
                db,
                instance,
                title=f"{label} export ready",
                message=f"Your {label} export is ready to download.",
                action_url=action_url,
            )
            return {
                "success": True,
                "instance_id": str(instance.instance_id),
                "output_size_bytes": len(data),
            }
        except Exception as exc:
            logger.exception("%s export failed for %s", label, instance_id)
            try:
                instance = ReportInstanceService.fail_generation(
                    db=db,
                    instance_id=instance.instance_id,
                    error_message=str(exc),
                )
                _notify_export_result(
                    db,
                    instance,
                    title=f"{label} export failed",
                    message=(
                        f"Your {label} export could not be generated. "
                        "Please narrow the filters and try again."
                    ),
                )
            except Exception:
                logger.exception("Failed to record %s export failure", label)
            return {"success": False, "instance_id": instance_id, "error": str(exc)}


@shared_task
def process_gl_journals_export(instance_id: str) -> dict[str, Any]:
    """Generate a queued GL Journals export and notify the requester."""
    return _process_list_export(instance_id, "GL_JOURNALS")


@shared_task
def process_ar_invoices_export(instance_id: str) -> dict[str, Any]:
    """Generate a queued AR Invoices export and notify the requester."""
    return _process_list_export(instance_id, "AR_INVOICES")


@shared_task
def process_ar_receipts_export(instance_id: str) -> dict[str, Any]:
    """Generate a queued AR Receipts export and notify the requester."""
    return _process_list_export(instance_id, "AR_RECEIPTS")


def _get_finance_recipients(
    db: Session,
    organization_id: UUID,
    role_names: list[str],
) -> list[UUID]:
    """
    Get user IDs with specified finance roles within a single organization.

    Args:
        db: Database session
        organization_id: Organization to scope recipients to
        role_names: List of role names to include

    Returns:
        List of person_ids with the specified roles in the given organization
    """
    stmt = (
        select(PersonRole.person_id)
        .join(Role, PersonRole.role_id == Role.id)
        .join(Person, PersonRole.person_id == Person.id)
        .where(
            Person.organization_id == organization_id,
            Role.name.in_(role_names),
            Role.is_active.is_(True),
        )
    )
    return list(db.scalars(stmt).all())


@shared_task
def process_monthly_depreciation_runs(
    auto_post: bool | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """
    Create due monthly fixed-asset depreciation runs for active organizations.

    The task scans for the next open/reopened fiscal period that has already
    ended and does not already have a non-failed depreciation run.
    """
    from app.services.fixed_assets.depreciation import DepreciationService

    logger.info("Processing monthly fixed-asset depreciation runs")

    results: dict[str, Any] = {
        "automation_enabled": False,
        "auto_post": False,
        "organizations_checked": 0,
        "runs_calculated": 0,
        "runs_posted": 0,
        "skipped": 0,
        "errors": [],
    }

    run_cutoff_date = date.fromisoformat(as_of_date) if as_of_date else date.today()

    with SessionLocal() as db:
        if not DepreciationService.automation_enabled(db):
            logger.info("Monthly FA depreciation automation is disabled")
            return results

        effective_auto_post = (
            auto_post
            if auto_post is not None
            else DepreciationService.automation_auto_post_enabled(db)
        )
        results["automation_enabled"] = True
        results["auto_post"] = effective_auto_post

        organization_ids = DepreciationService.list_active_organization_ids(db)
        results["organizations_checked"] = len(organization_ids)

        for organization_id in organization_ids:
            try:
                outcome = DepreciationService.create_automated_monthly_run(
                    db,
                    organization_id,
                    as_of_date=run_cutoff_date,
                    auto_post=effective_auto_post,
                )
                if outcome["status"] == "posted":
                    results["runs_posted"] += 1
                elif outcome["status"] == "calculated":
                    results["runs_calculated"] += 1
                else:
                    results["skipped"] += 1
            except Exception as exc:
                logger.exception(
                    "Monthly FA depreciation automation failed for org %s",
                    organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {
                        "organization_id": str(organization_id),
                        "error": str(exc),
                    }
                )

    logger.info(
        "Monthly FA depreciation automation complete: orgs=%d, calculated=%d, "
        "posted=%d, skipped=%d, errors=%d",
        results["organizations_checked"],
        results["runs_calculated"],
        results["runs_posted"],
        results["skipped"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_fiscal_period_reminders() -> dict[str, Any]:
    """
    Send notifications for fiscal periods that are ending soon.

    Sends reminders:
    - 7 days before period ends
    - 3 days before period ends
    - 1 day before period ends

    Returns:
        Dict with notification statistics
    """
    from app.services.finance.reminder_service import FinanceReminderService

    logger.info("Processing fiscal period close reminders")

    results: dict[str, Any] = {
        "periods_checked": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        service = FinanceReminderService(db)
        periods = service.get_periods_closing_soon()
        results["periods_checked"] = len(periods)

        for period in periods:
            try:
                notice_type = service.get_period_notice_type(period)
                if not notice_type:
                    continue

                # Get accountants and finance managers for this org
                recipients = _get_finance_recipients(
                    db,
                    period.organization_id,
                    ["accountant", "finance_manager", "controller", "cfo"],
                )

                if not recipients:
                    logger.warning(
                        "No finance recipients found for period %s (org %s)",
                        period.fiscal_period_id,
                        period.organization_id,
                    )
                    continue

                sent = service.send_fiscal_period_reminder(
                    period, recipients, notice_type
                )
                results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to send reminder for period %s",
                    period.fiscal_period_id,
                )
                results["errors"].append(f"Period {period.fiscal_period_id}: {str(e)}")

        db.commit()

    logger.info(
        "Fiscal period reminders complete: %d periods, %d notifications, %d errors",
        results["periods_checked"],
        results["notifications_sent"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_tax_period_reminders() -> dict[str, Any]:
    """
    Send notifications for tax periods with upcoming or overdue filing deadlines.

    Sends reminders at:
    - 30 days before due
    - 14 days before due
    - 7 days before due
    - 3 days before due (urgent)
    - Daily when overdue

    Returns:
        Dict with notification statistics
    """
    from app.services.finance.reminder_service import FinanceReminderService

    logger.info("Processing tax period filing reminders")

    results: dict[str, Any] = {
        "periods_due_soon": 0,
        "periods_overdue": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        service = FinanceReminderService(db)

        # Process periods due soon
        due_soon = service.get_tax_periods_due_soon()
        results["periods_due_soon"] = len(due_soon)

        for period in due_soon:
            try:
                notice_type = service.get_tax_period_notice_type(period)
                if not notice_type:
                    continue

                recipients = _get_finance_recipients(
                    db,
                    period.organization_id,
                    ["accountant", "finance_manager", "tax_accountant", "controller"],
                )

                if not recipients:
                    continue

                sent = service.send_tax_period_reminder(period, recipients, notice_type)
                results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to send tax reminder for period %s",
                    period.period_id,
                )
                results["errors"].append(f"Tax period {period.period_id}: {str(e)}")

        # Process overdue periods — send ONE digest per org instead of N
        # individual notifications (prevents email spam when many periods
        # are overdue)
        overdue = service.get_overdue_tax_periods()
        results["periods_overdue"] = len(overdue)

        if overdue:
            # Group by org for multi-tenant safety
            by_org: dict[UUID, list] = {}
            for period in overdue:
                by_org.setdefault(period.organization_id, []).append(period)

            for org_id, org_periods in by_org.items():
                try:
                    recipients = _get_finance_recipients(
                        db,
                        org_id,
                        [
                            "accountant",
                            "finance_manager",
                            "tax_accountant",
                            "controller",
                            "cfo",
                        ],
                    )

                    if not recipients:
                        continue

                    sent = service.send_tax_period_digest(
                        org_periods, recipients, org_id
                    )
                    results["notifications_sent"] += sent
                except Exception as e:
                    logger.exception(
                        "Failed to send tax overdue digest for org %s",
                        org_id,
                    )
                    results["errors"].append(
                        f"Tax overdue digest org {org_id}: {str(e)}"
                    )

        db.commit()

    logger.info(
        "Tax period reminders complete: %d due soon, %d overdue, %d notifications",
        results["periods_due_soon"],
        results["periods_overdue"],
        results["notifications_sent"],
    )
    return results


@shared_task
def process_bank_reconciliation_reminders() -> dict[str, Any]:
    """
    Send notifications for bank accounts that need reconciliation.

    Alerts when:
    - Account has never been reconciled
    - Last reconciliation is 15+ days old (warning)
    - Last reconciliation is 30+ days old (overdue)
    - Last reconciliation is 45+ days old (critical)

    Returns:
        Dict with notification statistics
    """
    from app.services.finance.reminder_service import FinanceReminderService

    logger.info("Processing bank reconciliation reminders")

    results: dict[str, Any] = {
        "accounts_checked": 0,
        "accounts_needing_action": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        service = FinanceReminderService(db)
        accounts = service.get_accounts_needing_reconciliation()
        results["accounts_checked"] = len(accounts)

        # Classify accounts by urgency, then send ONE digest per org
        # instead of N individual notifications
        accounts_with_urgency: list[tuple] = []
        for account in accounts:
            urgency = service.get_reconciliation_urgency(account)
            if urgency:
                accounts_with_urgency.append((account, urgency))
                results["accounts_needing_action"] += 1

        if accounts_with_urgency:
            # Group by org for multi-tenant safety
            by_org: dict[UUID, list[tuple]] = {}
            for acct, urg in accounts_with_urgency:
                by_org.setdefault(acct.organization_id, []).append((acct, urg))

            for org_id, org_accounts in by_org.items():
                try:
                    recipients = _get_finance_recipients(
                        db,
                        org_id,
                        ["accountant", "finance_manager", "controller"],
                    )

                    if not recipients:
                        continue

                    sent = service.send_reconciliation_digest(
                        org_accounts, recipients, org_id
                    )
                    results["notifications_sent"] += sent
                except Exception as e:
                    logger.exception(
                        "Failed to send reconciliation digest for org %s",
                        org_id,
                    )
                    results["errors"].append(f"Recon digest org {org_id}: {str(e)}")

        db.commit()

    logger.info(
        "Bank reconciliation reminders complete: %d accounts, %d need action, %d notifications",
        results["accounts_checked"],
        results["accounts_needing_action"],
        results["notifications_sent"],
    )
    return results


@shared_task
def process_ar_collection_reminders() -> dict[str, Any]:
    """
    Send notifications for overdue AR invoices that need collection follow-up.

    Prioritizes by aging bucket:
    - 90+ days: Critical alert
    - 60-90 days: Overdue notification
    - 30-60 days: Overdue notification
    - 1-30 days: Due soon notification

    Returns:
        Dict with notification statistics
    """
    from app.services.finance.reminder_service import FinanceReminderService

    logger.info("Processing AR collection reminders")

    results: dict[str, Any] = {
        "invoices_checked": 0,
        "by_bucket": {
            "1-30": 0,
            "31-60": 0,
            "61-90": 0,
            "over-90": 0,
        },
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        service = FinanceReminderService(db)
        invoices = service.get_overdue_invoices(min_days_overdue=1)
        results["invoices_checked"] = len(invoices)

        for invoice in invoices:
            try:
                bucket = service.get_invoice_aging_bucket(invoice)
                if bucket != "current" and bucket in results["by_bucket"]:
                    results["by_bucket"][bucket] += 1

                recipients = _get_finance_recipients(
                    db,
                    invoice.organization_id,
                    ["accountant", "ar_clerk", "finance_manager", "collections"],
                )

                if not recipients:
                    continue

                sent = service.send_collection_reminder(invoice, recipients)
                results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to send collection reminder for invoice %s",
                    invoice.invoice_id,
                )
                results["errors"].append(f"Invoice {invoice.invoice_id}: {str(e)}")

        db.commit()

    logger.info(
        "AR collection reminders complete: %d invoices, %d notifications, buckets=%s",
        results["invoices_checked"],
        results["notifications_sent"],
        results["by_bucket"],
    )
    return results


@shared_task
def process_subledger_reconciliation() -> dict[str, Any]:
    """
    Check for discrepancies between GL control accounts and subledgers.

    Compares:
    - AR control account vs sum of open customer balances
    - AP control account vs sum of open supplier balances

    Sends alerts when discrepancies are found.

    Returns:
        Dict with reconciliation statistics
    """
    from decimal import Decimal

    from app.services.finance.dashboard import DashboardService
    from app.services.finance.reminder_service import FinanceReminderService

    logger.info("Processing subledger reconciliation checks")

    results: dict[str, Any] = {
        "organizations_checked": 0,
        "ar_discrepancies": 0,
        "ap_discrepancies": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Get all active organizations
        organizations = db.scalars(
            select(Organization).where(Organization.is_active.is_(True))
        ).all()

        results["organizations_checked"] = len(organizations)
        reminder_service = FinanceReminderService(db)

        for org in organizations:
            try:
                # Use dashboard service to get reconciliation status
                recon_data = DashboardService.get_subledger_reconciliation(
                    db, org.organization_id
                )

                # Check AR discrepancy
                if not recon_data.get("ar_ok", True):
                    results["ar_discrepancies"] += 1

                    recipients = _get_finance_recipients(
                        db,
                        org.organization_id,
                        ["accountant", "finance_manager", "controller"],
                    )

                    if recipients:
                        sent = reminder_service.send_subledger_discrepancy_alert(
                            organization_id=org.organization_id,
                            recipient_ids=recipients,
                            subledger_type="AR",
                            gl_balance=Decimal(str(recon_data.get("gl_ar_balance", 0))),
                            subledger_balance=Decimal(
                                str(recon_data.get("subledger_ar_balance", 0))
                            ),
                        )
                        results["notifications_sent"] += sent

                # Check AP discrepancy
                if not recon_data.get("ap_ok", True):
                    results["ap_discrepancies"] += 1

                    recipients = _get_finance_recipients(
                        db,
                        org.organization_id,
                        ["accountant", "finance_manager", "controller"],
                    )

                    if recipients:
                        sent = reminder_service.send_subledger_discrepancy_alert(
                            organization_id=org.organization_id,
                            recipient_ids=recipients,
                            subledger_type="AP",
                            gl_balance=Decimal(str(recon_data.get("gl_ap_balance", 0))),
                            subledger_balance=Decimal(
                                str(recon_data.get("subledger_ap_balance", 0))
                            ),
                        )
                        results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to check subledger reconciliation for org %s",
                    org.organization_id,
                )
                results["errors"].append(f"Org {org.organization_id}: {str(e)}")

        db.commit()

    logger.info(
        "Subledger reconciliation complete: %d orgs, %d AR discrepancies, "
        "%d AP discrepancies, %d notifications",
        results["organizations_checked"],
        results["ar_discrepancies"],
        results["ap_discrepancies"],
        results["notifications_sent"],
    )
    return results


@shared_task
def process_all_finance_reminders() -> dict[str, Any]:
    """
    Master task that runs all finance reminder tasks.

    This can be scheduled as a single daily task, or individual tasks
    can be scheduled separately with different frequencies.

    Each subtask is run independently - failures in one don't stop others.

    Returns:
        Dict with combined results from all tasks
    """
    logger.info("Processing all finance reminders")

    results: dict[str, Any] = {
        "fiscal_periods": {},
        "tax_periods": {},
        "bank_reconciliation": {},
        "ar_collection": {},
        "subledger_reconciliation": {},
        "task_errors": [],
    }

    # Run each subtask directly (not via .delay()) so we can aggregate
    # results into a single return dict for monitoring. Each call is wrapped
    # in its own try/except so one failure does not prevent the others.
    task_runners = [
        ("fiscal_periods", process_fiscal_period_reminders),
        ("tax_periods", process_tax_period_reminders),
        ("bank_reconciliation", process_bank_reconciliation_reminders),
        ("ar_collection", process_ar_collection_reminders),
        ("subledger_reconciliation", process_subledger_reconciliation),
    ]

    for task_name, task_func in task_runners:
        try:
            results[task_name] = task_func()
        except Exception as e:
            logger.exception("Finance reminder subtask '%s' failed", task_name)
            results[task_name] = {"error": str(e)}
            results["task_errors"].append(f"{task_name}: {str(e)}")

    total_notifications = sum(
        r.get("notifications_sent", 0)
        for r in results.values()
        if isinstance(r, dict) and "notifications_sent" in r
    )

    logger.info(
        "All finance reminders complete: %d total notifications sent, %d task errors",
        total_notifications,
        len(results["task_errors"]),
    )

    return results


@shared_task
def sync_paystack_transactions(days_back: int = 1) -> dict[str, Any]:
    """
    Sync Paystack transactions to bank statements for reconciliation.

    This task fetches transactions and transfers from Paystack and creates
    bank statement lines for reconciliation.

    Args:
        days_back: Number of days to sync (default: 1 for daily sync)

    Returns:
        Dict with sync statistics per organization
    """
    from datetime import date, timedelta

    logger.info("Starting Paystack sync for last %d days", days_back)

    results: dict[str, Any] = {
        "organizations_synced": 0,
        "total_collections": 0,
        "total_transfers": 0,
        "total_credits": "0.00",
        "total_debits": "0.00",
        "errors": [],
    }

    to_date = date.today()
    from_date = to_date - timedelta(days=days_back)

    with SessionLocal() as db:
        from app.services.finance.payments.paystack_sync import PaystackSyncService

        # Get all organizations with Paystack configured
        organizations = db.scalars(
            select(Organization).where(Organization.is_active.is_(True))
        ).all()

        total_credits = 0.0
        total_debits = 0.0

        for org in organizations:
            try:
                sync_svc = PaystackSyncService(db, org.organization_id)

                # Check if Paystack is configured for this org
                try:
                    sync_svc._get_paystack_config()
                except ValueError:
                    # Paystack not configured for this org
                    continue

                result = sync_svc.sync_transactions(from_date, to_date)

                if result.success:
                    results["organizations_synced"] += 1
                    results["total_collections"] += result.transactions_synced
                    results["total_transfers"] += result.transfers_synced
                    total_credits += float(result.total_credits)
                    total_debits += float(result.total_debits)

                    logger.info(
                        "Paystack sync for org %s: %d collections, %d transfers",
                        org.organization_id,
                        result.transactions_synced,
                        result.transfers_synced,
                    )
                else:
                    results["errors"].append(
                        f"Org {org.organization_id}: {result.message}"
                    )

            except Exception as e:
                logger.exception(
                    "Failed to sync Paystack for org %s", org.organization_id
                )
                results["errors"].append(f"Org {org.organization_id}: {str(e)}")

        results["total_credits"] = f"{total_credits:,.2f}"
        results["total_debits"] = f"{total_debits:,.2f}"

        db.commit()

    logger.info(
        "Paystack sync complete: %d orgs, %d collections (₦%s), %d transfers (₦%s)",
        results["organizations_synced"],
        results["total_collections"],
        results["total_credits"],
        results["total_transfers"],
        results["total_debits"],
    )

    return results


@shared_task
def sync_mono_transactions(**_legacy_kwargs: Any) -> dict[str, Any]:
    """Incremental Mono sync across every linked bank account.

    Stateful — each account computes its own window from its own watermark,
    so missed runs self-heal on the next invocation without requiring a
    lookback parameter. ``**_legacy_kwargs`` swallows any ``days_back``
    kwarg from scheduled_tasks rows seeded before the incremental refactor.
    """
    logger.info("Starting Mono sync for all linked accounts")

    with SessionLocal() as db:
        from app.services.finance.banking.mono_sync import MonoSyncService

        sync_svc = MonoSyncService(db)

        if not sync_svc.is_configured():
            logger.info("Mono Connect not configured, skipping sync")
            return {"success": True, "message": "Mono not configured", "skipped": True}

        results = sync_svc.sync_all_linked_accounts(commit_per_account=True)

    logger.info(
        "Mono sync complete: %s accounts synced, %s transactions, %s errors",
        results.get("accounts_synced", 0),
        results.get("total_transactions", 0),
        results.get("total_errors", 0),
    )

    return results


@shared_task(
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def sync_mono_account(mono_account_id: str, **_legacy_kwargs: Any) -> dict[str, Any]:
    """Incremental sync for a single Mono-linked account.

    Enqueued by the Mono webhook handler when an account transitions to
    ``data_status=AVAILABLE``, so freshly linked accounts get their first
    transaction pull without waiting for the next beat cycle.
    ``**_legacy_kwargs`` swallows any pre-refactor ``days_back`` kwarg.

    Mono only fires ``account_updated`` once per scrape, so transient DB
    blips during webhook handling would otherwise lose the signal until the
    next beat cycle. ``OperationalError`` covers connection drops and
    deadlocks; persistent failures fall through to the next scheduled
    ``sync_mono_transactions`` run.
    """
    logger.info("Syncing Mono account %s", mono_account_id)

    with SessionLocal() as db:
        from app.services.finance.banking.mono_sync import MonoSyncService

        sync_svc = MonoSyncService(db)
        if not sync_svc.is_configured():
            logger.info("Mono Connect not configured, skipping webhook sync")
            return {"success": True, "skipped": True}

        result = sync_svc.sync_by_mono_account_id(mono_account_id)
        db.commit()

    if not result.success:
        logger.warning(
            "Mono account sync failed: mono_id=%s message=%s errors=%s",
            mono_account_id,
            result.message,
            result.errors,
        )

    return {
        "success": result.success,
        "mono_account_id": mono_account_id,
        "transactions_synced": result.transactions_synced,
        "duplicates_skipped": result.duplicates_skipped,
        "message": result.message,
    }


@shared_task
def rebuild_account_balances() -> dict[str, Any]:
    """
    Safety-net: rebuild all account balances from posted_ledger_line.

    The primary balance update mechanism is event-driven (outbox_relay.py
    handles ledger.posting.completed → AccountBalanceService.update_balance_for_posting).
    This daily task catches any missed events by recalculating balances for
    all open fiscal periods across all active organizations.

    Returns:
        Dict with rebuild statistics
    """
    from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
    from app.services.finance.gl.account_balance import AccountBalanceService

    logger.info("Starting daily account balance rebuild (safety net)")

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "periods_rebuilt": 0,
        "total_balance_records": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Get all active organizations
        organizations = db.scalars(
            select(Organization).where(Organization.is_active.is_(True))
        ).all()

        for org in organizations:
            try:
                # Get open/reopened periods for this org
                open_periods = db.scalars(
                    select(FiscalPeriod).where(
                        FiscalPeriod.organization_id == org.organization_id,
                        FiscalPeriod.status.in_(
                            [PeriodStatus.OPEN, PeriodStatus.REOPENED]
                        ),
                    )
                ).all()

                if not open_periods:
                    continue

                results["organizations_processed"] += 1

                for period in open_periods:
                    try:
                        count = AccountBalanceService.rebuild_balances_for_period(
                            db,
                            organization_id=org.organization_id,
                            fiscal_period_id=period.fiscal_period_id,
                        )
                        results["periods_rebuilt"] += 1
                        results["total_balance_records"] += count

                        logger.debug(
                            "Rebuilt %d balance records for org %s period %s",
                            count,
                            org.organization_id,
                            period.fiscal_period_id,
                        )
                    except Exception as e:
                        logger.exception(
                            "Failed to rebuild balances for period %s (org %s)",
                            period.fiscal_period_id,
                            org.organization_id,
                        )
                        results["errors"].append(
                            f"Period {period.fiscal_period_id}: {str(e)}"
                        )

            except Exception as e:
                logger.exception(
                    "Failed to process balance rebuild for org %s",
                    org.organization_id,
                )
                results["errors"].append(f"Org {org.organization_id}: {str(e)}")

        db.commit()

    logger.info(
        "Account balance rebuild complete: %d orgs, %d periods, %d records, %d errors",
        results["organizations_processed"],
        results["periods_rebuilt"],
        results["total_balance_records"],
        len(results["errors"]),
    )
    return results


@shared_task
def refresh_stale_balances(batch_size: int = 200) -> dict[str, Any]:
    """
    Process queued stale balance refresh entries.

    Runs frequently and refreshes only account/period keys invalidated by
    recent postings, keeping reporting aggregates current.
    """
    from app.services.finance.gl.balance_refresh import BalanceRefreshService

    with SessionLocal() as db:
        service = BalanceRefreshService(db)
        results = service.process_queue(batch_size=batch_size)
        db.commit()

    if results["refreshed"] > 0 or results["errors"] > 0:
        logger.info(
            "Balance refresh: processed=%d refreshed=%d errors=%d",
            results["processed"],
            results["refreshed"],
            results["errors"],
        )
    return results


@shared_task
def release_expired_stock_reservations(batch_size: int = 200) -> dict[str, Any]:
    """Release inventory reservations that passed expiry timestamp."""
    from app.services.inventory.stock_reservation import StockReservationService

    with SessionLocal() as db:
        service = StockReservationService(db)
        results = service.release_expired(batch_size=batch_size)
        db.commit()

    if results["released"] > 0 or results["errors"] > 0:
        logger.info(
            "Expired stock reservations: checked=%d released=%d errors=%d",
            results["checked"],
            results["released"],
            results["errors"],
        )
    return results


@shared_task
def refresh_analysis_cubes() -> dict[str, Any]:
    """Refresh due analysis cube materialized views."""
    from app.services.finance.rpt.analysis_cube import AnalysisCubeService

    with SessionLocal() as db:
        results = AnalysisCubeService(db).refresh_due_cubes()
        db.commit()

    if results["refreshed"] > 0 or results["errors"] > 0:
        logger.info(
            "Analysis cube refresh: checked=%d refreshed=%d errors=%d",
            results["checked"],
            results["refreshed"],
            results["errors"],
        )
    return results


@shared_task
def auto_generate_aging_snapshots(
    organization_id: str, fiscal_period_id: str, user_id: str
) -> dict[str, Any]:
    """
    Auto-generate AR + AP aging snapshots when a period is soft-closed.

    Deletes existing snapshots for the period first (unique constraint),
    then creates fresh snapshots from current outstanding balances.

    Args:
        organization_id: Organization UUID as string
        fiscal_period_id: Fiscal period UUID as string
        user_id: User who triggered the close

    Returns:
        Dict with snapshot generation statistics
    """
    from uuid import UUID as UUIDType

    logger.info(
        "Generating aging snapshots for period %s (org %s)",
        fiscal_period_id,
        organization_id,
    )

    results: dict[str, Any] = {
        "ar_snapshots": 0,
        "ap_snapshots": 0,
        "errors": [],
    }

    org_id = UUIDType(organization_id)
    period_id = UUIDType(fiscal_period_id)
    uid = UUIDType(user_id)

    with SessionLocal() as db:
        from sqlalchemy import delete as sa_delete

        # AR aging snapshots — use savepoint so failure doesn't affect AP
        try:
            with db.begin_nested():
                from app.models.finance.ar.ar_aging_snapshot import ARAgingSnapshot
                from app.services.finance.ar.ar_aging import ARAgingService

                # Delete existing snapshots for this period (unique constraint)
                db.execute(
                    sa_delete(ARAgingSnapshot).where(
                        ARAgingSnapshot.organization_id == org_id,
                        ARAgingSnapshot.fiscal_period_id == period_id,
                    )
                )
                db.flush()

                ar_snaps = ARAgingService.create_aging_snapshot(
                    db,
                    organization_id=org_id,
                    fiscal_period_id=period_id,
                    created_by_user_id=uid,
                )
                results["ar_snapshots"] = len(ar_snaps)
        except Exception as e:
            logger.exception(
                "Failed to generate AR aging snapshots for period %s", period_id
            )
            results["errors"].append(f"AR: {e}")

        # AP aging snapshots — use savepoint so failure doesn't affect AR
        try:
            with db.begin_nested():
                from app.models.finance.ap.ap_aging_snapshot import (
                    APAgingSnapshot,  # pragma: allowlist secret
                )
                from app.services.finance.ap.ap_aging import (
                    APAgingService,  # pragma: allowlist secret
                )

                db.execute(
                    sa_delete(APAgingSnapshot).where(
                        APAgingSnapshot.organization_id == org_id,
                        APAgingSnapshot.fiscal_period_id == period_id,
                    )
                )
                db.flush()

                ap_snaps = APAgingService.create_aging_snapshot(
                    db,
                    organization_id=org_id,
                    fiscal_period_id=period_id,
                    created_by_user_id=uid,
                )
                results["ap_snapshots"] = len(ap_snaps)
        except Exception as e:
            logger.exception(
                "Failed to generate AP aging snapshots for period %s", period_id
            )
            results["errors"].append(f"AP: {e}")

        db.commit()  # commit whatever succeeded

    logger.info(
        "Aging snapshots complete: AR=%d, AP=%d, errors=%d",
        results["ar_snapshots"],
        results["ap_snapshots"],
        len(results["errors"]),
    )
    return results
