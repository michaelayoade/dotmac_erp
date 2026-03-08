"""
Finance Reminder Service.

Identifies bookkeeping items that need attention and sends reminders.
Handles:
- Fiscal period close deadlines
- Tax filing due dates
- Bank reconciliation overdue
- AR/AP aging alerts
- Subledger reconciliation discrepancies
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.tax.tax_period import TaxPeriod, TaxPeriodStatus
from app.models.notification import (
    EntityType,
    Notification,
    NotificationChannel,
    NotificationType,
)
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)


@dataclass
class ReminderConfig:
    """Configuration for reminder thresholds."""

    # Fiscal period close reminders (days before end_date)
    fiscal_period_first_notice_days: int = 7
    fiscal_period_second_notice_days: int = 3
    fiscal_period_final_notice_days: int = 1

    # Tax period filing reminders (days before due_date)
    tax_period_first_notice_days: int = 30
    tax_period_second_notice_days: int = 14
    tax_period_third_notice_days: int = 7
    tax_period_urgent_notice_days: int = 3

    # Bank reconciliation overdue threshold (days since last reconciliation)
    bank_recon_warning_days: int = 15
    bank_recon_overdue_days: int = 30
    bank_recon_critical_days: int = 45

    # Tax overdue digest: periods overdue longer than this are "stale"
    # (mentioned as count only, not listed individually in digest)
    tax_overdue_max_age_days: int = 90

    # AR aging thresholds for collection reminders
    ar_first_reminder_days: int = 7  # Days past due
    ar_second_reminder_days: int = 30
    ar_third_reminder_days: int = 60
    ar_critical_days: int = 90


class FinanceReminderService:
    """Service for identifying finance items needing reminders."""

    def __init__(self, db: Session, config: ReminderConfig | None = None):
        self.db = db
        self.config = config or ReminderConfig()
        self.notification_service = NotificationService()

    # =========================================================================
    # Fiscal Period Close Reminders
    # =========================================================================

    def get_periods_closing_soon(
        self,
        organization_id: UUID | None = None,
    ) -> list[FiscalPeriod]:
        """
        Get fiscal periods that are ending soon and still open.

        Args:
            organization_id: Filter to specific org, or None for all orgs

        Returns:
            List of fiscal periods ending within the first notice window
        """
        today = date.today()
        cutoff = today + timedelta(days=self.config.fiscal_period_first_notice_days)

        stmt = select(FiscalPeriod).where(
            FiscalPeriod.status == PeriodStatus.OPEN,
            FiscalPeriod.end_date <= cutoff,
            FiscalPeriod.end_date >= today,
        )

        if organization_id:
            stmt = stmt.where(FiscalPeriod.organization_id == organization_id)

        return list(self.db.scalars(stmt).all())

    def get_period_notice_type(self, period: FiscalPeriod) -> str | None:
        """
        Determine which notice type to send based on days until close.

        Returns:
            'final', 'second', 'first', or None if not due for a notice
        """
        today = date.today()
        days_remaining = (period.end_date - today).days

        if days_remaining < 0:
            return None  # Already ended
        elif days_remaining <= self.config.fiscal_period_final_notice_days:
            return "final"
        elif days_remaining <= self.config.fiscal_period_second_notice_days:
            return "second"
        elif days_remaining <= self.config.fiscal_period_first_notice_days:
            return "first"
        return None

    def send_fiscal_period_reminder(
        self,
        period: FiscalPeriod,
        recipient_ids: list[UUID],
        notice_type: str,
    ) -> int:
        """
        Send fiscal period close reminder to recipients.

        Args:
            period: The fiscal period closing soon
            recipient_ids: List of user IDs to notify
            notice_type: 'first', 'second', or 'final'

        Returns:
            Number of notifications sent
        """
        days_remaining = (period.end_date - date.today()).days

        urgency_map = {
            "first": ("Period Closing Soon", NotificationType.DUE_SOON),
            "second": ("Period Closing in 3 Days", NotificationType.DUE_SOON),
            "final": ("Period Closes Tomorrow", NotificationType.ALERT),
        }

        title, notification_type = urgency_map.get(
            notice_type, ("Period Closing", NotificationType.REMINDER)
        )

        message = (
            f"Fiscal period {period.period_name} ends in {days_remaining} day(s) "
            f"on {period.end_date.strftime('%Y-%m-%d')}. "
            "Please complete all journal entries and reviews."
        )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                period.organization_id,
                EntityType.FISCAL_PERIOD,
                period.fiscal_period_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=period.organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.FISCAL_PERIOD,
                entity_id=period.fiscal_period_id,
                notification_type=notification_type,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url="/finance/gl/periods",
            )
            sent += 1

        return sent

    # =========================================================================
    # Tax Period Filing Reminders
    # =========================================================================

    def get_tax_periods_due_soon(
        self,
        organization_id: UUID | None = None,
    ) -> list[TaxPeriod]:
        """
        Get tax periods with filing due dates approaching.

        Args:
            organization_id: Filter to specific org, or None for all orgs

        Returns:
            List of tax periods due within the first notice window
        """
        today = date.today()
        cutoff = today + timedelta(days=self.config.tax_period_first_notice_days)

        # Use extended_due_date if extension filed, otherwise due_date
        stmt = select(TaxPeriod).where(
            TaxPeriod.status == TaxPeriodStatus.OPEN,
            or_(
                and_(
                    TaxPeriod.is_extension_filed.is_(False),
                    TaxPeriod.due_date <= cutoff,
                    TaxPeriod.due_date >= today,
                ),
                and_(
                    TaxPeriod.is_extension_filed.is_(True),
                    TaxPeriod.extended_due_date.isnot(None),
                    TaxPeriod.extended_due_date <= cutoff,
                    TaxPeriod.extended_due_date >= today,
                ),
            ),
        )

        if organization_id:
            stmt = stmt.where(TaxPeriod.organization_id == organization_id)

        return list(self.db.scalars(stmt).all())

    def get_overdue_tax_periods(
        self,
        organization_id: UUID | None = None,
    ) -> list[TaxPeriod]:
        """Get tax periods that are past their due date and still open."""
        today = date.today()

        stmt = select(TaxPeriod).where(
            TaxPeriod.status == TaxPeriodStatus.OPEN,
            or_(
                and_(
                    TaxPeriod.is_extension_filed.is_(False),
                    TaxPeriod.due_date < today,
                ),
                and_(
                    TaxPeriod.is_extension_filed.is_(True),
                    TaxPeriod.extended_due_date.isnot(None),
                    TaxPeriod.extended_due_date < today,
                ),
            ),
        )

        if organization_id:
            stmt = stmt.where(TaxPeriod.organization_id == organization_id)

        return list(self.db.scalars(stmt).all())

    def get_tax_period_notice_type(self, period: TaxPeriod) -> str | None:
        """Determine which notice type to send for a tax period."""
        today = date.today()
        effective_due = (
            period.extended_due_date
            if period.is_extension_filed and period.extended_due_date
            else period.due_date
        )
        days_remaining = (effective_due - today).days

        if days_remaining < 0:
            return "overdue"
        elif days_remaining <= self.config.tax_period_urgent_notice_days:
            return "urgent"
        elif days_remaining <= self.config.tax_period_third_notice_days:
            return "third"
        elif days_remaining <= self.config.tax_period_second_notice_days:
            return "second"
        elif days_remaining <= self.config.tax_period_first_notice_days:
            return "first"
        return None

    def send_tax_period_reminder(
        self,
        period: TaxPeriod,
        recipient_ids: list[UUID],
        notice_type: str,
    ) -> int:
        """Send tax period filing reminder to recipients."""
        effective_due = (
            period.extended_due_date
            if period.is_extension_filed and period.extended_due_date
            else period.due_date
        )
        days_remaining = (effective_due - date.today()).days

        urgency_map = {
            "first": ("Tax Filing Due in 30 Days", NotificationType.DUE_SOON),
            "second": ("Tax Filing Due in 14 Days", NotificationType.DUE_SOON),
            "third": ("Tax Filing Due in 7 Days", NotificationType.DUE_SOON),
            "urgent": ("Tax Filing Due Soon", NotificationType.ALERT),
            "overdue": ("Tax Filing OVERDUE", NotificationType.OVERDUE),
        }

        title, notification_type = urgency_map.get(
            notice_type, ("Tax Filing Reminder", NotificationType.REMINDER)
        )

        if days_remaining < 0:
            message = (
                f"Tax period {period.period_name} filing is OVERDUE by "
                f"{abs(days_remaining)} day(s). Please file immediately."
            )
        else:
            message = (
                f"Tax period {period.period_name} filing is due in "
                f"{days_remaining} day(s) on {effective_due.strftime('%Y-%m-%d')}."
            )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                period.organization_id,
                EntityType.TAX_PERIOD,
                period.period_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=period.organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.TAX_PERIOD,
                entity_id=period.period_id,
                notification_type=notification_type,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url="/finance/tax/periods",
            )
            sent += 1

        return sent

    def send_tax_period_digest(
        self,
        periods: list[TaxPeriod],
        recipient_ids: list[UUID],
        organization_id: UUID,
    ) -> int:
        """
        Send ONE digest notification summarising all overdue tax periods.

        Periods overdue longer than ``tax_overdue_max_age_days`` are counted
        but not listed individually — they appear as a stale-count footnote.

        Args:
            periods: All overdue tax periods for the organization
            recipient_ids: Users to notify
            organization_id: Owning organization

        Returns:
            Number of notifications actually sent (after dedup)
        """
        if not periods or not recipient_ids:
            return 0

        today = date.today()
        actionable: list[tuple[TaxPeriod, int]] = []
        stale_count = 0

        for p in periods:
            effective_due = (
                p.extended_due_date
                if p.is_extension_filed and p.extended_due_date
                else p.due_date
            )
            days_overdue = (today - effective_due).days
            if days_overdue > self.config.tax_overdue_max_age_days:
                stale_count += 1
            else:
                actionable.append((p, days_overdue))

        total = len(periods)
        title = f"{total} Tax Period{'s' if total != 1 else ''} OVERDUE"

        # Build message body — list actionable items, summarise stale
        lines: list[str] = []
        actionable.sort(key=lambda x: -x[1])  # most overdue first
        for p, days in actionable[:10]:
            lines.append(f"\u2022 {p.period_name}: {days} day(s) overdue")
        if len(actionable) > 10:
            lines.append(f"  \u2026 and {len(actionable) - 10} more")
        if stale_count:
            lines.append(
                f"Plus {stale_count} period(s) overdue >{self.config.tax_overdue_max_age_days} days"
            )
        lines.append("Review all at /finance/tax/periods")
        message = "\n".join(lines)

        # Deterministic entity_id so dedup works per org/day
        digest_id = uuid5(
            NAMESPACE_DNS,
            f"{organization_id}-tax_overdue_digest-{today}",
        )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                organization_id,
                EntityType.TAX_PERIOD,
                digest_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.TAX_PERIOD,
                entity_id=digest_id,
                notification_type=NotificationType.OVERDUE,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url="/finance/tax/periods",
            )
            sent += 1

        logger.info(
            "Tax overdue digest for org %s: %d total (%d actionable, %d stale), "
            "sent to %d recipient(s)",
            organization_id,
            total,
            len(actionable),
            stale_count,
            sent,
        )
        return sent

    # =========================================================================
    # Bank Reconciliation Overdue
    # =========================================================================

    def get_accounts_needing_reconciliation(
        self,
        organization_id: UUID | None = None,
    ) -> list[BankAccount]:
        """
        Get bank accounts that haven't been reconciled recently.

        Returns accounts where:
        - No reconciliation ever done, OR
        - Last reconciliation is older than warning threshold
        """
        today = date.today()
        warning_cutoff = today - timedelta(days=self.config.bank_recon_warning_days)

        stmt = select(BankAccount).where(
            BankAccount.status == BankAccountStatus.active,
            or_(
                BankAccount.last_reconciled_date.is_(None),
                func.date(BankAccount.last_reconciled_date) < warning_cutoff,
            ),
        )

        if organization_id:
            stmt = stmt.where(BankAccount.organization_id == organization_id)

        return list(self.db.scalars(stmt).all())

    def get_reconciliation_urgency(self, account: BankAccount) -> str | None:
        """Determine reconciliation urgency level."""
        if account.last_reconciled_date is None:
            return "critical"  # Never reconciled

        today = date.today()
        last_recon = account.last_reconciled_date
        days_since = (today - last_recon).days

        if days_since >= self.config.bank_recon_critical_days:
            return "critical"
        elif days_since >= self.config.bank_recon_overdue_days:
            return "overdue"
        elif days_since >= self.config.bank_recon_warning_days:
            return "warning"
        return None

    def send_reconciliation_reminder(
        self,
        account: BankAccount,
        recipient_ids: list[UUID],
        urgency: str,
    ) -> int:
        """Send bank reconciliation reminder to recipients."""
        if account.last_reconciled_date:
            days_since = (date.today() - account.last_reconciled_date.date()).days
            last_recon_str = account.last_reconciled_date.strftime("%Y-%m-%d")
        else:
            days_since = None
            last_recon_str = "never"

        urgency_map = {
            "warning": ("Reconciliation Due", NotificationType.DUE_SOON),
            "overdue": ("Reconciliation Overdue", NotificationType.OVERDUE),
            "critical": ("Reconciliation CRITICAL", NotificationType.ALERT),
        }

        title, notification_type = urgency_map.get(
            urgency, ("Reconciliation Reminder", NotificationType.REMINDER)
        )

        if days_since is None:
            message = (
                f"Bank account {account.account_name} ({account.masked_account_number}) "
                "has never been reconciled. Please perform initial reconciliation."
            )
        else:
            message = (
                f"Bank account {account.account_name} ({account.masked_account_number}) "
                f"was last reconciled {days_since} days ago ({last_recon_str}). "
                "Please reconcile to ensure accuracy."
            )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                account.organization_id,
                EntityType.BANK_RECONCILIATION,
                account.bank_account_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=account.organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.BANK_RECONCILIATION,
                entity_id=account.bank_account_id,
                notification_type=notification_type,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url=f"/finance/banking/accounts/{account.bank_account_id}/reconcile",
            )
            sent += 1

        return sent

    def send_reconciliation_digest(
        self,
        accounts: list[tuple[BankAccount, str]],
        recipient_ids: list[UUID],
        organization_id: UUID,
    ) -> int:
        """
        Send ONE digest notification summarising bank accounts needing reconciliation.

        Args:
            accounts: List of (BankAccount, urgency) tuples
            recipient_ids: Users to notify
            organization_id: Owning organization

        Returns:
            Number of notifications actually sent (after dedup)
        """
        if not accounts or not recipient_ids:
            return 0

        today = date.today()
        total = len(accounts)
        title = f"{total} Bank Account{'s' if total != 1 else ''} Need Reconciliation"

        # Group by urgency for the message
        by_urgency: dict[str, list[BankAccount]] = {}
        for acct, urgency in accounts:
            by_urgency.setdefault(urgency, []).append(acct)

        lines: list[str] = []
        urgency_labels = {
            "critical": "Critical (never reconciled or >45 days)",
            "overdue": "Overdue (>30 days)",
            "warning": "Warning (>15 days)",
        }
        for urg in ("critical", "overdue", "warning"):
            group = by_urgency.get(urg, [])
            if group:
                lines.append(
                    f"{urgency_labels[urg]}: "
                    f"{', '.join(a.account_name for a in group[:5])}"
                    + (f" +{len(group) - 5} more" if len(group) > 5 else "")
                )
        lines.append("Review all at /finance/banking/accounts")
        message = "\n".join(lines)

        digest_id = uuid5(
            NAMESPACE_DNS,
            f"{organization_id}-bank_recon_digest-{today}",
        )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                organization_id,
                EntityType.BANK_RECONCILIATION,
                digest_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.BANK_RECONCILIATION,
                entity_id=digest_id,
                notification_type=NotificationType.ALERT,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url="/finance/banking/accounts",
            )
            sent += 1

        logger.info(
            "Bank reconciliation digest for org %s: %d accounts, sent to %d recipient(s)",
            organization_id,
            total,
            sent,
        )
        return sent

    # =========================================================================
    # AR Overdue Collection Reminders
    # =========================================================================

    def get_overdue_invoices(
        self,
        organization_id: UUID | None = None,
        min_days_overdue: int = 1,
    ) -> list[Invoice]:
        """
        Get AR invoices that are past due.

        Args:
            organization_id: Filter to specific org
            min_days_overdue: Minimum days past due date

        Returns:
            List of overdue invoices
        """
        today = date.today()
        cutoff = today - timedelta(days=min_days_overdue)

        # Only include invoices that have outstanding balance
        stmt = select(Invoice).where(
            Invoice.status.in_(
                [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
            Invoice.due_date <= cutoff,
            Invoice.total_amount > Invoice.amount_paid,
        )

        if organization_id:
            stmt = stmt.where(Invoice.organization_id == organization_id)

        stmt = stmt.order_by(Invoice.due_date.asc())

        return list(self.db.scalars(stmt).all())

    def get_invoice_aging_bucket(self, invoice: Invoice) -> str:
        """
        Determine aging bucket for an invoice.

        Returns:
            'current', '1-30', '31-60', '61-90', 'over-90'
        """
        today = date.today()
        days_overdue = (today - invoice.due_date).days

        if days_overdue <= 0:
            return "current"
        elif days_overdue <= 30:
            return "1-30"
        elif days_overdue <= 60:
            return "31-60"
        elif days_overdue <= 90:
            return "61-90"
        else:
            return "over-90"

    def send_collection_reminder(
        self,
        invoice: Invoice,
        recipient_ids: list[UUID],
    ) -> int:
        """Send AR collection reminder to staff for overdue invoice."""
        days_overdue = (date.today() - invoice.due_date).days
        balance_due = invoice.total_amount - invoice.amount_paid

        if days_overdue >= self.config.ar_critical_days:
            title = "Critical: Invoice 90+ Days Overdue"
            notification_type = NotificationType.ALERT
        elif days_overdue >= self.config.ar_third_reminder_days:
            title = "Invoice 60+ Days Overdue"
            notification_type = NotificationType.OVERDUE
        elif days_overdue >= self.config.ar_second_reminder_days:
            title = "Invoice 30+ Days Overdue"
            notification_type = NotificationType.OVERDUE
        else:
            title = "Invoice Past Due"
            notification_type = NotificationType.DUE_SOON

        message = (
            f"Invoice {invoice.invoice_number} is {days_overdue} days overdue. "
            f"Balance due: {invoice.currency_code} {balance_due:,.2f}. "
            "Please follow up with customer."
        )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                invoice.organization_id,
                EntityType.INVOICE,
                invoice.invoice_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=invoice.organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.INVOICE,
                entity_id=invoice.invoice_id,
                notification_type=notification_type,
                title=title,
                message=message,
                channel=NotificationChannel.IN_APP,  # Don't email for every invoice
                action_url=f"/finance/ar/invoices/{invoice.invoice_id}",
            )
            sent += 1

        return sent

    # =========================================================================
    # Subledger Reconciliation
    # =========================================================================

    def send_subledger_discrepancy_alert(
        self,
        organization_id: UUID,
        recipient_ids: list[UUID],
        subledger_type: str,
        gl_balance: Decimal,
        subledger_balance: Decimal,
    ) -> int:
        """Send alert when GL doesn't match subledger."""
        difference = gl_balance - subledger_balance

        title = f"{subledger_type} Subledger Discrepancy"
        message = (
            f"The {subledger_type} control account in GL doesn't match the "
            f"subledger total. GL Balance: {gl_balance:,.2f}, "
            f"Subledger Total: {subledger_balance:,.2f}, "
            f"Difference: {difference:,.2f}. Please investigate."
        )

        # Create a pseudo-entity ID for the discrepancy (deterministic per org/type/day)
        pseudo_id = uuid5(
            NAMESPACE_DNS,
            f"{organization_id}-{subledger_type}-{date.today()}",
        )

        sent = 0
        for recipient_id in recipient_ids:
            if self._notification_sent_today(
                organization_id,
                EntityType.SUBLEDGER,
                pseudo_id,
                recipient_id,
            ):
                continue

            self.notification_service.create(
                self.db,
                organization_id=organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.SUBLEDGER,
                entity_id=pseudo_id,
                notification_type=NotificationType.ALERT,
                title=title,
                message=message,
                channel=NotificationChannel.BOTH,
                action_url="/finance/dashboard",
            )
            sent += 1

        return sent

    # =========================================================================
    # Helpers
    # =========================================================================

    def _notification_sent_today(
        self,
        organization_id: UUID,
        entity_type: EntityType,
        entity_id: UUID,
        recipient_id: UUID,
    ) -> bool:
        """Check if a notification was already sent today for this entity."""
        today_start = datetime.combine(date.today(), datetime.min.time())

        existing = self.db.scalar(
            select(func.count(Notification.notification_id)).where(
                Notification.organization_id == organization_id,
                Notification.entity_type == entity_type,
                Notification.entity_id == entity_id,
                Notification.recipient_id == recipient_id,
                Notification.created_at >= today_start,
            )
        )

        return bool(existing and existing > 0)
