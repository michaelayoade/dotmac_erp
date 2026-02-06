"""
Tests for GLPostingAdapter.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.finance.gl.gl_posting_adapter import (
    GLPostingAdapter,
    GLPostingResult,
)
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from tests.ifrs.gl.conftest import (
    MockJournalEntry,
    MockJournalStatus,
)


@pytest.fixture
def adapter():
    """Create GLPostingAdapter instance."""
    return GLPostingAdapter()


@pytest.fixture
def sample_journal_input():
    """Create sample journal input."""
    from app.models.finance.gl.journal_entry import JournalType

    return JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=date.today(),
        posting_date=date.today(),
        description="Test journal entry",
        currency_code="USD",
        lines=[
            JournalLineInput(
                account_id=uuid4(),
                debit_amount=Decimal("1000.00"),
                credit_amount=Decimal("0"),
                description="Debit line",
            ),
            JournalLineInput(
                account_id=uuid4(),
                debit_amount=Decimal("0"),
                credit_amount=Decimal("1000.00"),
                description="Credit line",
            ),
        ],
    )


class TestPostManualJournal:
    """Tests for post_manual_journal method."""

    def test_post_manual_journal_success(self, adapter, mock_db, org_id, user_id):
        """Test successful manual journal posting."""
        journal = MockJournalEntry(
            organization_id=org_id,
            journal_number="JE-0001",
            status=MockJournalStatus.POSTED,
        )

        with patch(
            "app.services.finance.gl.gl_posting_adapter.JournalService.post_journal"
        ) as mock_post:
            mock_post.return_value = journal

            result = adapter.post_manual_journal(
                mock_db,
                org_id,
                journal.journal_entry_id,
                date.today(),
                user_id,
                uuid4(),
            )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert result.entry_number == "JE-0001"
        assert "successfully" in result.message

    def test_post_manual_journal_failure(self, adapter, mock_db, org_id, user_id):
        """Test manual journal posting failure."""
        with patch(
            "app.services.finance.gl.gl_posting_adapter.JournalService.post_journal"
        ) as mock_post:
            mock_post.side_effect = Exception("Period is closed")

            result = adapter.post_manual_journal(
                mock_db,
                org_id,
                uuid4(),
                date.today(),
                user_id,
                uuid4(),
            )

        assert result.success is False
        assert "Period is closed" in result.message


class TestCreateAndPostJournal:
    """Tests for create_and_post_journal method."""

    def test_create_journal_without_auto_post(
        self, adapter, mock_db, org_id, user_id, sample_journal_input
    ):
        """Test creating journal without auto-posting."""
        journal = MockJournalEntry(
            organization_id=org_id,
            journal_number="JE-0001",
            status=MockJournalStatus.DRAFT,
        )

        with patch(
            "app.services.finance.gl.gl_posting_adapter.JournalService.create_journal"
        ) as mock_create:
            mock_create.return_value = journal

            result = adapter.create_and_post_journal(
                mock_db,
                org_id,
                sample_journal_input,
                user_id,
                auto_post=False,
            )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert "created" in result.message
        assert "posted" not in result.message

    def test_create_and_auto_post_journal(
        self, adapter, mock_db, org_id, user_id, sample_journal_input
    ):
        """Test creating and auto-posting journal."""
        draft_journal = MockJournalEntry(
            organization_id=org_id,
            journal_number="JE-0001",
            status=MockJournalStatus.DRAFT,
        )
        submitted_journal = MockJournalEntry(
            journal_entry_id=draft_journal.journal_entry_id,
            organization_id=org_id,
            journal_number="JE-0001",
            status=MockJournalStatus.SUBMITTED,
        )
        posted_journal = MockJournalEntry(
            journal_entry_id=draft_journal.journal_entry_id,
            organization_id=org_id,
            journal_number="JE-0001",
            status=MockJournalStatus.POSTED,
        )

        with (
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.create_journal"
            ) as mock_create,
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.submit_journal"
            ) as mock_submit,
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.post_journal"
            ) as mock_post,
        ):
            mock_create.return_value = draft_journal
            mock_submit.return_value = submitted_journal
            mock_post.return_value = posted_journal

            result = adapter.create_and_post_journal(
                mock_db,
                org_id,
                sample_journal_input,
                user_id,
                auto_post=True,
            )

        assert result.success is True
        assert "created" in result.message
        assert "posted" in result.message
        mock_submit.assert_called_once()
        mock_post.assert_called_once()

    def test_create_journal_failure(
        self, adapter, mock_db, org_id, user_id, sample_journal_input
    ):
        """Test journal creation failure."""
        with patch(
            "app.services.finance.gl.gl_posting_adapter.JournalService.create_journal"
        ) as mock_create:
            mock_create.side_effect = Exception("Invalid account")

            result = adapter.create_and_post_journal(
                mock_db,
                org_id,
                sample_journal_input,
                user_id,
            )

        assert result.success is False
        assert "Invalid account" in result.message

    def test_auto_post_submit_failure(
        self, adapter, mock_db, org_id, user_id, sample_journal_input
    ):
        """Test auto-post failure during submit."""
        journal = MockJournalEntry(organization_id=org_id)

        with (
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.create_journal"
            ) as mock_create,
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.submit_journal"
            ) as mock_submit,
        ):
            mock_create.return_value = journal
            mock_submit.side_effect = Exception("Cannot submit draft")

            result = adapter.create_and_post_journal(
                mock_db,
                org_id,
                sample_journal_input,
                user_id,
                auto_post=True,
            )

        assert result.success is False
        assert "Cannot submit draft" in result.message

    def test_auto_post_posting_failure(
        self, adapter, mock_db, org_id, user_id, sample_journal_input
    ):
        """Test auto-post failure during posting."""
        journal = MockJournalEntry(organization_id=org_id)

        with (
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.create_journal"
            ) as mock_create,
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.submit_journal"
            ) as mock_submit,
            patch(
                "app.services.finance.gl.gl_posting_adapter.JournalService.post_journal"
            ) as mock_post,
        ):
            mock_create.return_value = journal
            mock_submit.return_value = journal
            mock_post.side_effect = Exception("Period closed for posting")

            result = adapter.create_and_post_journal(
                mock_db,
                org_id,
                sample_journal_input,
                user_id,
                auto_post=True,
            )

        assert result.success is False
        assert "Period closed" in result.message


class TestGLPostingResult:
    """Tests for GLPostingResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = GLPostingResult(
            success=True,
            journal_entry_id=uuid4(),
            entry_number="JE-0001",
            message="Posted successfully",
        )

        assert result.success is True
        assert result.entry_number == "JE-0001"

    def test_failure_result(self):
        """Test creating a failure result."""
        result = GLPostingResult(
            success=False,
            message="Posting failed",
        )

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.entry_number is None
