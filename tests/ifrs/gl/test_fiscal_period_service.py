"""
Tests for FiscalPeriodService.
"""

from datetime import date
from uuid import uuid4

import pytest

from app.services.finance.gl.fiscal_period import (
    FiscalPeriodService,
    FiscalPeriodInput,
)
from app.models.finance.gl.fiscal_period import PeriodStatus
from tests.ifrs.gl.conftest import (
    MockFiscalPeriod,
)


@pytest.fixture
def service():
    """Create FiscalPeriodService instance."""
    return FiscalPeriodService()


@pytest.fixture
def sample_period_input():
    """Create sample period input."""
    return FiscalPeriodInput(
        fiscal_year_id=uuid4(),
        period_number=1,
        period_name="January 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )


class TestCreatePeriod:
    """Tests for create_period method."""

    def test_create_period_success(self, service, mock_db, org_id, sample_period_input):
        """Test successful period creation."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_period(mock_db, org_id, sample_period_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_period_duplicate_fails(
        self, service, mock_db, org_id, sample_period_input
    ):
        """Test that duplicate period number fails."""
        from fastapi import HTTPException

        existing = MockFiscalPeriod(
            fiscal_year_id=sample_period_input.fiscal_year_id,
            period_number=sample_period_input.period_number,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            service.create_period(mock_db, org_id, sample_period_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_adjustment_period(self, service, mock_db, org_id):
        """Test creating an adjustment period."""
        input_data = FiscalPeriodInput(
            fiscal_year_id=uuid4(),
            period_number=13,
            period_name="Adjustment Period",
            start_date=date(2024, 12, 31),
            end_date=date(2024, 12, 31),
            is_adjustment_period=True,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.create_period(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()


class TestOpenPeriod:
    """Tests for open_period method."""

    def test_open_period_from_future(self, service, mock_db, org_id, user_id):
        """Test opening a future period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.FUTURE,
        )
        mock_db.get.return_value = period

        result = service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        mock_db.commit.assert_called_once()
        assert result.status == PeriodStatus.OPEN

    def test_open_period_from_soft_closed(self, service, mock_db, org_id, user_id):
        """Test opening a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
        )
        mock_db.get.return_value = period

        result = service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert result.status == PeriodStatus.OPEN

    def test_open_period_not_found(self, service, mock_db, org_id, user_id):
        """Test opening non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_open_period_wrong_org(self, service, mock_db, org_id, user_id):
        """Test opening period from wrong organization."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(organization_id=uuid4())  # Different org
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 404

    def test_open_hard_closed_period_fails(self, service, mock_db, org_id, user_id):
        """Test opening a hard-closed period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.HARD_CLOSED,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400


class TestSoftClosePeriod:
    """Tests for soft_close_period method."""

    def test_soft_close_open_period(self, service, mock_db, org_id, user_id):
        """Test soft closing an open period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
        )
        mock_db.get.return_value = period

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        mock_db.commit.assert_called_once()
        assert result.status == PeriodStatus.SOFT_CLOSED

    def test_soft_close_reopened_period(self, service, mock_db, org_id, user_id):
        """Test soft closing a reopened period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.REOPENED,
        )
        mock_db.get.return_value = period

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        assert result.status == PeriodStatus.SOFT_CLOSED

    def test_soft_close_not_found(self, service, mock_db, org_id, user_id):
        """Test soft closing non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_soft_close_future_period_fails(self, service, mock_db, org_id, user_id):
        """Test soft closing a future period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.FUTURE,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400


class TestHardClosePeriod:
    """Tests for hard_close_period method."""

    def test_hard_close_soft_closed_period(self, service, mock_db, org_id, user_id):
        """Test hard closing a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
        )
        mock_db.get.return_value = period

        result = service.hard_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        mock_db.commit.assert_called_once()
        assert result.status == PeriodStatus.HARD_CLOSED

    def test_hard_close_open_period_fails(self, service, mock_db, org_id, user_id):
        """Test hard closing an open period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.hard_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400
        assert "soft closed" in exc.value.detail


class TestReopenPeriod:
    """Tests for reopen_period method."""

    def test_reopen_soft_closed_period(self, service, mock_db, org_id, user_id):
        """Test reopening a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
            reopen_count=0,
        )
        mock_db.get.return_value = period

        session_id = uuid4()
        result = service.reopen_period(
            mock_db, org_id, period.fiscal_period_id, user_id, session_id
        )

        mock_db.commit.assert_called_once()
        assert result.status == PeriodStatus.REOPENED
        assert result.reopen_count == 1
        assert result.last_reopen_session_id == session_id

    def test_reopen_hard_closed_period_fails(self, service, mock_db, org_id, user_id):
        """Test reopening a hard-closed period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.HARD_CLOSED,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.reopen_period(
                mock_db, org_id, period.fiscal_period_id, user_id, uuid4()
            )

        assert exc.value.status_code == 400
        assert "hard-closed" in exc.value.detail


class TestGetPeriod:
    """Tests for get method."""

    def test_get_existing_period(self, service, mock_db, org_id):
        """Test getting existing period."""
        period = MockFiscalPeriod(organization_id=org_id)
        mock_db.get.return_value = period

        result = service.get(mock_db, str(period.fiscal_period_id))

        assert result == period

    def test_get_nonexistent_period(self, service, mock_db):
        """Test getting non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListPeriods:
    """Tests for list method."""

    def test_list_all_periods(self, service, mock_db, org_id):
        """Test listing all periods."""
        periods = [MockFiscalPeriod(organization_id=org_id) for _ in range(12)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = periods

        result = service.list(mock_db, organization_id=str(org_id))

        assert len(result) == 12

    def test_list_by_fiscal_year(self, service, mock_db, org_id):
        """Test listing periods by fiscal year."""
        year_id = uuid4()
        periods = [MockFiscalPeriod(organization_id=org_id, fiscal_year_id=year_id)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = periods

        result = service.list(
            mock_db, organization_id=str(org_id), fiscal_year_id=str(year_id)
        )

        assert len(result) == 1

    def test_list_by_status(self, service, mock_db, org_id):
        """Test listing periods by status."""
        periods = [MockFiscalPeriod(organization_id=org_id, status=PeriodStatus.OPEN)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = periods

        result = service.list(
            mock_db, organization_id=str(org_id), status=PeriodStatus.OPEN
        )

        assert len(result) == 1
