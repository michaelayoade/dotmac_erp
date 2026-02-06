"""
Tests for ReversalService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.gl.reversal import (
    ReversalService,
    ReversalResult,
)


from app.models.finance.gl.journal_entry import JournalStatus, JournalType

MockJournalStatus = JournalStatus
MockJournalType = JournalType


class MockFiscalPeriod:
    """Mock fiscal period."""

    def __init__(self, fiscal_period_id=None, fiscal_year_id=None):
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid4()


class MockPeriodGuardResult:
    """Mock period guard result."""

    def __init__(self, is_allowed=True, message=""):
        self.is_allowed = is_allowed
        self.message = message


class MockJournalEntry:
    """Mock journal entry model."""

    def __init__(
        self,
        journal_entry_id=None,
        organization_id=None,
        journal_number="JE-0001",
        journal_type=None,
        entry_date=None,
        posting_date=None,
        fiscal_period_id=None,
        description="Test journal",
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        exchange_rate_type_id=None,
        total_debit=Decimal("1000.00"),
        total_credit=Decimal("1000.00"),
        total_debit_functional=Decimal("1000.00"),
        total_credit_functional=Decimal("1000.00"),
        status=None,
        is_reversal=False,
        reversal_journal_id=None,
        reversed_journal_id=None,
        source_module="GL",
        source_document_type=None,
        source_document_id=None,
        correlation_id=None,
        created_by_user_id=None,
    ):
        self.journal_entry_id = journal_entry_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.journal_number = journal_number
        self.journal_type = journal_type or MockJournalType.STANDARD
        self.entry_date = entry_date or date.today()
        self.posting_date = posting_date or date.today()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.description = description
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.exchange_rate_type_id = exchange_rate_type_id
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.total_debit_functional = total_debit_functional
        self.total_credit_functional = total_credit_functional
        self.status = status or MockJournalStatus.POSTED
        self.is_reversal = is_reversal
        self.reversal_journal_id = reversal_journal_id
        self.reversed_journal_id = reversed_journal_id
        self.source_module = source_module
        self.source_document_type = source_document_type
        self.source_document_id = source_document_id
        self.correlation_id = correlation_id
        self.created_by_user_id = created_by_user_id


class MockJournalEntryLine:
    """Mock journal entry line."""

    def __init__(
        self,
        journal_entry_line_id=None,
        journal_entry_id=None,
        line_number=1,
        account_id=None,
        description="Line",
        debit_amount=Decimal("0"),
        credit_amount=Decimal("0"),
        debit_amount_functional=Decimal("0"),
        credit_amount_functional=Decimal("0"),
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        business_unit_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
    ):
        self.journal_entry_line_id = journal_entry_line_id or uuid4()
        self.journal_entry_id = journal_entry_id or uuid4()
        self.line_number = line_number
        self.account_id = account_id or uuid4()
        self.description = description
        self.debit_amount = debit_amount
        self.credit_amount = credit_amount
        self.debit_amount_functional = debit_amount_functional
        self.credit_amount_functional = credit_amount_functional
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.business_unit_id = business_unit_id
        self.cost_center_id = cost_center_id
        self.project_id = project_id
        self.segment_id = segment_id


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
def posted_journal(org_id):
    """Create a posted journal for testing."""
    return MockJournalEntry(
        organization_id=org_id,
        status=MockJournalStatus.POSTED,
        total_debit=Decimal("1000.00"),
        total_credit=Decimal("1000.00"),
    )


@pytest.fixture
def journal_lines():
    """Create journal lines for testing."""
    return [
        MockJournalEntryLine(
            line_number=1,
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0"),
            debit_amount_functional=Decimal("1000.00"),
            credit_amount_functional=Decimal("0"),
        ),
        MockJournalEntryLine(
            line_number=2,
            debit_amount=Decimal("0"),
            credit_amount=Decimal("1000.00"),
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=Decimal("1000.00"),
        ),
    ]


class TestReversalResult:
    """Tests for ReversalResult dataclass."""

    def test_successful_result(self):
        """Test creating successful reversal result."""
        reversal_id = uuid4()
        result = ReversalResult(
            success=True,
            reversal_journal_id=reversal_id,
            reversal_journal_number="REV-0001",
            message="Reversal created successfully",
        )

        assert result.success is True
        assert result.reversal_journal_id == reversal_id
        assert result.reversal_journal_number == "REV-0001"

    def test_failed_result(self):
        """Test creating failed reversal result."""
        result = ReversalResult(success=False, message="Journal already reversed")

        assert result.success is False
        assert result.reversal_journal_id is None


class TestCreateReversal:
    """Tests for create_reversal method."""

    def test_successful_reversal(
        self, mock_db, org_id, user_id, posted_journal, journal_lines
    ):
        """Test successfully creating a reversal entry."""
        mock_db.get.return_value = posted_journal
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = journal_lines

        period = MockFiscalPeriod()
        period_result = MockPeriodGuardResult(is_allowed=True)

        with patch("app.services.finance.gl.reversal.JournalEntry") as mock_je:
            with patch("app.services.finance.gl.reversal.JournalEntryLine"):
                with patch(
                    "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
                ):
                    with patch(
                        "app.services.finance.gl.reversal.JournalType", MockJournalType
                    ):
                        with patch(
                            "app.services.finance.gl.reversal.PeriodGuardService.get_period_for_date"
                        ) as mock_period:
                            mock_period.return_value = period
                            with patch(
                                "app.services.finance.gl.reversal.SequenceService.get_next_number"
                            ) as mock_seq:
                                mock_seq.return_value = "REV-0001"

                                result = ReversalService.create_reversal(
                                    mock_db,
                                    org_id,
                                    posted_journal.journal_entry_id,
                                    date.today(),
                                    user_id,
                                    "Error correction",
                                )

        assert result.success is True
        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_reversal_journal_not_found(self, mock_db, org_id, user_id):
        """Test reversal fails when journal not found."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                ReversalService.create_reversal(
                    mock_db, org_id, uuid4(), date.today(), user_id, "Test"
                )

        assert exc.value.status_code == 404

    def test_reversal_wrong_org(self, mock_db, org_id, user_id):
        """Test reversal fails when journal is from different org."""
        from fastapi import HTTPException

        wrong_org_journal = MockJournalEntry(organization_id=uuid4())
        mock_db.get.return_value = wrong_org_journal

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                ReversalService.create_reversal(
                    mock_db,
                    org_id,
                    wrong_org_journal.journal_entry_id,
                    date.today(),
                    user_id,
                    "Test",
                )

        assert exc.value.status_code == 404

    def test_reversal_not_posted_fails(self, mock_db, org_id, user_id):
        """Test reversal fails when journal not posted."""
        from fastapi import HTTPException

        draft_journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.DRAFT
        )
        mock_db.get.return_value = draft_journal

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    ReversalService.create_reversal(
                        mock_db,
                        org_id,
                        draft_journal.journal_entry_id,
                        date.today(),
                        user_id,
                        "Test",
                    )

        assert exc.value.status_code == 400
        assert "status" in exc.value.detail.lower()

    def test_reversal_already_reversed_fails(self, mock_db, org_id, user_id):
        """Test reversal fails when journal already reversed."""
        from fastapi import HTTPException

        already_reversed = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.POSTED,
            reversal_journal_id=uuid4(),  # Has reversal
        )
        mock_db.get.return_value = already_reversed

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    ReversalService.create_reversal(
                        mock_db,
                        org_id,
                        already_reversed.journal_entry_id,
                        date.today(),
                        user_id,
                        "Test",
                    )

        assert exc.value.status_code == 400
        assert "already" in exc.value.detail.lower()

    def test_reversal_no_period_fails(self, mock_db, org_id, user_id, posted_journal):
        """Test reversal fails when no fiscal period for date."""
        from fastapi import HTTPException

        mock_db.get.return_value = posted_journal

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.reversal.PeriodGuardService.get_period_for_date"
                ) as mock_period:
                    mock_period.return_value = None

                    with pytest.raises(HTTPException) as exc:
                        ReversalService.create_reversal(
                            mock_db,
                            org_id,
                            posted_journal.journal_entry_id,
                            date.today(),
                            user_id,
                            "Test",
                        )

        assert exc.value.status_code == 400
        assert "fiscal period" in exc.value.detail.lower()


class TestCanReverse:
    """Tests for can_reverse method."""

    def test_can_reverse_posted_journal(self, mock_db, org_id, posted_journal):
        """Test that posted journal can be reversed."""
        mock_db.get.return_value = posted_journal

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                can, reason = ReversalService.can_reverse(
                    mock_db, org_id, posted_journal.journal_entry_id
                )

        assert can is True
        assert "can be reversed" in reason.lower()

    def test_cannot_reverse_not_found(self, mock_db, org_id):
        """Test that non-existent journal cannot be reversed."""
        mock_db.get.return_value = None

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            can, reason = ReversalService.can_reverse(mock_db, org_id, uuid4())

        assert can is False
        assert "not found" in reason.lower()

    def test_cannot_reverse_draft(self, mock_db, org_id):
        """Test that draft journal cannot be reversed."""
        draft = MockJournalEntry(organization_id=org_id, status=MockJournalStatus.DRAFT)
        mock_db.get.return_value = draft

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                can, reason = ReversalService.can_reverse(
                    mock_db, org_id, draft.journal_entry_id
                )

        assert can is False
        assert "status" in reason.lower()

    def test_cannot_reverse_already_reversed(self, mock_db, org_id):
        """Test that already reversed journal cannot be reversed again."""
        already_reversed = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.POSTED,
            reversal_journal_id=uuid4(),
        )
        mock_db.get.return_value = already_reversed

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            with patch(
                "app.services.finance.gl.reversal.JournalStatus", MockJournalStatus
            ):
                can, reason = ReversalService.can_reverse(
                    mock_db, org_id, already_reversed.journal_entry_id
                )

        assert can is False
        assert "already" in reason.lower()


class TestGetReversalForJournal:
    """Tests for get_reversal_for_journal method."""

    def test_get_existing_reversal(self, mock_db, org_id):
        """Test getting reversal for a journal that has one."""
        reversal_id = uuid4()
        original = MockJournalEntry(
            organization_id=org_id,
            reversal_journal_id=reversal_id,
        )
        reversal = MockJournalEntry(
            journal_entry_id=reversal_id,
            organization_id=org_id,
            is_reversal=True,
        )

        mock_db.get.side_effect = [original, reversal]

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            result = ReversalService.get_reversal_for_journal(
                mock_db, org_id, original.journal_entry_id
            )

        assert result == reversal

    def test_no_reversal_returns_none(self, mock_db, org_id):
        """Test that journal without reversal returns None."""
        original = MockJournalEntry(
            organization_id=org_id,
            reversal_journal_id=None,  # No reversal
        )
        mock_db.get.return_value = original

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            result = ReversalService.get_reversal_for_journal(
                mock_db, org_id, original.journal_entry_id
            )

        assert result is None

    def test_journal_not_found_returns_none(self, mock_db, org_id):
        """Test that non-existent journal returns None."""
        mock_db.get.return_value = None

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            result = ReversalService.get_reversal_for_journal(mock_db, org_id, uuid4())

        assert result is None


class TestListReversals:
    """Tests for list method."""

    def test_list_reversals(self, mock_db, org_id):
        """Test listing reversal journals."""
        reversals = [
            MockJournalEntry(organization_id=org_id, is_reversal=True),
            MockJournalEntry(organization_id=org_id, is_reversal=True),
        ]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = reversals
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            result = ReversalService.list(
                mock_db, organization_id=str(org_id), limit=50, offset=0
            )

        assert result == reversals
        assert len(result) == 2

    def test_list_by_original_journal(self, mock_db, org_id):
        """Test listing reversals filtered by original journal."""
        original_id = uuid4()
        reversal = MockJournalEntry(
            organization_id=org_id,
            is_reversal=True,
            reversed_journal_id=original_id,
        )
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [reversal]
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.reversal.JournalEntry"):
            result = ReversalService.list(
                mock_db,
                organization_id=str(org_id),
                original_journal_id=str(original_id),
            )

        assert len(result) == 1
        assert result[0].reversed_journal_id == original_id
