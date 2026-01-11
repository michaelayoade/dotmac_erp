"""
Tests for FiscalYearService.
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ifrs.gl.fiscal_year import (
    FiscalYearService,
    FiscalYearInput,
)
from app.models.ifrs.gl.fiscal_period import PeriodStatus
from tests.ifrs.gl.conftest import (
    MockFiscalYear,
    MockFiscalPeriod,
    MockPeriodStatus,
)


@pytest.fixture
def service():
    """Create FiscalYearService instance."""
    return FiscalYearService()


@pytest.fixture
def sample_year_input():
    """Create sample fiscal year input."""
    return FiscalYearInput(
        year_code="FY2024",
        year_name="Fiscal Year 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )


class TestCreateYear:
    """Tests for create_year method."""

    def test_create_year_success(self, service, mock_db, org_id, sample_year_input):
        """Test successful fiscal year creation."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_year(mock_db, org_id, sample_year_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_year_duplicate_fails(self, service, mock_db, org_id, sample_year_input):
        """Test that duplicate year code fails."""
        from fastapi import HTTPException

        existing = MockFiscalYear(
            organization_id=org_id,
            year_code=sample_year_input.year_code,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            service.create_year(mock_db, org_id, sample_year_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_adjustment_year(self, service, mock_db, org_id):
        """Test creating an adjustment year."""
        input_data = FiscalYearInput(
            year_code="FY2024-ADJ",
            year_name="FY2024 Adjustment",
            start_date=date(2024, 12, 31),
            end_date=date(2024, 12, 31),
            is_adjustment_year=True,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_year(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()

    def test_create_year_with_retained_earnings_account(self, service, mock_db, org_id):
        """Test creating a fiscal year with retained earnings account."""
        re_account_id = uuid4()
        input_data = FiscalYearInput(
            year_code="FY2024",
            year_name="Fiscal Year 2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            retained_earnings_account_id=re_account_id,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_year(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()


class TestCloseYear:
    """Tests for close_year method."""

    def test_close_year_success(self, service, mock_db, org_id, user_id):
        """Test successful fiscal year closing."""
        year = MockFiscalYear(organization_id=org_id, is_closed=False)
        mock_db.get.return_value = year
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        result = service.close_year(mock_db, org_id, year.fiscal_year_id, user_id)

        mock_db.commit.assert_called_once()
        assert result.is_closed is True

    def test_close_year_not_found(self, service, mock_db, org_id, user_id):
        """Test closing non-existent fiscal year."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.close_year(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_close_year_wrong_org(self, service, mock_db, org_id, user_id):
        """Test closing fiscal year from wrong organization."""
        from fastapi import HTTPException

        year = MockFiscalYear(organization_id=uuid4())  # Different org
        mock_db.get.return_value = year

        with pytest.raises(HTTPException) as exc:
            service.close_year(mock_db, org_id, year.fiscal_year_id, user_id)

        assert exc.value.status_code == 404

    def test_close_already_closed_year_fails(self, service, mock_db, org_id, user_id):
        """Test closing an already closed fiscal year fails."""
        from fastapi import HTTPException

        year = MockFiscalYear(organization_id=org_id, is_closed=True)
        mock_db.get.return_value = year

        with pytest.raises(HTTPException) as exc:
            service.close_year(mock_db, org_id, year.fiscal_year_id, user_id)

        assert exc.value.status_code == 400
        assert "already closed" in exc.value.detail

    def test_close_year_with_open_periods_fails(self, service, mock_db, org_id, user_id):
        """Test closing year with open periods fails."""
        from fastapi import HTTPException

        year = MockFiscalYear(organization_id=org_id, is_closed=False)
        mock_db.get.return_value = year
        mock_db.query.return_value.filter.return_value.count.return_value = 3  # 3 open periods

        with pytest.raises(HTTPException) as exc:
            service.close_year(mock_db, org_id, year.fiscal_year_id, user_id)

        assert exc.value.status_code == 400
        assert "not hard closed" in exc.value.detail


class TestGetYear:
    """Tests for get method."""

    def test_get_existing_year(self, service, mock_db, org_id):
        """Test getting existing fiscal year."""
        year = MockFiscalYear(organization_id=org_id)
        mock_db.get.return_value = year

        result = service.get(mock_db, str(year.fiscal_year_id))

        assert result == year

    def test_get_nonexistent_year(self, service, mock_db):
        """Test getting non-existent fiscal year."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetYearByCode:
    """Tests for get_by_code method."""

    def test_get_by_code_success(self, service, mock_db, org_id):
        """Test getting fiscal year by code."""
        year = MockFiscalYear(organization_id=org_id, year_code="FY2024")
        mock_db.query.return_value.filter.return_value.first.return_value = year

        result = service.get_by_code(mock_db, org_id, "FY2024")

        assert result == year

    def test_get_by_code_not_found(self, service, mock_db, org_id):
        """Test getting non-existent fiscal year by code."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get_by_code(mock_db, org_id, "FY9999")

        assert exc.value.status_code == 404


class TestListYears:
    """Tests for list method."""

    def test_list_all_years(self, service, mock_db, org_id):
        """Test listing all fiscal years."""
        years = [MockFiscalYear(organization_id=org_id) for _ in range(3)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            years
        )

        result = service.list(mock_db, organization_id=str(org_id))

        assert len(result) == 3

    def test_list_open_years(self, service, mock_db, org_id):
        """Test listing only open fiscal years."""
        years = [MockFiscalYear(organization_id=org_id, is_closed=False)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            years
        )

        result = service.list(mock_db, organization_id=str(org_id), is_closed=False)

        assert len(result) == 1

    def test_list_closed_years(self, service, mock_db, org_id):
        """Test listing only closed fiscal years."""
        years = [MockFiscalYear(organization_id=org_id, is_closed=True)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            years
        )

        result = service.list(mock_db, organization_id=str(org_id), is_closed=True)

        assert len(result) == 1

    def test_list_with_pagination(self, service, mock_db, org_id):
        """Test listing fiscal years with pagination."""
        years = [MockFiscalYear(organization_id=org_id)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            years
        )

        result = service.list(mock_db, organization_id=str(org_id), limit=10, offset=5)

        assert len(result) == 1
