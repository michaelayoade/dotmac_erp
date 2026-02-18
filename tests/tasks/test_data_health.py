"""Tests for data health Celery tasks."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

# ── Notification cleanup ─────────────────────────────────────


class TestCleanupOldNotifications:
    """Tests for cleanup_old_notifications task."""

    def test_deletes_old_read_notifications(self) -> None:
        """Old read notifications are deleted."""
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 5

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            from app.tasks.data_health import cleanup_old_notifications

            result = cleanup_old_notifications(read_days=30, unread_days=90)

        assert result["read_deleted"] == 5
        assert result["errors"] == []

    def test_handles_exception_gracefully(self) -> None:
        """Exceptions during cleanup are caught and reported."""
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.delete.side_effect = RuntimeError("DB error")

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            from app.tasks.data_health import cleanup_old_notifications

            result = cleanup_old_notifications()

        assert len(result["errors"]) == 1
        assert "DB error" in result["errors"][0]
        mock_db.rollback.assert_called_once()


# ── Stuck outbox recovery ────────────────────────────────────


class TestProcessStuckOutboxEvents:
    """Tests for process_stuck_outbox_events task."""

    def test_recovers_stuck_events(self) -> None:
        """Events stuck in PENDING for too long get retry_count incremented."""
        from app.tasks.data_health import process_stuck_outbox_events

        mock_event = MagicMock()
        mock_event.retry_count = 0
        mock_event.event_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_event]

        with (
            patch("app.tasks.data_health.SessionLocal") as mock_session,
            patch(
                "app.tasks.data_health.process_stuck_outbox_events.__wrapped__",
                process_stuck_outbox_events.__wrapped__,
            ),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = process_stuck_outbox_events(stuck_minutes=30, batch_size=100)

        assert result["recovered"] == 1
        assert result["marked_dead"] == 0

    def test_marks_dead_after_max_retries(self) -> None:
        """Events with >= 5 retries are marked DEAD."""
        from app.tasks.data_health import process_stuck_outbox_events

        mock_event = MagicMock()
        mock_event.retry_count = 5
        mock_event.event_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_event]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = process_stuck_outbox_events(stuck_minutes=30, batch_size=100)

        assert result["marked_dead"] == 1
        assert result["recovered"] == 0


# ── Invoice status reconciliation ────────────────────────────


class TestReconcileInvoiceStatuses:
    """Tests for reconcile_invoice_statuses task."""

    def test_fixes_false_paid_with_partial_payment(self) -> None:
        """Invoice with PAID status but partial payment -> PARTIALLY_PAID."""
        from app.tasks.data_health import reconcile_invoice_statuses

        mock_inv = MagicMock()
        mock_inv.total_amount = Decimal("1000.00")
        mock_inv.amount_paid = Decimal("500.00")
        mock_inv.invoice_number = "INV-001"
        mock_inv.invoice_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_inv]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = reconcile_invoice_statuses()

        assert result["fixed_to_partially_paid"] == 1
        assert result["fixed_to_posted"] == 0

    def test_fixes_false_paid_with_no_payment(self) -> None:
        """Invoice with PAID status but no payment -> POSTED."""
        from app.tasks.data_health import reconcile_invoice_statuses

        mock_inv = MagicMock()
        mock_inv.total_amount = Decimal("1000.00")
        mock_inv.amount_paid = Decimal("0")
        mock_inv.invoice_number = "INV-002"
        mock_inv.invoice_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_inv]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = reconcile_invoice_statuses()

        assert result["fixed_to_posted"] == 1
        assert result["fixed_to_partially_paid"] == 0


# ── Stale draft cleanup ─────────────────────────────────────


class TestCleanupStaleDrafts:
    """Tests for cleanup_stale_drafts task."""

    def test_dry_run_returns_counts_only(self) -> None:
        """Dry run reports counts without voiding."""
        from app.tasks.data_health import cleanup_stale_drafts

        mock_db = MagicMock()
        mock_db.scalar.side_effect = [3, 5, 2]  # journals, invoices, AP

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = cleanup_stale_drafts(draft_age_days=180, dry_run=True)

        assert result["journal_drafts"] == 3
        assert result["invoice_drafts"] == 5
        assert result["ap_invoice_drafts"] == 2
        assert result["voided"] == 0


# ── Data health check ────────────────────────────────────────


class TestRunDataHealthCheck:
    """Tests for run_data_health_check task."""

    def test_returns_all_check_keys(self) -> None:
        """Health check returns all expected keys."""
        from app.tasks.data_health import run_data_health_check

        mock_db = MagicMock()
        mock_db.scalar.return_value = 0

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = run_data_health_check()

        expected_keys = {
            "unbalanced_journals",
            "false_paid_invoices",
            "stuck_outbox_events",
            "dead_outbox_events",
            "stale_journal_drafts",
            "account_balance_rows",
            "notification_total",
            "notification_unread",
            "approved_invoices_stuck",
            "unallocated_payments",
        }
        assert expected_keys.issubset(set(result.keys()))


# ── Auto-post approved invoices ──────────────────────────────


class TestAutoPostApprovedInvoices:
    """Tests for auto_post_approved_invoices task."""

    def test_skips_on_post_failure(self) -> None:
        """Failed postings are counted as skipped, not errors."""
        from app.tasks.data_health import auto_post_approved_invoices

        mock_inv = MagicMock()
        mock_inv.invoice_number = "INV-003"
        mock_inv.invoice_id = uuid.uuid4()
        mock_inv.organization_id = uuid.uuid4()
        mock_inv.created_by_user_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_inv]

        with (
            patch("app.tasks.data_health.SessionLocal") as mock_session,
            patch(
                "app.services.finance.ar.invoice.ARInvoiceService.post_invoice",
                side_effect=ValueError("Period closed"),
            ),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = auto_post_approved_invoices(max_age_days=7)

        assert result["posted"] == 0
        assert result["skipped"] == 1
        assert len(result["errors"]) == 1


# ── Rebuild account balances ─────────────────────────────────


class TestRebuildAccountBalances:
    """Tests for rebuild_account_balances task."""

    def test_creates_balance_rows(self) -> None:
        """Balance rows are created from ledger aggregation."""
        from app.tasks.data_health import rebuild_account_balances

        org_id = uuid.uuid4()
        acc_id = uuid.uuid4()
        period_id = uuid.uuid4()

        mock_row = MagicMock()
        mock_row.organization_id = org_id
        mock_row.account_id = acc_id
        mock_row.fiscal_period_id = period_id
        mock_row.business_unit_id = None
        mock_row.cost_center_id = None
        mock_row.project_id = None
        mock_row.segment_id = None
        mock_row.total_debit = Decimal("5000.00")
        mock_row.total_credit = Decimal("3000.00")
        mock_row.txn_count = 10

        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [mock_row]
        mock_db.scalar.return_value = None  # No existing balance

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = rebuild_account_balances()

        assert result["rows_written"] == 1
        assert result["errors"] == []
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


# ── Payment allocation reconciliation ─────────────────────


class TestReconcilePaymentAllocations:
    """Tests for reconcile_payment_allocations task."""

    def test_dry_run_reports_without_creating_allocations(self) -> None:
        """Dry run counts matches but does not create allocation records."""
        from app.tasks.data_health import reconcile_payment_allocations

        org_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        mock_payment = MagicMock()
        mock_payment.payment_id = uuid.uuid4()
        mock_payment.customer_id = customer_id
        mock_payment.organization_id = org_id
        mock_payment.amount = Decimal("1000.00")
        mock_payment.payment_date = date(2026, 1, 15)

        mock_invoice = MagicMock()
        mock_invoice.invoice_id = uuid.uuid4()
        mock_invoice.customer_id = customer_id
        mock_invoice.total_amount = Decimal("1000.00")
        mock_invoice.amount_paid = Decimal("0")

        mock_db = MagicMock()
        # First scalars call = unallocated payments, second = matching invoices
        mock_db.scalars.return_value.all.side_effect = [
            [mock_payment],
            [mock_invoice],
        ]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = reconcile_payment_allocations(batch_size=100, dry_run=True)

        assert result["fully_allocated"] == 1
        assert result["allocations_created"] == 0
        assert result["dry_run"] is True
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_execute_creates_allocations(self) -> None:
        """Non-dry-run creates allocation records and updates invoice.

        Payment of 500 against a 1000 invoice: the *payment* is fully
        allocated (all 500 distributed), though the invoice is only
        partially paid.
        """
        from app.tasks.data_health import reconcile_payment_allocations

        org_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        mock_payment = MagicMock()
        mock_payment.payment_id = uuid.uuid4()
        mock_payment.customer_id = customer_id
        mock_payment.organization_id = org_id
        mock_payment.amount = Decimal("500.00")
        mock_payment.payment_date = date(2026, 1, 20)

        mock_invoice = MagicMock()
        mock_invoice.invoice_id = uuid.uuid4()
        mock_invoice.customer_id = customer_id
        mock_invoice.total_amount = Decimal("1000.00")
        mock_invoice.amount_paid = Decimal("0")

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.side_effect = [
            [mock_payment],
            [mock_invoice],
        ]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = reconcile_payment_allocations(batch_size=100, dry_run=False)

        # Payment is fully allocated (all 500 used up)
        assert result["fully_allocated"] == 1
        assert result["allocations_created"] == 1
        assert result["dry_run"] is False
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_no_match_when_no_outstanding_invoices(self) -> None:
        """Payments with no matching invoices are counted as no_match."""
        from app.tasks.data_health import reconcile_payment_allocations

        mock_payment = MagicMock()
        mock_payment.payment_id = uuid.uuid4()
        mock_payment.customer_id = uuid.uuid4()
        mock_payment.organization_id = uuid.uuid4()
        mock_payment.amount = Decimal("1000.00")
        mock_payment.payment_date = date(2026, 1, 10)

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.side_effect = [
            [mock_payment],
            [],  # No matching invoices
        ]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = reconcile_payment_allocations(batch_size=100, dry_run=True)

        assert result["no_match"] == 1
        assert result["fully_allocated"] == 0


# ── Unbalanced journal fix ─────────────────────────────────


class TestFixUnbalancedPostedJournals:
    """Tests for fix_unbalanced_posted_journals task."""

    def test_dry_run_reports_unbalanced_journals(self) -> None:
        """Dry run finds and reports unbalanced journals without fixing."""
        from app.tasks.data_health import fix_unbalanced_posted_journals

        mock_row = MagicMock()
        mock_row.journal_entry_id = uuid.uuid4()
        mock_row.journal_number = "JNL-001"
        mock_row.organization_id = uuid.uuid4()
        mock_row.entry_date = date(2026, 1, 5)
        mock_row.description = "Test journal"
        mock_row.created_by_user_id = uuid.uuid4()
        mock_row.total_debit = Decimal("10000.00")
        mock_row.total_credit = Decimal("9999.00")
        mock_row.imbalance = Decimal("1.00")

        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [mock_row]

        with patch("app.tasks.data_health.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = fix_unbalanced_posted_journals(dry_run=True)

        assert result["found"] == 1
        assert result["fixed"] == 0
        assert result["dry_run"] is True
        assert len(result["details"]) == 1
        assert result["details"][0]["journal_number"] == "JNL-001"
        assert result["details"][0]["imbalance"] == "1.00"

    def test_execute_reverses_unbalanced_journals(self) -> None:
        """Non-dry-run reverses unbalanced journals using ReversalService."""
        from app.tasks.data_health import fix_unbalanced_posted_journals

        journal_id = uuid.uuid4()
        reversal_id = uuid.uuid4()

        mock_row = MagicMock()
        mock_row.journal_entry_id = journal_id
        mock_row.journal_number = "JNL-002"
        mock_row.organization_id = uuid.uuid4()
        mock_row.entry_date = date(2026, 1, 10)
        mock_row.description = "Bad journal"
        mock_row.created_by_user_id = uuid.uuid4()
        mock_row.total_debit = Decimal("5000.00")
        mock_row.total_credit = Decimal("4995.00")
        mock_row.imbalance = Decimal("5.00")

        mock_reversal_result = MagicMock()
        mock_reversal_result.success = True
        mock_reversal_result.reversal_journal_id = reversal_id
        mock_reversal_result.message = "Reversed"

        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [mock_row]

        with (
            patch("app.tasks.data_health.SessionLocal") as mock_session,
            patch(
                "app.services.finance.gl.reversal.ReversalService.create_reversal",
                return_value=mock_reversal_result,
            ) as mock_reversal,
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = fix_unbalanced_posted_journals(dry_run=False)

        assert result["found"] == 1
        assert result["fixed"] == 1
        assert result["details"][0]["action"] == "reversed"
        mock_reversal.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_handles_reversal_failure(self) -> None:
        """Failed reversals are recorded in errors, not raised."""
        from app.tasks.data_health import fix_unbalanced_posted_journals

        mock_row = MagicMock()
        mock_row.journal_entry_id = uuid.uuid4()
        mock_row.journal_number = "JNL-003"
        mock_row.organization_id = uuid.uuid4()
        mock_row.entry_date = date(2026, 1, 15)
        mock_row.description = "Bad journal 2"
        mock_row.created_by_user_id = uuid.uuid4()
        mock_row.total_debit = Decimal("3000.00")
        mock_row.total_credit = Decimal("2990.00")
        mock_row.imbalance = Decimal("10.00")

        mock_reversal_result = MagicMock()
        mock_reversal_result.success = False
        mock_reversal_result.message = "Period is closed"

        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [mock_row]

        with (
            patch("app.tasks.data_health.SessionLocal") as mock_session,
            patch(
                "app.services.finance.gl.reversal.ReversalService.create_reversal",
                return_value=mock_reversal_result,
            ),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = fix_unbalanced_posted_journals(dry_run=False)

        assert result["found"] == 1
        assert result["fixed"] == 0
        assert result["details"][0]["action"] == "failed"
        assert len(result["errors"]) == 1
        assert "Period is closed" in result["errors"][0]
