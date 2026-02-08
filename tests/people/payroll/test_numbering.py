"""
Tests for payroll numbering service.

Verifies that PayrollNumberingService correctly delegates to
SyncNumberingService, and that convenience functions work.
"""

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.people.payroll.numbering import (
    PayrollNumberingService,
    PayrollNumberSequence,
    generate_entry_number,
    generate_slip_number,
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
    """Create a mock database session."""
    db = MagicMock()
    db.scalar.return_value = None
    return db


@pytest.fixture
def numbering_service(mock_db):
    """Create a numbering service with mock db."""
    return PayrollNumberingService(mock_db)


# ---------------------------------------------------------------------------
# Basic Generation Tests (now delegation tests)
# ---------------------------------------------------------------------------


class TestSlipNumberGeneration:
    """Tests for slip number generation via SyncNumberingService delegation."""

    def test_generates_slip_number(self, numbering_service, mock_db, org_id):
        """Slip number generation should delegate to SyncNumberingService."""
        year = datetime.now().year
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = f"SLIP-{year}-00001"

            result = numbering_service.generate_slip_number(org_id)

        assert result == f"SLIP-{year}-00001"
        MockSyncSvc.assert_called_once_with(mock_db)

    def test_generates_sequential_slip_number(self, numbering_service, mock_db, org_id):
        """Subsequent slips should return incremented numbers."""
        year = datetime.now().year
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = f"SLIP-{year}-00043"

            result = numbering_service.generate_slip_number(org_id)

        assert result == f"SLIP-{year}-00043"

    def test_slip_number_year_param_ignored(self, numbering_service, mock_db, org_id):
        """The year param is deprecated and should be ignored."""
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = "SLIP-2026-00001"

            # year=2025 is passed but ignored
            result = numbering_service.generate_slip_number(org_id, year=2025)

        assert result == "SLIP-2026-00001"


class TestEntryNumberGeneration:
    """Tests for payroll entry/run number generation."""

    def test_generates_entry_number(self, numbering_service, mock_db, org_id):
        """Entry number generation should delegate to SyncNumberingService."""
        year = datetime.now().year
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = f"PAY-{year}-0001"

            result = numbering_service.generate_entry_number(org_id)

        assert result == f"PAY-{year}-0001"
        MockSyncSvc.assert_called_once_with(mock_db)

    def test_generates_sequential_entry_number(
        self, numbering_service, mock_db, org_id
    ):
        """Next entry should return incremented number."""
        year = datetime.now().year
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = f"PAY-{year}-0016"

            result = numbering_service.generate_entry_number(org_id)

        assert result == f"PAY-{year}-0016"

    def test_entry_number_year_param_ignored(self, numbering_service, mock_db, org_id):
        """The year param is deprecated and should be ignored."""
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = "PAY-2026-0006"

            result = numbering_service.generate_entry_number(org_id, year=2024)

        assert result == "PAY-2026-0006"


# ---------------------------------------------------------------------------
# Peek Number Tests (still uses old PayrollNumberSequence table)
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
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            year = datetime.now().year
            mock_instance.generate_next_number.return_value = f"SLIP-{year}-00001"

            result = generate_slip_number(mock_db, org_id)

        assert result == f"SLIP-{year}-00001"

    def test_generate_entry_number_function(self, mock_db, org_id):
        """Convenience function should delegate to service."""
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = "PAY-2026-0001"

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
        year = datetime.now().year

        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = f"SLIP-{year}-00001"

            service = PayrollNumberingService(mock_db)

            result_a = service.generate_slip_number(org_a)
            result_b = service.generate_slip_number(org_b)

        # Both get 00001 since SyncNumberingService is called with each org
        assert result_a == f"SLIP-{year}-00001"
        assert result_b == f"SLIP-{year}-00001"

        # Verify correct org_id was used in calls
        calls = mock_instance.generate_next_number.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0] == org_a
        assert calls[1][0][0] == org_b


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
