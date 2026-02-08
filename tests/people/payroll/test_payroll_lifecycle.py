"""
Tests for PayrollLifecycle orchestrator.

Tests state transition validations and event dispatching.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.people.payroll.payroll_entry import PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.services.people.payroll.events import (
    PayrollEventDispatcher,
    RunApproved,
    RunCancelled,
    RunPosted,
    RunSlipsCreated,
    RunSubmitted,
    SlipApproved,
    SlipCancelled,
    SlipPaid,
    SlipPosted,
    SlipRejected,
    SlipSubmitted,
)
from app.services.people.payroll.lifecycle import (
    RUN_TRANSITIONS,
    SLIP_TRANSITIONS,
    PayrollLifecycle,
    PayrollLifecycleError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def dispatcher():
    """Create a fresh event dispatcher for each test."""
    return PayrollEventDispatcher()


@pytest.fixture
def org_id():
    """Test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Test user ID."""
    return uuid.uuid4()


@pytest.fixture
def other_user_id():
    """Different user ID for SoD tests."""
    return uuid.uuid4()


class MockSalarySlip:
    """Mock salary slip for testing."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        status: SalarySlipStatus = SalarySlipStatus.DRAFT,
        created_by_id: uuid.UUID = None,
    ):
        self.slip_id = uuid.uuid4()
        self.organization_id = organization_id
        self.slip_number = f"SLIP-2026-{uuid.uuid4().hex[:5]}"
        self.status = status
        self.status_changed_at = None
        self.status_changed_by_id = None
        self.created_by_id = created_by_id or uuid.uuid4()
        self.gross_pay = Decimal("500000")
        self.net_pay = Decimal("420000")
        self.currency_code = "NGN"
        self.exchange_rate = Decimal("1.0")
        self.journal_entry_id = None


class MockPayrollEntry:
    """Mock payroll entry for testing."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        status: PayrollEntryStatus = PayrollEntryStatus.DRAFT,
        created_by_id: uuid.UUID = None,
    ):
        self.entry_id = uuid.uuid4()
        self.organization_id = organization_id
        self.entry_number = f"PR-2026-{uuid.uuid4().hex[:5]}"
        self.status = status
        self.status_changed_at = None
        self.status_changed_by_id = None
        self.created_by_id = created_by_id or uuid.uuid4()


# ---------------------------------------------------------------------------
# State Transition Validation Tests
# ---------------------------------------------------------------------------


class TestSlipTransitionValidation:
    """Tests for slip state transition validation."""

    def test_all_slip_statuses_have_transitions_defined(self):
        """Every slip status should have transitions defined."""
        for status in SalarySlipStatus:
            assert status in SLIP_TRANSITIONS, f"Missing transitions for {status}"

    def test_draft_can_transition_to_submitted(self, mock_db, dispatcher):
        """DRAFT → SUBMITTED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_slip(
            SalarySlipStatus.DRAFT, SalarySlipStatus.SUBMITTED
        )

    def test_draft_cannot_transition_to_approved(self, mock_db, dispatcher):
        """DRAFT → APPROVED is not allowed (must submit first)."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert not lifecycle.can_transition_slip(
            SalarySlipStatus.DRAFT, SalarySlipStatus.APPROVED
        )

    def test_submitted_can_transition_to_approved(self, mock_db, dispatcher):
        """SUBMITTED → APPROVED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_slip(
            SalarySlipStatus.SUBMITTED, SalarySlipStatus.APPROVED
        )

    def test_submitted_can_be_rejected_to_draft(self, mock_db, dispatcher):
        """SUBMITTED → DRAFT is allowed (rejection)."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_slip(
            SalarySlipStatus.SUBMITTED, SalarySlipStatus.DRAFT
        )

    def test_approved_can_transition_to_posted(self, mock_db, dispatcher):
        """APPROVED → POSTED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_slip(
            SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED
        )

    def test_posted_can_transition_to_paid(self, mock_db, dispatcher):
        """POSTED → PAID is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_slip(
            SalarySlipStatus.POSTED, SalarySlipStatus.PAID
        )

    def test_cancelled_is_terminal(self, mock_db, dispatcher):
        """CANCELLED cannot transition to anything."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        for status in SalarySlipStatus:
            if status != SalarySlipStatus.CANCELLED:
                assert not lifecycle.can_transition_slip(
                    SalarySlipStatus.CANCELLED, status
                ), f"CANCELLED should not transition to {status}"

    def test_any_state_can_be_cancelled(self, mock_db, dispatcher):
        """Most states can transition to CANCELLED."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        cancellable_states = [
            SalarySlipStatus.DRAFT,
            SalarySlipStatus.SUBMITTED,
            SalarySlipStatus.APPROVED,
            SalarySlipStatus.POSTED,
            SalarySlipStatus.PAID,
        ]
        for status in cancellable_states:
            assert lifecycle.can_transition_slip(status, SalarySlipStatus.CANCELLED), (
                f"{status} should be cancellable"
            )

    def test_validate_slip_transition_raises_on_invalid(self, mock_db, dispatcher):
        """validate_slip_transition raises PayrollLifecycleError for invalid transitions."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError) as exc_info:
            lifecycle.validate_slip_transition(
                SalarySlipStatus.DRAFT, SalarySlipStatus.POSTED
            )

        assert exc_info.value.current_status == "DRAFT"
        assert exc_info.value.target_status == "POSTED"


class TestRunTransitionValidation:
    """Tests for run state transition validation."""

    def test_all_run_statuses_have_transitions_defined(self):
        """Every run status should have transitions defined."""
        for status in PayrollEntryStatus:
            assert status in RUN_TRANSITIONS, f"Missing transitions for {status}"

    def test_draft_can_transition_to_pending(self, mock_db, dispatcher):
        """DRAFT → PENDING is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_run(
            PayrollEntryStatus.DRAFT, PayrollEntryStatus.PENDING
        )

    def test_pending_can_transition_to_slips_created(self, mock_db, dispatcher):
        """PENDING → SLIPS_CREATED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_run(
            PayrollEntryStatus.PENDING, PayrollEntryStatus.SLIPS_CREATED
        )

    def test_slips_created_can_transition_to_submitted(self, mock_db, dispatcher):
        """SLIPS_CREATED → SUBMITTED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_run(
            PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.SUBMITTED
        )

    def test_approved_can_transition_to_posted(self, mock_db, dispatcher):
        """APPROVED → POSTED is allowed."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        assert lifecycle.can_transition_run(
            PayrollEntryStatus.APPROVED, PayrollEntryStatus.POSTED
        )

    def test_cancelled_is_terminal(self, mock_db, dispatcher):
        """CANCELLED cannot transition to anything."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        for status in PayrollEntryStatus:
            if status != PayrollEntryStatus.CANCELLED:
                assert not lifecycle.can_transition_run(
                    PayrollEntryStatus.CANCELLED, status
                ), f"CANCELLED should not transition to {status}"


# ---------------------------------------------------------------------------
# Slip Lifecycle Action Tests
# ---------------------------------------------------------------------------


class TestSubmitSlip:
    """Tests for submit_slip method."""

    def test_submit_slip_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful slip submission."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.DRAFT)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipSubmitted, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.submit_slip(org_id, slip.slip_id, user_id)

        assert result.success is True
        assert result.previous_status == "DRAFT"
        assert result.new_status == "SUBMITTED"
        assert slip.status == SalarySlipStatus.SUBMITTED
        assert slip.status_changed_by_id == user_id
        mock_db.flush.assert_called()

        # Verify event dispatched
        assert len(events_received) == 1
        assert events_received[0].slip_id == slip.slip_id

    def test_submit_slip_from_wrong_status_fails(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Cannot submit an already posted slip."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.POSTED)
        mock_db.get.return_value = slip

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError) as exc_info:
            lifecycle.submit_slip(org_id, slip.slip_id, user_id)

        assert exc_info.value.current_status == "POSTED"
        assert exc_info.value.target_status == "SUBMITTED"


class TestApproveSlip:
    """Tests for approve_slip method."""

    def test_approve_slip_success(
        self, mock_db, dispatcher, org_id, user_id, other_user_id
    ):
        """Test successful slip approval."""
        slip = MockSalarySlip(
            organization_id=org_id,
            status=SalarySlipStatus.SUBMITTED,
            created_by_id=other_user_id,  # Different user created it
        )
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipApproved, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.approve_slip(org_id, slip.slip_id, user_id)

        assert result.success is True
        assert result.new_status == "APPROVED"
        assert slip.status == SalarySlipStatus.APPROVED
        assert len(events_received) == 1

    def test_approve_slip_sod_violation(self, mock_db, dispatcher, org_id, user_id):
        """Creator cannot approve their own slip (SoD)."""
        from fastapi import HTTPException

        slip = MockSalarySlip(
            organization_id=org_id,
            status=SalarySlipStatus.SUBMITTED,
            created_by_id=user_id,  # Same user trying to approve
        )
        mock_db.get.return_value = slip

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(HTTPException) as exc_info:
            lifecycle.approve_slip(org_id, slip.slip_id, user_id)

        assert exc_info.value.status_code == 403
        assert "Segregation of duties" in exc_info.value.detail


class TestPaySlip:
    """Tests for pay_slip method."""

    def test_pay_slip_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful slip payment."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.POSTED)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipPaid, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.pay_slip(org_id, slip.slip_id, user_id, "PAY-REF-001")

        assert result.success is True
        assert result.new_status == "PAID"
        assert slip.status == SalarySlipStatus.PAID
        assert len(events_received) == 1
        assert events_received[0].payment_reference == "PAY-REF-001"


class TestCancelSlip:
    """Tests for cancel_slip method."""

    def test_cancel_draft_slip(self, mock_db, dispatcher, org_id, user_id):
        """Test cancelling a draft slip."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.DRAFT)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipCancelled, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.cancel_slip(org_id, slip.slip_id, user_id, "Test reason")

        assert result.success is True
        assert result.new_status == "CANCELLED"
        assert slip.status == SalarySlipStatus.CANCELLED
        assert len(events_received) == 1
        assert events_received[0].reason == "Test reason"

    def test_cannot_cancel_already_cancelled(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Cannot cancel an already cancelled slip."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.CANCELLED)
        mock_db.get.return_value = slip

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError):
            lifecycle.cancel_slip(org_id, slip.slip_id, user_id)


class TestRejectSlip:
    """Tests for reject_slip method."""

    def test_reject_submitted_slip(self, mock_db, dispatcher, org_id, user_id):
        """Test rejecting a submitted slip back to draft."""
        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.SUBMITTED)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipRejected, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.reject_slip(org_id, slip.slip_id, user_id)

        assert result.success is True
        assert result.new_status == "DRAFT"
        assert slip.status == SalarySlipStatus.DRAFT
        assert len(events_received) == 1
        assert events_received[0].slip_id == slip.slip_id


# ---------------------------------------------------------------------------
# Run Lifecycle Action Tests
# ---------------------------------------------------------------------------


class TestApproveRun:
    """Tests for approve_run method."""

    def test_approve_run_success(
        self, mock_db, dispatcher, org_id, user_id, other_user_id
    ):
        """Test successful run approval."""
        run = MockPayrollEntry(
            organization_id=org_id,
            status=PayrollEntryStatus.SUBMITTED,
            created_by_id=other_user_id,
        )
        mock_db.get.return_value = run

        events_received = []
        dispatcher.register(RunApproved, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.approve_run(org_id, run.entry_id, user_id)

        assert result.success is True
        assert result.new_status == "APPROVED"
        assert run.status == PayrollEntryStatus.APPROVED
        assert len(events_received) == 1

    def test_approve_run_sod_violation(self, mock_db, dispatcher, org_id, user_id):
        """Creator cannot approve their own run (SoD)."""
        from fastapi import HTTPException

        run = MockPayrollEntry(
            organization_id=org_id,
            status=PayrollEntryStatus.SUBMITTED,
            created_by_id=user_id,
        )
        mock_db.get.return_value = run

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(HTTPException) as exc_info:
            lifecycle.approve_run(org_id, run.entry_id, user_id)

        assert exc_info.value.status_code == 403


class TestMarkSlipsCreated:
    """Tests for mark_slips_created method."""

    def test_mark_slips_created_from_draft(self, mock_db, dispatcher, org_id, user_id):
        """DRAFT → SLIPS_CREATED is allowed for existing flow."""
        run = MockPayrollEntry(organization_id=org_id, status=PayrollEntryStatus.DRAFT)
        mock_db.get.return_value = run

        events_received = []
        dispatcher.register(RunSlipsCreated, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.mark_slips_created(org_id, run.entry_id, user_id, 5)

        assert result.success is True
        assert result.new_status == "SLIPS_CREATED"
        assert run.status == PayrollEntryStatus.SLIPS_CREATED
        assert len(events_received) == 1
        assert events_received[0].slip_count == 5

    def test_mark_slips_created_invalid_status(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Cannot mark slips created from POSTED status."""
        run = MockPayrollEntry(organization_id=org_id, status=PayrollEntryStatus.POSTED)
        mock_db.get.return_value = run

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError):
            lifecycle.mark_slips_created(org_id, run.entry_id, user_id, 1)


class TestSubmitRun:
    """Tests for submit_run method."""

    def test_submit_run_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful run submission."""
        run = MockPayrollEntry(
            organization_id=org_id,
            status=PayrollEntryStatus.SLIPS_CREATED,
        )
        mock_db.get.return_value = run

        events_received = []
        dispatcher.register(RunSubmitted, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.submit_run(org_id, run.entry_id, user_id)

        assert result.success is True
        assert result.new_status == "SUBMITTED"
        assert run.status == PayrollEntryStatus.SUBMITTED
        assert len(events_received) == 1

    def test_submit_run_invalid_status(self, mock_db, dispatcher, org_id, user_id):
        """Cannot submit run from DRAFT."""
        run = MockPayrollEntry(
            organization_id=org_id,
            status=PayrollEntryStatus.DRAFT,
        )
        mock_db.get.return_value = run

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError):
            lifecycle.submit_run(org_id, run.entry_id, user_id)


class TestCancelRun:
    """Tests for cancel_run method."""

    def test_cancel_run_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful run cancellation."""
        run = MockPayrollEntry(
            organization_id=org_id,
            status=PayrollEntryStatus.SUBMITTED,
        )
        mock_db.get.return_value = run

        events_received = []
        dispatcher.register(RunCancelled, lambda e: events_received.append(e))

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.cancel_run(org_id, run.entry_id, user_id, "Test reason")

        assert result.success is True
        assert result.new_status == "CANCELLED"
        assert run.status == PayrollEntryStatus.CANCELLED
        assert len(events_received) == 1


class TestNotFoundGuards:
    """Tests for slip/run not-found handling."""

    def test_submit_slip_not_found(self, mock_db, dispatcher, org_id, user_id):
        """Submitting a missing slip returns 404."""
        from fastapi import HTTPException

        mock_db.get.return_value = None
        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(HTTPException) as exc_info:
            lifecycle.submit_slip(org_id, uuid.uuid4(), user_id)

        assert exc_info.value.status_code == 404

    def test_submit_run_not_found(self, mock_db, dispatcher, org_id, user_id):
        """Submitting a missing run returns 404."""
        from fastapi import HTTPException

        mock_db.get.return_value = None
        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(HTTPException) as exc_info:
            lifecycle.submit_run(org_id, uuid.uuid4(), user_id)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Event Dispatcher Tests
# ---------------------------------------------------------------------------


class TestEventDispatcher:
    """Tests for PayrollEventDispatcher."""

    def test_register_and_dispatch(self, dispatcher, org_id, user_id):
        """Test registering handlers and dispatching events."""
        received = []

        def handler(event):
            received.append(event)

        dispatcher.register(SlipSubmitted, handler)

        event = SlipSubmitted(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=uuid.uuid4(),
            slip_number="SLIP-001",
        )
        dispatcher.dispatch(event)

        assert len(received) == 1
        assert received[0] == event

    def test_multiple_handlers(self, dispatcher, org_id, user_id):
        """Multiple handlers for same event type."""
        received1 = []
        received2 = []

        dispatcher.register(SlipApproved, lambda e: received1.append(e))
        dispatcher.register(SlipApproved, lambda e: received2.append(e))

        event = SlipApproved(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=uuid.uuid4(),
            slip_number="SLIP-001",
            approved_by_id=user_id,
        )
        dispatcher.dispatch(event)

        assert len(received1) == 1
        assert len(received2) == 1

    def test_handler_exception_does_not_stop_others(self, dispatcher, org_id, user_id):
        """One handler failing doesn't prevent others from running."""
        received = []

        def failing_handler(event):
            raise ValueError("Handler failed")

        def working_handler(event):
            received.append(event)

        dispatcher.register(SlipPosted, failing_handler)
        dispatcher.register(SlipPosted, working_handler)

        event = SlipPosted(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=uuid.uuid4(),
            slip_number="SLIP-001",
        )

        # Should not raise, and working handler should still receive event
        dispatcher.dispatch(event)
        assert len(received) == 1

    def test_unregister_handler(self, dispatcher, org_id, user_id):
        """Test unregistering handlers."""
        received = []

        def handler(event):
            received.append(event)

        dispatcher.register(SlipPaid, handler)
        dispatcher.unregister(SlipPaid, handler)

        event = SlipPaid(
            organization_id=org_id,
            triggered_by_id=user_id,
            slip_id=uuid.uuid4(),
            slip_number="SLIP-001",
        )
        dispatcher.dispatch(event)

        assert len(received) == 0

    def test_clear_all_handlers(self, dispatcher, org_id, user_id):
        """Test clearing all handlers."""
        received = []

        dispatcher.register(SlipSubmitted, lambda e: received.append(e))
        dispatcher.register(SlipApproved, lambda e: received.append(e))
        dispatcher.clear()

        dispatcher.dispatch(
            SlipSubmitted(
                organization_id=org_id,
                triggered_by_id=user_id,
                slip_id=uuid.uuid4(),
                slip_number="SLIP-001",
            )
        )

        assert len(received) == 0


# ---------------------------------------------------------------------------
# Transition Table Tests (comprehensive matrix)
# ---------------------------------------------------------------------------


class TestSlipTransitionMatrix:
    """Comprehensive tests for all slip transitions."""

    @pytest.mark.parametrize(
        "from_status,to_status,expected",
        [
            # DRAFT transitions
            (SalarySlipStatus.DRAFT, SalarySlipStatus.SUBMITTED, True),
            (SalarySlipStatus.DRAFT, SalarySlipStatus.APPROVED, False),
            (SalarySlipStatus.DRAFT, SalarySlipStatus.POSTED, False),
            (SalarySlipStatus.DRAFT, SalarySlipStatus.PAID, False),
            (SalarySlipStatus.DRAFT, SalarySlipStatus.CANCELLED, True),
            # SUBMITTED transitions
            (SalarySlipStatus.SUBMITTED, SalarySlipStatus.DRAFT, True),  # rejection
            (SalarySlipStatus.SUBMITTED, SalarySlipStatus.APPROVED, True),
            (SalarySlipStatus.SUBMITTED, SalarySlipStatus.POSTED, False),
            (SalarySlipStatus.SUBMITTED, SalarySlipStatus.PAID, False),
            (SalarySlipStatus.SUBMITTED, SalarySlipStatus.CANCELLED, True),
            # APPROVED transitions
            (SalarySlipStatus.APPROVED, SalarySlipStatus.DRAFT, False),
            (SalarySlipStatus.APPROVED, SalarySlipStatus.SUBMITTED, True),  # unapprove
            (SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED, True),
            (SalarySlipStatus.APPROVED, SalarySlipStatus.PAID, False),
            (SalarySlipStatus.APPROVED, SalarySlipStatus.CANCELLED, True),
            # POSTED transitions
            (SalarySlipStatus.POSTED, SalarySlipStatus.DRAFT, False),
            (SalarySlipStatus.POSTED, SalarySlipStatus.SUBMITTED, False),
            (SalarySlipStatus.POSTED, SalarySlipStatus.APPROVED, False),
            (SalarySlipStatus.POSTED, SalarySlipStatus.PAID, True),
            (SalarySlipStatus.POSTED, SalarySlipStatus.CANCELLED, True),
            # PAID transitions
            (SalarySlipStatus.PAID, SalarySlipStatus.DRAFT, False),
            (SalarySlipStatus.PAID, SalarySlipStatus.CANCELLED, True),
            # CANCELLED transitions (terminal)
            (SalarySlipStatus.CANCELLED, SalarySlipStatus.DRAFT, False),
            (SalarySlipStatus.CANCELLED, SalarySlipStatus.SUBMITTED, False),
            (SalarySlipStatus.CANCELLED, SalarySlipStatus.APPROVED, False),
            (SalarySlipStatus.CANCELLED, SalarySlipStatus.POSTED, False),
            (SalarySlipStatus.CANCELLED, SalarySlipStatus.PAID, False),
        ],
    )
    def test_slip_transition(
        self, mock_db, dispatcher, from_status, to_status, expected
    ):
        """Parametrized test for all slip status transitions."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.can_transition_slip(from_status, to_status)
        assert result == expected, f"{from_status} → {to_status} should be {expected}"


class TestRunTransitionMatrix:
    """Comprehensive tests for all run transitions."""

    @pytest.mark.parametrize(
        "from_status,to_status,expected",
        [
            # DRAFT transitions
            (PayrollEntryStatus.DRAFT, PayrollEntryStatus.PENDING, True),
            (PayrollEntryStatus.DRAFT, PayrollEntryStatus.SLIPS_CREATED, True),
            (PayrollEntryStatus.DRAFT, PayrollEntryStatus.CANCELLED, True),
            # PENDING transitions
            (PayrollEntryStatus.PENDING, PayrollEntryStatus.SLIPS_CREATED, True),
            (PayrollEntryStatus.PENDING, PayrollEntryStatus.SUBMITTED, False),
            (PayrollEntryStatus.PENDING, PayrollEntryStatus.CANCELLED, True),
            # SLIPS_CREATED transitions
            (PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.SUBMITTED, True),
            (PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.APPROVED, False),
            (PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.CANCELLED, True),
            # SUBMITTED transitions
            (PayrollEntryStatus.SUBMITTED, PayrollEntryStatus.APPROVED, True),
            (
                PayrollEntryStatus.SUBMITTED,
                PayrollEntryStatus.SLIPS_CREATED,
                True,
            ),  # rejection
            (PayrollEntryStatus.SUBMITTED, PayrollEntryStatus.CANCELLED, True),
            # APPROVED transitions
            (PayrollEntryStatus.APPROVED, PayrollEntryStatus.POSTED, True),
            (
                PayrollEntryStatus.APPROVED,
                PayrollEntryStatus.SUBMITTED,
                True,
            ),  # unapprove
            (PayrollEntryStatus.APPROVED, PayrollEntryStatus.CANCELLED, True),
            # POSTED transitions
            (PayrollEntryStatus.POSTED, PayrollEntryStatus.APPROVED, False),
            (PayrollEntryStatus.POSTED, PayrollEntryStatus.CANCELLED, True),
            # CANCELLED (terminal)
            (PayrollEntryStatus.CANCELLED, PayrollEntryStatus.DRAFT, False),
            (PayrollEntryStatus.CANCELLED, PayrollEntryStatus.PENDING, False),
        ],
    )
    def test_run_transition(
        self, mock_db, dispatcher, from_status, to_status, expected
    ):
        """Parametrized test for all run status transitions."""
        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.can_transition_run(from_status, to_status)
        assert result == expected, f"{from_status} → {to_status} should be {expected}"


# ---------------------------------------------------------------------------
# Unified Posting Tests (PR 3 - post_slip_to_gl, post_run_to_gl)
# ---------------------------------------------------------------------------


class TestPostSlipToGL:
    """Tests for post_slip_to_gl unified posting method."""

    def test_post_slip_to_gl_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful unified slip posting to GL."""
        from datetime import date
        from unittest.mock import MagicMock

        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipPosted, lambda e: events_received.append(e))

        # Mock the GL adapter
        mock_gl_result = MagicMock()
        mock_gl_result.success = True
        mock_gl_result.journal_entry_id = uuid.uuid4()
        mock_gl_result.posting_batch_id = uuid.uuid4()
        mock_gl_result.message = "Journal created"

        with patch(
            "app.services.people.payroll.payroll_gl_adapter.PayrollGLAdapter.create_slip_journal"
        ) as mock_create_journal:
            mock_create_journal.return_value = mock_gl_result

            lifecycle = PayrollLifecycle(mock_db, dispatcher)
            result = lifecycle.post_slip_to_gl(
                org_id, slip.slip_id, date(2026, 1, 31), user_id
            )

        assert result.success is True
        assert result.new_status == "POSTED"
        assert slip.status == SalarySlipStatus.POSTED
        assert slip.journal_entry_id == mock_gl_result.journal_entry_id
        mock_db.commit.assert_called_once()
        assert len(events_received) == 1
        assert events_received[0].journal_entry_id == mock_gl_result.journal_entry_id

    def test_post_slip_to_gl_handler_failure_does_not_block(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Handler failure should not prevent successful posting."""
        from datetime import date
        from unittest.mock import MagicMock

        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED)
        mock_db.get.return_value = slip

        def failing_handler(_event):
            raise ValueError("boom")

        dispatcher.register(SlipPosted, failing_handler)

        mock_gl_result = MagicMock()
        mock_gl_result.success = True
        mock_gl_result.journal_entry_id = uuid.uuid4()
        mock_gl_result.posting_batch_id = uuid.uuid4()
        mock_gl_result.message = "Journal created"

        with patch(
            "app.services.people.payroll.payroll_gl_adapter.PayrollGLAdapter.create_slip_journal"
        ) as mock_create_journal:
            mock_create_journal.return_value = mock_gl_result

            lifecycle = PayrollLifecycle(mock_db, dispatcher)
            result = lifecycle.post_slip_to_gl(
                org_id, slip.slip_id, date(2026, 1, 31), user_id
            )

        assert result.success is True
        assert slip.status == SalarySlipStatus.POSTED

    def test_post_slip_to_gl_gl_failure(self, mock_db, dispatcher, org_id, user_id):
        """Test unified slip posting when GL fails."""
        from datetime import date
        from unittest.mock import MagicMock

        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED)
        mock_db.get.return_value = slip

        events_received = []
        dispatcher.register(SlipPosted, lambda e: events_received.append(e))

        # Mock GL adapter failure
        mock_gl_result = MagicMock()
        mock_gl_result.success = False
        mock_gl_result.message = "No expense account configured"

        with patch(
            "app.services.people.payroll.payroll_gl_adapter.PayrollGLAdapter.create_slip_journal"
        ) as mock_create_journal:
            mock_create_journal.return_value = mock_gl_result

            lifecycle = PayrollLifecycle(mock_db, dispatcher)
            result = lifecycle.post_slip_to_gl(
                org_id, slip.slip_id, date(2026, 1, 31), user_id
            )

        assert result.success is False
        assert "No expense account" in result.message
        assert slip.status == SalarySlipStatus.APPROVED  # Unchanged
        mock_db.commit.assert_not_called()
        assert len(events_received) == 0  # No event on failure

    def test_post_slip_to_gl_wrong_status(self, mock_db, dispatcher, org_id, user_id):
        """Cannot post slip that isn't APPROVED."""
        from datetime import date

        slip = MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.DRAFT)
        mock_db.get.return_value = slip

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError) as exc_info:
            lifecycle.post_slip_to_gl(org_id, slip.slip_id, date(2026, 1, 31), user_id)

        assert exc_info.value.current_status == "DRAFT"
        assert exc_info.value.target_status == "POSTED"


class TestPostRunToGL:
    """Tests for post_run_to_gl unified posting method."""

    def test_post_run_to_gl_success(self, mock_db, dispatcher, org_id, user_id):
        """Test successful unified run posting to GL."""
        from datetime import date
        from unittest.mock import MagicMock

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.APPROVED
        )
        mock_db.get.return_value = run

        # Create mock slips
        slips = [
            MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED)
            for _ in range(3)
        ]

        # Mock scalars to return slips
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = slips
        mock_db.scalars.return_value = mock_scalars

        events_received = []
        dispatcher.register(RunPosted, lambda e: events_received.append(e))

        # Mock GL adapter
        mock_gl_result = MagicMock()
        mock_gl_result.success = True
        mock_gl_result.journal_entry_id = uuid.uuid4()
        mock_gl_result.posting_batch_id = uuid.uuid4()
        mock_gl_result.message = "Journal created"

        with patch(
            "app.services.people.payroll.payroll_gl_adapter.PayrollGLAdapter.create_run_journal"
        ) as mock_create_journal:
            mock_create_journal.return_value = mock_gl_result

            lifecycle = PayrollLifecycle(mock_db, dispatcher)
            result = lifecycle.post_run_to_gl(
                org_id, run.entry_id, date(2026, 1, 31), user_id
            )

        assert result.success is True
        assert result.new_status == "POSTED"
        assert run.status == PayrollEntryStatus.POSTED
        assert run.journal_entry_id == mock_gl_result.journal_entry_id

        # All slips should be updated
        for slip in slips:
            assert slip.status == SalarySlipStatus.POSTED
            assert slip.journal_entry_id == mock_gl_result.journal_entry_id

        mock_db.commit.assert_called_once()
        assert len(events_received) == 1

    def test_post_run_to_gl_no_approved_slips(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Test run posting fails when no slips exist."""
        from datetime import date

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.APPROVED
        )
        mock_db.get.return_value = run

        # No slips
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_db.scalars.return_value = mock_scalars

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.post_run_to_gl(
            org_id, run.entry_id, date(2026, 1, 31), user_id
        )

        assert result.success is False
        assert "No salary slips found" in result.message
        assert run.status == PayrollEntryStatus.APPROVED  # Unchanged

    def test_post_run_to_gl_gl_failure(self, mock_db, dispatcher, org_id, user_id):
        """Test run posting when GL fails."""
        from datetime import date
        from unittest.mock import MagicMock

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.APPROVED
        )
        mock_db.get.return_value = run

        slips = [
            MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED)
        ]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = slips
        mock_db.scalars.return_value = mock_scalars

        # Mock GL adapter failure
        mock_gl_result = MagicMock()
        mock_gl_result.success = False
        mock_gl_result.message = "Salaries Expense account not configured"

        with patch(
            "app.services.people.payroll.payroll_gl_adapter.PayrollGLAdapter.create_run_journal"
        ) as mock_create_journal:
            mock_create_journal.return_value = mock_gl_result

            lifecycle = PayrollLifecycle(mock_db, dispatcher)
            result = lifecycle.post_run_to_gl(
                org_id, run.entry_id, date(2026, 1, 31), user_id
            )

        assert result.success is False
        assert "Salaries Expense" in result.message
        assert run.status == PayrollEntryStatus.APPROVED  # Unchanged
        mock_db.commit.assert_not_called()

    def test_post_run_to_gl_rejects_mixed_status(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Run posting should fail if any slip is not APPROVED."""
        from datetime import date

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.APPROVED
        )
        mock_db.get.return_value = run

        slips = [
            MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.APPROVED),
            MockSalarySlip(organization_id=org_id, status=SalarySlipStatus.SUBMITTED),
        ]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = slips
        mock_db.scalars.return_value = mock_scalars

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.post_run_to_gl(
            org_id, run.entry_id, date(2026, 1, 31), user_id
        )

        assert result.success is False
        assert "All slips must be APPROVED" in result.message
        mock_db.commit.assert_not_called()

    def test_post_run_to_gl_rejects_mixed_currency(
        self, mock_db, dispatcher, org_id, user_id
    ):
        """Run posting should fail if slips have mixed currency or rates."""
        from datetime import date

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.APPROVED
        )
        mock_db.get.return_value = run

        slip_ngn = MockSalarySlip(
            organization_id=org_id, status=SalarySlipStatus.APPROVED
        )
        slip_usd = MockSalarySlip(
            organization_id=org_id, status=SalarySlipStatus.APPROVED
        )
        slip_usd.currency_code = "USD"
        slips = [slip_ngn, slip_usd]

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = slips
        mock_db.scalars.return_value = mock_scalars

        lifecycle = PayrollLifecycle(mock_db, dispatcher)
        result = lifecycle.post_run_to_gl(
            org_id, run.entry_id, date(2026, 1, 31), user_id
        )

        assert result.success is False
        assert "Mixed currency" in result.message
        mock_db.commit.assert_not_called()

    def test_post_run_to_gl_wrong_status(self, mock_db, dispatcher, org_id, user_id):
        """Cannot post run that isn't APPROVED."""
        from datetime import date

        run = MockPayrollEntry(
            organization_id=org_id, status=PayrollEntryStatus.SUBMITTED
        )
        mock_db.get.return_value = run

        lifecycle = PayrollLifecycle(mock_db, dispatcher)

        with pytest.raises(PayrollLifecycleError) as exc_info:
            lifecycle.post_run_to_gl(org_id, run.entry_id, date(2026, 1, 31), user_id)

        assert exc_info.value.current_status == "SUBMITTED"
        assert exc_info.value.target_status == "POSTED"
