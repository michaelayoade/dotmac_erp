"""
Expense Module Background Tasks - Celery tasks for expense workflows.

Handles:
- Period usage cache refresh
- Pending approval reminders
- Batch expense posting
- Expense analytics calculations
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
import uuid

from celery import shared_task
from sqlalchemy import and_, func, or_, select

from app.db import SessionLocal
from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpensePeriodUsage,
    LimitPeriodType,
)
from app.models.finance.core_org.organization import Organization
from app.models.people.hr.employee import Employee

logger = logging.getLogger(__name__)


@shared_task
def refresh_period_usage_cache(organization_id: Optional[str] = None) -> dict:
    """
    Refresh period usage cache for expense limits.

    Recalculates period totals for all active employees to ensure
    limit evaluations are accurate without expensive real-time queries.

    Args:
        organization_id: Optional org ID. If None, refreshes all organizations.

    Returns:
        Dict with refresh statistics
    """
    from app.services.expense.limit_service import ExpenseLimitService

    logger.info("Starting period usage cache refresh")

    results = {
        "organizations_processed": 0,
        "employees_refreshed": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Get organizations to process
        org_query = select(Organization).where(Organization.is_active == True)
        if organization_id:
            org_query = org_query.where(Organization.organization_id == uuid.UUID(organization_id))

        organizations = db.scalars(org_query).all()

        for org in organizations:
            try:
                # Get all active employees
                employees = db.scalars(
                    select(Employee).where(
                        Employee.organization_id == org.organization_id,
                        Employee.is_active == True,
                    )
                ).all()

                limit_service = ExpenseLimitService(db)

                for employee in employees:
                    try:
                        # Refresh for each period type
                        for period_type in [
                            LimitPeriodType.DAY,
                            LimitPeriodType.WEEK,
                            LimitPeriodType.MONTH,
                            LimitPeriodType.QUARTER,
                            LimitPeriodType.YEAR,
                        ]:
                            limit_service.refresh_usage_cache(
                                org.organization_id,
                                employee.employee_id,
                                period_type,
                            )

                        results["employees_refreshed"] += 1

                    except Exception as e:
                        logger.error(
                            "Failed to refresh usage for employee %s: %s",
                            employee.employee_id,
                            e,
                        )
                        results["errors"].append({
                            "employee_id": str(employee.employee_id),
                            "error": str(e),
                        })

                db.commit()
                results["organizations_processed"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process organization %s: %s",
                    org.organization_id,
                    e,
                )
                db.rollback()
                results["errors"].append({
                    "organization_id": str(org.organization_id),
                    "error": str(e),
                })

    logger.info(
        "Period usage cache refresh complete: %d orgs, %d employees",
        results["organizations_processed"],
        results["employees_refreshed"],
    )

    return results


@shared_task
def process_expense_approval_reminders() -> dict:
    """
    Send reminders for pending expense approvals.

    Checks for claims that have been pending for configured thresholds
    and sends reminder emails to approvers.

    Default reminder thresholds:
    - First reminder: 3 days
    - Second reminder: 7 days
    - Escalation warning: 14 days

    Returns:
        Dict with reminder statistics
    """
    from app.services.expense.expense_notifications import ExpenseNotificationService

    FIRST_REMINDER_DAYS = 3
    SECOND_REMINDER_DAYS = 7
    ESCALATION_WARNING_DAYS = 14

    logger.info("Processing expense approval reminders")

    results = {
        "first_reminders_sent": 0,
        "second_reminders_sent": 0,
        "escalation_warnings_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()

        # Find all pending claims
        pending_claims = db.scalars(
            select(ExpenseClaim)
            .where(
                ExpenseClaim.status.in_([
                    ExpenseClaimStatus.SUBMITTED,
                    ExpenseClaimStatus.PENDING_APPROVAL,
                ]),
            )
            .order_by(ExpenseClaim.claim_date)
        ).all()

        notification_service = ExpenseNotificationService(db)

        for claim in pending_claims:
            try:
                days_pending = (today - claim.claim_date).days

                # Determine reminder type
                if days_pending >= ESCALATION_WARNING_DAYS:
                    reminder_type = "escalation"
                elif days_pending >= SECOND_REMINDER_DAYS:
                    reminder_type = "second"
                elif days_pending >= FIRST_REMINDER_DAYS:
                    reminder_type = "first"
                else:
                    continue  # Too early for reminder

                # Get approver
                approver = None
                if claim.approver_id:
                    approver = db.get(Employee, claim.approver_id)

                # Fall back to employee's manager
                if not approver and claim.employee and claim.employee.reports_to_id:
                    approver = db.get(Employee, claim.employee.reports_to_id)

                if not approver:
                    continue  # No approver to remind

                # Send reminder
                success = notification_service.send_pending_approval_reminder(
                    claim,
                    approver,
                    days_pending=days_pending,
                )

                if success:
                    if reminder_type == "first":
                        results["first_reminders_sent"] += 1
                    elif reminder_type == "second":
                        results["second_reminders_sent"] += 1
                    else:
                        results["escalation_warnings_sent"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process reminder for claim %s: %s",
                    claim.claim_id,
                    e,
                )
                results["errors"].append({
                    "claim_id": str(claim.claim_id),
                    "error": str(e),
                })

        db.commit()

    total_sent = (
        results["first_reminders_sent"] +
        results["second_reminders_sent"] +
        results["escalation_warnings_sent"]
    )
    logger.info("Approval reminders complete: %d sent", total_sent)

    return results


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def post_approved_expense(
    self,
    organization_id: str,
    claim_id: str,
    user_id: str,
    *,
    create_supplier_invoice: bool = False,
    auto_post_gl: bool = True,
) -> dict:
    """
    Post an approved expense claim to the general ledger.

    This task is triggered after claim approval to create GL entries
    and optionally a supplier invoice for AP processing.

    Args:
        organization_id: UUID of the organization
        claim_id: UUID of the expense claim
        user_id: UUID of the user posting
        create_supplier_invoice: Whether to create AP invoice
        auto_post_gl: Whether to auto-post to ledger

    Returns:
        Dict with posting result
    """
    from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

    logger.info("Posting approved expense claim %s", claim_id)

    with SessionLocal() as db:
        try:
            org_id = uuid.UUID(organization_id)
            c_id = uuid.UUID(claim_id)
            u_id = uuid.UUID(user_id)

            # Post to GL
            result = ExpensePostingAdapter.post_expense_claim(
                db,
                org_id,
                c_id,
                date.today(),
                u_id,
                auto_post=auto_post_gl,
            )

            if not result.success:
                logger.error("GL posting failed: %s", result.message)
                return {
                    "success": False,
                    "error": result.message,
                }

            # Optionally create supplier invoice
            invoice_id = None
            if create_supplier_invoice:
                invoice_result = ExpensePostingAdapter.create_supplier_invoice_from_expense(
                    db,
                    org_id,
                    c_id,
                    u_id,
                )
                if invoice_result.success:
                    invoice_id = str(invoice_result.supplier_invoice_id)
                else:
                    logger.warning(
                        "Supplier invoice creation failed: %s",
                        invoice_result.message,
                    )

            db.commit()

            return {
                "success": True,
                "journal_entry_id": str(result.journal_entry_id) if result.journal_entry_id else None,
                "posting_batch_id": str(result.posting_batch_id) if result.posting_batch_id else None,
                "supplier_invoice_id": invoice_id,
            }

        except Exception as e:
            logger.exception("Expense posting failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def post_cash_advance_disbursement(
    self,
    organization_id: str,
    advance_id: str,
    user_id: str,
    bank_account_id: str,
) -> dict:
    """
    Post a cash advance disbursement to the general ledger.

    Creates GL entries for the cash advance:
    - Debit: Employee advance account
    - Credit: Bank/Cash account

    Args:
        organization_id: UUID of the organization
        advance_id: UUID of the cash advance
        user_id: UUID of the user posting
        bank_account_id: UUID of the bank account for credit

    Returns:
        Dict with posting result
    """
    from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

    logger.info("Posting cash advance disbursement %s", advance_id)

    with SessionLocal() as db:
        try:
            result = ExpensePostingAdapter.post_cash_advance(
                db,
                uuid.UUID(organization_id),
                uuid.UUID(advance_id),
                date.today(),
                uuid.UUID(user_id),
                bank_account_id=uuid.UUID(bank_account_id),
            )

            if not result.success:
                logger.error("Advance posting failed: %s", result.message)
                return {
                    "success": False,
                    "error": result.message,
                }

            db.commit()

            return {
                "success": True,
                "journal_entry_id": str(result.journal_entry_id) if result.journal_entry_id else None,
                "posting_batch_id": str(result.posting_batch_id) if result.posting_batch_id else None,
            }

        except Exception as e:
            logger.exception("Advance posting failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def settle_cash_advance_with_claim(
    self,
    organization_id: str,
    advance_id: str,
    claim_id: str,
    user_id: str,
    settlement_amount: Optional[str] = None,
) -> dict:
    """
    Settle a cash advance against an expense claim.

    Posts GL entries to offset the advance against expenses.

    Args:
        organization_id: UUID of the organization
        advance_id: UUID of the cash advance
        claim_id: UUID of the expense claim
        user_id: UUID of the user posting
        settlement_amount: Optional specific amount to settle (default: auto-calculate)

    Returns:
        Dict with settlement result
    """
    from decimal import Decimal
    from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

    logger.info(
        "Settling cash advance %s with claim %s",
        advance_id,
        claim_id,
    )

    with SessionLocal() as db:
        try:
            settle_amt = Decimal(settlement_amount) if settlement_amount else None

            result = ExpensePostingAdapter.settle_cash_advance(
                db,
                uuid.UUID(organization_id),
                uuid.UUID(advance_id),
                uuid.UUID(claim_id),
                date.today(),
                uuid.UUID(user_id),
                settlement_amount=settle_amt,
            )

            if not result.success:
                logger.error("Settlement posting failed: %s", result.message)
                return {
                    "success": False,
                    "error": result.message,
                }

            db.commit()

            return {
                "success": True,
                "journal_entry_id": str(result.journal_entry_id) if result.journal_entry_id else None,
                "message": result.message,
            }

        except Exception as e:
            logger.exception("Settlement posting failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task
def calculate_expense_analytics(
    organization_id: str,
    period: str = "month",
) -> dict:
    """
    Calculate expense analytics for reporting dashboards.

    Generates aggregate statistics for:
    - Total expenses by category
    - Top spenders by department
    - Approval time metrics
    - Limit utilization rates

    Args:
        organization_id: UUID of the organization
        period: "day", "week", "month", "quarter", "year"

    Returns:
        Dict with calculated analytics
    """
    from app.services.expense.expense_service import ExpenseService

    logger.info(
        "Calculating expense analytics for org %s, period %s",
        organization_id,
        period,
    )

    with SessionLocal() as db:
        try:
            org_id = uuid.UUID(organization_id)
            service = ExpenseService(db)

            # Get date range based on period
            today = date.today()
            if period == "day":
                start_date = today
            elif period == "week":
                start_date = today - timedelta(days=today.weekday())
            elif period == "month":
                start_date = today.replace(day=1)
            elif period == "quarter":
                quarter = (today.month - 1) // 3
                start_date = date(today.year, quarter * 3 + 1, 1)
            else:  # year
                start_date = date(today.year, 1, 1)

            # Get analytics
            summary = service.get_expense_summary_report(
                org_id,
                start_date=start_date,
                end_date=today,
            )

            category_breakdown = service.get_expense_by_category_report(
                org_id,
                start_date=start_date,
                end_date=today,
            )

            # Calculate average approval time
            approved_claims = db.scalars(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.status.in_([
                        ExpenseClaimStatus.APPROVED,
                        ExpenseClaimStatus.PAID,
                    ]),
                    ExpenseClaim.claim_date >= start_date,
                    ExpenseClaim.approved_on.isnot(None),
                )
            ).all()

            total_days = 0
            count = 0
            for claim in approved_claims:
                if claim.approved_on:
                    total_days += (claim.approved_on - claim.claim_date).days
                    count += 1

            avg_approval_days = total_days / count if count > 0 else 0

            return {
                "success": True,
                "period": period,
                "start_date": str(start_date),
                "end_date": str(today),
                "summary": {
                    "total_claims": summary["total_claims"],
                    "total_claimed": float(summary["total_claimed"]),
                    "approved_count": summary["approved_count"],
                    "approved_amount": float(summary["approved_amount"]),
                    "rejected_count": summary["rejected_count"],
                },
                "category_breakdown": [
                    {
                        "category": cat["category_name"],
                        "amount": float(cat["claimed_amount"]),
                        "percentage": cat["percentage"],
                    }
                    for cat in category_breakdown["categories"][:10]
                ],
                "metrics": {
                    "avg_approval_days": round(avg_approval_days, 1),
                    "claims_approved": count,
                },
            }

        except Exception as e:
            logger.exception("Analytics calculation failed: %s", e)
            return {
                "success": False,
                "error": str(e),
            }


@shared_task
def poll_stuck_expense_transfers() -> dict:
    """
    Poll Paystack for status of stuck expense reimbursement transfers.

    Checks transfers in PROCESSING state for more than 1 hour and
    updates their status via direct API query.

    Returns:
        Dict with polling results
    """
    from datetime import timedelta

    from app.models.finance.payments.payment_intent import (
        PaymentDirection,
        PaymentIntent,
        PaymentIntentStatus,
    )
    from app.services.domain_settings import SettingDomain, resolve_value
    from app.services.finance.payments.payment_service import PaymentService
    from app.services.finance.payments.paystack_client import PaystackConfig

    logger.info("Polling stuck expense transfers")

    results = {
        "intents_checked": 0,
        "completed": 0,
        "failed": 0,
        "still_pending": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Find transfers stuck in PROCESSING for more than 1 hour
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        stuck_intents = db.scalars(
            select(PaymentIntent).where(
                PaymentIntent.direction == PaymentDirection.OUTBOUND,
                PaymentIntent.status == PaymentIntentStatus.PROCESSING,
                PaymentIntent.source_type == "EXPENSE_CLAIM",
                PaymentIntent.transfer_code.isnot(None),
                PaymentIntent.created_at < cutoff,
            )
        ).all()

        if not stuck_intents:
            logger.info("No stuck transfers found")
            return results

        # Group by organization to use correct config
        by_org: dict[uuid.UUID, list[PaymentIntent]] = {}
        for intent in stuck_intents:
            by_org.setdefault(intent.organization_id, []).append(intent)

        for org_id, intents in by_org.items():
            # Get Paystack config for this org
            secret_key = resolve_value(db, SettingDomain.payments, "paystack_secret_key")
            if not secret_key:
                logger.warning(f"No Paystack key for org {org_id}")
                continue

            config = PaystackConfig(secret_key=secret_key)
            svc = PaymentService(db, org_id)

            for intent in intents:
                try:
                    results["intents_checked"] += 1
                    old_status = intent.status

                    svc.poll_transfer_status(intent, config)

                    if intent.status == PaymentIntentStatus.COMPLETED:
                        results["completed"] += 1
                    elif intent.status == PaymentIntentStatus.FAILED:
                        results["failed"] += 1
                    else:
                        results["still_pending"] += 1

                except Exception as e:
                    logger.error(
                        "Failed to poll transfer %s: %s",
                        intent.intent_id,
                        e,
                    )
                    results["errors"].append({
                        "intent_id": str(intent.intent_id),
                        "error": str(e),
                    })

            db.commit()

    logger.info(
        "Transfer polling complete: %d checked, %d completed, %d failed",
        results["intents_checked"],
        results["completed"],
        results["failed"],
    )

    return results
