"""
Tests for SequenceService.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockColumn, MockNumberingSequence


@contextmanager
def patch_sequence_service():
    """Helper context manager that sets up all required patches for SequenceService."""
    with patch('app.services.ifrs.platform.sequence.NumberingSequence') as mock_seq:
        mock_seq.organization_id = MockColumn()
        mock_seq.sequence_type = MockColumn()
        mock_seq.fiscal_year_id = MockColumn()
        with patch('app.services.ifrs.platform.sequence.and_', return_value=MagicMock()):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                yield mock_seq


class TestSequenceService:
    """Tests for SequenceService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        with patch.dict('sys.modules', {
            'app.models.ifrs.core_config.numbering_sequence': MagicMock(),
        }):
            from app.services.finance.platform.sequence import SequenceService
            return SequenceService

    @pytest.fixture
    def mock_sequence_type(self):
        """Create a mock SequenceType enum value."""
        mock_type = MagicMock()
        mock_type.value = "INVOICE"
        return mock_type

    def test_get_next_number_returns_formatted_number(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_next_number should return formatted sequence number."""
        mock_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            prefix="INV-",
            current_number=5,
            min_digits=6,
        )
        mock_db_session.query.return_value.filter.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_sequence

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                result = service.get_next_number(
                    mock_db_session,
                    organization_id=organization_id,
                    sequence_type=mock_sequence_type,
                )

        assert result == "INV-000006"
        assert mock_sequence.current_number == 6
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_get_next_number_raises_404_when_not_configured(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_next_number should raise 404 for unconfigured sequence."""
        mock_db_session.query.return_value.filter.return_value.filter.return_value.with_for_update.return_value.first.return_value = None

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                with pytest.raises(HTTPException) as exc_info:
                    service.get_next_number(
                        mock_db_session,
                        organization_id=organization_id,
                        sequence_type=mock_sequence_type,
                    )

        assert exc_info.value.status_code == 404
        assert "not configured" in exc_info.value.detail

    def test_get_next_number_with_suffix(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_next_number should include suffix in formatted number."""
        mock_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            prefix="JRN-",
            suffix="-2024",
            current_number=99,
            min_digits=4,
        )
        mock_db_session.query.return_value.filter.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_sequence

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                result = service.get_next_number(
                    mock_db_session,
                    organization_id=organization_id,
                    sequence_type=mock_sequence_type,
                )

        assert result == "JRN-0100-2024"
        mock_db_session.flush.assert_called_once()

    def test_configure_sequence_creates_new(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """configure_sequence should create new sequence."""
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        with patch('app.services.ifrs.platform.sequence.NumberingSequence') as MockModel:
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                result = service.configure_sequence(
                    mock_db_session,
                    organization_id=organization_id,
                    sequence_type=mock_sequence_type,
                    prefix="INV-",
                    min_digits=6,
                    start_number=100,
                )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_configure_sequence_updates_existing(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """configure_sequence should update existing sequence."""
        existing_sequence = MockNumberingSequence(
            organization_id=organization_id,
            sequence_type="INVOICE",
            prefix="OLD-",
        )
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = existing_sequence

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                result = service.configure_sequence(
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
        mock_db_session.commit.assert_called_once()

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

        # First query for base sequence returns None
        # Second query for FY sequence returns the existing sequence
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            None,  # base_sequence
            fy_sequence,  # fy_sequence
        ]

        with patch_sequence_service():
            result = service.reset_sequence(
                mock_db_session,
                organization_id=organization_id,
                sequence_type=mock_sequence_type,
                fiscal_year_id=fiscal_year_id,
                start_number=0,
            )

        assert fy_sequence.current_number == 0
        mock_db_session.commit.assert_called_once()

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

        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            base_sequence,  # base_sequence
            None,  # fy_sequence
        ]

        with patch('app.services.ifrs.platform.sequence.NumberingSequence') as MockModel:
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            MockModel.organization_id = MockColumn()
            MockModel.sequence_type = MockColumn()
            MockModel.fiscal_year_id = MockColumn()
            with patch('app.services.ifrs.platform.sequence.and_', return_value=MagicMock()):
                with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                    result = service.reset_sequence(
                        mock_db_session,
                        organization_id=organization_id,
                        sequence_type=mock_sequence_type,
                        fiscal_year_id=fiscal_year_id,
                    )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_get_current_number_returns_value(
        self, service, mock_db_session, organization_id, mock_sequence_type
    ):
        """get_current_number should return current value without increment."""
        mock_sequence = MockNumberingSequence(
            organization_id=organization_id,
            current_number=42,
        )
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = mock_sequence

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
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
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                with pytest.raises(HTTPException) as exc_info:
                    service.get_current_number(
                        mock_db_session,
                        organization_id=organization_id,
                        sequence_type=mock_sequence_type,
                    )

        assert exc_info.value.status_code == 404

    def test_get_raises_404_for_missing_id(self, service, mock_db_session):
        """get should raise 404 for non-existent sequence ID."""
        mock_db_session.get.return_value = None

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                with pytest.raises(HTTPException) as exc_info:
                    service.get(mock_db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_returns_sequences(self, service, mock_db_session, organization_id):
        """list should return filtered sequences."""
        mock_sequences = [
            MockNumberingSequence(organization_id=organization_id),
            MockNumberingSequence(organization_id=organization_id),
        ]
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_sequences

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
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
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = mock_sequence

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
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
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        with patch('app.services.ifrs.platform.sequence.NumberingSequence'):
            with patch('app.services.ifrs.platform.sequence.coerce_uuid', side_effect=lambda x: x):
                with pytest.raises(HTTPException) as exc_info:
                    service.preview_next_number(
                        mock_db_session,
                        organization_id=organization_id,
                        sequence_type=mock_sequence_type,
                    )

        assert exc_info.value.status_code == 404
