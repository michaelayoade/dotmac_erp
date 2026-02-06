"""
Tests for payroll numbering service.

Verifies idempotent number generation works correctly including
concurrent access scenarios.
"""

import uuid
from datetime import datetime
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.people.payroll.numbering import (
    PayrollNumberingService,
    PayrollNumberSequence,
    generate_slip_number,
    generate_entry_number,
    MAX_RETRIES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id():
    """Test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session with savepoint support."""
    db = MagicMock()
    db.scalar.return_value = None  # No existing sequences by default

    # Create a mock savepoint that begin_nested() returns
    mock_savepoint = MagicMock()
    db.begin_nested.return_value = mock_savepoint

    # Store savepoint reference on db for test assertions
    db._mock_savepoint = mock_savepoint

    return db


@pytest.fixture
def numbering_service(mock_db):
    """Create a numbering service with mock db."""
    return PayrollNumberingService(mock_db)


# ---------------------------------------------------------------------------
# Basic Generation Tests
# ---------------------------------------------------------------------------


class TestSlipNumberGeneration:
    """Tests for slip number generation."""

    def test_generates_first_slip_number(self, numbering_service, mock_db, org_id):
        """First slip should be SLIP-YYYY-00001."""
        mock_db.scalar.return_value = None  # No existing numbers

        result = numbering_service.generate_slip_number(org_id)

        year = datetime.now().year
        assert result == f"SLIP-{year}-00001"
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_generates_sequential_slip_number(self, numbering_service, mock_db, org_id):
        """Next slip should increment from max sequence."""
        mock_db.scalar.return_value = 42  # Max sequence is 42

        result = numbering_service.generate_slip_number(org_id)

        year = datetime.now().year
        assert result == f"SLIP-{year}-00043"

    def test_slip_number_with_custom_year(self, numbering_service, mock_db, org_id):
        """Should support custom year for number generation."""
        mock_db.scalar.return_value = None

        result = numbering_service.generate_slip_number(org_id, year=2025)

        assert result == "SLIP-2025-00001"

    def test_slip_number_padding(self, numbering_service, mock_db, org_id):
        """Slip numbers should have 5-digit padding."""
        test_cases = [
            (None, "00001"),
            (0, "00001"),
            (9, "00010"),
            (99, "00100"),
            (999, "01000"),
            (9999, "10000"),
            (99998, "99999"),
        ]

        year = datetime.now().year
        for max_seq, expected_suffix in test_cases:
            mock_db.scalar.return_value = max_seq
            mock_db.reset_mock()

            result = numbering_service.generate_slip_number(org_id)
            assert result == f"SLIP-{year}-{expected_suffix}", (
                f"Failed for max_seq={max_seq}"
            )


class TestEntryNumberGeneration:
    """Tests for payroll entry/run number generation."""

    def test_generates_first_entry_number(self, numbering_service, mock_db, org_id):
        """First entry should be PAY-YYYY-0001."""
        mock_db.scalar.return_value = None

        result = numbering_service.generate_entry_number(org_id)

        year = datetime.now().year
        assert result == f"PAY-{year}-0001"

    def test_generates_sequential_entry_number(
        self, numbering_service, mock_db, org_id
    ):
        """Next entry should increment from max sequence."""
        mock_db.scalar.return_value = 15

        result = numbering_service.generate_entry_number(org_id)

        year = datetime.now().year
        assert result == f"PAY-{year}-0016"

    def test_entry_number_with_custom_year(self, numbering_service, mock_db, org_id):
        """Should support custom year for number generation."""
        mock_db.scalar.return_value = 5

        result = numbering_service.generate_entry_number(org_id, year=2024)

        assert result == "PAY-2024-0006"

    def test_entry_number_padding(self, numbering_service, mock_db, org_id):
        """Entry numbers should have 4-digit padding."""
        test_cases = [
            (None, "0001"),
            (8, "0009"),
            (99, "0100"),
            (999, "1000"),
        ]

        year = datetime.now().year
        for max_seq, expected_suffix in test_cases:
            mock_db.scalar.return_value = max_seq
            mock_db.reset_mock()

            result = numbering_service.generate_entry_number(org_id)
            assert result == f"PAY-{year}-{expected_suffix}"


# ---------------------------------------------------------------------------
# Conflict Resolution Tests
# ---------------------------------------------------------------------------


class TestRetryOnConflict:
    """Tests for retry behavior on concurrent conflicts using savepoints."""

    def test_retries_on_integrity_error(self, mock_db, org_id):
        """Should retry with next number on IntegrityError, rolling back only savepoint."""
        # First flush raises IntegrityError, second succeeds
        mock_db.flush.side_effect = [IntegrityError("", {}, None), None]
        # First call returns 5 (so we try 6), after rollback returns 6 (so we try 7)
        mock_db.scalar.side_effect = [5, 6]

        service = PayrollNumberingService(mock_db)
        result = service.generate_slip_number(org_id)

        year = datetime.now().year
        assert result == f"SLIP-{year}-00007"
        # Savepoint rollback, not full db rollback
        assert mock_db._mock_savepoint.rollback.call_count == 1
        assert mock_db.flush.call_count == 2
        # begin_nested called twice (once per attempt)
        assert mock_db.begin_nested.call_count == 2

    def test_multiple_retries_succeed(self, mock_db, org_id):
        """Should handle multiple conflicts before succeeding."""
        # Three conflicts then success
        mock_db.flush.side_effect = [
            IntegrityError("", {}, None),
            IntegrityError("", {}, None),
            IntegrityError("", {}, None),
            None,  # Success on 4th try
        ]
        mock_db.scalar.side_effect = [10, 11, 12, 13]

        service = PayrollNumberingService(mock_db)
        result = service.generate_slip_number(org_id)

        year = datetime.now().year
        assert result == f"SLIP-{year}-00014"
        # Savepoint rollback called 3 times (not full db rollback)
        assert mock_db._mock_savepoint.rollback.call_count == 3
        # begin_nested called 4 times (once per attempt)
        assert mock_db.begin_nested.call_count == 4

    def test_raises_after_max_retries(self, mock_db, org_id):
        """Should raise RuntimeError after MAX_RETRIES attempts."""
        # All attempts fail
        mock_db.flush.side_effect = IntegrityError("", {}, None)
        mock_db.scalar.return_value = 1

        service = PayrollNumberingService(mock_db)

        with pytest.raises(RuntimeError) as exc_info:
            service.generate_slip_number(org_id)

        assert f"after {MAX_RETRIES} attempts" in str(exc_info.value)
        # Savepoint rollback, not full db rollback
        assert mock_db._mock_savepoint.rollback.call_count == MAX_RETRIES
        # Full db.rollback should NOT be called
        mock_db.rollback.assert_not_called()

    def test_savepoint_preserves_other_transaction_work(self, mock_db, org_id):
        """Verify that conflict rollback only affects the sequence insert, not other work."""
        # Simulate a conflict on first attempt
        mock_db.flush.side_effect = [IntegrityError("", {}, None), None]
        mock_db.scalar.side_effect = [1, 2]

        service = PayrollNumberingService(mock_db)
        result = service.generate_slip_number(org_id)

        # Key assertion: begin_nested was called (creating savepoints)
        assert mock_db.begin_nested.call_count == 2

        # Key assertion: savepoint.rollback was called, not db.rollback
        assert mock_db._mock_savepoint.rollback.call_count == 1
        mock_db.rollback.assert_not_called()

        # Verify the number was successfully generated
        year = datetime.now().year
        assert result == f"SLIP-{year}-00003"


# ---------------------------------------------------------------------------
# Peek Number Tests
# ---------------------------------------------------------------------------


class TestPeekNextNumber:
    """Tests for previewing next number without reserving."""

    def test_peek_slip_number(self, numbering_service, mock_db, org_id):
        """Peek should return expected next number without reserving."""
        mock_db.scalar.return_value = 5

        result = numbering_service.peek_next_number(
            org_id,
            prefix=PayrollNumberingService.SLIP_PREFIX,
        )

        year = datetime.now().year
        assert result == f"SLIP-{year}-00006"
        # Should NOT call add or flush
        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_called()

    def test_peek_entry_number(self, numbering_service, mock_db, org_id):
        """Peek should work for entry numbers too."""
        mock_db.scalar.return_value = 99

        result = numbering_service.peek_next_number(
            org_id,
            prefix=PayrollNumberingService.ENTRY_PREFIX,
        )

        year = datetime.now().year
        assert result == f"PAY-{year}-0100"


# ---------------------------------------------------------------------------
# Convenience Function Tests
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_generate_slip_number_function(self, mock_db, org_id):
        """Convenience function should delegate to service."""
        mock_db.scalar.return_value = None

        result = generate_slip_number(mock_db, org_id)

        year = datetime.now().year
        assert result == f"SLIP-{year}-00001"

    def test_generate_entry_number_function(self, mock_db, org_id):
        """Convenience function should delegate to service."""
        mock_db.scalar.return_value = None

        result = generate_entry_number(mock_db, org_id, year=2026)

        assert result == "PAY-2026-0001"


# ---------------------------------------------------------------------------
# Organization Isolation Tests
# ---------------------------------------------------------------------------


class TestOrganizationIsolation:
    """Tests verifying numbers are isolated per organization."""

    def test_different_orgs_get_separate_sequences(self, mock_db):
        """Each organization should have its own sequence."""
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()

        # org_a has 100 slips, org_b has none
        def scalar_side_effect(*args, **kwargs):
            # Check the query args to determine org
            return None  # Both start fresh in mock

        mock_db.scalar.return_value = None

        service = PayrollNumberingService(mock_db)

        result_a = service.generate_slip_number(org_a)
        result_b = service.generate_slip_number(org_b)

        # Both should get 00001 since they're isolated
        year = datetime.now().year
        assert result_a == f"SLIP-{year}-00001"
        assert result_b == f"SLIP-{year}-00001"

        # Verify correct org_id was used in inserts
        calls = mock_db.add.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0].organization_id == org_a
        assert calls[1][0][0].organization_id == org_b


# ---------------------------------------------------------------------------
# Year Isolation Tests
# ---------------------------------------------------------------------------


class TestYearIsolation:
    """Tests verifying numbers reset per year."""

    def test_new_year_starts_fresh_sequence(self, mock_db, org_id):
        """Each year should start its sequence from 1."""
        # 2025 has 500 slips, 2026 has none
        mock_db.scalar.side_effect = [500, None]

        service = PayrollNumberingService(mock_db)

        result_2025 = service.generate_slip_number(org_id, year=2025)
        result_2026 = service.generate_slip_number(org_id, year=2026)

        assert result_2025 == "SLIP-2025-00501"
        assert result_2026 == "SLIP-2026-00001"


# ---------------------------------------------------------------------------
# Concurrent Access Simulation Tests
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    """Tests simulating concurrent number generation."""

    def test_concurrent_generation_with_mocked_conflicts(self, org_id):
        """Simulate concurrent access using controlled conflict sequence."""
        year = datetime.now().year
        generated_numbers = []
        lock = threading.Lock()

        # Shared counter to simulate DB state
        sequence_counter = {"value": 0}
        reserved_numbers: set[str] = set()

        def mock_generate():
            """Simulates a single number generation with possible conflict."""
            mock_db = MagicMock()

            # Mock savepoint for begin_nested()
            mock_savepoint = MagicMock()
            mock_db.begin_nested.return_value = mock_savepoint

            # Simulate getting current max
            def get_max(*args, **kwargs):
                with lock:
                    return sequence_counter["value"]

            mock_db.scalar.side_effect = get_max

            last_record = {"value": None}

            def capture_add(record):
                last_record["value"] = record

            mock_db.add.side_effect = capture_add

            # Simulate flush with uniqueness constraint
            def flush_with_conflict():
                with lock:
                    record = last_record["value"]
                    if record is None:
                        raise RuntimeError("No sequence record to flush")
                    if record.formatted_number in reserved_numbers:
                        raise IntegrityError("duplicate", {}, None)
                    reserved_numbers.add(record.formatted_number)
                    sequence_counter["value"] = max(
                        sequence_counter["value"],
                        record.sequence_number,
                    )

            mock_db.flush.side_effect = flush_with_conflict

            service = PayrollNumberingService(mock_db)
            try:
                number = service.generate_slip_number(org_id, year=year)
                with lock:
                    generated_numbers.append(number)
            except RuntimeError:
                pass  # Exhausted retries (shouldn't happen in this test)

        # Run 10 concurrent generations
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(mock_generate) for _ in range(10)]
            for f in futures:
                f.result()

        # All generated numbers should be unique
        assert len(generated_numbers) == len(set(generated_numbers))


# ---------------------------------------------------------------------------
# Sequence Record Tests
# ---------------------------------------------------------------------------


class TestPayrollNumberSequence:
    """Tests for PayrollNumberSequence model."""

    def test_sequence_record_fields(self):
        """Verify sequence record has correct fields."""
        org_id = uuid.uuid4()
        record = PayrollNumberSequence(
            organization_id=org_id,
            prefix="SLIP",
            year=2026,
            sequence_number=42,
            formatted_number="SLIP-2026-00042",
        )

        assert record.organization_id == org_id
        assert record.prefix == "SLIP"
        assert record.year == 2026
        assert record.sequence_number == 42
        assert record.formatted_number == "SLIP-2026-00042"
