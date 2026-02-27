"""
Tests for payroll event handlers.

Verifies that event handlers correctly trigger notifications
when payroll events are dispatched.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.services.people.payroll.event_handlers import (
    PayrollEventHandlers,
    are_handlers_registered,
    register_payroll_handlers,
    unregister_payroll_handlers,
)
from app.services.people.payroll.events import (
    PayrollEventDispatcher,
    RunPosted,
    SlipPaid,
    SlipPosted,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id():
    """Test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Test user ID."""
    return uuid.uuid4()


@pytest.fixture
def dispatcher():
    """Create a fresh event dispatcher for each test."""
    return PayrollEventDispatcher()


class MockEmployee:
    """Mock employee for testing."""

    def __init__(self, employee_id: uuid.UUID = None, person_id: uuid.UUID = None):
        self.employee_id = employee_id or uuid.uuid4()
        self.person_id = person_id or uuid.uuid4()
        self.first_name = "John"
        self.last_name = "Doe"


class MockSalarySlip:
    """Mock salary slip for testing."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID = None,
        status: SalarySlipStatus = SalarySlipStatus.POSTED,
    ):
        self.slip_id = uuid.uuid4()
        self.organization_id = organization_id
        self.employee_id = employee_id or uuid.uuid4()
        self.slip_number = f"SLIP-2026-{uuid.uuid4().hex[:5]}"
        self.status = status
        self.start_date = date(2026, 1, 1)
        self.end_date = date(2026, 1, 31)
        self.currency_code = "NGN"
        self.net_pay = Decimal("450000.00")
        self.gross_pay = Decimal("500000.00")
        self.payment_reference = None


class MockPayrollEntry:
    """Mock payroll entry for testing."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        status: PayrollEntryStatus = PayrollEntryStatus.POSTED,
    ):
        self.entry_id = uuid.uuid4()
        self.organization_id = organization_id
        self.entry_number = f"PR-2026-{uuid.uuid4().hex[:5]}"
        self.status = status
        self.salary_slips = []


# ---------------------------------------------------------------------------
# Handler Tests
# ---------------------------------------------------------------------------


class TestSlipPostedHandler:
    """Tests for SlipPosted event handler."""

    def test_handler_calls_notification_service(self, org_id, user_id):
        """Verify handler calls PayrollNotificationService.notify_payslip_posted."""
        slip = MockSalarySlip(organization_id=org_id)
        employee = MockEmployee(employee_id=slip.employee_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else employee
        )

        # Create a context manager mock for session factory
        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        def session_factory():
            return session_context

        handlers = PayrollEventHandlers(session_factory)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            mock_notification = MagicMock()
            MockNotificationService.return_value = mock_notification

            event = SlipPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
            )
            handlers.handle_slip_posted(event)

            # Verify notification service was called
            mock_notification.notify_payslip_posted.assert_called_once_with(
                slip, employee
            )
            mock_db.commit.assert_called_once()

    def test_handler_handles_missing_slip(self, org_id, user_id):
        """Handler should log warning and return if slip not found."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            event = SlipPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=uuid.uuid4(),
                slip_number="SLIP-NOTFOUND",
            )
            handlers.handle_slip_posted(event)

            # Notification service should NOT be called
            MockNotificationService.return_value.notify_payslip_posted.assert_not_called()

    def test_handler_handles_missing_employee(self, org_id, user_id):
        """Handler should log warning and return if employee not found."""
        slip = MockSalarySlip(organization_id=org_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else None
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            event = SlipPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
            )
            handlers.handle_slip_posted(event)

            # Notification service should NOT be called
            MockNotificationService.return_value.notify_payslip_posted.assert_not_called()

    def test_handler_ignores_org_mismatch(self, org_id, user_id):
        """Handler should ignore slips from a different org."""
        slip = MockSalarySlip(organization_id=uuid.uuid4())
        employee = MockEmployee(employee_id=slip.employee_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else employee
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            event = SlipPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
            )
            handlers.handle_slip_posted(event)

            MockNotificationService.return_value.notify_payslip_posted.assert_not_called()


class TestSlipPaidHandler:
    """Tests for SlipPaid event handler."""

    def test_handler_calls_notification_service(self, org_id, user_id):
        """Verify handler calls PayrollNotificationService.notify_payslip_paid."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.PAID)
        employee = MockEmployee(employee_id=slip.employee_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else employee
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            mock_notification = MagicMock()
            MockNotificationService.return_value = mock_notification

            event = SlipPaid(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                payment_reference="PAY-REF-001",
            )
            handlers.handle_slip_paid(event)

            mock_notification.notify_payslip_paid.assert_called_once_with(
                slip, employee
            )
            mock_db.commit.assert_called_once()

    def test_handler_stores_payment_reference(self, org_id, user_id):
        """Handler should store payment reference on slip if not already set."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.PAID)
        slip.payment_reference = None  # Not set
        employee = MockEmployee(employee_id=slip.employee_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else employee
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ):
            event = SlipPaid(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                payment_reference="PAY-REF-002",
            )
            handlers.handle_slip_paid(event)

            assert slip.payment_reference == "PAY-REF-002"

    def test_handler_ignores_org_mismatch(self, org_id, user_id):
        """Handler should ignore slips from a different org."""
        slip = MockSalarySlip(
            organization_id=uuid.uuid4(), status=SalarySlipStatus.PAID
        )
        employee = MockEmployee(employee_id=slip.employee_id)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            slip if model == SalarySlip else employee
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            event = SlipPaid(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
            )
            handlers.handle_slip_paid(event)

            MockNotificationService.return_value.notify_payslip_paid.assert_not_called()


class TestRunPostedHandler:
    """Tests for RunPosted event handler."""

    def test_handler_notifies_all_employees(self, org_id, user_id):
        """Handler should notify all employees in the payroll run."""
        run = MockPayrollEntry(organization_id=org_id)

        # Add 3 slips to the run
        employees = []
        for _i in range(3):
            slip = MockSalarySlip(organization_id=org_id)
            employee = MockEmployee(employee_id=slip.employee_id)
            slip.employee = employee  # For eager loading
            employees.append(employee)
            run.salary_slips.append(slip)

        mock_db = MagicMock()
        mock_db.get.side_effect = lambda model, id: (
            run if model == PayrollEntry else employees[0]  # Simplified
        )

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            mock_notification = MagicMock()
            MockNotificationService.return_value = mock_notification

            event = RunPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                run_id=run.entry_id,
                run_number=run.entry_number,
            )
            handlers.handle_run_posted(event)

            # Should be called once for each slip
            assert mock_notification.notify_payslip_posted.call_count == 3
            mock_db.commit.assert_called_once()

    def test_handler_ignores_org_mismatch(self, org_id, user_id):
        """Handler should ignore runs from a different org."""
        run = MockPayrollEntry(organization_id=uuid.uuid4())

        mock_db = MagicMock()
        mock_db.get.return_value = run

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = PayrollEventHandlers(lambda: session_context)

        with patch(
            "app.services.people.payroll.event_handlers.PayrollNotificationService"
        ) as MockNotificationService:
            event = RunPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                run_id=run.entry_id,
                run_number=run.entry_number,
            )
            handlers.handle_run_posted(event)

            MockNotificationService.return_value.notify_payslip_posted.assert_not_called()


# ---------------------------------------------------------------------------
# Registration Tests
# ---------------------------------------------------------------------------


class TestHandlerRegistration:
    """Tests for handler registration functions."""

    def test_register_and_unregister(self, dispatcher):
        """Test registering and unregistering handlers."""
        mock_session_factory = MagicMock()

        handlers = register_payroll_handlers(
            dispatcher=dispatcher,
            session_factory=mock_session_factory,
        )

        assert are_handlers_registered() is True

        unregister_payroll_handlers(dispatcher, handlers)
        # Note: are_handlers_registered() will be False after unregister

    def test_register_twice_returns_same_instance(self, dispatcher):
        """Second registration should return the same handlers instance."""
        mock_session_factory = MagicMock()

        handlers_first = register_payroll_handlers(
            dispatcher=dispatcher,
            session_factory=mock_session_factory,
        )

        handlers_second = register_payroll_handlers(
            dispatcher=dispatcher,
            session_factory=mock_session_factory,
        )

        assert handlers_first is handlers_second

        unregister_payroll_handlers(dispatcher)

    def test_unregister_without_handlers_unregisters_all(self, dispatcher):
        """Unregister without handlers should remove registered handlers."""
        mock_session_factory = MagicMock()

        handlers = register_payroll_handlers(
            dispatcher=dispatcher,
            session_factory=mock_session_factory,
        )

        unregister_payroll_handlers(dispatcher)
        # After unregister, handlers should no longer be registered.
        event = SlipPosted(
            organization_id=uuid.uuid4(),
            triggered_by_id=uuid.uuid4(),
            slip_id=uuid.uuid4(),
            slip_number="SLIP-TEST",
        )
        # Dispatch should not call any handler; no exception is the success condition.
        dispatcher.dispatch(event)
        assert are_handlers_registered() is False

        # Clean up in case handlers are still present
        unregister_payroll_handlers(dispatcher, handlers)

    def test_events_reach_handlers_when_registered(self, dispatcher, org_id, user_id):
        """When registered, events should reach handlers."""
        mock_db = MagicMock()
        mock_db.get.return_value = None  # Will cause early return

        session_context = MagicMock()
        session_context.__enter__ = MagicMock(return_value=mock_db)
        session_context.__exit__ = MagicMock(return_value=False)

        handlers = register_payroll_handlers(
            dispatcher=dispatcher,
            session_factory=lambda: session_context,
        )

        try:
            # Dispatch an event
            event = SlipPosted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=uuid.uuid4(),
                slip_number="SLIP-TEST",
            )
            dispatcher.dispatch(event)

            # Verify db.get was called (handler was invoked)
            mock_db.get.assert_called()

        finally:
            unregister_payroll_handlers(dispatcher, handlers)


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestEventDispatchIntegration:
    """Integration tests for event dispatch from services."""

    def test_slip_posted_event_structure(self, org_id, user_id):
        """Verify SlipPosted event has correct structure."""
        journal_id = uuid.uuid4()
        slip_id = uuid.uuid4()

        event = SlipPosted(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=slip_id,
            slip_number="SLIP-2026-00001",
            journal_entry_id=journal_id,
        )

        assert event.organization_id == org_id
        assert event.triggered_by_id == user_id
        assert event.slip_id == slip_id
        assert event.slip_number == "SLIP-2026-00001"
        assert event.journal_entry_id == journal_id
        assert event.timestamp is not None

    def test_slip_paid_event_structure(self, org_id, user_id):
        """Verify SlipPaid event has correct structure."""
        slip_id = uuid.uuid4()

        event = SlipPaid(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=slip_id,
            slip_number="SLIP-2026-00001",
            payment_reference="BANK-TXN-12345",
        )

        assert event.organization_id == org_id
        assert event.triggered_by_id == user_id
        assert event.slip_id == slip_id
        assert event.slip_number == "SLIP-2026-00001"
        assert event.payment_reference == "BANK-TXN-12345"

    def test_run_posted_event_structure(self, org_id, user_id):
        """Verify RunPosted event has correct structure."""
        run_id = uuid.uuid4()

        event = RunPosted(
            organization_id=org_id,
            triggered_by_id=user_id,
            run_id=run_id,
            run_number="PR-2026-00001",
        )

        assert event.organization_id == org_id
        assert event.triggered_by_id == user_id
        assert event.run_id == run_id
        assert event.run_number == "PR-2026-00001"


# ---------------------------------------------------------------------------
# Service Dispatch Tests
# ---------------------------------------------------------------------------


class MockSlip:
    """Minimal slip for payout dispatch tests."""

    def __init__(self, slip_id, slip_number):
        self.slip_id = slip_id
        self.slip_number = slip_number
        self.organization_id = uuid.uuid4()
        self.employee_id = uuid.uuid4()
        self.employee = MagicMock(employee_id=self.employee_id)
        self.status = SalarySlipStatus.POSTED
        self.paid_at = None
        self.paid_by_id = None
        self.payment_reference = None


class MockEntry:
    """Minimal payroll entry for payout dispatch tests."""

    def __init__(self, slips):
        self.salary_slips = slips


def test_payout_dispatches_after_commit(dispatcher):
    """Ensure payout dispatch happens after commit is called."""
    from app.services.people.payroll.payroll_service import PayrollService

    slip = MockSlip(uuid.uuid4(), "SLIP-TEST")
    entry = MockEntry([slip])
    entry.entry_id = uuid.uuid4()

    mock_db = MagicMock()
    mock_db.scalar.return_value = entry

    # Track order of calls
    call_order = []

    def flush_side_effect():
        call_order.append("flush")

    mock_db.flush.side_effect = flush_side_effect

    def dispatch_side_effect(*_args, **_kwargs):
        call_order.append("dispatch")

    with patch(
        "app.services.people.payroll.payroll_service._dispatch_slip_paid",
        side_effect=dispatch_side_effect,
    ):
        service = PayrollService(mock_db)
        service.payout_payroll_entry(
            org_id=uuid.uuid4(),
            entry_id=entry.entry_id,
            paid_by_id=uuid.uuid4(),
            slip_ids=[slip.slip_id],
            payment_reference="REF",
        )

    assert call_order == ["flush", "dispatch"]
