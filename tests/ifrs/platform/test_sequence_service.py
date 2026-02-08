"""
Tests for SequenceService.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockNumberingSequence


class TestSequenceService:
    """Tests for SequenceService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        from app.services.finance.platform.sequence import SequenceService

        return SequenceService

    @pytest.fixture
    def mock_sequence_type(self):
        """Create a mock SequenceType enum value."""
        mock_type = MagicMock()
        mock_type.value = "INVOICE"
        return mock_type

    def test_get_next_number_delegates_to_sync_numbering_service(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_next_number should delegate to SyncNumberingService."""
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = "INV202602-0006"

            result = service.get_next_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
            )

        assert result == "INV202602-0006"
        MockSyncSvc.assert_called_once_with(mock_db_session)
        mock_instance.generate_next_number.assert_called_once_with(
            organization_id, mock_sequence_type
        )
        mock_db_session.commit.assert_not_called()

    def test_get_next_number_ignores_fiscal_year_id(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_next_number should accept but ignore fiscal_year_id."""
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as MockSyncSvc:
            mock_instance = MockSyncSvc.return_value
            mock_instance.generate_next_number.return_value = "INV202602-0001"

            result = service.get_next_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                fiscal_year_id=uuid.uuid4(),
            )

        assert result == "INV202602-0001"
        # Should still be called without fiscal_year_id
        mock_instance.generate_next_number.assert_called_once()

    def test_configure_sequence_creates_new(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """configure_sequence should create new sequence when none exists."""
        mock_db_session.scalar.return_value = None

        with (
            patch(
                "app.services.finance.platform.sequence.NumberingSequence"
            ) as MockModel,
            patch("app.services.finance.platform.sequence.select") as mock_select,
        ):
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            mock_select.return_value = MagicMock()

            service.configure_sequence(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                prefix="INV-",
                min_digits=6,
                start_number=100,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_configure_sequence_updates_existing(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """configure_sequence should update existing sequence."""
        existing_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            prefix="OLD-",
        )
        mock_db_session.scalar.return_value = existing_sequence

        with patch("app.services.finance.platform.sequence.select") as mock_select:
            mock_select.return_value = MagicMock()

            service.configure_sequence(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                prefix="NEW-",
                suffix="-2024",
                min_digits=8,
            )

        assert existing_sequence.prefix == "NEW-"
        assert existing_sequence.suffix == "-2024"
        assert existing_sequence.min_digits == 8
        mock_db_session.add.assert_not_called()
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_reset_sequence_resets_existing(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """reset_sequence should reset existing fiscal year sequence."""
        fiscal_year_id = uuid.uuid4()
        fy_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            fiscal_year_id=fiscal_year_id,
            current_number=500,
        )

        # First call returns None (base), second returns fy_sequence
        mock_db_session.scalar.side_effect = [None, fy_sequence]

        with patch("app.services.finance.platform.sequence.select") as mock_select:
            mock_select.return_value = MagicMock()

            service.reset_sequence(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                fiscal_year_id=fiscal_year_id,
                start_number=0,
            )

        assert fy_sequence.current_number == 0
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_reset_sequence_creates_from_base(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """reset_sequence should create new sequence copying base config."""
        fiscal_year_id = uuid.uuid4()
        base_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            prefix="INV-",
            suffix="-BASE",
            min_digits=8,
        )

        # First call returns base, second returns None (no fy sequence)
        mock_db_session.scalar.side_effect = [base_sequence, None]

        with (
            patch(
                "app.services.finance.platform.sequence.NumberingSequence"
            ) as MockModel,
            patch("app.services.finance.platform.sequence.select") as mock_select,
        ):
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            mock_select.return_value = MagicMock()

            service.reset_sequence(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                fiscal_year_id=fiscal_year_id,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_get_current_number_returns_value(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_current_number should return current value without increment."""
        mock_sequence = MockNumberingSequence(
            organization_id=organization_id,
            current_number=42,
        )
        mock_db_session.scalar.return_value = mock_sequence

        with patch("app.services.finance.platform.sequence.select") as mock_select:
            mock_select.return_value = MagicMock()

            result = service.get_current_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
            )

        assert result == 42
        assert mock_sequence.current_number == 42  # Unchanged

    def test_get_current_number_raises_404_for_missing(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_current_number should raise 404 for missing sequence."""
        mock_db_session.scalar.return_value = None

        with (
            patch("app.services.finance.platform.sequence.select") as mock_select,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_select.return_value = MagicMock()
            service.get_current_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
            )

        assert exc_info.value.status_code == 404

    def test_get_raises_404_for_missing_id(self, service, mock_db_session):
        """get should raise 404 for non-existent sequence ID."""
        mock_db_session.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            service.get(mock_db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_returns_sequences(self, service, mock_db_session, organization_id):
        """list should return filtered sequences."""
        mock_sequences = [
            MockNumberingSequence(organization_id=organization_id),
            MockNumberingSequence(organization_id=organization_id),
        ]
        # db.scalars(stmt).all() returns the list
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_sequences
        mock_db_session.scalars.return_value = mock_scalars

        with patch("app.services.finance.platform.sequence.select") as mock_select:
            mock_select.return_value = MagicMock()

            result = service.list(
                mock_db_session,
                organization_id=str(organization_id),
                limit=50,
                offset=0,
            )

        assert len(result) == 2

    def test_preview_next_number_doesnt_increment(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """preview_next_number should show next without incrementing."""
        mock_sequence = MockNumberingSequence(
            organization_id=organization_id,
            prefix="PRV-",
            current_number=10,
            min_digits=4,
        )
        mock_db_session.scalar.return_value = mock_sequence

        with patch("app.services.finance.platform.sequence.select") as mock_select:
            mock_select.return_value = MagicMock()

            result = service.preview_next_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
            )

        assert result == "PRV-0011"
        assert mock_sequence.current_number == 10  # Still unchanged
        mock_db_session.commit.assert_not_called()

    def test_preview_next_number_raises_404_for_missing(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """preview_next_number should raise 404 for missing sequence."""
        mock_db_session.scalar.return_value = None

        with (
            patch("app.services.finance.platform.sequence.select") as mock_select,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_select.return_value = MagicMock()
            service.preview_next_number(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
            )

        assert exc_info.value.status_code == 404
