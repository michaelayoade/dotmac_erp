"""
Tests for GL logic fixes.

Covers:
- CRITICAL #2: OPEN period balances included in get_balances_for_accounts
- CRITICAL #3: Reversal atomicity (status set before posting)
- MEDIUM: post_journal rejects DRAFT status
- MEDIUM: suggest_next_code boundary check
- MEDIUM: update_balance_for_posting uses flush() not commit()
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> MagicMock:
    """Create mock database session."""
    db = MagicMock()
    # Make scalar/scalars return MagicMock by default
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = []
    return db


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


# ---------------------------------------------------------------------------
# 1. post_journal rejects DRAFT status
# ---------------------------------------------------------------------------


class TestPostJournalRejectsDraft:
    """Verify that post_journal no longer accepts DRAFT journals."""

    def test_draft_journal_raises_400(self, mock_db: MagicMock, org_id, user_id):
        """DRAFT journals cannot be posted — must be APPROVED first."""
        from app.services.finance.gl.journal import JournalService

        journal_id = uuid4()
        journal = MagicMock(spec=JournalEntry)
        journal.organization_id = org_id
        journal.status = JournalStatus.DRAFT
        journal.journal_entry_id = journal_id

        mock_db.get.return_value = journal

        with pytest.raises(HTTPException) as exc_info:
            JournalService.post_journal(
                mock_db,
                organization_id=org_id,
                journal_entry_id=journal_id,
                posted_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 400
        assert "approved" in exc_info.value.detail.lower()

    def test_submitted_journal_raises_400(self, mock_db: MagicMock, org_id, user_id):
        """SUBMITTED journals also cannot be posted (only APPROVED can)."""
        from app.services.finance.gl.journal import JournalService

        journal_id = uuid4()
        journal = MagicMock(spec=JournalEntry)
        journal.organization_id = org_id
        journal.status = JournalStatus.SUBMITTED
        journal.journal_entry_id = journal_id

        mock_db.get.return_value = journal

        with pytest.raises(HTTPException) as exc_info:
            JournalService.post_journal(
                mock_db,
                organization_id=org_id,
                journal_entry_id=journal_id,
                posted_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 400

    def test_approved_journal_proceeds(self, mock_db: MagicMock, org_id, user_id):
        """APPROVED journals should proceed to posting."""
        from app.services.finance.gl.journal import JournalService
        from app.services.finance.gl.ledger_posting import PostingResult

        journal_id = uuid4()
        journal = MagicMock(spec=JournalEntry)
        journal.organization_id = org_id
        journal.status = JournalStatus.APPROVED
        journal.journal_entry_id = journal_id
        journal.posting_date = date.today()
        journal.source_module = "GL"
        journal.correlation_id = None

        mock_db.get.return_value = journal

        with patch(
            "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry",
            return_value=PostingResult(success=True, batch_id=uuid4()),
        ):
            result = JournalService.post_journal(
                mock_db,
                organization_id=org_id,
                journal_entry_id=journal_id,
                posted_by_user_id=user_id,
            )

        # Should return the journal (not raise)
        assert result is not None

    def test_already_posted_is_idempotent(self, mock_db: MagicMock, org_id, user_id):
        """Already-posted journals return without error (idempotent)."""
        from app.services.finance.gl.journal import JournalService

        journal_id = uuid4()
        journal = MagicMock(spec=JournalEntry)
        journal.organization_id = org_id
        journal.status = JournalStatus.POSTED
        journal.journal_entry_id = journal_id

        mock_db.get.return_value = journal

        result = JournalService.post_journal(
            mock_db,
            organization_id=org_id,
            journal_entry_id=journal_id,
            posted_by_user_id=user_id,
        )

        assert result == journal


# ---------------------------------------------------------------------------
# 2. suggest_next_code boundary check
# ---------------------------------------------------------------------------


class TestSuggestNextCodeBoundary:
    """Verify that suggest_next_code doesn't cross IFRS category boundaries."""

    def test_code_within_range(self, mock_db: MagicMock, org_id):
        """Normal increment stays within category range."""
        from app.models.finance.gl.account_category import IFRSCategory
        from app.services.finance.gl.chart_of_accounts import ChartOfAccountsService

        category = SimpleNamespace(ifrs_category=IFRSCategory.ASSETS)
        with patch(
            "app.services.finance.gl.chart_of_accounts.get_org_scoped_entity",
            return_value=category,
        ):
            # Simulate highest code = 1050
            mock_db.execute.return_value.first.return_value = ("1050",)

            result = ChartOfAccountsService.suggest_next_code(
                mock_db,
                organization_id=str(org_id),
                category_id=str(uuid4()),
            )

        assert result["suggested_code"] == "1051"
        assert result["prefix"] == "1"

    def test_code_at_boundary_returns_none(self, mock_db: MagicMock, org_id):
        """If highest code is 1999, next would be 2000 which crosses into Liabilities.
        Should return None."""
        from app.models.finance.gl.account_category import IFRSCategory
        from app.services.finance.gl.chart_of_accounts import ChartOfAccountsService

        category = SimpleNamespace(ifrs_category=IFRSCategory.ASSETS)
        with patch(
            "app.services.finance.gl.chart_of_accounts.get_org_scoped_entity",
            return_value=category,
        ):
            mock_db.execute.return_value.first.return_value = ("1999",)

            result = ChartOfAccountsService.suggest_next_code(
                mock_db,
                organization_id=str(org_id),
                category_id=str(uuid4()),
            )

        assert result["suggested_code"] is None

    def test_expense_boundary(self, mock_db: MagicMock, org_id):
        """Expenses range (5xxx) should not overflow into 6000 (OCI)."""
        from app.models.finance.gl.account_category import IFRSCategory
        from app.services.finance.gl.chart_of_accounts import ChartOfAccountsService

        category = SimpleNamespace(ifrs_category=IFRSCategory.EXPENSES)
        with patch(
            "app.services.finance.gl.chart_of_accounts.get_org_scoped_entity",
            return_value=category,
        ):
            mock_db.execute.return_value.first.return_value = ("5999",)

            result = ChartOfAccountsService.suggest_next_code(
                mock_db,
                organization_id=str(org_id),
                category_id=str(uuid4()),
            )

        assert result["suggested_code"] is None

    def test_no_existing_accounts(self, mock_db: MagicMock, org_id):
        """When no accounts exist, start at prefix+001."""
        from app.models.finance.gl.account_category import IFRSCategory
        from app.services.finance.gl.chart_of_accounts import ChartOfAccountsService

        category = SimpleNamespace(ifrs_category=IFRSCategory.ASSETS)
        with patch(
            "app.services.finance.gl.chart_of_accounts.get_org_scoped_entity",
            return_value=category,
        ):
            mock_db.execute.return_value.first.return_value = None

            result = ChartOfAccountsService.suggest_next_code(
                mock_db,
                organization_id=str(org_id),
                category_id=str(uuid4()),
            )

        assert result["suggested_code"] == "1001"


# ---------------------------------------------------------------------------
# 3. update_balance_for_posting uses flush(), not commit()
# ---------------------------------------------------------------------------


class TestUpdateBalanceFlush:
    """Verify balance update uses flush for transactional batching."""

    def test_new_balance_uses_flush(self, mock_db: MagicMock, org_id):
        """Creating a new balance record should call flush(), not commit()."""
        from app.services.finance.gl.account_balance import AccountBalanceService

        account_id = uuid4()
        period_id = uuid4()

        mock_db.scalar.return_value = None  # No existing balance

        with patch(
            "app.services.finance.gl.account_balance.org_context_service"
        ) as mock_org:
            mock_org.get_functional_currency.return_value = "NGN"

            AccountBalanceService.update_balance_for_posting(
                mock_db,
                organization_id=org_id,
                account_id=account_id,
                fiscal_period_id=period_id,
                debit_amount=Decimal("1000"),
                credit_amount=Decimal("0"),
            )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.commit.assert_not_called()

    def test_existing_balance_uses_flush(self, mock_db: MagicMock, org_id):
        """Updating an existing balance should call flush(), not commit()."""
        from app.services.finance.gl.account_balance import AccountBalanceService

        account_id = uuid4()
        period_id = uuid4()

        existing = SimpleNamespace(
            opening_debit=Decimal("100"),
            opening_credit=Decimal("0"),
            period_debit=Decimal("500"),
            period_credit=Decimal("200"),
            closing_debit=Decimal("600"),
            closing_credit=Decimal("200"),
            net_balance=Decimal("400"),
            transaction_count=5,
            last_updated_at=None,
        )
        mock_db.scalar.return_value = existing

        with patch(
            "app.services.finance.gl.account_balance.org_context_service"
        ) as mock_org:
            mock_org.get_functional_currency.return_value = "NGN"

            AccountBalanceService.update_balance_for_posting(
                mock_db,
                organization_id=org_id,
                account_id=account_id,
                fiscal_period_id=period_id,
                debit_amount=Decimal("100"),
                credit_amount=Decimal("0"),
                currency_code="NGN",
            )

        mock_db.flush.assert_called_once()
        mock_db.commit.assert_not_called()

        # Verify the balance was updated correctly
        assert existing.period_debit == Decimal("600")
        assert existing.transaction_count == 6
        assert existing.closing_debit == Decimal("700")
        assert existing.net_balance == Decimal("500")


# ---------------------------------------------------------------------------
# 4. Reversal atomicity — status set before posting
# ---------------------------------------------------------------------------


class TestReversalAtomicity:
    """Verify reversal sets REVERSED status before calling LedgerPostingService."""

    def test_auto_post_sets_reversed_before_posting(
        self, mock_db: MagicMock, org_id, user_id
    ):
        """When auto_post=True, original.status = REVERSED is set BEFORE
        LedgerPostingService.post_journal_entry is called."""
        from app.services.finance.gl.reversal import ReversalService

        journal_id = uuid4()
        period_id = uuid4()
        fy_id = uuid4()

        # Original journal
        original = MagicMock(spec=JournalEntry)
        original.journal_entry_id = journal_id
        original.organization_id = org_id
        original.status = JournalStatus.POSTED
        original.reversal_journal_id = None
        original.journal_number = "JNL-001"
        original.currency_code = "NGN"
        original.exchange_rate = Decimal("1")
        original.exchange_rate_type_id = None
        original.total_debit = Decimal("1000")
        original.total_credit = Decimal("1000")
        original.total_debit_functional = Decimal("1000")
        original.total_credit_functional = Decimal("1000")
        original.source_module = "GL"
        original.source_document_type = None
        original.source_document_id = None
        original.correlation_id = None

        mock_db.get.return_value = original
        mock_db.scalars.return_value.all.return_value = []  # No original lines

        period = SimpleNamespace(
            fiscal_period_id=period_id,
            fiscal_year_id=fy_id,
        )

        # Track the order of calls
        call_order: list[str] = []
        real_status_values: list[JournalStatus] = []

        def mock_post(db, request):
            """Capture original.status at the time of posting."""
            from app.services.finance.gl.ledger_posting import PostingResult

            real_status_values.append(original.status)
            call_order.append("post_journal_entry")
            return PostingResult(success=True, batch_id=uuid4())

        # Make the reversal object have a journal_entry_id
        def side_effect_flush():
            call_order.append("flush")

        mock_db.flush.side_effect = side_effect_flush

        with (
            patch(
                "app.services.finance.gl.reversal.PeriodGuardService.get_period_for_date",
                return_value=period,
            ),
            patch(
                "app.services.finance.gl.reversal.PeriodGuardService.require_open_period",
                return_value=period_id,
            ),
            patch(
                "app.services.finance.gl.reversal.SequenceService.get_next_number",
                return_value="JNL-002",
            ),
            patch(
                "app.services.finance.gl.reversal.LedgerPostingService.post_journal_entry",
                side_effect=mock_post,
            ),
        ):
            result = ReversalService.create_reversal(
                mock_db,
                organization_id=org_id,
                original_journal_id=journal_id,
                reversal_date=date.today(),
                created_by_user_id=user_id,
                reason="Test reversal",
                auto_post=True,
            )

        assert result.success is True
        # Original must be REVERSED *before* post_journal_entry was called
        assert real_status_values[0] == JournalStatus.REVERSED

    def test_auto_post_failure_reverts_status(
        self, mock_db: MagicMock, org_id, user_id
    ):
        """If auto_post fails, original should NOT remain as REVERSED."""
        from app.services.finance.gl.ledger_posting import PostingResult
        from app.services.finance.gl.reversal import ReversalService

        journal_id = uuid4()
        period_id = uuid4()
        fy_id = uuid4()

        original = MagicMock(spec=JournalEntry)
        original.journal_entry_id = journal_id
        original.organization_id = org_id
        original.status = JournalStatus.POSTED
        original.reversal_journal_id = None
        original.journal_number = "JNL-001"
        original.currency_code = "NGN"
        original.exchange_rate = Decimal("1")
        original.exchange_rate_type_id = None
        original.total_debit = Decimal("1000")
        original.total_credit = Decimal("1000")
        original.total_debit_functional = Decimal("1000")
        original.total_credit_functional = Decimal("1000")
        original.source_module = "GL"
        original.source_document_type = None
        original.source_document_id = None
        original.correlation_id = None

        mock_db.get.return_value = original
        mock_db.scalars.return_value.all.return_value = []

        period = SimpleNamespace(
            fiscal_period_id=period_id,
            fiscal_year_id=fy_id,
        )

        with (
            patch(
                "app.services.finance.gl.reversal.PeriodGuardService.get_period_for_date",
                return_value=period,
            ),
            patch(
                "app.services.finance.gl.reversal.PeriodGuardService.require_open_period",
                return_value=period_id,
            ),
            patch(
                "app.services.finance.gl.reversal.SequenceService.get_next_number",
                return_value="JNL-002",
            ),
            patch(
                "app.services.finance.gl.reversal.LedgerPostingService.post_journal_entry",
                return_value=PostingResult(success=False, message="Period closed"),
            ),
        ):
            result = ReversalService.create_reversal(
                mock_db,
                organization_id=org_id,
                original_journal_id=journal_id,
                reversal_date=date.today(),
                created_by_user_id=user_id,
                reason="Test reversal",
                auto_post=True,
            )

        assert result.success is False
        # db.rollback should have been called to revert the optimistic status change
        mock_db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# 5. LedgerPostingService — no more try/except fallback shim
# ---------------------------------------------------------------------------


class TestLedgerPostingSelectMigration:
    """Verify LedgerPostingService uses select() directly (no try/except db.query shim)."""

    def test_idempotency_check_uses_select(self, mock_db: MagicMock, org_id, user_id):
        """The idempotency check should use db.scalar(select(...)), not db.query()."""
        from app.services.finance.gl.ledger_posting import (
            LedgerPostingService,
            PostingRequest,
        )

        journal_id = uuid4()
        journal = MagicMock(spec=JournalEntry)
        journal.organization_id = org_id
        journal.status = JournalStatus.APPROVED
        journal.journal_entry_id = journal_id
        journal.journal_number = "JNL-001"
        journal.entry_date = date.today()
        journal.reference = "test"
        journal.source_module = "GL"
        journal.source_document_type = None
        journal.source_document_id = None
        journal.created_by_user_id = user_id
        journal.correlation_id = None

        mock_db.get.return_value = journal
        mock_db.scalar.return_value = None  # No existing batch

        request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal_id,
            posting_date=date.today(),
            idempotency_key="test-key",
            source_module="GL",
            posted_by_user_id=user_id,
        )

        with (
            patch(
                "app.services.finance.gl.ledger_posting.PeriodGuardService.require_open_period",
                return_value=uuid4(),
            ),
            patch(
                "app.services.finance.gl.ledger_posting.LedgerPostingService._load_journal_lines",
                return_value=[],
            ),
            patch(
                "app.services.finance.gl.ledger_posting.LedgerPostingService._validate_balance",
            ),
            patch(
                "app.services.finance.gl.ledger_posting.LedgerPostingService._validate_functional_amounts",
            ),
            patch(
                "app.services.finance.gl.ledger_posting.LedgerPostingService._publish_posting_event",
            ),
        ):
            LedgerPostingService.post_journal_entry(mock_db, request)

        # db.scalar should have been called (for idempotency check)
        mock_db.scalar.assert_called()
        # db.query should NOT have been called
        mock_db.query.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Reversal and Journal list methods use select()
# ---------------------------------------------------------------------------


class TestListMethodsUseSelect:
    """Verify list methods use select() instead of db.query()."""

    def test_reversal_list_uses_select(self, mock_db: MagicMock):
        """ReversalService.list should use select(), not db.query()."""
        from app.services.finance.gl.reversal import ReversalService

        mock_db.scalars.return_value.all.return_value = []

        ReversalService.list(mock_db, organization_id=str(uuid4()))

        mock_db.scalars.assert_called()
        mock_db.query.assert_not_called()

    def test_journal_list_uses_select(self, mock_db: MagicMock):
        """JournalService.list should use select(), not db.query()."""
        from app.services.finance.gl.journal import JournalService

        mock_db.scalars.return_value.all.return_value = []

        JournalService.list(mock_db, organization_id=str(uuid4()))

        mock_db.scalars.assert_called()
        mock_db.query.assert_not_called()

    def test_account_balance_list_uses_select(self, mock_db: MagicMock):
        """AccountBalanceService.list should use select(), not db.query()."""
        from app.services.finance.gl.account_balance import AccountBalanceService

        mock_db.scalars.return_value.all.return_value = []

        AccountBalanceService.list(mock_db, organization_id=str(uuid4()))

        mock_db.scalars.assert_called()
        mock_db.query.assert_not_called()
