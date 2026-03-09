"""
Tests for TaxPeriodService - Tax period management.
"""

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.finance.tax.tax_period import (
    TaxPeriodInput,
    TaxPeriodService,
)


class MockTaxPeriodStatus:
    """Mock status enum."""

    OPEN = "open"
    FILED = "filed"
    PAID = "paid"
    CLOSED = "closed"


class MockTaxPeriodFrequency:
    """Mock frequency enum."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class MockTaxPeriod:
    """Mock TaxPeriod model."""

    def __init__(self, **kwargs):
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        self.period_id = kwargs.get("period_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.fiscal_period_id = kwargs.get("fiscal_period_id")
        self.period_name = kwargs.get("period_name", "2024-01")
        self.frequency = kwargs.get("frequency")
        self.start_date = kwargs.get("start_date", date(2024, 1, 1))
        self.end_date = kwargs.get("end_date", date(2024, 1, 31))
        self.due_date = kwargs.get("due_date", date(2024, 3, 1))
        self.status = kwargs.get("status", TaxPeriodStatus.OPEN)
        self.is_extension_filed = kwargs.get("is_extension_filed", False)
        self.extended_due_date = kwargs.get("extended_due_date")


class MockTaxJurisdiction:
    """Mock TaxJurisdiction model."""

    def __init__(self, **kwargs):
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.jurisdiction_name = kwargs.get("jurisdiction_name", "US Federal")


class TestTaxPeriodServiceCreatePeriod:
    """Tests for create_period method."""

    def test_create_period_success(self, mock_db):
        """Test successful period creation."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.scalars.return_value.first.side_effect = [
            mock_jurisdiction,
            None,
        ]  # Found jurisdiction, no overlap

        input_data = TaxPeriodInput(
            jurisdiction_id=jur_id,
            period_name="2024-01",
            frequency=TaxPeriodFrequency.MONTHLY,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            due_date=date(2024, 3, 1),
        )

        TaxPeriodService.create_period(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_period_jurisdiction_not_found(self, mock_db):
        """Test period creation with missing jurisdiction."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        mock_db.scalars.return_value.first.return_value = None

        input_data = TaxPeriodInput(
            jurisdiction_id=uuid4(),
            period_name="2024-01",
            frequency=TaxPeriodFrequency.MONTHLY,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            due_date=date(2024, 3, 1),
        )

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.create_period(mock_db, uuid4(), input_data)

        assert exc.value.status_code == 404
        assert "jurisdiction not found" in exc.value.detail.lower()

    def test_create_period_invalid_dates(self, mock_db):
        """Test period creation with end date before start date."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.scalars.return_value.first.return_value = mock_jurisdiction

        input_data = TaxPeriodInput(
            jurisdiction_id=jur_id,
            period_name="2024-01",
            frequency=TaxPeriodFrequency.MONTHLY,
            start_date=date(2024, 1, 31),
            end_date=date(2024, 1, 1),  # Before start date
            due_date=date(2024, 3, 1),
        )

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.create_period(mock_db, org_id, input_data)

        assert exc.value.status_code == 400
        assert "End date must be after start date" in exc.value.detail

    def test_create_period_overlapping(self, mock_db):
        """Test period creation with overlapping dates."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )
        existing_period = MockTaxPeriod(period_name="2024-01")

        mock_db.scalars.return_value.first.side_effect = [mock_jurisdiction, existing_period]

        input_data = TaxPeriodInput(
            jurisdiction_id=jur_id,
            period_name="2024-01-overlap",
            frequency=TaxPeriodFrequency.MONTHLY,
            start_date=date(2024, 1, 15),
            end_date=date(2024, 2, 15),
            due_date=date(2024, 3, 15),
        )

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.create_period(mock_db, org_id, input_data)

        assert exc.value.status_code == 400
        assert "overlaps" in exc.value.detail.lower()


class TestTaxPeriodServiceGeneratePeriods:
    """Tests for generate_periods method."""

    def test_generate_monthly_periods(self, mock_db):
        """Test generating monthly periods for a year."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.scalars.return_value.first.side_effect = [mock_jurisdiction, None] * 12  # For each month

        TaxPeriodService.generate_periods(
            db=mock_db,
            organization_id=org_id,
            jurisdiction_id=jur_id,
            year=2024,
            frequency=TaxPeriodFrequency.MONTHLY,
        )

        # Should create 12 periods
        assert mock_db.add.call_count == 12
        assert mock_db.commit.call_count == 12

    def test_generate_quarterly_periods(self, mock_db):
        """Test generating quarterly periods for a year."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.scalars.return_value.first.side_effect = [mock_jurisdiction, None] * 4  # For each quarter

        TaxPeriodService.generate_periods(
            db=mock_db,
            organization_id=org_id,
            jurisdiction_id=jur_id,
            year=2024,
            frequency=TaxPeriodFrequency.QUARTERLY,
        )

        # Should create 4 periods
        assert mock_db.add.call_count == 4

    def test_generate_annual_period(self, mock_db):
        """Test generating annual period."""
        from app.models.finance.tax.tax_period import TaxPeriodFrequency

        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.scalars.return_value.first.side_effect = [mock_jurisdiction, None]

        TaxPeriodService.generate_periods(
            db=mock_db,
            organization_id=org_id,
            jurisdiction_id=jur_id,
            year=2024,
            frequency=TaxPeriodFrequency.ANNUAL,
        )

        # Should create 1 period
        assert mock_db.add.call_count == 1


class TestTaxPeriodServiceFileExtension:
    """Tests for file_extension method."""

    def test_file_extension_success(self, mock_db):
        """Test filing extension successfully."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        period_id = uuid4()

        mock_period = MockTaxPeriod(
            period_id=period_id,
            organization_id=org_id,
            status=TaxPeriodStatus.OPEN,
            due_date=date(2024, 3, 1),
        )

        mock_db.scalars.return_value.first.return_value = mock_period

        TaxPeriodService.file_extension(
            db=mock_db,
            organization_id=org_id,
            period_id=period_id,
            extended_due_date=date(2024, 6, 1),
        )

        assert mock_period.is_extension_filed is True
        assert mock_period.extended_due_date == date(2024, 6, 1)
        mock_db.commit.assert_called_once()

    def test_file_extension_period_not_found(self, mock_db):
        """Test filing extension for non-existent period."""
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.file_extension(
                db=mock_db,
                organization_id=uuid4(),
                period_id=uuid4(),
                extended_due_date=date(2024, 6, 1),
            )

        assert exc.value.status_code == 404

    def test_file_extension_period_not_open(self, mock_db):
        """Test filing extension for non-open period."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.FILED
        )

        mock_db.scalars.return_value.first.return_value = mock_period

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.file_extension(
                db=mock_db,
                organization_id=org_id,
                period_id=uuid4(),
                extended_due_date=date(2024, 6, 1),
            )

        assert exc.value.status_code == 400
        assert "Cannot file extension" in exc.value.detail

    def test_file_extension_invalid_date(self, mock_db):
        """Test filing extension with date before original due date."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id,
            status=TaxPeriodStatus.OPEN,
            due_date=date(2024, 3, 1),
        )

        mock_db.scalars.return_value.first.return_value = mock_period

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.file_extension(
                db=mock_db,
                organization_id=org_id,
                period_id=uuid4(),
                extended_due_date=date(2024, 2, 1),  # Before original due date
            )

        assert exc.value.status_code == 400
        assert "must be after original due date" in exc.value.detail


class TestTaxPeriodServiceStatusTransitions:
    """Tests for status transition methods."""

    def test_mark_filed_success(self, mock_db):
        """Test marking period as filed."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(organization_id=org_id, status=TaxPeriodStatus.OPEN)

        mock_db.scalars.return_value.first.return_value = mock_period

        TaxPeriodService.mark_filed(mock_db, org_id, uuid4())

        assert mock_period.status == TaxPeriodStatus.FILED
        mock_db.commit.assert_called_once()

    def test_mark_filed_already_filed(self, mock_db):
        """Test marking already filed period."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.FILED
        )

        mock_db.scalars.return_value.first.return_value = mock_period

        with pytest.raises(HTTPException) as exc:
            TaxPeriodService.mark_filed(mock_db, org_id, uuid4())

        assert exc.value.status_code == 400

    def test_mark_paid_success(self, mock_db):
        """Test marking period as paid."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.FILED
        )

        mock_db.scalars.return_value.first.return_value = mock_period

        TaxPeriodService.mark_paid(mock_db, org_id, uuid4())

        assert mock_period.status == TaxPeriodStatus.PAID

    def test_close_period_success(self, mock_db):
        """Test closing a period."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(organization_id=org_id, status=TaxPeriodStatus.PAID)

        mock_db.scalars.return_value.first.return_value = mock_period

        TaxPeriodService.close_period(mock_db, org_id, uuid4())

        assert mock_period.status == TaxPeriodStatus.CLOSED


class TestTaxPeriodServiceQueries:
    """Tests for query methods."""

    def test_get_current_period(self, mock_db):
        """Test getting current period."""
        org_id = uuid4()
        jur_id = uuid4()
        mock_period = MockTaxPeriod()

        mock_db.scalars.return_value.first.return_value = mock_period

        result = TaxPeriodService.get_current_period(
            mock_db, org_id, jur_id, as_of_date=date(2024, 1, 15)
        )

        assert result is not None

    def test_get_current_period_none(self, mock_db):
        """Test getting current period when none exists."""
        mock_db.scalars.return_value.first.return_value = None

        result = TaxPeriodService.get_current_period(
            mock_db, uuid4(), uuid4(), as_of_date=date(2024, 1, 15)
        )

        assert result is None

    def test_get_overdue_periods(self, mock_db):
        """Test getting overdue periods."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        overdue_periods = [
            MockTaxPeriod(due_date=date(2023, 12, 1), status=TaxPeriodStatus.OPEN),
            MockTaxPeriod(due_date=date(2024, 1, 1), status=TaxPeriodStatus.OPEN),
        ]

        mock_db.scalars.return_value.all.return_value = overdue_periods

        result = TaxPeriodService.get_overdue_periods(
            mock_db, org_id, as_of_date=date(2024, 2, 1)
        )

        assert len(result) == 2

    def test_get_period_by_id(self, mock_db):
        """Test getting period by ID."""
        period_id = uuid4()
        mock_period = MockTaxPeriod(period_id=period_id)

        mock_db.scalars.return_value.first.return_value = mock_period

        result = TaxPeriodService.get(mock_db, str(period_id))

        assert result is not None
        assert result.period_id == period_id

    def test_list_periods_with_filters(self, mock_db):
        """Test listing periods with filters."""
        from app.models.finance.tax.tax_period import (
            TaxPeriodFrequency,
            TaxPeriodStatus,
        )

        periods = [MockTaxPeriod(), MockTaxPeriod()]

        mock_db.scalars.return_value.all.return_value = periods

        result = TaxPeriodService.list(
            mock_db,
            organization_id=str(uuid4()),
            jurisdiction_id=str(uuid4()),
            status=TaxPeriodStatus.OPEN,
            frequency=TaxPeriodFrequency.MONTHLY,
            year=2024,
            limit=10,
            offset=0,
        )

        assert len(result) == 2
