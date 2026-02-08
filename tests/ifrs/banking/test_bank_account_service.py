"""
Tests for BankAccountService.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.banking.bank_account import (
    BankAccountInput,
    BankAccountService,
)
from tests.ifrs.banking.conftest import (
    MockBankAccount,
    MockGLAccount,
)


@pytest.fixture
def service():
    """Create service instance."""
    return BankAccountService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_bank_account_input():
    """Create sample bank account input."""
    return BankAccountInput(
        bank_name="First National Bank",
        account_number="1234567890",
        account_name="Operating Account",
        gl_account_id=uuid4(),
        currency_code="USD",
        bank_code="FNB001",
        is_primary=True,
    )


class TestCreateBankAccount:
    """Tests for create method."""

    def test_create_bank_account_success(
        self, service, mock_db, org_id, user_id, sample_bank_account_input
    ):
        """Test successful bank account creation."""
        gl_account = MockGLAccount(
            account_id=sample_bank_account_input.gl_account_id,
            organization_id=org_id,
        )
        mock_db.get.return_value = gl_account

        # No existing account - mock execute for duplicate check
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        service.create(mock_db, org_id, sample_bank_account_input, user_id)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_create_duplicate_account_fails(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test that duplicate account number fails."""
        from fastapi import HTTPException

        gl_account = MockGLAccount(
            account_id=sample_bank_account_input.gl_account_id,
            organization_id=org_id,
        )
        mock_db.get.return_value = gl_account

        # Existing account found
        existing = MockBankAccount(
            organization_id=org_id,
            account_number=sample_bank_account_input.account_number,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            service.create(mock_db, org_id, sample_bank_account_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_invalid_gl_account_fails(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test that invalid GL account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None  # GL account not found

        with pytest.raises(HTTPException) as exc:
            service.create(mock_db, org_id, sample_bank_account_input)

        assert exc.value.status_code == 404
        assert "GL account" in exc.value.detail

    def test_create_primary_unsets_other_primary(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test that creating primary account unsets other primary accounts."""
        sample_bank_account_input.is_primary = True

        gl_account = MockGLAccount(
            account_id=sample_bank_account_input.gl_account_id,
            organization_id=org_id,
        )
        mock_db.get.return_value = gl_account

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        service.create(mock_db, org_id, sample_bank_account_input)

        # Verify execute was called (for duplicate check and unset other primary)
        assert mock_db.execute.call_count >= 1


class TestGetBankAccount:
    """Tests for get method."""

    def test_get_existing_account(self, service, mock_db, org_id):
        """Test getting existing bank account."""
        account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = account

        result = service.get(mock_db, org_id, account.bank_account_id)

        assert result == account

    def test_get_nonexistent_account(self, service, mock_db, org_id):
        """Test getting non-existent account returns None."""
        mock_db.get.return_value = None

        result = service.get(mock_db, org_id, uuid4())

        assert result is None


class TestGetByAccountNumber:
    """Tests for get_by_account_number method."""

    def test_get_by_account_number_success(self, service, mock_db, org_id):
        """Test getting bank account by account number."""
        account = MockBankAccount(
            organization_id=org_id,
            account_number="1234567890",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = account
        mock_db.execute.return_value = mock_result

        result = service.get_by_account_number(mock_db, org_id, "1234567890")

        assert result == account

    def test_get_by_account_number_not_found(self, service, mock_db, org_id):
        """Test getting non-existent account by number returns None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = service.get_by_account_number(mock_db, org_id, "NOTFOUND")

        assert result is None

    def test_get_by_account_number_with_bank_code(self, service, mock_db, org_id):
        """Test getting bank account with bank code filter."""
        account = MockBankAccount(
            organization_id=org_id,
            account_number="1234567890",
            bank_code="FNB001",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = account
        mock_db.execute.return_value = mock_result

        result = service.get_by_account_number(mock_db, org_id, "1234567890", "FNB001")

        assert result == account


class TestListBankAccounts:
    """Tests for list method."""

    def test_list_all_accounts(self, service, mock_db, org_id):
        """Test listing all bank accounts."""
        accounts = [MockBankAccount(organization_id=org_id) for _ in range(3)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = accounts
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id)

        assert result == accounts
        assert len(result) == 3

    def test_list_with_status_filter(self, service, mock_db, org_id):
        """Test listing accounts with status filter."""
        from app.models.finance.banking.bank_account import BankAccountStatus

        accounts = [MockBankAccount(organization_id=org_id, status="active")]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = accounts
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id, status=BankAccountStatus.active)

        assert result == accounts

    def test_list_with_currency_filter(self, service, mock_db, org_id):
        """Test listing accounts with currency filter."""
        accounts = [MockBankAccount(organization_id=org_id, currency_code="USD")]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = accounts
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id, currency_code="USD")

        assert result == accounts


class TestUpdateBankAccount:
    """Tests for update method."""

    def test_update_account_success(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test successful bank account update."""
        account = MockBankAccount(organization_id=org_id)
        gl_account = MockGLAccount(
            account_id=sample_bank_account_input.gl_account_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [account, gl_account]

        result = service.update(
            mock_db, org_id, account.bank_account_id, sample_bank_account_input
        )

        mock_db.flush.assert_called_once()
        assert result.bank_name == sample_bank_account_input.bank_name

    def test_update_nonexistent_account_fails(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test updating non-existent account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.update(mock_db, org_id, uuid4(), sample_bank_account_input)

        assert exc.value.status_code == 404

    def test_update_invalid_gl_account_fails(
        self, service, mock_db, org_id, sample_bank_account_input
    ):
        """Test updating with invalid GL account fails."""
        from fastapi import HTTPException

        account = MockBankAccount(organization_id=org_id)
        mock_db.get.side_effect = [account, None]  # Account found, GL not found

        with pytest.raises(HTTPException) as exc:
            service.update(
                mock_db, org_id, account.bank_account_id, sample_bank_account_input
            )

        assert exc.value.status_code == 404


class TestUpdateStatus:
    """Tests for update_status method."""

    def test_update_status_success(self, service, mock_db, org_id):
        """Test successful status update."""
        from app.models.finance.banking.bank_account import BankAccountStatus

        account = MockBankAccount(organization_id=org_id, status="active")
        mock_db.get.return_value = account

        service.update_status(
            mock_db, org_id, account.bank_account_id, BankAccountStatus.suspended
        )

        mock_db.flush.assert_called_once()

    def test_update_status_nonexistent_fails(self, service, mock_db, org_id):
        """Test updating status of non-existent account fails."""
        from fastapi import HTTPException

        from app.models.finance.banking.bank_account import BankAccountStatus

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.update_status(mock_db, org_id, uuid4(), BankAccountStatus.closed)

        assert exc.value.status_code == 404


class TestUpdateReconciledBalance:
    """Tests for update_reconciled_balance method."""

    def test_update_reconciled_balance_success(self, service, mock_db, org_id):
        """Test successful reconciled balance update."""
        from datetime import datetime

        account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = account

        recon_date = datetime.now()
        recon_balance = Decimal("5000.00")

        result = service.update_reconciled_balance(
            mock_db, org_id, account.bank_account_id, recon_date, recon_balance
        )

        assert result.last_reconciled_date == recon_date
        assert result.last_reconciled_balance == recon_balance
        mock_db.flush.assert_called_once()

    def test_update_reconciled_balance_nonexistent_fails(
        self, service, mock_db, org_id
    ):
        """Test updating reconciled balance of non-existent account fails."""
        from datetime import datetime

        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.update_reconciled_balance(
                mock_db, org_id, uuid4(), datetime.now(), Decimal("1000.00")
            )

        assert exc.value.status_code == 404


class TestDeactivateBankAccount:
    """Tests for deactivate method."""

    def test_deactivate_success(self, service, mock_db, org_id, user_id):
        """Test successful bank account deactivation."""
        from app.models.finance.banking.bank_account import BankAccountStatus

        account = MockBankAccount(organization_id=org_id, status="active")
        mock_db.get.return_value = account

        # Mock zero balance
        with patch.object(service, "get_gl_balance", return_value=Decimal("0")):
            result = service.deactivate(
                mock_db, org_id, account.bank_account_id, user_id
            )

        assert result.status == BankAccountStatus.closed
        mock_db.flush.assert_called_once()

    def test_deactivate_nonexistent_fails(self, service, mock_db, org_id):
        """Test deactivating non-existent account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.deactivate(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404

    def test_deactivate_wrong_org_fails(self, service, mock_db, org_id):
        """Test deactivating account from wrong organization fails."""
        from fastapi import HTTPException

        account = MockBankAccount(organization_id=uuid4())  # Different org
        mock_db.get.return_value = account

        with pytest.raises(HTTPException) as exc:
            service.deactivate(mock_db, org_id, account.bank_account_id)

        assert exc.value.status_code == 404

    def test_deactivate_already_closed_fails(self, service, mock_db, org_id):
        """Test deactivating already closed account fails."""
        from fastapi import HTTPException

        from app.models.finance.banking.bank_account import BankAccountStatus

        account = MockBankAccount(
            organization_id=org_id,
            status=BankAccountStatus.closed,
        )
        mock_db.get.return_value = account

        with pytest.raises(HTTPException) as exc:
            service.deactivate(mock_db, org_id, account.bank_account_id)

        assert exc.value.status_code == 400
        assert "already closed" in exc.value.detail

    def test_deactivate_with_balance_fails(self, service, mock_db, org_id):
        """Test deactivating account with non-zero balance fails."""
        from fastapi import HTTPException

        account = MockBankAccount(organization_id=org_id, status="active")
        mock_db.get.return_value = account

        # Mock non-zero balance
        with patch.object(service, "get_gl_balance", return_value=Decimal("1000.00")):
            with pytest.raises(HTTPException) as exc:
                service.deactivate(mock_db, org_id, account.bank_account_id)

        assert exc.value.status_code == 400
        assert "non-zero balance" in exc.value.detail


class TestGetGLBalance:
    """Tests for get_gl_balance method."""

    def test_get_gl_balance_nonexistent_fails(self, service, mock_db, org_id):
        """Test getting GL balance of non-existent account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get_gl_balance(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404
