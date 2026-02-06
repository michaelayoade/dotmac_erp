"""
Tests for JournalService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.gl.journal import (
    JournalService,
    JournalInput,
    JournalLineInput,
)


from app.models.finance.gl.journal_entry import JournalStatus, JournalType

MockJournalStatus = JournalStatus
MockJournalType = JournalType


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
        source_module=None,
        correlation_id=None,
        reversal_journal_id=None,
        lines=None,
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
        self.source_module = source_module
        self.correlation_id = correlation_id
        self.reversal_journal_id = reversal_journal_id
        self.lines = lines or []


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
        from app.models.finance.gl.journal_entry import JournalType

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
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with pytest.raises(HTTPException) as exc:
                JournalService.create_journal(mock_db, org_id, journal_input, user_id)

        assert exc.value.status_code == 400
        assert "balance" in exc.value.detail.lower()

    def test_no_fiscal_period_fails(self, mock_db, org_id, user_id, sample_lines):
        """Test that missing fiscal period fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Test",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = None

            with pytest.raises(HTTPException) as exc:
                JournalService.create_journal(mock_db, org_id, journal_input, user_id)

        assert exc.value.status_code == 400
        assert "fiscal period" in exc.value.detail.lower()

    def test_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test that journal with no lines fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    # Same user who created tries to approve
                    JournalService.approve_journal(
                        mock_db, org_id, journal.journal_entry_id, creator_id
                    )

        assert exc.value.status_code == 403
        assert (
            "segregation" in exc.value.detail.lower()
            or "creator" in exc.value.detail.lower()
        )


class TestVoidJournal:
    """Tests for void_journal method."""

    def test_void_draft_journal(self, mock_db, org_id, user_id):
        """Test voiding a draft journal."""
        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.DRAFT
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            result = JournalService.get(mock_db, str(journal.journal_entry_id))

        assert result == journal

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent journal raises exception."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
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

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
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

        with patch("app.services.finance.gl.journal.JournalEntryLine"):
            result = JournalService.get_lines(mock_db, str(journal_id))

        assert result == lines


class TestCreateJournalSuccess:
    """Tests for create_journal success path."""

    def test_create_journal_success(self, mock_db, org_id, user_id, sample_lines):
        """Test successful journal creation."""
        from app.models.finance.gl.journal_entry import JournalType

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Test journal",
            currency_code="USD",
            lines=sample_lines,
        )

        period = MockFiscalPeriod()

        with patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with patch(
                "app.services.finance.gl.journal.SequenceService.get_next_number"
            ) as mock_seq:
                mock_seq.return_value = "JE-0001"
                with patch("app.services.finance.gl.journal.JournalEntry") as MockJE:
                    mock_journal = MockJournalEntry(
                        organization_id=org_id,
                        journal_entry_id=uuid4(),
                        status=MockJournalStatus.DRAFT,
                    )
                    MockJE.return_value = mock_journal
                    with patch("app.services.finance.gl.journal.JournalEntryLine"):
                        result = JournalService.create_journal(
                            mock_db, org_id, journal_input, user_id
                        )

        mock_db.add.assert_called()
        mock_db.flush.assert_called()
        mock_db.commit.assert_called()

    def test_create_journal_with_dimensions(self, mock_db, org_id, user_id):
        """Test creating journal with analytical dimensions."""
        from app.models.finance.gl.journal_entry import JournalType

        business_unit = uuid4()
        cost_center = uuid4()
        project = uuid4()

        lines_with_dims = [
            JournalLineInput(
                account_id=uuid4(),
                description="Debit line",
                debit_amount=Decimal("1000.00"),
                credit_amount=Decimal("0"),
                business_unit_id=business_unit,
                cost_center_id=cost_center,
                project_id=project,
            ),
            JournalLineInput(
                account_id=uuid4(),
                description="Credit line",
                debit_amount=Decimal("0"),
                credit_amount=Decimal("1000.00"),
                business_unit_id=business_unit,
                cost_center_id=cost_center,
            ),
        ]

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="With dimensions",
            currency_code="USD",
            lines=lines_with_dims,
        )

        period = MockFiscalPeriod()

        with patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with patch(
                "app.services.finance.gl.journal.SequenceService.get_next_number"
            ) as mock_seq:
                mock_seq.return_value = "JE-0002"
                with patch("app.services.finance.gl.journal.JournalEntry") as MockJE:
                    mock_journal = MockJournalEntry(
                        organization_id=org_id,
                        status=MockJournalStatus.DRAFT,
                    )
                    MockJE.return_value = mock_journal
                    with patch("app.services.finance.gl.journal.JournalEntryLine"):
                        result = JournalService.create_journal(
                            mock_db, org_id, journal_input, user_id
                        )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_journal_with_exchange_rate(self, mock_db, org_id, user_id):
        """Test creating journal with foreign currency exchange rate."""
        from app.models.finance.gl.journal_entry import JournalType

        lines = [
            JournalLineInput(
                account_id=uuid4(),
                description="Debit",
                debit_amount=Decimal("100.00"),
                credit_amount=Decimal("0"),
            ),
            JournalLineInput(
                account_id=uuid4(),
                description="Credit",
                debit_amount=Decimal("0"),
                credit_amount=Decimal("100.00"),
            ),
        ]

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Foreign currency",
            currency_code="EUR",
            exchange_rate=Decimal("1.10"),
            lines=lines,
        )

        period = MockFiscalPeriod()

        with patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with patch(
                "app.services.finance.gl.journal.SequenceService.get_next_number"
            ) as mock_seq:
                mock_seq.return_value = "JE-0003"
                with patch("app.services.finance.gl.journal.JournalEntry") as MockJE:
                    mock_journal = MockJournalEntry(
                        organization_id=org_id,
                        status=MockJournalStatus.DRAFT,
                    )
                    MockJE.return_value = mock_journal
                    with patch("app.services.finance.gl.journal.JournalEntryLine"):
                        result = JournalService.create_journal(
                            mock_db, org_id, journal_input, user_id
                        )

        mock_db.commit.assert_called()


class TestUpdateJournal:
    """Tests for update_journal method."""

    def test_update_draft_journal_success(self, mock_db, org_id, user_id, sample_lines):
        """Test updating a draft journal successfully."""
        from app.models.finance.gl.journal_entry import JournalType

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal
        mock_db.query.return_value.filter.return_value.delete.return_value = 2

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Updated description",
            currency_code="USD",
            lines=sample_lines,
        )

        period = MockFiscalPeriod()

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
                ) as mock_period:
                    mock_period.return_value = period
                    with patch("app.services.finance.gl.journal.JournalEntryLine"):
                        result = JournalService.update_journal(
                            mock_db,
                            org_id,
                            journal.journal_entry_id,
                            updated_input,
                            user_id,
                        )

        assert result.description == "Updated description"
        mock_db.commit.assert_called()

    def test_update_non_draft_fails(self, mock_db, org_id, user_id, sample_lines):
        """Test updating a non-draft journal fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.SUBMITTED,
        )
        mock_db.get.return_value = journal

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Updated",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.update_journal(
                        mock_db,
                        org_id,
                        journal.journal_entry_id,
                        updated_input,
                        user_id,
                    )

        assert exc.value.status_code == 400
        assert "SUBMITTED" in exc.value.detail

    def test_update_journal_not_found(self, mock_db, org_id, user_id, sample_lines):
        """Test updating non-existent journal fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        mock_db.get.return_value = None

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Updated",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.update_journal(
                    mock_db, org_id, uuid4(), updated_input, user_id
                )

        assert exc.value.status_code == 404

    def test_update_journal_unbalanced_fails(self, mock_db, org_id, user_id):
        """Test updating journal with unbalanced lines fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

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
                credit_amount=Decimal("500.00"),
            ),
        ]

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Unbalanced",
            currency_code="USD",
            lines=unbalanced_lines,
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.update_journal(
                        mock_db,
                        org_id,
                        journal.journal_entry_id,
                        updated_input,
                        user_id,
                    )

        assert exc.value.status_code == 400
        assert "unbalanced" in exc.value.detail.lower()

    def test_update_journal_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test updating journal with no lines fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Empty",
            currency_code="USD",
            lines=[],
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.update_journal(
                        mock_db,
                        org_id,
                        journal.journal_entry_id,
                        updated_input,
                        user_id,
                    )

        assert exc.value.status_code == 400

    def test_update_journal_no_fiscal_period_fails(
        self, mock_db, org_id, user_id, sample_lines
    ):
        """Test updating journal when fiscal period not found fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Updated",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
                ) as mock_period:
                    mock_period.return_value = None
                    with pytest.raises(HTTPException) as exc:
                        JournalService.update_journal(
                            mock_db,
                            org_id,
                            journal.journal_entry_id,
                            updated_input,
                            user_id,
                        )

        assert exc.value.status_code == 400
        assert "fiscal period" in exc.value.detail.lower()


class TestPostJournal:
    """Tests for post_journal method."""

    def test_post_approved_journal_success(self, mock_db, org_id, user_id):
        """Test posting an approved journal successfully."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
            posting_date=date.today(),
            source_module="GL",
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Posted"

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch("app.services.finance.gl.journal.PostingRequest"):
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        result = JournalService.post_journal(
                            mock_db, org_id, journal.journal_entry_id, user_id
                        )

        mock_posting.assert_called_once()
        mock_db.refresh.assert_called()

    def test_post_already_posted_journal_is_idempotent(self, mock_db, org_id, user_id):
        """Test posting already posted journal returns without error (idempotent)."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.POSTED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                result = JournalService.post_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert result == journal

    def test_post_journal_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent journal fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.post_journal(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_post_journal_wrong_status_fails(self, mock_db, org_id, user_id):
        """Test posting journal with wrong status fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.SUBMITTED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.post_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id
                    )

        assert exc.value.status_code == 400

    def test_post_journal_posting_fails(self, mock_db, org_id, user_id):
        """Test posting journal when LedgerPostingService fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
            posting_date=date.today(),
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.message = "Period is closed"

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch("app.services.finance.gl.journal.PostingRequest"):
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        with pytest.raises(HTTPException) as exc:
                            JournalService.post_journal(
                                mock_db, org_id, journal.journal_entry_id, user_id
                            )

        assert exc.value.status_code == 400
        assert "Period is closed" in exc.value.detail

    def test_post_journal_with_idempotency_key(self, mock_db, org_id, user_id):
        """Test posting journal with custom idempotency key."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
            posting_date=date.today(),
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = True

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.journal.PostingRequest"
                ) as MockPostReq:
                    mock_req = MagicMock()
                    mock_req.idempotency_key = "custom-key-123"
                    MockPostReq.return_value = mock_req
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        result = JournalService.post_journal(
                            mock_db,
                            org_id,
                            journal.journal_entry_id,
                            user_id,
                            idempotency_key="custom-key-123",
                        )

        # Verify the posting was called
        mock_posting.assert_called_once()


class TestReverseEntry:
    """Tests for reverse_entry method."""

    def test_reverse_posted_journal_success(self, mock_db, org_id, user_id):
        """Test reversing a posted journal successfully."""
        from app.models.finance.gl.journal_entry import JournalStatus

        mock_line = MagicMock()
        mock_line.account_id = uuid4()
        mock_line.debit_amount = Decimal("1000.00")
        mock_line.credit_amount = Decimal("0")
        mock_line.debit_amount_functional = Decimal("1000.00")
        mock_line.credit_amount_functional = Decimal("0")
        mock_line.currency_code = "USD"
        mock_line.exchange_rate = Decimal("1.0")
        mock_line.description = "Test line"
        mock_line.business_unit_id = None
        mock_line.cost_center_id = None
        mock_line.project_id = None
        mock_line.segment_id = None

        journal = MagicMock()
        journal.organization_id = org_id
        journal.status = JournalStatus.POSTED
        journal.journal_entry_id = uuid4()
        journal.fiscal_period_id = uuid4()
        journal.journal_number = "JE-0001"
        journal.description = "Test"
        journal.total_debit = Decimal("1000.00")
        journal.total_credit = Decimal("1000.00")
        journal.total_debit_functional = Decimal("1000.00")
        journal.total_credit_functional = Decimal("1000.00")
        journal.currency_code = "USD"
        journal.exchange_rate = Decimal("1.0")
        journal.source_module = "GL"
        journal.reversal_journal_id = None
        journal.lines = [mock_line]

        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry") as MockJE:
            mock_reversal = MagicMock()
            mock_reversal.lines = []
            mock_reversal.journal_entry_id = uuid4()
            MockJE.return_value = mock_reversal
            with patch("app.services.finance.gl.journal.JournalEntryLine"):
                result = JournalService.reverse_entry(
                    mock_db, org_id, journal.journal_entry_id, date.today(), user_id
                )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_reverse_non_posted_journal_fails(self, mock_db, org_id, user_id):
        """Test reversing a non-posted journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.reverse_entry(
                        mock_db, org_id, journal.journal_entry_id, date.today(), user_id
                    )

        assert exc.value.status_code == 400
        assert "Only posted" in exc.value.detail

    def test_reverse_already_reversed_journal_fails(self, mock_db, org_id, user_id):
        """Test reversing an already reversed journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.POSTED,
        )
        journal.reversal_journal_id = uuid4()  # Already reversed
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.reverse_entry(
                        mock_db, org_id, journal.journal_entry_id, date.today(), user_id
                    )

        assert exc.value.status_code == 400
        assert "already been reversed" in exc.value.detail

    def test_reverse_journal_not_found(self, mock_db, org_id, user_id):
        """Test reversing non-existent journal fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.reverse_entry(
                    mock_db, org_id, uuid4(), date.today(), user_id
                )

        assert exc.value.status_code == 404


class TestSubmitJournalNotFound:
    """Tests for submit_journal when journal not found."""

    def test_submit_journal_not_found(self, mock_db, org_id, user_id):
        """Test submitting non-existent journal fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.submit_journal(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404


class TestApproveJournalNotFound:
    """Tests for approve_journal when journal not found."""

    def test_approve_journal_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent journal fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.approve_journal(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404


class TestVoidJournalNotFound:
    """Tests for void_journal when journal not found."""

    def test_void_journal_not_found(self, mock_db, org_id, user_id):
        """Test voiding non-existent journal fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.void_journal(mock_db, org_id, uuid4(), user_id, "Reason")

        assert exc.value.status_code == 404


class TestListJournalsWithFilters:
    """Additional tests for list method with various filters."""

    def test_list_by_journal_type(self, mock_db, org_id):
        """Test listing journals filtered by journal type."""
        from app.models.finance.gl.journal_entry import JournalType

        journals = [MockJournalEntry(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch("app.services.finance.gl.journal.JournalType") as MockType:
                MockType.STANDARD = JournalType.STANDARD
                result = JournalService.list(
                    mock_db,
                    organization_id=str(org_id),
                    journal_type=JournalType.STANDARD,
                )

        assert result == journals

    def test_list_by_fiscal_period(self, mock_db, org_id):
        """Test listing journals filtered by fiscal period."""
        fiscal_period_id = uuid4()
        journals = [MockJournalEntry(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.journal.JournalEntry"):
            result = JournalService.list(
                mock_db,
                organization_id=str(org_id),
                fiscal_period_id=str(fiscal_period_id),
            )

        assert result == journals

    def test_list_by_date_range(self, mock_db, org_id):
        """Test listing journals within date range."""
        from_date = date(2024, 1, 1)
        to_date = date(2024, 12, 31)
        journals = [MockJournalEntry(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        # Don't patch JournalEntry so posting_date attribute works
        result = JournalService.list(
            mock_db,
            organization_id=str(org_id),
            from_date=from_date,
            to_date=to_date,
        )

        assert result == journals

    def test_list_with_pagination(self, mock_db, org_id):
        """Test listing journals with pagination."""
        journals = [MockJournalEntry(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.journal.JournalEntry"):
            result = JournalService.list(
                mock_db,
                organization_id=str(org_id),
                limit=10,
                offset=20,
            )

        mock_query.limit.assert_called_with(10)
        mock_query.offset.assert_called_with(20)
        assert result == journals


class TestVoidSubmittedJournal:
    """Test voiding submitted journal."""

    def test_void_submitted_journal(self, mock_db, org_id, user_id):
        """Test voiding a submitted journal."""
        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.SUBMITTED
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                result = JournalService.void_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id, "Rejected"
                )

        assert result.status == MockJournalStatus.VOID
        mock_db.commit.assert_called()


class TestVoidReversedJournalFails:
    """Test voiding reversed journal fails."""

    def test_void_reversed_journal_fails(self, mock_db, org_id, user_id):
        """Test that voiding a reversed journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id, status=MockJournalStatus.REVERSED
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.void_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id, "Reason"
                    )

        assert exc.value.status_code == 400
        assert "reversal" in exc.value.detail.lower()


class TestCreateEntry:
    """Tests for create_entry alias method."""

    def test_create_entry_is_alias(self, mock_db, org_id, user_id, sample_lines):
        """Test that create_entry is alias for create_journal."""
        from app.models.finance.gl.journal_entry import JournalType

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Test",
            currency_code="USD",
            lines=sample_lines,
        )

        period = MockFiscalPeriod()

        with patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date"
        ) as mock_period:
            mock_period.return_value = period
            with patch(
                "app.services.finance.gl.journal.SequenceService.get_next_number"
            ) as mock_seq:
                mock_seq.return_value = "JE-ALIAS"
                with patch("app.services.finance.gl.journal.JournalEntry") as MockJE:
                    mock_journal = MockJournalEntry(
                        organization_id=org_id,
                        status=MockJournalStatus.DRAFT,
                    )
                    MockJE.return_value = mock_journal
                    with patch("app.services.finance.gl.journal.JournalEntryLine"):
                        result = JournalService.create_entry(
                            mock_db, org_id, journal_input, user_id
                        )

        mock_db.commit.assert_called()


class TestPostDraftJournal:
    """Tests for posting draft journals directly."""

    def test_post_draft_journal_success(self, mock_db, org_id, user_id):
        """Test posting a draft journal directly (allowed per service logic)."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.DRAFT,
            posting_date=date.today(),
            source_module="GL",
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Posted"

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch("app.services.finance.gl.journal.PostingRequest"):
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        result = JournalService.post_journal(
                            mock_db, org_id, journal.journal_entry_id, user_id
                        )

        mock_posting.assert_called_once()
        mock_db.refresh.assert_called()


class TestPostJournalWithAdjustment:
    """Tests for posting journal with adjustment period options."""

    def test_post_journal_with_allow_adjustment(self, mock_db, org_id, user_id):
        """Test posting journal with allow_adjustment flag."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
            posting_date=date.today(),
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = True

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.journal.PostingRequest"
                ) as MockPostReq:
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        result = JournalService.post_journal(
                            mock_db,
                            org_id,
                            journal.journal_entry_id,
                            user_id,
                            allow_adjustment=True,
                        )

        mock_posting.assert_called_once()

    def test_post_journal_with_reopen_session(self, mock_db, org_id, user_id):
        """Test posting journal with reopen session ID."""
        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.APPROVED,
            posting_date=date.today(),
        )
        mock_db.get.return_value = journal

        mock_result = MagicMock()
        mock_result.success = True
        reopen_session_id = uuid4()

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with patch(
                    "app.services.finance.gl.journal.PostingRequest"
                ) as MockPostReq:
                    with patch(
                        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
                    ) as mock_posting:
                        mock_posting.return_value = mock_result
                        result = JournalService.post_journal(
                            mock_db,
                            org_id,
                            journal.journal_entry_id,
                            user_id,
                            reopen_session_id=reopen_session_id,
                        )

        mock_posting.assert_called_once()


class TestJournalWrongOrganization:
    """Tests for journal operations with wrong organization."""

    def test_submit_journal_wrong_organization(self, mock_db, org_id, user_id):
        """Test submitting journal from wrong organization fails."""
        from fastapi import HTTPException

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.submit_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert exc.value.status_code == 404

    def test_approve_journal_wrong_organization(self, mock_db, org_id, user_id):
        """Test approving journal from wrong organization fails."""
        from fastapi import HTTPException

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.SUBMITTED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.approve_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert exc.value.status_code == 404

    def test_void_journal_wrong_organization(self, mock_db, org_id, user_id):
        """Test voiding journal from wrong organization fails."""
        from fastapi import HTTPException

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.void_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id, "Wrong org"
                )

        assert exc.value.status_code == 404

    def test_post_journal_wrong_organization(self, mock_db, org_id, user_id):
        """Test posting journal from wrong organization fails."""
        from fastapi import HTTPException

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.APPROVED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.post_journal(
                    mock_db, org_id, journal.journal_entry_id, user_id
                )

        assert exc.value.status_code == 404

    def test_update_journal_wrong_organization(
        self, mock_db, org_id, user_id, sample_lines
    ):
        """Test updating journal from wrong organization fails."""
        from fastapi import HTTPException
        from app.models.finance.gl.journal_entry import JournalType

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.DRAFT,
        )
        mock_db.get.return_value = journal

        updated_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Updated",
            currency_code="USD",
            lines=sample_lines,
        )

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.update_journal(
                    mock_db, org_id, journal.journal_entry_id, updated_input, user_id
                )

        assert exc.value.status_code == 404

    def test_reverse_journal_wrong_organization(self, mock_db, org_id, user_id):
        """Test reversing journal from wrong organization fails."""
        from fastapi import HTTPException

        other_org_id = uuid4()
        journal = MockJournalEntry(
            organization_id=other_org_id,
            status=MockJournalStatus.POSTED,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with pytest.raises(HTTPException) as exc:
                JournalService.reverse_entry(
                    mock_db, org_id, journal.journal_entry_id, date.today(), user_id
                )

        assert exc.value.status_code == 404


class TestListJournalsEmpty:
    """Test listing journals with empty results."""

    def test_list_empty_results(self, mock_db, org_id):
        """Test listing journals returns empty list."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.journal.JournalEntry"):
            result = JournalService.list(mock_db, organization_id=str(org_id))

        assert result == []

    def test_list_without_organization(self, mock_db):
        """Test listing journals without organization filter."""
        journals = [MockJournalEntry()]
        mock_query = MagicMock()
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = journals
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.journal.JournalEntry"):
            result = JournalService.list(mock_db)

        assert result == journals


class TestJournalLineInputDefaults:
    """Tests for JournalLineInput defaults."""

    def test_line_input_default_values(self):
        """Test default values for line input."""
        account_id = uuid4()
        line = JournalLineInput(account_id=account_id)

        assert line.account_id == account_id
        assert line.debit_amount == Decimal("0")
        assert line.credit_amount == Decimal("0")
        assert line.description is None
        assert line.debit_amount_functional is None
        assert line.credit_amount_functional is None
        assert line.currency_code is None
        assert line.exchange_rate is None
        assert line.business_unit_id is None
        assert line.cost_center_id is None
        assert line.project_id is None
        assert line.segment_id is None

    def test_line_input_with_functional_amounts(self):
        """Test line input with explicit functional amounts."""
        account_id = uuid4()
        line = JournalLineInput(
            account_id=account_id,
            debit_amount=Decimal("100.00"),
            credit_amount=Decimal("0"),
            debit_amount_functional=Decimal("110.00"),
            credit_amount_functional=Decimal("0"),
            currency_code="EUR",
            exchange_rate=Decimal("1.10"),
        )

        assert line.debit_amount_functional == Decimal("110.00")
        assert line.credit_amount_functional == Decimal("0")
        assert line.currency_code == "EUR"
        assert line.exchange_rate == Decimal("1.10")


class TestJournalInputDefaults:
    """Tests for JournalInput defaults."""

    def test_journal_input_default_values(self):
        """Test default values for journal input."""
        from app.models.finance.gl.journal_entry import JournalType

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Test",
        )

        assert journal_input.lines == []
        assert journal_input.reference is None
        assert journal_input.currency_code == "USD"
        assert journal_input.exchange_rate == Decimal("1.0")
        assert journal_input.exchange_rate_type_id is None
        assert journal_input.source_module is None
        assert journal_input.source_document_type is None
        assert journal_input.source_document_id is None
        assert journal_input.auto_reverse_date is None
        assert journal_input.correlation_id is None

    def test_journal_input_with_source_document(self):
        """Test journal input with source document reference."""
        from app.models.finance.gl.journal_entry import JournalType

        source_doc_id = uuid4()
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=date.today(),
            posting_date=date.today(),
            description="From invoice",
            source_module="AR",
            source_document_type="INVOICE",
            source_document_id=source_doc_id,
        )

        assert journal_input.source_module == "AR"
        assert journal_input.source_document_type == "INVOICE"
        assert journal_input.source_document_id == source_doc_id

    def test_journal_input_with_auto_reverse(self):
        """Test journal input with auto reverse date."""
        from app.models.finance.gl.journal_entry import JournalType
        from datetime import timedelta

        reversal_date = date.today() + timedelta(days=30)
        journal_input = JournalInput(
            journal_type=JournalType.RECURRING,
            entry_date=date.today(),
            posting_date=date.today(),
            description="Monthly accrual",
            auto_reverse_date=reversal_date,
        )

        assert journal_input.auto_reverse_date == reversal_date


class TestGetLinesEmpty:
    """Test getting lines for journal with no lines."""

    def test_get_lines_empty_result(self, mock_db):
        """Test getting lines returns empty list when journal has no lines."""
        journal_id = uuid4()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch("app.services.finance.gl.journal.JournalEntryLine"):
            result = JournalService.get_lines(mock_db, journal_id)

        assert result == []


class TestApproveJournalVoid:
    """Test attempting to approve void journal."""

    def test_approve_void_journal_fails(self, mock_db, org_id, user_id):
        """Test approving a void journal fails."""
        from fastapi import HTTPException

        journal = MockJournalEntry(
            organization_id=org_id,
            status=MockJournalStatus.VOID,
        )
        mock_db.get.return_value = journal

        with patch("app.services.finance.gl.journal.JournalEntry"):
            with patch(
                "app.services.finance.gl.journal.JournalStatus", MockJournalStatus
            ):
                with pytest.raises(HTTPException) as exc:
                    JournalService.approve_journal(
                        mock_db, org_id, journal.journal_entry_id, user_id
                    )

        assert exc.value.status_code == 400
