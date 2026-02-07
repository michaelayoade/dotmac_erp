"""
Tests for app/services/ifrs/gl/bulk.py

Tests for AccountBulkService that handles bulk operations on GL account entities.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.bulk.conftest import MockAccount, MockAccountType, MockAccountCategory


# ============ TestCanDelete ============


class TestCanDelete:
    """Tests for the can_delete method."""

    def test_cannot_delete_control_account(
        self, mock_db, mock_control_account, organization_id
    ):
        """Control accounts cannot be deleted."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_control_account)

            assert can_delete is False
            assert "control account" in reason.lower()

    def test_can_delete_non_control(self, mock_db, mock_account, organization_id):
        """Non-control accounts without journal entries can be deleted."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_account)

            assert can_delete is True
            assert reason == ""

    def test_cannot_delete_with_journal_lines(
        self, mock_db, mock_account, organization_id
    ):
        """Accounts with journal lines cannot be deleted."""
        mock_db.query.return_value.filter.return_value.count.return_value = 50

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_account)

            assert can_delete is False
            assert "50 journal entries" in reason

    def test_can_delete_no_journal_lines(self, mock_db, mock_account, organization_id):
        """Accounts without journal lines can be deleted."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_account)

            assert can_delete is True
            assert reason == ""

    def test_returns_journal_count(self, mock_db, organization_id):
        """Error message should include the journal entry count."""
        account = MockAccount(account_name="Test Account")
        mock_db.query.return_value.filter.return_value.count.return_value = 100

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(account)

            assert "100 journal entries" in reason

    def test_control_checked_before_journals(
        self, mock_db, mock_control_account, organization_id
    ):
        """Control account check should run before journal check."""
        # Even if there are no journal entries, control account should fail
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_control_account)

            assert can_delete is False
            assert "control account" in reason.lower()

    def test_error_includes_account_name(self, mock_db, organization_id):
        """Error message should include the account name."""
        account = MockAccount(account_name="Cash at Bank", is_control_account=False)
        mock_db.query.return_value.filter.return_value.count.return_value = 25

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(account)

            assert "Cash at Bank" in reason

    def test_error_includes_control_account_name(self, mock_db, organization_id):
        """Control account error should include account name."""
        account = MockAccount(account_name="AR Control", is_control_account=True)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(account)

            assert "AR Control" in reason

    def test_returns_tuple_format(self, mock_db, mock_account, organization_id):
        """Method should return a tuple of (bool, str)."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = service.can_delete(mock_account)

            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            assert isinstance(result[1], str)


# ============ TestGetExportValue ============


class TestGetExportValue:
    """Tests for the _get_export_value method."""

    def test_export_account_type_posting(self, mock_db, organization_id):
        """Should export account_type enum value as string."""
        account = MockAccount(account_type=MockAccountType.posting)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_type")

            assert value == "posting"

    def test_export_account_type_control(self, mock_db, organization_id):
        """Should export control type correctly."""
        account = MockAccount(account_type=MockAccountType.control)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_type")

            assert value == "control"

    def test_export_account_type_none(self, mock_db, organization_id):
        """Should handle None account_type."""
        account = MockAccount(account_type=None)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_type")

            assert value == ""

    def test_export_account_category_assets(self, mock_db, organization_id):
        """Should export assets category."""
        account = MockAccount(account_category=MockAccountCategory.assets)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == "assets"

    def test_export_account_category_liabilities(self, mock_db, organization_id):
        """Should export liabilities category."""
        account = MockAccount(account_category=MockAccountCategory.liabilities)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == "liabilities"

    def test_export_account_category_equity(self, mock_db, organization_id):
        """Should export equity category."""
        account = MockAccount(account_category=MockAccountCategory.equity)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == "equity"

    def test_export_account_category_revenue(self, mock_db, organization_id):
        """Should export revenue category."""
        account = MockAccount(account_category=MockAccountCategory.revenue)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == "revenue"

    def test_export_account_category_expenses(self, mock_db, organization_id):
        """Should export expenses category."""
        account = MockAccount(account_category=MockAccountCategory.expenses)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == "expenses"

    def test_export_account_category_none(self, mock_db, organization_id):
        """Should handle None account_category."""
        account = MockAccount(account_category=None)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "account_category")

            assert value == ""

    def test_export_is_control_account_true(self, mock_db, organization_id):
        """Should export is_control_account boolean."""
        account = MockAccount(is_control_account=True)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "is_control_account")

            assert value == "True"

    def test_export_is_control_account_false(self, mock_db, organization_id):
        """Should export is_control_account false value."""
        account = MockAccount(is_control_account=False)

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            value = service._get_export_value(account, "is_control_account")

            assert value == "False"

    def test_export_simple_field_delegates(self, mock_db, organization_id):
        """Simple fields should delegate to parent class."""
        account = MockAccount(
            account_code="1000",
            account_name="Cash",
            currency_code="EUR",
            description="Cash at bank",
        )

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            code_value = service._get_export_value(account, "account_code")
            name_value = service._get_export_value(account, "account_name")
            currency_value = service._get_export_value(account, "currency_code")
            desc_value = service._get_export_value(account, "description")

            assert code_value == "1000"
            assert name_value == "Cash"
            assert currency_value == "EUR"
            assert desc_value == "Cash at bank"


# ============ TestGetExportFilename ============


class TestGetExportFilename:
    """Tests for the _get_export_filename method."""

    def test_filename_includes_accounts(self, mock_db, organization_id):
        """Filename should include 'accounts'."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert "accounts" in filename

    def test_filename_includes_timestamp(self, mock_db, organization_id):
        """Filename should include a timestamp."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            parts = filename.replace(".csv", "").split("_")
            assert len(parts) >= 3

    def test_filename_ends_csv(self, mock_db, organization_id):
        """Filename should end with .csv."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert filename.endswith(".csv")


# ============ TestBulkDelete ============


class TestBulkDelete:
    """Tests for the bulk_delete method."""

    @pytest.mark.asyncio
    async def test_bulk_delete_all_success(self, mock_db, organization_id):
        """All non-control accounts without journals should be deleted."""
        account1 = MockAccount(is_control_account=False)
        account2 = MockAccount(is_control_account=False)

        mock_db.query.return_value.filter.return_value.all.return_value = [
            account1,
            account2,
        ]
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [account1.account_id, account2.account_id]
            )

            assert result.success_count == 2
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_bulk_delete_control_account_blocked(self, mock_db, organization_id):
        """Control accounts should fail to delete."""
        control_account = MockAccount(
            account_name="AR Control", is_control_account=True
        )

        mock_db.query.return_value.filter.return_value.all.return_value = [
            control_account
        ]

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = await service.bulk_delete([control_account.account_id])

            assert result.success_count == 0
            assert result.failed_count == 1
            assert "control account" in result.errors[0].lower()

    @pytest.mark.asyncio
    async def test_bulk_delete_with_journals_blocked(self, mock_db, organization_id):
        """Accounts with journal entries should fail to delete."""
        account = MockAccount(account_name="Revenue", is_control_account=False)

        mock_db.query.return_value.filter.return_value.all.return_value = [account]
        mock_db.query.return_value.filter.return_value.count.return_value = 100

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = await service.bulk_delete([account.account_id])

            assert result.success_count == 0
            assert result.failed_count == 1
            assert "journal entries" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_partial(self, mock_db, organization_id):
        """Mix of deletable and non-deletable accounts."""
        deletable = MockAccount(account_name="Deletable", is_control_account=False)
        control = MockAccount(account_name="Control", is_control_account=True)

        mock_db.query.return_value.filter.return_value.all.return_value = [
            deletable,
            control,
        ]
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [deletable.account_id, control.account_id]
            )

            assert result.success_count == 1
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_bulk_delete_empty_ids(self, mock_db, organization_id):
        """Empty IDs list should return failure."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            result = await service.bulk_delete([])

            assert result.success_count == 0
            assert "No IDs provided" in result.errors[0]


# ============ TestBulkExport ============


class TestBulkExport:
    """Tests for the bulk_export method."""

    @pytest.mark.asyncio
    async def test_export_csv_headers(self, mock_db, mock_account, organization_id):
        """CSV export should include correct headers."""
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_account]

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_account.account_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            headers = content.split("\n")[0]
            assert "Account Code" in headers
            assert "Account Name" in headers
            assert "Account Type" in headers
            assert "Category" in headers
            assert "Normal Balance" in headers

    @pytest.mark.asyncio
    async def test_export_csv_data(self, mock_db, mock_account, organization_id):
        """CSV export should include entity data."""
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_account]

        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import AccountBulkService

            service = AccountBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_account.account_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            assert mock_account.account_name in content
            assert mock_account.account_code in content


# ============ TestFactoryFunction ============


class TestFactoryFunction:
    """Tests for the get_account_bulk_service factory function."""

    def test_factory_creates_service(self, mock_db, organization_id, user_id):
        """Factory should create AccountBulkService instance."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import get_account_bulk_service

            service = get_account_bulk_service(mock_db, organization_id, user_id)

            assert service.db is mock_db
            assert service.organization_id == organization_id
            assert service.user_id == user_id

    def test_factory_user_id_optional(self, mock_db, organization_id):
        """Factory should work without user_id."""
        with patch("app.services.finance.gl.bulk.Account", MagicMock()):
            from app.services.finance.gl.bulk import get_account_bulk_service

            service = get_account_bulk_service(mock_db, organization_id)

            assert service.user_id is None
