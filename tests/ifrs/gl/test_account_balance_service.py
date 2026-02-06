"""
Tests for AccountBalanceService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.gl.account_balance import (
    AccountBalanceService,
    BalanceSummary,
)


class MockBalanceType:
    """Mock balance type enum."""

    ACTUAL = "ACTUAL"
    BUDGET = "BUDGET"
    FORECAST = "FORECAST"


class MockFiscalPeriod:
    """Mock fiscal period."""

    def __init__(
        self,
        fiscal_period_id=None,
        fiscal_year_id=None,
        period_number=1,
        start_date=None,
        end_date=None,
    ):
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid4()
        self.period_number = period_number
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 1, 31)


class MockAccountBalance:
    """Mock account balance model."""

    def __init__(
        self,
        account_balance_id=None,
        organization_id=None,
        account_id=None,
        fiscal_period_id=None,
        fiscal_year_id=None,
        balance_type=None,
        currency_code="USD",
        opening_debit=Decimal("0"),
        opening_credit=Decimal("0"),
        period_debit=Decimal("0"),
        period_credit=Decimal("0"),
        closing_debit=Decimal("0"),
        closing_credit=Decimal("0"),
        net_balance=Decimal("0"),
        transaction_count=0,
        business_unit_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
    ):
        self.account_balance_id = account_balance_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.account_id = account_id or uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid4()
        self.balance_type = balance_type or MockBalanceType.ACTUAL
        self.currency_code = currency_code
        self.opening_debit = opening_debit
        self.opening_credit = opening_credit
        self.period_debit = period_debit
        self.period_credit = period_credit
        self.closing_debit = closing_debit
        self.closing_credit = closing_credit
        self.net_balance = net_balance
        self.transaction_count = transaction_count
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
def account_id():
    """Create test account ID."""
    return uuid4()


@pytest.fixture
def fiscal_period():
    """Create test fiscal period."""
    return MockFiscalPeriod()


class TestBalanceSummary:
    """Tests for BalanceSummary dataclass."""

    def test_create_balance_summary(self, account_id):
        """Test creating a balance summary."""
        period_id = uuid4()
        summary = BalanceSummary(
            account_id=account_id,
            account_code="1001",
            fiscal_period_id=period_id,
            balance_type="ACTUAL",
            currency_code="USD",
            opening_balance=Decimal("0"),
            period_debit=Decimal("1000.00"),
            period_credit=Decimal("500.00"),
            closing_balance=Decimal("500.00"),
            net_balance=Decimal("500.00"),
            transaction_count=10,
        )

        assert summary.account_id == account_id
        assert summary.period_debit == Decimal("1000.00")
        assert summary.net_balance == Decimal("500.00")
        assert summary.transaction_count == 10


class TestUpdateBalanceForPosting:
    """Tests for update_balance_for_posting method."""

    def test_update_existing_balance(self, mock_db, org_id, fiscal_period):
        """Test updating an existing account balance."""
        account_id = uuid4()
        existing_balance = MockAccountBalance(
            organization_id=org_id,
            account_id=account_id,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            period_debit=Decimal("500.00"),
            period_credit=Decimal("200.00"),
            transaction_count=5,
        )

        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_balance
        )

        with patch("app.services.finance.gl.account_balance.AccountBalance"):
            with patch(
                "app.services.finance.gl.account_balance.BalanceType", MockBalanceType
            ):
                result = AccountBalanceService.update_balance_for_posting(
                    mock_db,
                    org_id,
                    account_id,
                    fiscal_period.fiscal_period_id,
                    Decimal("100.00"),  # Debit
                    Decimal("0"),  # Credit
                    "USD",
                )

        # Balance should be updated
        mock_db.commit.assert_called()

    def test_create_new_balance(self, mock_db, org_id, fiscal_period):
        """Test creating a new account balance record."""
        account_id = uuid4()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.services.finance.gl.account_balance.AccountBalance") as mock_ab:
            with patch(
                "app.services.finance.gl.account_balance.BalanceType", MockBalanceType
            ):
                result = AccountBalanceService.update_balance_for_posting(
                    mock_db,
                    org_id,
                    account_id,
                    fiscal_period.fiscal_period_id,
                    Decimal("1000.00"),
                    Decimal("0"),
                    "USD",
                )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()


class TestGetBalance:
    """Tests for get_balance method."""

    def test_get_existing_balance(self, mock_db, org_id, account_id, fiscal_period):
        """Test getting an existing balance."""
        balance = MockAccountBalance(
            organization_id=org_id,
            account_id=account_id,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            net_balance=Decimal("500.00"),
        )
        mock_db.query.return_value.filter.return_value.first.return_value = balance

        with patch("app.services.finance.gl.account_balance.AccountBalance"):
            with patch(
                "app.services.finance.gl.account_balance.BalanceType", MockBalanceType
            ):
                result = AccountBalanceService.get_balance(
                    mock_db, org_id, account_id, fiscal_period.fiscal_period_id
                )

        assert result == balance

    def test_get_balance_with_dimensions(
        self, mock_db, org_id, account_id, fiscal_period
    ):
        """Test getting balance filtered by dimensions."""
        business_unit_id = uuid4()
        balance = MockAccountBalance(
            organization_id=org_id,
            account_id=account_id,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            business_unit_id=business_unit_id,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = balance

        with patch("app.services.finance.gl.account_balance.AccountBalance"):
            with patch(
                "app.services.finance.gl.account_balance.BalanceType", MockBalanceType
            ):
                result = AccountBalanceService.get_balance(
                    mock_db,
                    org_id,
                    account_id,
                    fiscal_period.fiscal_period_id,
                    business_unit_id=business_unit_id,
                )

        assert result == balance
        assert result.business_unit_id == business_unit_id


class TestGetAccountBalances:
    """Tests for get_account_balances method."""

    def test_get_balances_for_period(self, mock_db, org_id, fiscal_period):
        """Test getting all account balances for a period."""
        account1_id = uuid4()
        account2_id = uuid4()

        # Create mock aggregated rows (returned from group_by query)
        class MockAggRow:
            def __init__(self, account_id, opening, debit, credit, closing, net, count):
                self.account_id = account_id
                self.opening_balance = opening
                self.period_debit = debit
                self.period_credit = credit
                self.closing_balance = closing
                self.net_balance = net
                self.transaction_count = count

        agg_rows = [
            MockAggRow(
                account1_id,
                Decimal("0"),
                Decimal("100"),
                Decimal("50"),
                Decimal("50"),
                Decimal("50"),
                5,
            ),
            MockAggRow(
                account2_id,
                Decimal("0"),
                Decimal("200"),
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                3,
            ),
        ]

        # Create mock accounts
        class MockAccount:
            def __init__(self, account_id, account_code):
                self.account_id = account_id
                self.account_code = account_code

        accounts = [
            MockAccount(account1_id, "1001"),
            MockAccount(account2_id, "1002"),
        ]

        # Setup query chain - first returns agg rows, second returns accounts
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.group_by.return_value = mock_query
        mock_query.all.side_effect = [agg_rows, accounts]
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.account_balance.AccountBalance"):
            with patch("app.services.finance.gl.account_balance.Account"):
                with patch(
                    "app.services.finance.gl.account_balance.BalanceType",
                    MockBalanceType,
                ):
                    with patch(
                        "app.services.finance.gl.account_balance.and_",
                        return_value=MagicMock(),
                    ):
                        with patch("app.services.finance.gl.account_balance.func"):
                            result = AccountBalanceService.get_account_balances(
                                mock_db, org_id, fiscal_period.fiscal_period_id
                            )

        assert len(result) == 2


class TestListBalances:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing balances with filters."""
        balances = [MockAccountBalance(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = balances
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.gl.account_balance.AccountBalance"):
            result = AccountBalanceService.list(
                mock_db, organization_id=str(org_id), limit=50, offset=0
            )

        assert result == balances


class TestGetYTDBalance:
    """Tests for get_ytd_balance method."""

    def test_get_ytd_balance(self, mock_db, org_id, account_id, fiscal_period):
        """Test getting year-to-date balance."""
        fiscal_year_id = uuid4()
        up_to_period_id = uuid4()

        # Mock the target period
        mock_target_period = MockFiscalPeriod(
            fiscal_period_id=up_to_period_id,
            fiscal_year_id=fiscal_year_id,
            period_number=3,
        )
        mock_db.get.return_value = mock_target_period

        # Mock the query chain for periods and balance sum
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [mock_target_period]
        mock_query.scalar.return_value = Decimal("2000.00")
        mock_db.query.return_value = mock_query

        # Create a mock column that supports comparisons
        class MockColumn:
            def __le__(self, other):
                return MagicMock()

            def __eq__(self, other):
                return MagicMock()

            def in_(self, values):
                return MagicMock()

        with patch("app.services.finance.gl.account_balance.AccountBalance") as mock_ab:
            mock_ab.organization_id = MockColumn()
            mock_ab.account_id = MockColumn()
            mock_ab.fiscal_period_id = MockColumn()
            mock_ab.balance_type = MockColumn()
            mock_ab.net_balance = MagicMock()

            with patch(
                "app.services.finance.gl.account_balance.FiscalPeriod"
            ) as mock_fp:
                mock_fp.fiscal_year_id = MockColumn()
                mock_fp.organization_id = MockColumn()
                mock_fp.period_number = MockColumn()

                with patch(
                    "app.services.finance.gl.account_balance.BalanceType",
                    MockBalanceType,
                ):
                    with patch(
                        "app.services.finance.gl.account_balance.and_",
                        return_value=MagicMock(),
                    ):
                        with patch(
                            "app.services.finance.gl.account_balance.func"
                        ) as mock_func:
                            mock_func.sum.return_value = MagicMock()

                            result = AccountBalanceService.get_ytd_balance(
                                mock_db,
                                org_id,
                                account_id,
                                fiscal_year_id,
                                up_to_period_id,
                            )

        assert result == Decimal("2000.00")
