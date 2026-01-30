"""
Tests for GL API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.finance import gl as gl_api
from app.schemas.finance.gl import (
    AccountCreate,
    AccountUpdate,
    FiscalPeriodCreate,
    JournalEntryCreate,
    JournalLineCreate,
)
from tests.api.ifrs.conftest import (
    MockAccount,
    MockFiscalPeriod,
    MockJournalEntry,
    MockAccountBalance,
    MockTrialBalance,
    MockPostingResult,
)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


class TestAccountsAPI:
    """Tests for GL accounts endpoints."""

    def test_create_account_success(self, mock_db, mock_auth_dict, org_id):
        """Test successful account creation."""
        mock_account = MockAccount(organization_id=org_id)
        category_id = uuid.uuid4()

        with patch("app.api.finance.gl.chart_of_accounts_service.create_account") as mock_create:
            mock_create.return_value = mock_account
            payload = AccountCreate(
                account_code="1000",
                account_name="Cash",
                account_type="POSTING",
                normal_balance="DEBIT",
            )
            result = gl_api.create_account(
                payload=payload,
                organization_id=org_id,
                category_id=category_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result == mock_account

    def test_get_account_success(self, mock_db, mock_auth_dict, org_id):
        """Test getting an account."""
        mock_account = MockAccount(organization_id=org_id)

        with patch("app.api.finance.gl.chart_of_accounts_service.get") as mock_get:
            mock_get.return_value = mock_account
            result = gl_api.get_account(
                mock_account.account_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.account_code == "1000"

    def test_list_accounts(self, mock_db, mock_auth_dict, org_id):
        """Test listing accounts."""
        mock_accounts = [MockAccount(organization_id=org_id) for _ in range(5)]

        with patch("app.api.finance.gl.chart_of_accounts_service.list") as mock_list:
            mock_list.return_value = mock_accounts

            result = gl_api.list_accounts(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 5
        assert len(result.items) == 5

    def test_list_accounts_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing accounts with filters."""
        mock_accounts = [MockAccount(organization_id=org_id, is_active=True)]

        with patch("app.api.finance.gl.chart_of_accounts_service.list") as mock_list:
            mock_list.return_value = mock_accounts

            result = gl_api.list_accounts(
                organization_id=org_id,
                is_active=True,
                account_type="POSTING",
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1
        mock_list.assert_called_once()

    def test_update_account(self, mock_db, mock_auth_dict, org_id):
        """Test updating an account."""
        mock_account = MockAccount(organization_id=org_id)
        updated_account = MockAccount(
            account_id=mock_account.account_id,
            organization_id=org_id,
            account_name="Updated Cash",
        )

        with patch("app.api.finance.gl.chart_of_accounts_service.update_account") as mock_update:
            mock_update.return_value = updated_account

            payload = AccountUpdate(account_name="Updated Cash")
            result = gl_api.update_account(
                mock_account.account_id,
                payload,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.account_name == "Updated Cash"

    def test_deactivate_account(self, mock_db, mock_auth_dict, org_id):
        """Test deactivating an account."""
        mock_account = MockAccount(organization_id=org_id, is_active=True)
        deactivated = MockAccount(
            account_id=mock_account.account_id,
            organization_id=org_id,
            is_active=False,
        )

        with patch("app.api.finance.gl.chart_of_accounts_service.deactivate_account") as mock_deactivate:
            mock_deactivate.return_value = deactivated

            result = gl_api.deactivate_account(
                mock_account.account_id,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.is_active is False


class TestFiscalPeriodsAPI:
    """Tests for fiscal period endpoints."""

    def test_create_fiscal_period(self, mock_db, mock_auth_dict, org_id):
        """Test creating a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id)

        with patch("app.api.finance.gl.fiscal_period_service.create_period") as mock_create:
            mock_create.return_value = mock_period

            payload = FiscalPeriodCreate(
                fiscal_year_id=uuid.uuid4(),
                period_number=1,
                period_name="January 2024",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )
            result = gl_api.create_fiscal_period(
                payload,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result == mock_period

    def test_get_fiscal_period(self, mock_db, mock_auth_dict, org_id):
        """Test getting a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id)

        with patch("app.api.finance.gl.fiscal_period_service.get") as mock_get:
            mock_get.return_value = mock_period

            result = gl_api.get_fiscal_period(
                mock_period.fiscal_period_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.fiscal_period_id == mock_period.fiscal_period_id

    def test_list_fiscal_periods(self, mock_db, mock_auth_dict, org_id):
        """Test listing fiscal periods."""
        mock_periods = [MockFiscalPeriod(organization_id=org_id, period_number=i) for i in range(12)]

        with patch("app.api.finance.gl.fiscal_period_service.list") as mock_list:
            mock_list.return_value = mock_periods

            result = gl_api.list_fiscal_periods(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 12
        assert len(result.items) == 12

    def test_list_fiscal_periods_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing fiscal periods with filters."""
        mock_periods = [MockFiscalPeriod(organization_id=org_id, status="OPEN")]

        with patch("app.api.finance.gl.fiscal_period_service.list") as mock_list:
            mock_list.return_value = mock_periods

            result = gl_api.list_fiscal_periods(
                organization_id=org_id,
                fiscal_year_id=uuid.uuid4(),
                status="OPEN",
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1

    def test_open_fiscal_period(self, mock_db, mock_auth_dict, org_id):
        """Test opening a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id, status="FUTURE")
        opened_period = MockFiscalPeriod(
            fiscal_period_id=mock_period.fiscal_period_id,
            organization_id=org_id,
            status="OPEN",
        )

        with patch("app.api.finance.gl.fiscal_period_service.open_period") as mock_open:
            mock_open.return_value = opened_period

            result = gl_api.open_fiscal_period(
                mock_period.fiscal_period_id,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.status == "OPEN"

    def test_close_fiscal_period(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test closing a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id, status="OPEN")
        closed_period = MockFiscalPeriod(
            fiscal_period_id=mock_period.fiscal_period_id,
            organization_id=org_id,
            status="CLOSED",
        )

        with patch("app.api.finance.gl.fiscal_period_service.close_period") as mock_close:
            mock_close.return_value = closed_period

            result = gl_api.close_fiscal_period(
                mock_period.fiscal_period_id,
                organization_id=org_id,
                closed_by_user_id=user_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.status == "CLOSED"


class TestJournalEntriesAPI:
    """Tests for journal entry endpoints."""

    def test_create_journal_entry(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test creating a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id)

        with patch("app.api.finance.gl.journal_service.create_entry") as mock_create:
            mock_create.return_value = mock_entry

            payload = JournalEntryCreate(
                fiscal_period_id=uuid.uuid4(),
                journal_date=date.today(),
                description="Test journal entry",
                source_module="GL",
                lines=[
                    JournalLineCreate(
                        account_id=uuid.uuid4(),
                        debit_amount=Decimal("1000.00"),
                        credit_amount=Decimal("0"),
                        currency_code="USD",
                    ),
                    JournalLineCreate(
                        account_id=uuid.uuid4(),
                        debit_amount=Decimal("0"),
                        credit_amount=Decimal("1000.00"),
                        currency_code="USD",
                    ),
                ],
            )
            result = gl_api.create_journal_entry(
                payload,
                organization_id=org_id,
                created_by_user_id=user_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result == mock_entry

    def test_get_journal_entry(self, mock_db, mock_auth_dict, org_id):
        """Test getting a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id)

        with patch("app.api.finance.gl.journal_service.get") as mock_get:
            mock_get.return_value = mock_entry

            result = gl_api.get_journal_entry(
                mock_entry.journal_entry_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.journal_entry_id == mock_entry.journal_entry_id

    def test_list_journal_entries(self, mock_db, mock_auth_dict, org_id):
        """Test listing journal entries."""
        mock_entries = [MockJournalEntry(organization_id=org_id) for _ in range(5)]

        with patch("app.api.finance.gl.journal_service.list") as mock_list:
            mock_list.return_value = mock_entries

            result = gl_api.list_journal_entries(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 5
        assert len(result.items) == 5

    def test_list_journal_entries_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing journal entries with filters."""
        mock_entries = [MockJournalEntry(organization_id=org_id, status="POSTED")]

        with patch("app.api.finance.gl.journal_service.list") as mock_list:
            mock_list.return_value = mock_entries

            result = gl_api.list_journal_entries(
                organization_id=org_id,
                status="POSTED",
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 31),
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1

    def test_post_journal_entry(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test posting a journal entry."""
        mock_result = MockPostingResult(
            success=True,
            entry_id=uuid.uuid4(),
            entry_number="JE-0001",
            message="Posted successfully",
        )

        with patch("app.api.finance.gl.ledger_posting_service.post_entry") as mock_post:
            mock_post.return_value = mock_result

            result = gl_api.post_journal_entry(
                entry_id=uuid.uuid4(),
                organization_id=org_id,
                posted_by_user_id=user_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.success is True

    def test_reverse_journal_entry(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test reversing a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id, status="POSTED")
        reversal_entry = MockJournalEntry(
            organization_id=org_id,
            journal_number="JE-REV-0001",
            status="POSTED",
        )

        with patch("app.api.finance.gl.journal_service.reverse_entry") as mock_reverse:
            mock_reverse.return_value = reversal_entry

            result = gl_api.reverse_journal_entry(
                entry_id=mock_entry.journal_entry_id,
                reversal_date=date.today(),
                organization_id=org_id,
                reversed_by_user_id=user_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.journal_number == "JE-REV-0001"


class TestAccountBalancesAPI:
    """Tests for account balance endpoints."""

    def test_get_account_balance(self, mock_db, mock_auth_dict, org_id):
        """Test getting account balance."""
        mock_account = MockAccount(organization_id=org_id)
        mock_balance = MockAccountBalance(
            account_id=mock_account.account_id,
            account_code=mock_account.account_code,
            account_name=mock_account.account_name,
            fiscal_period_id=uuid.uuid4(),
            opening_debit=Decimal("0"),
            opening_credit=Decimal("0"),
            period_debit=Decimal("1000"),
            period_credit=Decimal("500"),
            closing_debit=Decimal("500"),
            closing_credit=Decimal("0"),
            currency_code="USD",
        )

        with patch("app.services.finance.gl.balance_service.get_balance") as mock_get_balance, \
             patch("app.api.finance.gl.chart_of_accounts_service.get") as mock_get_account:
            mock_get_balance.return_value = mock_balance
            mock_get_account.return_value = mock_account

            result = gl_api.get_account_balance(
                mock_account.account_id,
                fiscal_period_id=mock_balance.fiscal_period_id,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.account_id == mock_account.account_id

    def test_get_account_balance_not_found(self, mock_db, mock_auth_dict, org_id):
        """Test getting non-existent account balance."""
        with patch("app.services.finance.gl.balance_service.get_balance") as mock_get:
            mock_get.return_value = None

            with pytest.raises(HTTPException) as exc:
                gl_api.get_account_balance(
                    uuid.uuid4(),
                    fiscal_period_id=uuid.uuid4(),
                    organization_id=org_id,
                    auth=mock_auth_dict,
                    db=mock_db,
                )

        assert exc.value.status_code == 404

    def test_get_trial_balance(self, mock_db, mock_auth_dict, org_id):
        """Test getting trial balance."""
        fiscal_period_id = uuid.uuid4()
        mock_trial_balance = MockTrialBalance(
            fiscal_period_id=fiscal_period_id,
            period_name="January 2024",
            as_of_date=date.today(),
            lines=[],
            total_debit=Decimal("10000"),
            total_credit=Decimal("10000"),
            is_balanced=True,
        )

        with patch("app.services.finance.gl.balance_service.get_trial_balance") as mock_get:
            mock_get.return_value = mock_trial_balance

            result = gl_api.get_trial_balance(
                organization_id=org_id,
                fiscal_period_id=fiscal_period_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.total_debit == Decimal("10000")
