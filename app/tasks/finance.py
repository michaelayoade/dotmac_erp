"""
Finance Module Background Tasks - Celery tasks for finance workflows.

Handles:
- Fiscal period close reminders
- Tax filing due date reminders
- Bank reconciliation overdue alerts
- AR collection reminders for overdue invoices
- Subledger reconciliation discrepancy alerts
"""

import logging
from typing import Any, List
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.models.rbac import PersonRole, Role

logger = logging.getLogger(__name__)


def _get_finance_recipients(
    db: Session,
    role_names: List[str],
) -> List[UUID]:
    """
    Get user IDs with specified finance roles.

    Args:
        db: Database session
        role_names: List of role names to include

    Returns:
        List of person_ids with the specified roles
    """
    stmt = (
        select(PersonRole.person_id)
        .join(Role, PersonRole.role_id == Role.id)
        .where(
            Role.name.in_(role_names),
            Role.is_active.is_(True),
        )
    )
    return list(db.scalars(stmt).all())


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

                # Get accountants and finance managers
                recipients = _get_finance_recipients(
                    db,
                    ["accountant", "finance_manager", "controller", "cfo"],
                )

                if not recipients:
                    logger.warning(
                        "No finance recipients found for period %s",
                        period.fiscal_period_id,
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

        # Process overdue periods
        overdue = service.get_overdue_tax_periods()
        results["periods_overdue"] = len(overdue)

        for period in overdue:
            try:
                recipients = _get_finance_recipients(
                    db,
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

                sent = service.send_tax_period_reminder(period, recipients, "overdue")
                results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to send overdue tax reminder for period %s",
                    period.period_id,
                )
                results["errors"].append(
                    f"Overdue tax period {period.period_id}: {str(e)}"
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

        for account in accounts:
            try:
                urgency = service.get_reconciliation_urgency(account)
                if not urgency:
                    continue

                results["accounts_needing_action"] += 1

                recipients = _get_finance_recipients(
                    db,
                    ["accountant", "finance_manager", "controller"],
                )

                if not recipients:
                    continue

                sent = service.send_reconciliation_reminder(
                    account, recipients, urgency
                )
                results["notifications_sent"] += sent

            except Exception as e:
                logger.exception(
                    "Failed to send reconciliation reminder for account %s",
                    account.bank_account_id,
                )
                results["errors"].append(
                    f"Bank account {account.bank_account_id}: {str(e)}"
                )

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
                        ["accountant", "finance_manager", "controller"],
                    )

                    if recipients:
                        sent = reminder_service.send_subledger_discrepancy_alert(
                            organization_id=org.organization_id,
                            recipient_ids=recipients,
                            subledger_type="AR",
                            gl_balance=Decimal(str(recon_data.get("ar_gl_balance", 0))),
                            subledger_balance=Decimal(
                                str(recon_data.get("ar_subledger_balance", 0))
                            ),
                        )
                        results["notifications_sent"] += sent

                # Check AP discrepancy
                if not recon_data.get("ap_ok", True):
                    results["ap_discrepancies"] += 1

                    recipients = _get_finance_recipients(
                        db,
                        ["accountant", "finance_manager", "controller"],
                    )

                    if recipients:
                        sent = reminder_service.send_subledger_discrepancy_alert(
                            organization_id=org.organization_id,
                            recipient_ids=recipients,
                            subledger_type="AP",
                            gl_balance=Decimal(str(recon_data.get("ap_gl_balance", 0))),
                            subledger_balance=Decimal(
                                str(recon_data.get("ap_subledger_balance", 0))
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

    # Run each task independently - catch errors so one failure doesn't stop others
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
            logger.exception("Task %s failed", task_name)
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
