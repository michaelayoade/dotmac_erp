"""
Tests for TaxReturnService - Tax return preparation and filing.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.finance.tax.tax_return import (
    TaxReturnService,
    TaxReturnInput,
    TaxReturnBoxValue,
)


class MockTaxReturnStatus:
    """Mock status enum."""
    DRAFT = "draft"
    PREPARED = "prepared"
    REVIEWED = "reviewed"
    FILED = "filed"
    AMENDED = "amended"


class MockTaxReturnType:
    """Mock return type enum."""
    VAT = "vat"
    GST = "gst"
    SALES_TAX = "sales_tax"


class MockTaxReturn:
    """Mock TaxReturn model."""

    def __init__(self, **kwargs):
        from app.models.finance.tax.tax_return import TaxReturnStatus, TaxReturnType

        self.return_id = kwargs.get("return_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.tax_period_id = kwargs.get("tax_period_id", uuid4())
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.return_type = kwargs.get("return_type", TaxReturnType.VAT)
        self.total_output_tax = kwargs.get("total_output_tax", Decimal("10000.00"))
        self.total_input_tax = kwargs.get("total_input_tax", Decimal("3000.00"))
        self.net_tax_payable = kwargs.get("net_tax_payable", Decimal("7000.00"))
        self.adjustments = kwargs.get("adjustments", Decimal("0"))
        self.final_amount = kwargs.get("final_amount", Decimal("7000.00"))
        self.box_values = kwargs.get("box_values", {})
        self.status = kwargs.get("status", TaxReturnStatus.DRAFT)
        self.is_amendment = kwargs.get("is_amendment", False)
        self.original_return_id = kwargs.get("original_return_id")
        self.amendment_reason = kwargs.get("amendment_reason")
        self.prepared_by_user_id = kwargs.get("prepared_by_user_id")
        self.prepared_at = kwargs.get("prepared_at")
        self.reviewed_by_user_id = kwargs.get("reviewed_by_user_id")
        self.reviewed_at = kwargs.get("reviewed_at")
        self.filed_date = kwargs.get("filed_date")
        self.filed_by_user_id = kwargs.get("filed_by_user_id")
        self.filing_reference = kwargs.get("filing_reference")
        self.is_paid = kwargs.get("is_paid", False)
        self.payment_date = kwargs.get("payment_date")
        self.payment_reference = kwargs.get("payment_reference")
        self.payment_journal_entry_id = kwargs.get("payment_journal_entry_id")
        self.return_reference = kwargs.get("return_reference")
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))


class MockTaxPeriod:
    """Mock TaxPeriod model."""

    def __init__(self, **kwargs):
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        self.period_id = kwargs.get("period_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.fiscal_period_id = kwargs.get("fiscal_period_id", uuid4())
        self.period_name = kwargs.get("period_name", "2024-01")
        self.status = kwargs.get("status", TaxPeriodStatus.OPEN)


class TestTaxReturnServicePrepareReturn:
    """Tests for prepare_return method."""

    def test_prepare_return_success(self, mock_db):
        """Test successful return preparation."""
        from app.models.finance.tax.tax_return import TaxReturnType
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        period_id = uuid4()
        jur_id = uuid4()
        user_id = uuid4()

        mock_period = MockTaxPeriod(
            period_id=period_id,
            organization_id=org_id,
            status=TaxPeriodStatus.OPEN,
        )

        # Setup mock queries
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.group_by.return_value = mock_query
        mock_query.first.side_effect = [mock_period, None]  # Period found, no existing return
        mock_query.scalar.side_effect = [Decimal("10000"), Decimal("3000")]  # Output, input tax
        mock_query.all.return_value = []  # Box values
        mock_db.query.return_value = mock_query

        input_data = TaxReturnInput(
            tax_period_id=period_id,
            jurisdiction_id=jur_id,
            return_type=TaxReturnType.VAT,
        )

        result = TaxReturnService.prepare_return(mock_db, org_id, input_data, user_id)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_prepare_return_period_not_found(self, mock_db):
        """Test preparation with missing period."""
        from app.models.finance.tax.tax_return import TaxReturnType

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.prepare_return(mock_db, uuid4(), input_data, uuid4())

        assert exc.value.status_code == 404
        assert "Tax period not found" in exc.value.detail

    def test_prepare_return_period_not_open(self, mock_db):
        """Test preparation with non-open period."""
        from app.models.finance.tax.tax_return import TaxReturnType
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.FILED
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_period
        mock_db.query.return_value = mock_query

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.prepare_return(mock_db, org_id, input_data, uuid4())

        assert exc.value.status_code == 400

    def test_prepare_return_existing_non_draft(self, mock_db):
        """Test preparation when non-draft return exists."""
        from app.models.finance.tax.tax_return import TaxReturnType, TaxReturnStatus
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.OPEN
        )
        existing_return = MockTaxReturn(status=TaxReturnStatus.PREPARED)

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.side_effect = [mock_period, existing_return]
        mock_db.query.return_value = mock_query

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.prepare_return(mock_db, org_id, input_data, uuid4())

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestTaxReturnServiceReviewReturn:
    """Tests for review_return method."""

    def test_review_return_success(self, mock_db):
        """Test successful return review."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        return_id = uuid4()
        preparer_id = uuid4()
        reviewer_id = uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=org_id,
            status=TaxReturnStatus.PREPARED,
            prepared_by_user_id=preparer_id,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        result = TaxReturnService.review_return(
            mock_db, org_id, return_id, reviewer_id
        )

        assert mock_return.status == TaxReturnStatus.REVIEWED
        assert mock_return.reviewed_by_user_id == reviewer_id
        mock_db.commit.assert_called_once()

    def test_review_return_not_found(self, mock_db):
        """Test review of non-existent return."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.review_return(mock_db, uuid4(), uuid4(), uuid4())

        assert exc.value.status_code == 404

    def test_review_return_wrong_status(self, mock_db):
        """Test review of return in wrong status."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.DRAFT
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.review_return(mock_db, org_id, uuid4(), uuid4())

        assert exc.value.status_code == 400
        assert "Cannot review" in exc.value.detail

    def test_review_return_sod_violation(self, mock_db):
        """Test review by same user who prepared (SoD check)."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        user_id = uuid4()  # Same user for both

        mock_return = MockTaxReturn(
            organization_id=org_id,
            status=TaxReturnStatus.PREPARED,
            prepared_by_user_id=user_id,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.review_return(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 400
        assert "Segregation of Duties" in exc.value.detail


class TestTaxReturnServiceFileReturn:
    """Tests for file_return method."""

    def test_file_return_success(self, mock_db):
        """Test successful return filing."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        return_id = uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=org_id,
            status=TaxReturnStatus.REVIEWED,
        )
        mock_period = MockTaxPeriod()

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.side_effect = [mock_return, mock_period]
        mock_query.update.return_value = 0
        mock_db.query.return_value = mock_query

        result = TaxReturnService.file_return(
            mock_db, org_id, return_id, uuid4(), filing_reference="REF-001"
        )

        assert mock_return.status == TaxReturnStatus.FILED
        assert mock_return.filing_reference == "REF-001"

    def test_file_return_wrong_status(self, mock_db):
        """Test filing return in wrong status."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.DRAFT
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.file_return(mock_db, org_id, uuid4(), uuid4())

        assert exc.value.status_code == 400


class TestTaxReturnServiceRecordPayment:
    """Tests for record_payment method."""

    def test_record_payment_success(self, mock_db):
        """Test successful payment recording."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        return_id = uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=org_id,
            status=TaxReturnStatus.FILED,
        )
        mock_period = MockTaxPeriod()

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.side_effect = [mock_return, mock_period]
        mock_db.query.return_value = mock_query

        result = TaxReturnService.record_payment(
            mock_db,
            org_id,
            return_id,
            payment_date=date.today(),
            payment_reference="PAY-001",
        )

        assert mock_return.is_paid is True
        assert mock_return.payment_reference == "PAY-001"

    def test_record_payment_not_filed(self, mock_db):
        """Test payment recording on unfiled return."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.PREPARED
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.record_payment(
                mock_db, org_id, uuid4(), payment_date=date.today()
            )

        assert exc.value.status_code == 400
        assert "must be filed" in exc.value.detail


class TestTaxReturnServiceCreateAmendment:
    """Tests for create_amendment method."""

    def test_create_amendment_success(self, mock_db):
        """Test successful amendment creation."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        original_id = uuid4()
        user_id = uuid4()

        mock_original = MockTaxReturn(
            return_id=original_id,
            organization_id=org_id,
            status=TaxReturnStatus.FILED,
        )

        mock_db.get.return_value = mock_original
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_original
        mock_db.query.return_value = mock_query

        result = TaxReturnService.create_amendment(
            mock_db,
            org_id,
            original_id,
            amendment_reason="Correction of input tax",
            adjustments=Decimal("-500.00"),
            prepared_by_user_id=user_id,
        )

        assert mock_original.status == TaxReturnStatus.AMENDED
        mock_db.add.assert_called_once()

    def test_create_amendment_not_filed(self, mock_db):
        """Test amendment of unfiled return."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_original = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.PREPARED
        )

        mock_db.get.return_value = mock_original
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_original
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            TaxReturnService.create_amendment(
                mock_db,
                org_id,
                uuid4(),
                amendment_reason="Test",
                adjustments=Decimal("0"),
                prepared_by_user_id=uuid4(),
            )

        assert exc.value.status_code == 400
        assert "only amend filed returns" in exc.value.detail.lower()


class TestTaxReturnServiceGetBoxValues:
    """Tests for get_box_values method."""

    def test_get_box_values_success(self, mock_db):
        """Test getting box values."""
        org_id = uuid4()
        return_id = uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=org_id,
            box_values={
                "1": {"tax_amount": "5000.00", "transaction_count": 10},
                "2": {"tax_amount": "2000.00", "transaction_count": 5},
            },
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        result = TaxReturnService.get_box_values(mock_db, org_id, return_id)

        assert len(result) == 2
        assert isinstance(result[0], TaxReturnBoxValue)

    def test_get_box_values_empty(self, mock_db):
        """Test getting box values when none exist."""
        org_id = uuid4()
        mock_return = MockTaxReturn(organization_id=org_id, box_values=None)

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        result = TaxReturnService.get_box_values(mock_db, org_id, uuid4())

        assert result == []


class TestTaxReturnServiceQueries:
    """Tests for query methods."""

    def test_get_return_by_id(self, mock_db):
        """Test getting return by ID."""
        return_id = uuid4()
        mock_return = MockTaxReturn(return_id=return_id)

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_return
        mock_db.query.return_value = mock_query

        result = TaxReturnService.get(mock_db, str(return_id))

        assert result is not None

    def test_list_returns_with_filters(self, mock_db):
        """Test listing returns with filters."""
        from app.models.finance.tax.tax_return import TaxReturnType, TaxReturnStatus

        returns = [MockTaxReturn(), MockTaxReturn()]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = returns
        mock_db.query.return_value = mock_query

        result = TaxReturnService.list(
            mock_db,
            organization_id=str(uuid4()),
            tax_period_id=str(uuid4()),
            return_type=TaxReturnType.VAT,
            status=TaxReturnStatus.FILED,
        )

        assert len(result) == 2


class TestTaxReturnBoxValue:
    """Tests for TaxReturnBoxValue dataclass."""

    def test_create_box_value(self):
        """Test creating box value."""
        box = TaxReturnBoxValue(
            box_number="1",
            description="Output Tax",
            amount=Decimal("5000.00"),
            transaction_count=10,
        )

        assert box.box_number == "1"
        assert box.description == "Output Tax"
        assert box.amount == Decimal("5000.00")
        assert box.transaction_count == 10
