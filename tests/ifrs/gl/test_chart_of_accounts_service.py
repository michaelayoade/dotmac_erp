"""
Tests for ChartOfAccountsService.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ifrs.gl.chart_of_accounts import (
    ChartOfAccountsService,
    AccountInput,
)
from tests.ifrs.gl.conftest import (
    MockAccount,
    MockAccountType,
    MockNormalBalance,
)


@pytest.fixture
def service():
    """Create ChartOfAccountsService instance."""
    return ChartOfAccountsService()


@pytest.fixture
def sample_account_input():
    """Create sample account input."""
    return AccountInput(
        account_code="1000",
        account_name="Cash",
        category_id=uuid4(),
        normal_balance=MockNormalBalance.DEBIT.value,
        account_type=MockAccountType.POSTING.value,
        description="Cash and cash equivalents",
        is_cash_equivalent=True,
    )


class TestCreateAccount:
    """Tests for create_account method."""

    def test_create_account_success(self, service, mock_db, org_id, sample_account_input):
        """Test successful account creation."""
        # No existing account
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_account(mock_db, org_id, sample_account_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_account_duplicate_fails(self, service, mock_db, org_id, sample_account_input):
        """Test that duplicate account code fails."""
        from fastapi import HTTPException

        existing = MockAccount(
            organization_id=org_id,
            account_code=sample_account_input.account_code,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            service.create_account(mock_db, org_id, sample_account_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_multi_currency_account(self, service, mock_db, org_id):
        """Test creating a multi-currency account."""
        input_data = AccountInput(
            account_code="1100",
            account_name="Foreign Currency Cash",
            category_id=uuid4(),
            normal_balance=MockNormalBalance.DEBIT.value,
            is_multi_currency=True,
            default_currency_code="EUR",
        )
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_account(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()


class TestUpdateAccount:
    """Tests for update_account method."""

    def test_update_account_success(self, service, mock_db, org_id):
        """Test successful account update."""
        account = MockAccount(organization_id=org_id)
        mock_db.get.return_value = account

        result = service.update_account(
            mock_db,
            org_id,
            account.account_id,
            account_name="Updated Cash Account",
            description="Updated description",
        )

        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()
        assert result.account_name == "Updated Cash Account"

    def test_update_account_not_found(self, service, mock_db, org_id):
        """Test updating non-existent account."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.update_account(
                mock_db,
                org_id,
                uuid4(),
                account_name="New Name",
            )

        assert exc.value.status_code == 404

    def test_update_account_wrong_org(self, service, mock_db, org_id):
        """Test updating account from wrong organization."""
        from fastapi import HTTPException

        account = MockAccount(organization_id=uuid4())  # Different org
        mock_db.get.return_value = account

        with pytest.raises(HTTPException) as exc:
            service.update_account(
                mock_db,
                org_id,
                account.account_id,
                account_name="New Name",
            )

        assert exc.value.status_code == 404

    def test_update_account_deactivate(self, service, mock_db, org_id):
        """Test deactivating an account."""
        account = MockAccount(organization_id=org_id, is_active=True)
        mock_db.get.return_value = account

        result = service.update_account(
            mock_db,
            org_id,
            account.account_id,
            is_active=False,
        )

        assert result.is_active is False

    def test_update_account_disable_posting(self, service, mock_db, org_id):
        """Test disabling posting for an account."""
        account = MockAccount(organization_id=org_id, is_posting_allowed=True)
        mock_db.get.return_value = account

        result = service.update_account(
            mock_db,
            org_id,
            account.account_id,
            is_posting_allowed=False,
        )

        assert result.is_posting_allowed is False


class TestGetAccount:
    """Tests for get method."""

    def test_get_existing_account(self, service, mock_db, org_id):
        """Test getting existing account."""
        account = MockAccount(organization_id=org_id)
        mock_db.get.return_value = account

        result = service.get(mock_db, str(account.account_id))

        assert result == account

    def test_get_nonexistent_account(self, service, mock_db):
        """Test getting non-existent account."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetAccountByCode:
    """Tests for get_by_code method."""

    def test_get_by_code_success(self, service, mock_db, org_id):
        """Test getting account by code."""
        account = MockAccount(organization_id=org_id, account_code="1000")
        mock_db.query.return_value.filter.return_value.first.return_value = account

        result = service.get_by_code(mock_db, org_id, "1000")

        assert result == account

    def test_get_by_code_not_found(self, service, mock_db, org_id):
        """Test getting non-existent account by code."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get_by_code(mock_db, org_id, "9999")

        assert exc.value.status_code == 404


class TestListAccounts:
    """Tests for list method."""

    def test_list_all_accounts(self, service, mock_db, org_id):
        """Test listing all accounts."""
        accounts = [MockAccount(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            accounts
        )

        result = service.list(mock_db, organization_id=str(org_id))

        assert len(result) == 5

    def test_list_with_category_filter(self, service, mock_db, org_id):
        """Test listing accounts with category filter."""
        category_id = uuid4()
        accounts = [MockAccount(organization_id=org_id, category_id=category_id)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            accounts
        )

        result = service.list(mock_db, organization_id=str(org_id), category_id=str(category_id))

        assert len(result) == 1

    def test_list_with_active_filter(self, service, mock_db, org_id):
        """Test listing active accounts only."""
        accounts = [MockAccount(organization_id=org_id, is_active=True)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            accounts
        )

        result = service.list(mock_db, organization_id=str(org_id), is_active=True)

        assert len(result) == 1

    def test_list_with_search(self, service, mock_db, org_id):
        """Test listing accounts with search term."""
        accounts = [MockAccount(organization_id=org_id, account_name="Cash")]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            accounts
        )

        result = service.list(mock_db, organization_id=str(org_id), search="Cash")

        assert len(result) == 1

    def test_list_with_pagination(self, service, mock_db, org_id):
        """Test listing accounts with pagination."""
        accounts = [MockAccount(organization_id=org_id)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            accounts
        )

        result = service.list(mock_db, organization_id=str(org_id), limit=10, offset=5)

        assert len(result) == 1
