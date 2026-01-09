"""
Tests for JournalService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ifrs.gl.journal import (
    JournalService,
    JournalInput,
    JournalLineInput,
)


class MockEnumValue:
    """A mock enum value with .value attribute."""
    def __init__(self, value: str):
        self.value = value

    def __eq__(self, other):
        if isinstance(other, MockEnumValue):
            return self.value == other.value
        return self.value == other

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return f"MockEnumValue({self.value})"


class MockJournalStatus:
    """Mock journal status enum."""
    DRAFT = MockEnumValue("DRAFT")
    SUBMITTED = MockEnumValue("SUBMITTED")
    APPROVED = MockEnumValue("APPROVED")
    POSTED = MockEnumValue("POSTED")
    VOID = MockEnumValue("VOID")
    REVERSED = MockEnumValue("REVERSED")


class MockJournalType:
    """Mock journal type enum."""
    STANDARD = MockEnumValue("STANDARD")
    ADJUSTING = MockEnumValue("ADJUSTING")
    CLOSING = MockEnumValue("CLOSING")
    REVERSAL = MockEnumValue("REVERSAL")


class MockFiscalPeriod:
    """Mock fiscal period."""

    def __init__(self, fiscal_period_id=None, fiscal_year_id=None):
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid4()


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
        reference=None,
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        total_debit=Decimal("0"),
        total_credit=Decimal("0"),
        total_debit_functional=Decimal("0"),
        total_credit_functional=Decimal("0"),
        status=None,
        created_by_user_id=None,
        submitted_by_user_id=None,
        approved_by_user_id=None,
        posted_by_user_id=None,
    ):
        self.journal_entry_id = journal_entry_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.journal_number = journal_number
        self.journal_type = journal_type or MockJournalType.STANDARD
        self.entry_date = entry_date or date.today()
        self.posting_date = posting_date or date.today()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.description = description
        self.reference = reference
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.total_debit_functional = total_debit_functional
        self.total_credit_functional = total_credit_functional
        self.status = status or MockJournalStatus.DRAFT
        self.created_by_user_id = created_by_user_id
        self.submitted_by_user_id = submitted_by_user_id
        self.approved_by_user_id = approved_by_user_id
        self.posted_by_user_id = posted_by_user_id


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
def sample_lines():
    """Create sample journal line inputs."""
    return [
        JournalLineInput(
            account_id=uuid4(),
            description="Debit line",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0"),
        ),
        JournalLineInput(
            account_id=uuid4(),
            description="Credit line",
            debit_amount=Decimal("0"),
            credit_amount=Decimal("1000.00"),
        ),
    ]


class TestJournalLineInput:
    """Tests for JournalLineInput dataclass."""

    def test_debit_line(self):
        """Test creating debit line input."""
        account_id = uuid4()
        line = JournalLineInput(
            account_id=account_id,
            description="Debit",
            debit_amount=Decimal("100.00"),
            credit_amount=Decimal("0"),
        )

        assert line.account_id == account_id
        assert line.debit_amount == Decimal("100.00")
        assert line.credit_amount == Decimal("0")

    def test_line_with_dimensions(self):
        """Test creating line with analytical dimensions."""
        account_id = uuid4()
        business_unit = uuid4()
        cost_center = uuid4()

        line = JournalLineInput(
            account_id=account_id,
            description="With dimensions",
            debit_amount=Decimal("500.00"),
            credit_amount=Decimal("0"),
            business_unit_id=business_unit,
            cost_center_id=cost_center,
        )

        assert line.business_unit_id == business_unit
        assert line.cost_center_id == cost_center


class TestCreateJournal:
    """Tests for create_journal method."""

    def test_unbalanced_journal_fails(self, mock_db, org_id, user_id):
        """Test that unbalanced journal fails validation."""
        from fastapi import HTTPException
        from app.models.ifrs.gl.journal_entry import JournalType

        unbalanced_lines = [
            JournalLineInput(
                account_id=uuid4(),
                description="Debit",
                debit_amount=Decimal("1000.00"),
                credit_amount=Decimal("0"),
            ),
            JournalLineInput(
                account_id=uuid4(),
                description="Credit",
                debit_amount=Decimal("0"),
                credit_amount=Decimal("500.00"),  # Not balanced!
            ),
        ]

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Unbalanced",
            currency_code="USD",
            lines=unbalanced_lines,
        )

        period = MockFiscalPeriod()

        with patch(
            "app.services.ifrs.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with pytest.raises(HTTPException) as exc:
                JournalService.create_journal(mock_db, org_id, journal_input, user_id)

        assert exc.value.status_code == 400
        assert "balance" in exc.value.detail.lower()

    def test_no_fiscal_period_fails(self, mock_db, org_id, user_id, sample_lines):
        """Test that missing fiscal period fails."""
        from fastapi import HTTPException
        from app.models.ifrs.gl.journal_entry import JournalType

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Test",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch(
            "app.services.ifrs.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = None

            with pytest.raises(HTTPException) as exc:
                JournalService.create_journal(mock_db, org_id, journal_input, user_id)

        assert exc.value.status_code == 400
        assert "fiscal period" in exc.value.detail.lower()

    def test_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test that journal with no lines fails."""
        from fastapi import HTTPException
        from app.models.ifrs.gl.journal_entry import JournalType

        empty_journal = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Empty",
            currency_code="USD",
            lines=[],
        )

        with pytest.raises(HTTPException) as exc:
            JournalService.create_journal(mock_db, org_id, empty_journal, user_id)

        assert exc.value.status_code == 400


class TestSubmitJournal:
    """Tests for submit_journal method."""

    def test_submit_draft_journal(self, mock_db, org_id, user_id):
        """Test submitting a draft journal."""
        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.DRAFT
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                result = JournalService.submit_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert result.status == MockJournalStatus.SUBMITTED
        mock_db.commit.assert_called()

    def test_submit_non_draft_fails(self, mock_db, org_id, user_id):
        """Test submitting non-draft journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.APPROVED
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                with pytest.raises(HTTPException) as exc:
                    JournalService.submit_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id
                    )

        assert exc.value.status_code == 400


class TestApproveJournal:
    """Tests for approve_journal method."""

    def test_approve_submitted_journal(self, mock_db, org_id, user_id):
        """Test approving a submitted journal."""
        submitter_id = uuid4()
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                result = JournalService.approve_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert result.status == MockJournalStatus.APPROVED
        mock_db.commit.assert_called()

    def test_approve_not_submitted_fails(self, mock_db, org_id, user_id):
        """Test approving non-submitted journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.DRAFT
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                with pytest.raises(HTTPException) as exc:
                    JournalService.approve_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id
                    )

        assert exc.value.status_code == 400

    def test_self_approval_fails_sod(self, mock_db, org_id):
        """Test that self-approval fails segregation of duties check."""
        from fastapi import HTTPException

        creator_id = uuid4()
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.SUBMITTED,
            created_by_user_id=creator_id,
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                with pytest.raises(HTTPException) as exc:
                    # Same user who created tries to approve
                    JournalService.approve_journal(
                        mock_db, org_id, journal.journal_entry_id, creator_id
                    )

        assert exc.value.status_code == 403
        assert "segregation" in exc.value.detail.lower() or "creator" in exc.value.detail.lower()


class TestVoidJournal:
    """Tests for void_journal method."""

    def test_void_draft_journal(self, mock_db, org_id, user_id):
        """Test voiding a draft journal."""
        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.DRAFT
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                result = JournalService.void_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id, "Not needed"
                )

        assert result.status == MockJournalStatus.VOID
        mock_db.commit.assert_called()

    def test_void_posted_journal_fails(self, mock_db, org_id, user_id):
        """Test that voiding posted journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.POSTED
        )
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                with pytest.raises(HTTPException) as exc:
                    JournalService.void_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id, "Mistake"
                    )

        assert exc.value.status_code == 400


class TestGetJournal:
    """Tests for get method."""

    def test_get_existing_journal(self, mock_db, org_id):
        """Test getting existing journal."""
        journal = MockJournalEntry(organization_id=org_id)
        mock_db.get.return_value = journal

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            result = JournalService.get(mock_db, str(journal.journal_entry_id))

        assert result == journal

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent journal raises exception."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListJournals:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing journals with filters."""
        journals = [MockJournalEntry(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        with patch("app.services.ifrs.gl.journal.JournalEntry"):
            with patch("app.services.ifrs.gl.journal.JournalStatus", MockJournalStatus):
                result = JournalService.list(
                    mock_db,
                    organization_id=str(org_id),
                    status=MockJournalStatus.DRAFT,
                )

        assert result == journals


class TestGetJournalLines:
    """Tests for get_lines method."""

    def test_get_journal_lines(self, mock_db, org_id):
        """Test getting lines for a journal."""
        journal_id = uuid4()
        lines = [MagicMock(), MagicMock()]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = lines

        with patch("app.services.ifrs.gl.journal.JournalEntryLine"):
            result = JournalService.get_lines(mock_db, str(journal_id))

        assert result == lines
