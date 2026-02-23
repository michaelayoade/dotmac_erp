"""
Tests for digest notification methods in FinanceReminderService.

Verifies that overdue tax periods and bank reconciliation alerts are
aggregated into a single digest notification per org per recipient per day,
instead of one notification per entity.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock
from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest

from app.models.notification import (
    NotificationChannel,
    NotificationType,
)
from app.services.finance.reminder_service import (
    FinanceReminderService,
    ReminderConfig,
)

# ============ Fixtures ============

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
RECIPIENT_A = UUID("00000000-0000-0000-0000-000000000010")
RECIPIENT_B = UUID("00000000-0000-0000-0000-000000000011")


def _make_tax_period(
    *,
    period_name: str = "Q1 2026",
    due_date: date | None = None,
    days_overdue: int = 10,
    extended: bool = False,
    extended_due_date: date | None = None,
    organization_id: UUID = ORG_ID,
) -> MagicMock:
    """Create a mock TaxPeriod with sensible defaults."""
    p = MagicMock()
    p.period_id = uuid.uuid4()
    p.period_name = period_name
    p.organization_id = organization_id
    p.due_date = due_date or (date.today() - timedelta(days=days_overdue))
    p.is_extension_filed = extended
    p.extended_due_date = extended_due_date
    return p


def _make_bank_account(
    *,
    account_name: str = "Main Account",
    last_reconciled_date: date | None = None,
    organization_id: UUID = ORG_ID,
) -> MagicMock:
    """Create a mock BankAccount."""
    a = MagicMock()
    a.bank_account_id = uuid.uuid4()
    a.account_name = account_name
    a.masked_account_number = "****1234"
    a.organization_id = organization_id
    if last_reconciled_date:
        a.last_reconciled_date = MagicMock()
        a.last_reconciled_date.date.return_value = last_reconciled_date
    else:
        a.last_reconciled_date = None
    return a


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    # _notification_sent_today uses db.scalar → return 0 (no prior notification)
    db.scalar.return_value = 0
    return db


@pytest.fixture
def service(mock_db: MagicMock) -> FinanceReminderService:
    svc = FinanceReminderService(mock_db)
    # Replace NotificationService with mock so we can assert calls
    svc.notification_service = MagicMock()
    return svc


# ============ Tax Period Digest Tests ============


class TestSendTaxPeriodDigest:
    """Tests for send_tax_period_digest()."""

    def test_single_period_produces_one_notification(
        self, service: FinanceReminderService
    ) -> None:
        """One overdue period → one digest notification, not one per-entity."""
        period = _make_tax_period(days_overdue=5, period_name="Jan 2026")
        sent = service.send_tax_period_digest([period], [RECIPIENT_A], ORG_ID)

        assert sent == 1
        service.notification_service.create.assert_called_once()
        kw = service.notification_service.create.call_args
        assert "1 Tax Period OVERDUE" in kw.kwargs["title"]
        assert "Jan 2026" in kw.kwargs["message"]
        assert kw.kwargs["notification_type"] == NotificationType.OVERDUE
        assert kw.kwargs["channel"] == NotificationChannel.BOTH

    def test_many_periods_still_one_notification_per_recipient(
        self, service: FinanceReminderService
    ) -> None:
        """97 overdue periods → 1 notification per recipient, not 97."""
        periods = [
            _make_tax_period(
                days_overdue=i + 1,
                period_name=f"Period-{i}",
            )
            for i in range(97)
        ]

        sent = service.send_tax_period_digest(
            periods, [RECIPIENT_A, RECIPIENT_B], ORG_ID
        )

        assert sent == 2  # one per recipient
        assert service.notification_service.create.call_count == 2
        # Title mentions total count
        title = service.notification_service.create.call_args_list[0].kwargs["title"]
        assert title == "97 Tax Periods OVERDUE"

    def test_age_cap_splits_actionable_and_stale(
        self, service: FinanceReminderService
    ) -> None:
        """Periods >90 days overdue are stale; mentioned as count only."""
        actionable = _make_tax_period(days_overdue=10, period_name="Recent")
        stale = _make_tax_period(days_overdue=200, period_name="Ancient")

        sent = service.send_tax_period_digest(
            [actionable, stale], [RECIPIENT_A], ORG_ID
        )

        assert sent == 1
        msg = service.notification_service.create.call_args.kwargs["message"]
        # Actionable listed by name
        assert "Recent" in msg
        # Stale mentioned as count
        assert "1 period(s) overdue >90 days" in msg
        # Stale period name NOT listed individually
        assert "Ancient" not in msg

    def test_custom_max_age_config(self, mock_db: MagicMock) -> None:
        """Custom tax_overdue_max_age_days is respected."""
        config = ReminderConfig(tax_overdue_max_age_days=30)
        svc = FinanceReminderService(mock_db, config=config)
        svc.notification_service = MagicMock()

        p_35_days = _make_tax_period(days_overdue=35, period_name="35d")
        p_25_days = _make_tax_period(days_overdue=25, period_name="25d")

        svc.send_tax_period_digest([p_35_days, p_25_days], [RECIPIENT_A], ORG_ID)

        msg = svc.notification_service.create.call_args.kwargs["message"]
        assert "25d" in msg  # actionable
        assert "1 period(s) overdue >30 days" in msg  # stale

    def test_deterministic_entity_id_for_dedup(
        self, service: FinanceReminderService
    ) -> None:
        """Digest entity_id is deterministic per org+date for dedup."""
        period = _make_tax_period(days_overdue=5)
        service.send_tax_period_digest([period], [RECIPIENT_A], ORG_ID)

        expected_id = uuid5(
            NAMESPACE_DNS,
            f"{ORG_ID}-tax_overdue_digest-{date.today()}",
        )
        actual_id = service.notification_service.create.call_args.kwargs["entity_id"]
        assert actual_id == expected_id

    def test_dedup_skips_already_sent(
        self, service: FinanceReminderService, mock_db: MagicMock
    ) -> None:
        """If digest was already sent today, skip."""
        # db.scalar returns >0 meaning notification already exists
        mock_db.scalar.return_value = 1

        period = _make_tax_period(days_overdue=5)
        sent = service.send_tax_period_digest([period], [RECIPIENT_A], ORG_ID)

        assert sent == 0
        service.notification_service.create.assert_not_called()

    def test_empty_periods_returns_zero(self, service: FinanceReminderService) -> None:
        """No periods → no notification."""
        assert service.send_tax_period_digest([], [RECIPIENT_A], ORG_ID) == 0
        service.notification_service.create.assert_not_called()

    def test_empty_recipients_returns_zero(
        self, service: FinanceReminderService
    ) -> None:
        """No recipients → no notification."""
        period = _make_tax_period(days_overdue=5)
        assert service.send_tax_period_digest([period], [], ORG_ID) == 0
        service.notification_service.create.assert_not_called()

    def test_extension_filed_uses_extended_due_date(
        self, service: FinanceReminderService
    ) -> None:
        """When extension is filed, use extended_due_date for age calc."""
        p = _make_tax_period(
            days_overdue=5,
            period_name="Extended",
            extended=True,
            extended_due_date=date.today() - timedelta(days=5),
        )
        # Override due_date to something much older
        p.due_date = date.today() - timedelta(days=200)

        service.send_tax_period_digest([p], [RECIPIENT_A], ORG_ID)

        msg = service.notification_service.create.call_args.kwargs["message"]
        # Should show 5 days overdue (from extended date), not 200
        assert "5 day(s) overdue" in msg

    def test_more_than_10_actionable_shows_overflow(
        self, service: FinanceReminderService
    ) -> None:
        """When >10 actionable periods, show first 10 + overflow count."""
        periods = [
            _make_tax_period(days_overdue=i + 1, period_name=f"P-{i}")
            for i in range(15)
        ]

        service.send_tax_period_digest(periods, [RECIPIENT_A], ORG_ID)

        msg = service.notification_service.create.call_args.kwargs["message"]
        assert "and 5 more" in msg


# ============ Bank Reconciliation Digest Tests ============


class TestSendReconciliationDigest:
    """Tests for send_reconciliation_digest()."""

    def test_single_account_produces_one_notification(
        self, service: FinanceReminderService
    ) -> None:
        """One critical account → one digest notification."""
        acct = _make_bank_account(account_name="Ops Account")
        sent = service.send_reconciliation_digest(
            [(acct, "critical")], [RECIPIENT_A], ORG_ID
        )

        assert sent == 1
        kw = service.notification_service.create.call_args.kwargs
        assert "1 Bank Account" in kw["title"]
        assert "Need Reconciliation" in kw["title"]
        assert "Ops Account" in kw["message"]
        assert kw["notification_type"] == NotificationType.ALERT

    def test_many_accounts_one_notification_per_recipient(
        self, service: FinanceReminderService
    ) -> None:
        """10 accounts → 1 notification per recipient."""
        accounts_with_urgency = [
            (_make_bank_account(account_name=f"Acct-{i}"), "critical")
            for i in range(10)
        ]

        sent = service.send_reconciliation_digest(
            accounts_with_urgency,
            [RECIPIENT_A, RECIPIENT_B],
            ORG_ID,
        )

        assert sent == 2
        title = service.notification_service.create.call_args_list[0].kwargs["title"]
        assert title == "10 Bank Accounts Need Reconciliation"

    def test_groups_by_urgency_in_message(
        self, service: FinanceReminderService
    ) -> None:
        """Message groups accounts by urgency level."""
        critical = _make_bank_account(account_name="Critical-A")
        overdue = _make_bank_account(account_name="Overdue-B")
        warning = _make_bank_account(account_name="Warning-C")

        service.send_reconciliation_digest(
            [(critical, "critical"), (overdue, "overdue"), (warning, "warning")],
            [RECIPIENT_A],
            ORG_ID,
        )

        msg = service.notification_service.create.call_args.kwargs["message"]
        assert "Critical-A" in msg
        assert "Overdue-B" in msg
        assert "Warning-C" in msg

    def test_deterministic_entity_id(self, service: FinanceReminderService) -> None:
        """Digest entity_id is deterministic per org+date."""
        acct = _make_bank_account()
        service.send_reconciliation_digest([(acct, "critical")], [RECIPIENT_A], ORG_ID)

        expected_id = uuid5(
            NAMESPACE_DNS,
            f"{ORG_ID}-bank_recon_digest-{date.today()}",
        )
        actual_id = service.notification_service.create.call_args.kwargs["entity_id"]
        assert actual_id == expected_id

    def test_dedup_skips_already_sent(
        self, service: FinanceReminderService, mock_db: MagicMock
    ) -> None:
        """If digest was already sent today, skip."""
        mock_db.scalar.return_value = 1

        acct = _make_bank_account()
        sent = service.send_reconciliation_digest(
            [(acct, "critical")], [RECIPIENT_A], ORG_ID
        )

        assert sent == 0

    def test_empty_accounts_returns_zero(self, service: FinanceReminderService) -> None:
        assert service.send_reconciliation_digest([], [RECIPIENT_A], ORG_ID) == 0

    def test_empty_recipients_returns_zero(
        self, service: FinanceReminderService
    ) -> None:
        acct = _make_bank_account()
        assert service.send_reconciliation_digest([(acct, "critical")], [], ORG_ID) == 0

    def test_more_than_5_accounts_in_group_shows_overflow(
        self, service: FinanceReminderService
    ) -> None:
        """When >5 accounts in a single urgency group, show overflow."""
        accounts = [
            (_make_bank_account(account_name=f"A-{i}"), "critical") for i in range(8)
        ]

        service.send_reconciliation_digest(accounts, [RECIPIENT_A], ORG_ID)

        msg = service.notification_service.create.call_args.kwargs["message"]
        assert "+3 more" in msg


# ============ ReminderConfig Tests ============


class TestReminderConfig:
    """Tests for ReminderConfig defaults."""

    def test_default_tax_overdue_max_age(self) -> None:
        config = ReminderConfig()
        assert config.tax_overdue_max_age_days == 90

    def test_custom_tax_overdue_max_age(self) -> None:
        config = ReminderConfig(tax_overdue_max_age_days=60)
        assert config.tax_overdue_max_age_days == 60
