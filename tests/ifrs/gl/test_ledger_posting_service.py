"""
Tests for LedgerPostingService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.ifrs.gl.journal_entry import JournalStatus
from app.models.ifrs.gl.posting_batch import BatchStatus
from app.services.ifrs.gl.ledger_posting import (
    LedgerPostingService,
    PostingRequest,
    PostingEntry,
    PostingResult,
)

class MockJournalEntry:
    """Mock journal entry model."""

    def __init__(
        self,
        journal_entry_id=None,
        organization_id=None,
        status=None,
        entry_date=None,
        posting_date=None,
        description="Test journal",
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        total_debit=Decimal("1000.00"),
        total_credit=Decimal("1000.00"),
        total_debit_functional=Decimal("1000.00"),
        total_credit_functional=Decimal("1000.00"),
        source_module="GL",
        source_document_type=None,
        source_document_id=None,
        correlation_id=None,
    ):
        self.journal_entry_id = journal_entry_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.status = status or JournalStatus.APPROVED
        self.entry_date = entry_date or date.today()
        self.posting_date = posting_date or date.today()
        self.description = description
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.total_debit_functional = total_debit_functional
        self.total_credit_functional = total_credit_functional
        self.source_module = source_module
        self.source_document_type = source_document_type
        self.source_document_id = source_document_id
        self.correlation_id = correlation_id


class MockPostingBatch:
    """Mock posting batch."""

    def __init__(
        self,
        batch_id=None,
        status=None,
        posted_entries=0,
        correlation_id=None,
    ):
        self.batch_id = batch_id or uuid4()
        self.status = status or BatchStatus.PENDING
        self.posted_entries = posted_entries
        self.correlation_id = correlation_id


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def journal_id():
    """Create test journal ID."""
    return uuid4()


@pytest.fixture
def posting_request(org_id, journal_id, user_id):
    """Create a test posting request."""
    return PostingRequest(
        organization_id=org_id,
        journal_entry_id=journal_id,
        posting_date=date.today(),
        idempotency_key=f"{org_id}:GL:{journal_id}:post:v1",
        source_module="GL",
        posted_by_user_id=user_id,
    )


class TestPostJournalEntry:
    """Tests for post_journal_entry method."""

    def test_idempotent_response_returned(self, mock_db, posting_request):
        """Test that cached idempotent response is returned."""
        mock_batch = MockPostingBatch(
            status=BatchStatus.POSTED,
            posted_entries=3,
            correlation_id="corr-123",
        )
        mock_db.query.return_value.filter.return_value.first.return_value = mock_batch

        result = LedgerPostingService.post_journal_entry(mock_db, posting_request)

        assert result.success is True
        assert result.message == "Already posted (idempotent replay)"
        assert result.batch_id == mock_batch.batch_id
        assert result.posted_lines == mock_batch.posted_entries
        mock_db.get.assert_not_called()

    def test_journal_not_found_raises(self, mock_db, posting_request):
        """Test posting non-existent journal fails."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            LedgerPostingService.post_journal_entry(mock_db, posting_request)

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    def test_wrong_organization_raises(self, mock_db, posting_request):
        """Test posting journal from different org fails."""
        wrong_org_journal = MockJournalEntry(organization_id=uuid4())
        mock_db.query.return_value.filter.return_value.first.return_value = None

        mock_db.get.return_value = wrong_org_journal

        with pytest.raises(HTTPException) as exc:
            LedgerPostingService.post_journal_entry(mock_db, posting_request)

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    def test_already_posted_raises(self, mock_db, posting_request, org_id):
        """Test posting already posted journal fails."""
        posted_journal = MockJournalEntry(
            organization_id=org_id, status=JournalStatus.POSTED
        )
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = posted_journal

        with pytest.raises(HTTPException) as exc:
            LedgerPostingService.post_journal_entry(mock_db, posting_request)

        assert exc.value.status_code == 400
        assert "already posted" in exc.value.detail.lower()

    def test_period_guard_blocks_posting(self, mock_db, posting_request, org_id):
        """Test that closed period blocks posting."""
        journal = MockJournalEntry(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = journal

        with patch(
            "app.services.ifrs.gl.ledger_posting.PeriodGuardService.require_open_period",
            side_effect=HTTPException(status_code=400, detail="Period is closed"),
        ):
            with pytest.raises(HTTPException) as exc:
                LedgerPostingService.post_journal_entry(mock_db, posting_request)

        assert exc.value.status_code == 400
        assert "closed" in exc.value.detail.lower()


class TestPostingResult:
    """Tests for PostingResult dataclass."""

    def test_successful_result(self):
        """Test creating successful posting result."""
        batch_id = uuid4()
        result = PostingResult(
            success=True,
            batch_id=batch_id,
            posted_lines=5,
            total_debit=Decimal("10.00"),
            total_credit=Decimal("10.00"),
            message="Posted successfully",
        )

        assert result.success is True
        assert result.batch_id == batch_id
        assert result.posted_lines == 5

    def test_failed_result(self):
        """Test creating failed posting result."""
        result = PostingResult(success=False, message="Period is closed")

        assert result.success is False
        assert result.batch_id is None
        assert "Period is closed" in result.message


class TestPostingRequest:
    """Tests for PostingRequest dataclass."""

    def test_minimal_request(self, org_id, journal_id, user_id):
        """Test creating minimal posting request."""
        request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal_id,
            posting_date=date.today(),
            idempotency_key="test-key",
            source_module="GL",
            posted_by_user_id=user_id,
        )

        assert request.organization_id == org_id
        assert request.journal_entry_id == journal_id
        assert request.allow_adjustment_period is False
        assert request.reopen_session_id is None

    def test_full_request(self, org_id, journal_id, user_id):
        """Test creating full posting request with all options."""
        correlation_id = uuid4()
        reopen_session_id = uuid4()

        request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal_id,
            posting_date=date.today(),
            idempotency_key="test-key",
            source_module="AP",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            allow_adjustment_period=True,
            reopen_session_id=reopen_session_id,
        )

        assert request.source_module == "AP"
        assert request.correlation_id == correlation_id
        assert request.allow_adjustment_period is True
        assert request.reopen_session_id == reopen_session_id


class TestPostingEntry:
    """Tests for PostingEntry dataclass."""

    def test_debit_entry(self):
        """Test creating debit posting entry."""
        account_id = uuid4()
        entry = PostingEntry(
            account_id=account_id,
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0"),
            debit_amount_functional=Decimal("1000.00"),
            credit_amount_functional=Decimal("0"),
            original_currency_code="USD",
            exchange_rate=Decimal("1.0"),
        )

        assert entry.account_id == account_id
        assert entry.debit_amount == Decimal("1000.00")
        assert entry.credit_amount == Decimal("0")
        assert entry.original_currency_code == "USD"

    def test_credit_entry_with_dimensions(self):
        """Test creating credit entry with analytical dimensions."""
        account_id = uuid4()
        business_unit_id = uuid4()
        cost_center_id = uuid4()
        project_id = uuid4()
        segment_id = uuid4()

        entry = PostingEntry(
            account_id=account_id,
            debit_amount=Decimal("0"),
            credit_amount=Decimal("500.00"),
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=Decimal("500.00"),
            original_currency_code="EUR",
            exchange_rate=Decimal("1.1"),
            business_unit_id=business_unit_id,
            cost_center_id=cost_center_id,
            project_id=project_id,
            segment_id=segment_id,
        )

        assert entry.business_unit_id == business_unit_id
        assert entry.cost_center_id == cost_center_id
        assert entry.project_id == project_id
        assert entry.segment_id == segment_id


class TestGetBatch:
    """Tests for get_batch method."""

    def test_get_existing_batch(self, mock_db):
        """Test getting existing posting batch."""
        batch_id = uuid4()
        batch = MockPostingBatch(batch_id=batch_id)
        mock_db.get.return_value = batch

        with patch("app.services.ifrs.gl.ledger_posting.PostingBatch"):
            result = LedgerPostingService.get_batch(mock_db, batch_id)

        assert result == batch

    def test_get_nonexistent_batch_raises(self, mock_db):
        """Test getting non-existent batch raises exception."""
        mock_db.get.return_value = None
        batch_id = uuid4()

        with patch("app.services.ifrs.gl.ledger_posting.PostingBatch"):
            with pytest.raises(HTTPException) as exc:
                LedgerPostingService.get_batch(mock_db, batch_id)

        assert exc.value.status_code == 404


class TestListPostingBatches:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing posting batches with filters."""
        batches = [MockPostingBatch(), MockPostingBatch()]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = batches
        mock_db.query.return_value = mock_query

        with patch("app.services.ifrs.gl.ledger_posting.PostingBatch"):
            result = LedgerPostingService.list(
                mock_db, organization_id=str(org_id), limit=50, offset=0
            )

        assert result == batches
        mock_query.filter.assert_called()


class TestGetLedgerLines:
    """Tests for get_ledger_lines method."""

    def test_get_lines_for_batch(self, mock_db, org_id):
        """Test getting ledger lines for a batch."""
        batch_id = uuid4()
        lines = [MagicMock(), MagicMock()]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = lines
        mock_db.query.return_value = mock_query

        with patch("app.services.ifrs.gl.ledger_posting.PostedLedgerLine"):
            result = LedgerPostingService.get_ledger_lines(
                mock_db, org_id, posting_batch_id=batch_id
            )

        assert result == lines
