"""
Tests for TaxWebService.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


class MockTaxReturn:
    """Mock TaxReturn model for testing."""

    def __init__(self, **kwargs):
        from app.models.finance.tax.tax_return import TaxReturnStatus, TaxReturnType

        self.return_id = kwargs.get("return_id", uuid.uuid4())
        self.tax_period_id = kwargs.get("tax_period_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid.uuid4())
        self.return_type = kwargs.get("return_type", TaxReturnType.VAT)
        self.return_reference = kwargs.get("return_reference", "VAT-2024-01")
        self.status = kwargs.get("status", TaxReturnStatus.DRAFT)
        self.total_output_tax = kwargs.get("total_output_tax", Decimal("1000.00"))
        self.total_input_tax = kwargs.get("total_input_tax", Decimal("300.00"))
        self.net_tax_payable = kwargs.get("net_tax_payable", Decimal("700.00"))
        self.adjustments = kwargs.get("adjustments", Decimal("0.00"))
        self.final_amount = kwargs.get("final_amount", Decimal("700.00"))
        self.filed_date = kwargs.get("filed_date")
        self.filing_reference = kwargs.get("filing_reference")
        self.is_paid = kwargs.get("is_paid", False)
        self.payment_date = kwargs.get("payment_date")
        self.payment_reference = kwargs.get("payment_reference")
        self.is_amendment = kwargs.get("is_amendment", False)
        self.original_return_id = kwargs.get("original_return_id")
        self.amendment_reason = kwargs.get("amendment_reason")
        self.prepared_at = kwargs.get("prepared_at")
        self.reviewed_at = kwargs.get("reviewed_at")


class MockBoxValue:
    """Mock TaxReturnBoxValue for testing."""

    def __init__(self, **kwargs):
        self.box_number = kwargs.get("box_number", "1")
        self.description = kwargs.get("description", "Standard Rate Sales")
        self.amount = kwargs.get("amount", Decimal("1000.00"))
        self.transaction_count = kwargs.get("transaction_count", 10)


class TestTaxWebServiceHelpers:
    """Tests for helper functions."""

    def test_format_date_with_value(self):
        """Test date formatting with valid date."""
        from app.services.finance.tax.web import _format_date

        result = _format_date(date(2024, 1, 15))
        assert result == "2024-01-15"

    def test_format_date_none(self):
        """Test date formatting with None."""
        from app.services.finance.tax.web import _format_date

        result = _format_date(None)
        assert result == ""

    def test_format_currency_usd(self):
        """Test currency formatting for USD."""
        from app.services.finance.tax.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "USD")
        assert result == "$1,234.56"

    def test_format_currency_other(self):
        """Test currency formatting for other currencies."""
        from app.services.finance.tax.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "EUR")
        assert result == "EUR 1,234.56"

    def test_format_currency_none(self):
        """Test currency formatting with None."""
        from app.services.finance.tax.web import _format_currency

        result = _format_currency(None)
        assert result == ""


class TestTaxWebServiceReturnDetail:
    """Tests for return_detail_context method."""

    @patch("app.services.finance.tax.web.tax_return_service")
    def test_return_detail_context_success(self, mock_service):
        """Test successful return detail context."""
        from app.services.finance.tax.web import TaxWebService

        org_id = uuid.uuid4()
        return_id = uuid.uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=org_id,
            filed_date=date(2024, 1, 20),
            prepared_at=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
        )
        mock_box_values = [
            MockBoxValue(box_number="1"),
            MockBoxValue(box_number="2"),
        ]

        mock_service.get.return_value = mock_return
        mock_service.get_box_values.return_value = mock_box_values

        mock_db = MagicMock()

        result = TaxWebService.return_detail_context(
            mock_db, str(org_id), str(return_id)
        )

        assert result["tax_return"] is not None
        assert result["tax_return"]["return_id"] == return_id
        assert len(result["box_values"]) == 2

    @patch("app.services.finance.tax.web.tax_return_service")
    def test_return_detail_context_not_found(self, mock_service):
        """Test return detail context with missing return."""
        from app.services.finance.tax.web import TaxWebService

        org_id = uuid.uuid4()
        return_id = uuid.uuid4()

        mock_service.get.return_value = None

        mock_db = MagicMock()

        result = TaxWebService.return_detail_context(
            mock_db, str(org_id), str(return_id)
        )

        assert result["tax_return"] is None
        assert result["box_values"] == []

    @patch("app.services.finance.tax.web.tax_return_service")
    def test_return_detail_context_wrong_org(self, mock_service):
        """Test return detail context with wrong organization."""
        from app.services.finance.tax.web import TaxWebService

        org_id = uuid.uuid4()
        other_org_id = uuid.uuid4()
        return_id = uuid.uuid4()

        mock_return = MockTaxReturn(
            return_id=return_id,
            organization_id=other_org_id,  # Different org
        )

        mock_service.get.return_value = mock_return

        mock_db = MagicMock()

        result = TaxWebService.return_detail_context(
            mock_db, str(org_id), str(return_id)
        )

        assert result["tax_return"] is None
        assert result["box_values"] == []


class TestTaxReturnView:
    """Tests for _tax_return_view function."""

    def test_tax_return_view_complete(self):
        """Test tax return view with all fields."""
        from app.services.finance.tax.web import _tax_return_view

        mock_return = MockTaxReturn(
            filed_date=date(2024, 1, 20),
            filing_reference="FILE-001",
            is_paid=True,
            payment_date=date(2024, 1, 25),
            payment_reference="PAY-001",
            is_amendment=False,
            prepared_at=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
            reviewed_at=datetime(2024, 1, 18, 14, 0, tzinfo=timezone.utc),
        )

        result = _tax_return_view(mock_return)

        assert result["return_id"] == mock_return.return_id
        assert result["is_paid"] is True
        assert result["filed_date"] == "2024-01-20"
        assert result["payment_date"] == "2024-01-25"
        assert result["prepared_at"] == "2024-01-15"
        assert result["reviewed_at"] == "2024-01-18"

    def test_tax_return_view_minimal(self):
        """Test tax return view with minimal fields."""
        from app.services.finance.tax.web import _tax_return_view

        mock_return = MockTaxReturn()

        result = _tax_return_view(mock_return)

        assert result["return_id"] == mock_return.return_id
        assert result["filed_date"] == ""
        assert result["prepared_at"] == ""


class TestBoxValueView:
    """Tests for _box_value_view function."""

    def test_box_value_view(self):
        """Test box value view."""
        from app.services.finance.tax.web import _box_value_view

        mock_box = MockBoxValue(
            box_number="1",
            description="Standard Rate Sales",
            amount=Decimal("5000.00"),
            transaction_count=25,
        )

        result = _box_value_view(mock_box)

        assert result["box_number"] == "1"
        assert result["description"] == "Standard Rate Sales"
        assert result["amount"] == "$5,000.00"
        assert result["transaction_count"] == 25
