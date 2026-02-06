"""
Tests for NumberingService and SyncNumberingService.

Tests document number generation, sequence reset logic, and formatting.
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from app.models.finance.core_config.numbering_sequence import (
    SequenceType,
    ResetFrequency,
)
from app.services.finance.common.numbering import (
    NumberingService,
    SyncNumberingService,
    DEFAULT_PREFIXES,
)


class MockNumberingSequence:
    """Mock NumberingSequence model."""

    def __init__(
        self,
        sequence_id=None,
        organization_id=None,
        sequence_type=SequenceType.INVOICE,
        prefix="INV",
        suffix="",
        separator="-",
        min_digits=4,
        include_year=True,
        include_month=True,
        year_format=4,
        current_number=0,
        current_year=None,
        current_month=None,
        reset_frequency=ResetFrequency.MONTHLY,
        last_used_at=None,
    ):
        self.sequence_id = sequence_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.sequence_type = sequence_type
        self.prefix = prefix
        self.suffix = suffix
        self.separator = separator
        self.min_digits = min_digits
        self.include_year = include_year
        self.include_month = include_month
        self.year_format = year_format
        self.current_number = current_number
        self.current_year = current_year
        self.current_month = current_month
        self.reset_frequency = reset_frequency
        self.last_used_at = last_used_at


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_async_db():
    return AsyncMock()


class TestDefaultPrefixes:
    """Tests for DEFAULT_PREFIXES constant."""

    def test_default_prefixes_defined(self):
        """Test that all sequence types have default prefixes."""
        assert DEFAULT_PREFIXES[SequenceType.INVOICE] == "INV"
        assert DEFAULT_PREFIXES[SequenceType.CREDIT_NOTE] == "CN"
        assert DEFAULT_PREFIXES[SequenceType.PAYMENT] == "PMT"
        assert DEFAULT_PREFIXES[SequenceType.RECEIPT] == "RCT"
        assert DEFAULT_PREFIXES[SequenceType.JOURNAL] == "JE"
        assert DEFAULT_PREFIXES[SequenceType.PURCHASE_ORDER] == "PO"
        assert DEFAULT_PREFIXES[SequenceType.SUPPLIER_INVOICE] == "SINV"
        assert DEFAULT_PREFIXES[SequenceType.ASSET] == "FA"
        assert DEFAULT_PREFIXES[SequenceType.LEASE] == "LS"
        assert DEFAULT_PREFIXES[SequenceType.GOODS_RECEIPT] == "GR"
        assert DEFAULT_PREFIXES[SequenceType.QUOTE] == "QT"
        assert DEFAULT_PREFIXES[SequenceType.SALES_ORDER] == "SO"
        assert DEFAULT_PREFIXES[SequenceType.SHIPMENT] == "SHP"
        assert DEFAULT_PREFIXES[SequenceType.EXPENSE] == "EXP"


class TestSyncNumberingServiceGetSequence:
    """Tests for SyncNumberingService.get_sequence method."""

    def test_get_sequence_exists(self, mock_db, org_id):
        """Test getting an existing sequence."""
        sequence = MockNumberingSequence(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.get_sequence(org_id, SequenceType.INVOICE)

        assert result == sequence

    def test_get_sequence_not_found(self, mock_db, org_id):
        """Test getting a non-existent sequence."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        service = SyncNumberingService(mock_db)
        result = service.get_sequence(org_id, SequenceType.INVOICE)

        assert result is None


class TestSyncNumberingServiceGetOrCreateSequence:
    """Tests for SyncNumberingService.get_or_create_sequence method."""

    def test_get_or_create_existing_sequence(self, mock_db, org_id):
        """Test getting an existing sequence doesn't create new one."""
        sequence = MockNumberingSequence(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.get_or_create_sequence(org_id, SequenceType.INVOICE)

        assert result == sequence
        mock_db.add.assert_not_called()

    @patch("app.services.finance.common.numbering.NumberingSequence")
    def test_get_or_create_new_sequence(self, mock_seq_class, mock_db, org_id):
        """Test creating a new sequence when none exists."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_seq_class.return_value = MockNumberingSequence(organization_id=org_id)

        service = SyncNumberingService(mock_db)
        result = service.get_or_create_sequence(org_id, SequenceType.INVOICE)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    @patch("app.services.finance.common.numbering.NumberingSequence")
    def test_get_or_create_uses_default_prefix(self, mock_seq_class, mock_db, org_id):
        """Test that new sequences use default prefixes."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        service = SyncNumberingService(mock_db)

        for seq_type in SequenceType:
            mock_seq_class.reset_mock()
            mock_db.reset_mock()
            mock_db.query.return_value.filter.return_value.first.return_value = None
            mock_seq_class.return_value = MockNumberingSequence(
                organization_id=org_id, sequence_type=seq_type
            )

            service.get_or_create_sequence(org_id, seq_type)

            # Verify default prefix was used
            call_kwargs = mock_seq_class.call_args[1]
            expected_prefix = DEFAULT_PREFIXES.get(seq_type, "DOC")
            assert call_kwargs["prefix"] == expected_prefix


class TestSyncNumberingServiceShouldReset:
    """Tests for SyncNumberingService._should_reset method."""

    def test_should_reset_never_frequency(self, mock_db):
        """Test sequence with NEVER reset frequency."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.NEVER,
            current_year=2024,
            current_month=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 6, 15))

        assert result is False

    def test_should_reset_no_current_year(self, mock_db):
        """Test sequence with no current year set (first use)."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.MONTHLY,
            current_year=None,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 1, 15))

        assert result is True

    def test_should_reset_yearly_same_year(self, mock_db):
        """Test yearly reset in same year."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.YEARLY,
            current_year=2025,
            current_month=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 6, 15))

        assert result is False

    def test_should_reset_yearly_different_year(self, mock_db):
        """Test yearly reset in different year."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.YEARLY,
            current_year=2024,
            current_month=12,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 1, 1))

        assert result is True

    def test_should_reset_monthly_same_month(self, mock_db):
        """Test monthly reset in same month."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.MONTHLY,
            current_year=2025,
            current_month=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 1, 15))

        assert result is False

    def test_should_reset_monthly_different_month(self, mock_db):
        """Test monthly reset in different month."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.MONTHLY,
            current_year=2025,
            current_month=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 2, 1))

        assert result is True

    def test_should_reset_monthly_different_year(self, mock_db):
        """Test monthly reset when year changes."""
        sequence = MockNumberingSequence(
            reset_frequency=ResetFrequency.MONTHLY,
            current_year=2024,
            current_month=12,
        )

        service = SyncNumberingService(mock_db)
        result = service._should_reset(sequence, date(2025, 1, 1))

        assert result is True


class TestSyncNumberingServiceFormatNumber:
    """Tests for SyncNumberingService._format_number method."""

    def test_format_number_full(self, mock_db):
        """Test formatting with prefix, year, month, and separator."""
        sequence = MockNumberingSequence(
            prefix="INV",
            suffix="",
            separator="-",
            min_digits=4,
            include_year=True,
            include_month=True,
            year_format=4,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV202501-0001"

    def test_format_number_two_digit_year(self, mock_db):
        """Test formatting with two-digit year."""
        sequence = MockNumberingSequence(
            prefix="INV",
            year_format=2,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV2501-0001"

    def test_format_number_no_year(self, mock_db):
        """Test formatting without year."""
        sequence = MockNumberingSequence(
            prefix="INV",
            include_year=False,
            include_month=True,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV01-0001"

    def test_format_number_no_month(self, mock_db):
        """Test formatting without month."""
        sequence = MockNumberingSequence(
            prefix="INV",
            include_year=True,
            include_month=False,
            year_format=4,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV2025-0001"

    def test_format_number_no_prefix(self, mock_db):
        """Test formatting without prefix."""
        sequence = MockNumberingSequence(
            prefix="",
            include_year=True,
            include_month=True,
            year_format=4,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "202501-0001"

    def test_format_number_with_suffix(self, mock_db):
        """Test formatting with suffix."""
        sequence = MockNumberingSequence(
            prefix="INV",
            suffix="-A",
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV202501-0001-A"

    def test_format_number_custom_separator(self, mock_db):
        """Test formatting with custom separator."""
        sequence = MockNumberingSequence(
            prefix="INV",
            separator="/",
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV202501/0001"

    def test_format_number_more_digits(self, mock_db):
        """Test formatting with more minimum digits."""
        sequence = MockNumberingSequence(
            prefix="INV",
            min_digits=6,
            current_number=1,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV202501-000001"

    def test_format_number_large_number(self, mock_db):
        """Test formatting with large sequence number."""
        sequence = MockNumberingSequence(
            prefix="INV",
            min_digits=4,
            current_number=12345,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "INV202501-12345"

    def test_format_number_minimal(self, mock_db):
        """Test minimal formatting (number only)."""
        sequence = MockNumberingSequence(
            prefix="",
            suffix="",
            include_year=False,
            include_month=False,
            current_number=42,
            min_digits=4,
        )

        service = SyncNumberingService(mock_db)
        result = service._format_number(sequence, date(2025, 1, 15))

        assert result == "0042"


class TestSyncNumberingServiceGenerateNextNumber:
    """Tests for SyncNumberingService.generate_next_number method."""

    def test_generate_next_number_first_time(self, mock_db, org_id):
        """Test generating first number (sequence reset)."""
        sequence = MockNumberingSequence(
            organization_id=org_id,
            current_number=0,
            current_year=None,
            reset_frequency=ResetFrequency.MONTHLY,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.generate_next_number(org_id, SequenceType.INVOICE)

        assert sequence.current_number == 1
        assert sequence.current_year == date.today().year
        assert sequence.current_month == date.today().month
        mock_db.flush.assert_called_once()

    def test_generate_next_number_increment(self, mock_db, org_id):
        """Test incrementing existing sequence."""
        today = date.today()
        sequence = MockNumberingSequence(
            organization_id=org_id,
            current_number=5,
            current_year=today.year,
            current_month=today.month,
            reset_frequency=ResetFrequency.MONTHLY,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.generate_next_number(org_id, SequenceType.INVOICE)

        assert sequence.current_number == 6
        assert "0006" in result

    def test_generate_next_number_with_reset(self, mock_db, org_id):
        """Test generating number with monthly reset."""
        sequence = MockNumberingSequence(
            organization_id=org_id,
            current_number=100,
            current_year=2024,
            current_month=12,
            reset_frequency=ResetFrequency.MONTHLY,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.generate_next_number(
            org_id, SequenceType.INVOICE, reference_date=date(2025, 1, 1)
        )

        assert sequence.current_number == 1
        assert sequence.current_year == 2025
        assert sequence.current_month == 1

    def test_generate_next_number_no_reset(self, mock_db, org_id):
        """Test generating number without reset (NEVER frequency)."""
        sequence = MockNumberingSequence(
            organization_id=org_id,
            current_number=100,
            current_year=2024,
            reset_frequency=ResetFrequency.NEVER,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        result = service.generate_next_number(
            org_id, SequenceType.INVOICE, reference_date=date(2025, 1, 1)
        )

        assert sequence.current_number == 101

    def test_generate_next_number_updates_last_used(self, mock_db, org_id):
        """Test that generate_next_number updates last_used_at."""
        today = date.today()
        sequence = MockNumberingSequence(
            organization_id=org_id,
            current_number=1,
            current_year=today.year,
            current_month=today.month,
            last_used_at=None,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = sequence

        service = SyncNumberingService(mock_db)
        service.generate_next_number(org_id, SequenceType.INVOICE)

        assert sequence.last_used_at is not None


class TestAsyncNumberingService:
    """Tests for async NumberingService."""

    @pytest.mark.asyncio
    async def test_get_sequence_async(self, mock_async_db, org_id):
        """Test async get_sequence method."""
        sequence = MockNumberingSequence(organization_id=org_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sequence
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.get_sequence(org_id, SequenceType.INVOICE)

        assert result == sequence

    @pytest.mark.asyncio
    async def test_get_sequence_by_id_async(self, mock_async_db):
        """Test async get_sequence_by_id method."""
        sequence_id = uuid4()
        sequence = MockNumberingSequence(sequence_id=sequence_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sequence
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.get_sequence_by_id(sequence_id)

        assert result == sequence

    @pytest.mark.asyncio
    async def test_get_all_sequences_async(self, mock_async_db, org_id):
        """Test async get_all_sequences method."""
        sequences = [MockNumberingSequence(), MockNumberingSequence()]

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = sequences
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.get_all_sequences(org_id)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_sequence_async(self, mock_async_db):
        """Test async update_sequence method."""
        sequence_id = uuid4()
        sequence = MockNumberingSequence(
            sequence_id=sequence_id,
            prefix="OLD",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sequence
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.update_sequence(
            sequence_id,
            prefix="NEW",
            min_digits=6,
        )

        assert result.prefix == "NEW"
        assert result.min_digits == 6
        mock_async_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_sequence_not_found(self, mock_async_db):
        """Test async update_sequence with non-existent sequence."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.update_sequence(uuid4(), prefix="NEW")

        assert result is None

    @pytest.mark.asyncio
    async def test_reset_sequence_counter_async(self, mock_async_db):
        """Test async reset_sequence_counter method."""
        sequence_id = uuid4()
        sequence = MockNumberingSequence(
            sequence_id=sequence_id,
            current_number=100,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sequence
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.reset_sequence_counter(sequence_id, new_value=50)

        assert result.current_number == 50
        mock_async_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_sequence_counter_not_found(self, mock_async_db):
        """Test async reset_sequence_counter with non-existent sequence."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)
        result = await service.reset_sequence_counter(uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_initialize_all_sequences_async(self, mock_async_db, org_id):
        """Test async initialize_all_sequences method."""
        # Mock get_or_create_sequence to return sequences
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_async_db.execute.return_value = mock_result

        service = NumberingService(mock_async_db)

        # Patch get_or_create_sequence
        with patch.object(
            service, "get_or_create_sequence", new_callable=AsyncMock
        ) as mock_get_or_create:
            mock_get_or_create.return_value = MockNumberingSequence()
            result = await service.initialize_all_sequences(org_id)

        # Should create one sequence per SequenceType
        assert mock_get_or_create.call_count == len(SequenceType)


class TestPreviewFormat:
    """Tests for preview_format method."""

    def test_preview_format(self, mock_db):
        """Test preview formatting."""
        sequence = MockNumberingSequence(
            prefix="INV",
            include_year=True,
            include_month=True,
            year_format=4,
            min_digits=4,
            separator="-",
        )

        service = NumberingService(mock_db)
        result = service.preview_format(sequence, sample_number=1)

        today = date.today()
        expected = f"INV{today.year}{today.month:02d}-0001"
        assert result == expected

    def test_preview_format_custom_number(self, mock_db):
        """Test preview formatting with custom sample number."""
        sequence = MockNumberingSequence(
            prefix="INV",
            min_digits=4,
        )

        service = NumberingService(mock_db)
        result = service.preview_format(sequence, sample_number=999)

        assert "0999" in result

    def test_preview_format_with_suffix(self, mock_db):
        """Test preview formatting with suffix."""
        sequence = MockNumberingSequence(
            prefix="QT",
            suffix="-DRAFT",
            min_digits=4,
        )

        service = NumberingService(mock_db)
        result = service.preview_format(sequence, sample_number=1)

        assert result.endswith("-DRAFT")
