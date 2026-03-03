"""
Tests for TaxReturnService - Tax return preparation and filing.

Covers prepare_return, review_return, file_return, record_payment,
create_amendment, get, list, get_box_values, and auto_refresh_return.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.finance.tax.tax_return import (
    TaxReturnBoxValue,
    TaxReturnInput,
    TaxReturnService,
)


class MockTaxReturn:
    """Mock TaxReturn model."""

    def __init__(self, **kwargs: object) -> None:
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
        self.created_at = kwargs.get("created_at", datetime.now(UTC))


class MockTaxPeriod:
    """Mock TaxPeriod model."""

    def __init__(self, **kwargs: object) -> None:
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        self.period_id = kwargs.get("period_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.fiscal_period_id = kwargs.get("fiscal_period_id", uuid4())
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.period_name = kwargs.get("period_name", "2024-01")
        self.status = kwargs.get("status", TaxPeriodStatus.OPEN)


class TestTaxReturnServicePrepareReturn:
    """Tests for prepare_return method."""

    def test_prepare_return_success(self, mock_db: MagicMock) -> None:
        """Test successful return preparation."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturnType

        org_id = uuid4()
        period_id = uuid4()
        jur_id = uuid4()
        user_id = uuid4()

        mock_period = MockTaxPeriod(
            period_id=period_id,
            organization_id=org_id,
            status=TaxPeriodStatus.OPEN,
        )

        # db.scalar() is called 4 times:
        # 1. TaxPeriod lookup
        # 2. Existing TaxReturn check
        # 3. Output tax sum (_calculate_tax_totals)
        # 4. Input tax sum (_calculate_tax_totals)
        mock_db.scalar.side_effect = [
            mock_period,
            None,  # No existing return
            Decimal("10000"),  # Output tax
            Decimal("3000"),  # Input tax
        ]

        # _calculate_box_values uses db.execute().all()
        mock_execute_result = MagicMock()
        mock_execute_result.all.return_value = []
        mock_db.execute.return_value = mock_execute_result

        input_data = TaxReturnInput(
            tax_period_id=period_id,
            jurisdiction_id=jur_id,
            return_type=TaxReturnType.VAT,
        )

        TaxReturnService.prepare_return(mock_db, org_id, input_data, user_id)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_prepare_return_period_not_found(self, mock_db: MagicMock) -> None:
        """Test preparation with missing period."""
        from app.models.finance.tax.tax_return import TaxReturnType

        mock_db.scalar.return_value = None

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(ValueError, match="Tax period not found"):
            TaxReturnService.prepare_return(mock_db, uuid4(), input_data, uuid4())

    def test_prepare_return_period_not_open(self, mock_db: MagicMock) -> None:
        """Test preparation with non-open period."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturnType

        org_id = uuid4()
        mock_period = MockTaxPeriod(
            organization_id=org_id, status=TaxPeriodStatus.FILED
        )

        mock_db.scalar.return_value = mock_period

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(ValueError, match="status"):
            TaxReturnService.prepare_return(mock_db, org_id, input_data, uuid4())

    def test_prepare_return_existing_non_draft(self, mock_db: MagicMock) -> None:
        """Test preparation when non-draft return exists."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturnStatus, TaxReturnType

        org_id = uuid4()
        mock_period = MockTaxPeriod(organization_id=org_id, status=TaxPeriodStatus.OPEN)
        existing_return = MockTaxReturn(status=TaxReturnStatus.PREPARED)

        mock_db.scalar.side_effect = [mock_period, existing_return]

        input_data = TaxReturnInput(
            tax_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            return_type=TaxReturnType.VAT,
        )

        with pytest.raises(ValueError, match="already exists"):
            TaxReturnService.prepare_return(mock_db, org_id, input_data, uuid4())


class TestTaxReturnServiceReviewReturn:
    """Tests for review_return method."""

    def test_review_return_success(self, mock_db: MagicMock) -> None:
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

        mock_db.scalar.return_value = mock_return

        TaxReturnService.review_return(mock_db, org_id, return_id, reviewer_id)

        assert mock_return.status == TaxReturnStatus.REVIEWED
        assert mock_return.reviewed_by_user_id == reviewer_id
        mock_db.flush.assert_called_once()

    def test_review_return_not_found(self, mock_db: MagicMock) -> None:
        """Test review of non-existent return."""
        mock_db.scalar.return_value = None

        with pytest.raises(ValueError, match="Tax return not found"):
            TaxReturnService.review_return(mock_db, uuid4(), uuid4(), uuid4())

    def test_review_return_wrong_status(self, mock_db: MagicMock) -> None:
        """Test review of return in wrong status."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.DRAFT
        )

        mock_db.scalar.return_value = mock_return

        with pytest.raises(ValueError, match="Cannot review"):
            TaxReturnService.review_return(mock_db, org_id, uuid4(), uuid4())

    def test_review_return_sod_violation(self, mock_db: MagicMock) -> None:
        """Test review by same user who prepared (SoD check)."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        user_id = uuid4()  # Same user for both

        mock_return = MockTaxReturn(
            organization_id=org_id,
            status=TaxReturnStatus.PREPARED,
            prepared_by_user_id=user_id,
        )

        mock_db.scalar.return_value = mock_return

        with pytest.raises(ValueError, match="Segregation of Duties"):
            TaxReturnService.review_return(mock_db, org_id, uuid4(), user_id)


class TestTaxReturnServiceFileReturn:
    """Tests for file_return method."""

    def test_file_return_success(self, mock_db: MagicMock) -> None:
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

        # db.scalar() called twice: TaxReturn lookup, TaxPeriod lookup
        mock_db.scalar.side_effect = [mock_return, mock_period]

        TaxReturnService.file_return(
            mock_db, org_id, return_id, uuid4(), filing_reference="REF-001"
        )

        assert mock_return.status == TaxReturnStatus.FILED
        assert mock_return.filing_reference == "REF-001"

    def test_file_return_wrong_status(self, mock_db: MagicMock) -> None:
        """Test filing return in wrong status."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.DRAFT
        )

        mock_db.scalar.return_value = mock_return

        with pytest.raises(ValueError, match="Cannot file"):
            TaxReturnService.file_return(mock_db, org_id, uuid4(), uuid4())


class TestTaxReturnServiceRecordPayment:
    """Tests for record_payment method."""

    def test_record_payment_success(self, mock_db: MagicMock) -> None:
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

        # db.scalar() called twice: TaxReturn lookup, TaxPeriod lookup
        mock_db.scalar.side_effect = [mock_return, mock_period]

        TaxReturnService.record_payment(
            mock_db,
            org_id,
            return_id,
            payment_date=date.today(),
            payment_reference="PAY-001",
        )

        assert mock_return.is_paid is True
        assert mock_return.payment_reference == "PAY-001"

    def test_record_payment_not_filed(self, mock_db: MagicMock) -> None:
        """Test payment recording on unfiled return."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_return = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.PREPARED
        )

        mock_db.scalar.return_value = mock_return

        with pytest.raises(ValueError, match="must be filed"):
            TaxReturnService.record_payment(
                mock_db, org_id, uuid4(), payment_date=date.today()
            )


class TestTaxReturnServiceCreateAmendment:
    """Tests for create_amendment method."""

    def test_create_amendment_success(self, mock_db: MagicMock) -> None:
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

        mock_db.scalar.return_value = mock_original

        TaxReturnService.create_amendment(
            mock_db,
            org_id,
            original_id,
            amendment_reason="Correction of input tax",
            adjustments=Decimal("-500.00"),
            prepared_by_user_id=user_id,
        )

        assert mock_original.status == TaxReturnStatus.AMENDED
        mock_db.add.assert_called_once()

    def test_create_amendment_not_filed(self, mock_db: MagicMock) -> None:
        """Test amendment of unfiled return."""
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        mock_original = MockTaxReturn(
            organization_id=org_id, status=TaxReturnStatus.PREPARED
        )

        mock_db.scalar.return_value = mock_original

        with pytest.raises(ValueError, match="only amend filed returns"):
            TaxReturnService.create_amendment(
                mock_db,
                org_id,
                uuid4(),
                amendment_reason="Test",
                adjustments=Decimal("0"),
                prepared_by_user_id=uuid4(),
            )


class TestTaxReturnServiceGetBoxValues:
    """Tests for get_box_values method."""

    def test_get_box_values_success(self, mock_db: MagicMock) -> None:
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

        mock_db.scalar.return_value = mock_return

        result = TaxReturnService.get_box_values(mock_db, org_id, return_id)

        assert len(result) == 2
        assert isinstance(result[0], TaxReturnBoxValue)

    def test_get_box_values_empty(self, mock_db: MagicMock) -> None:
        """Test getting box values when none exist."""
        org_id = uuid4()
        mock_return = MockTaxReturn(organization_id=org_id, box_values=None)

        mock_db.scalar.return_value = mock_return

        result = TaxReturnService.get_box_values(mock_db, org_id, uuid4())

        assert result == []


class TestTaxReturnServiceQueries:
    """Tests for query methods."""

    def test_get_return_by_id(self, mock_db: MagicMock) -> None:
        """Test getting return by ID."""
        return_id = uuid4()
        mock_return = MockTaxReturn(return_id=return_id)

        mock_db.get.return_value = mock_return

        result = TaxReturnService.get(mock_db, str(return_id))

        assert result is not None
        mock_db.get.assert_called_once()

    def test_list_returns_with_filters(self, mock_db: MagicMock) -> None:
        """Test listing returns with filters."""
        from app.models.finance.tax.tax_return import TaxReturnStatus, TaxReturnType

        returns = [MockTaxReturn(), MockTaxReturn()]

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = returns
        mock_db.scalars.return_value = mock_scalars

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

    def test_create_box_value(self) -> None:
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


class TestAutoRefreshReturn:
    """Tests for auto_refresh_return method."""

    def test_auto_refresh_creates_draft_return(self, mock_db: MagicMock) -> None:
        """Happy path: creates a new DRAFT tax return."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        org_id = uuid4()
        fp_id = uuid4()
        jur_id = uuid4()
        user_id = uuid4()
        period_id = uuid4()

        mock_period = MockTaxPeriod(
            period_id=period_id,
            organization_id=org_id,
            fiscal_period_id=fp_id,
            jurisdiction_id=jur_id,
            status=TaxPeriodStatus.OPEN,
        )

        # db.scalar calls:
        # 1. TaxPeriod lookup -> found
        # 2. Existing TaxReturn check -> None
        # 3. Output tax sum -> 5000
        # 4. Input tax sum -> 2000
        mock_db.scalar.side_effect = [
            mock_period,
            None,
            Decimal("5000"),
            Decimal("2000"),
        ]

        # _calculate_box_values uses db.execute().all()
        mock_execute_result = MagicMock()
        mock_execute_result.all.return_value = []
        mock_db.execute.return_value = mock_execute_result

        result = TaxReturnService.auto_refresh_return(
            mock_db, org_id, fp_id, jur_id, user_id
        )

        assert result is not None
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_auto_refresh_no_period_returns_none(self, mock_db: MagicMock) -> None:
        """Returns None when no TaxPeriod found."""
        mock_db.scalar.return_value = None

        result = TaxReturnService.auto_refresh_return(
            mock_db, uuid4(), uuid4(), uuid4(), uuid4()
        )

        assert result is None
        mock_db.add.assert_not_called()

    def test_auto_refresh_closed_period_returns_none(self, mock_db: MagicMock) -> None:
        """Returns None when period is CLOSED (finalized)."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        mock_period = MockTaxPeriod(status=TaxPeriodStatus.CLOSED)
        mock_db.scalar.return_value = mock_period

        result = TaxReturnService.auto_refresh_return(
            mock_db, uuid4(), uuid4(), uuid4(), uuid4()
        )

        assert result is None

    def test_auto_refresh_filed_period_returns_none(self, mock_db: MagicMock) -> None:
        """Returns None when period is FILED."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        mock_period = MockTaxPeriod(status=TaxPeriodStatus.FILED)
        mock_db.scalar.return_value = mock_period

        result = TaxReturnService.auto_refresh_return(
            mock_db, uuid4(), uuid4(), uuid4(), uuid4()
        )

        assert result is None

    def test_auto_refresh_existing_filed_return_returns_none(
        self, mock_db: MagicMock
    ) -> None:
        """Returns None when existing return is beyond DRAFT (user-finalized)."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturnStatus

        mock_period = MockTaxPeriod(status=TaxPeriodStatus.OPEN)
        existing_return = MockTaxReturn(status=TaxReturnStatus.FILED)

        mock_db.scalar.side_effect = [mock_period, existing_return]

        result = TaxReturnService.auto_refresh_return(
            mock_db, uuid4(), uuid4(), uuid4(), uuid4()
        )

        assert result is None

    def test_auto_refresh_no_transactions_returns_none(
        self, mock_db: MagicMock
    ) -> None:
        """Returns None when no tax transactions exist (both totals = 0)."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus

        mock_period = MockTaxPeriod(status=TaxPeriodStatus.OPEN)

        # Period found, no existing return, zero output, zero input
        mock_db.scalar.side_effect = [
            mock_period,
            None,
            Decimal("0"),
            Decimal("0"),
        ]

        result = TaxReturnService.auto_refresh_return(
            mock_db, uuid4(), uuid4(), uuid4(), uuid4()
        )

        assert result is None
        mock_db.add.assert_not_called()

    def test_auto_refresh_updates_existing_draft(self, mock_db: MagicMock) -> None:
        """Idempotent: updates an existing DRAFT return instead of creating new."""
        from app.models.finance.tax.tax_period import TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturnStatus

        org_id = uuid4()
        fp_id = uuid4()
        jur_id = uuid4()
        user_id = uuid4()

        mock_period = MockTaxPeriod(
            organization_id=org_id,
            fiscal_period_id=fp_id,
            status=TaxPeriodStatus.OPEN,
        )
        existing_return = MockTaxReturn(
            organization_id=org_id,
            status=TaxReturnStatus.DRAFT,
            total_output_tax=Decimal("1000"),
            total_input_tax=Decimal("500"),
            adjustments=Decimal("100"),
        )

        # Period found, existing DRAFT return, new output tax, new input tax
        mock_db.scalar.side_effect = [
            mock_period,
            existing_return,
            Decimal("8000"),
            Decimal("3000"),
        ]

        mock_execute_result = MagicMock()
        mock_execute_result.all.return_value = []
        mock_db.execute.return_value = mock_execute_result

        result = TaxReturnService.auto_refresh_return(
            mock_db, org_id, fp_id, jur_id, user_id
        )

        assert result is existing_return
        assert existing_return.total_output_tax == Decimal("8000")
        assert existing_return.total_input_tax == Decimal("3000")
        assert existing_return.net_tax_payable == Decimal("5000")
        # Preserves existing adjustments
        assert existing_return.final_amount == Decimal("5100")
        # Does NOT call db.add (updates in place)
        mock_db.add.assert_not_called()
        mock_db.flush.assert_called_once()
