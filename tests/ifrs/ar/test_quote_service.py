"""
Tests for QuoteService.

Tests quote creation, workflow, and conversion to invoices/sales orders.
"""

import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.finance.ar.quote import QuoteService
from app.models.finance.ar.quote import QuoteStatus


class MockQuote:
    """Mock Quote model."""

    def __init__(
        self,
        quote_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        quote_number: str = "QT-001",
        customer_id: uuid.UUID = None,
        quote_date: date = None,
        valid_until: date = None,
        status: QuoteStatus = QuoteStatus.DRAFT,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1"),
        subtotal: Decimal = Decimal("1000"),
        discount_amount: Decimal = Decimal("0"),
        tax_amount: Decimal = Decimal("100"),
        total_amount: Decimal = Decimal("1100"),
        lines: list = None,
        payment_terms_id: uuid.UUID = None,
        sent_by: uuid.UUID = None,
        sent_at: datetime = None,
        viewed_at: datetime = None,
        accepted_at: datetime = None,
        rejected_at: datetime = None,
        rejection_reason: str = None,
        converted_at: datetime = None,
        converted_to_invoice_id: uuid.UUID = None,
        converted_to_so_id: uuid.UUID = None,
        updated_by: uuid.UUID = None,
        updated_at: datetime = None,
        customer_notes: str = None,
        internal_notes: str = None,
    ):
        self.quote_id = quote_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.quote_number = quote_number
        self.customer_id = customer_id or uuid.uuid4()
        self.quote_date = quote_date or date.today()
        self.valid_until = valid_until or (date.today() + timedelta(days=30))
        self.status = status
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.subtotal = subtotal
        self.discount_amount = discount_amount
        self.tax_amount = tax_amount
        self.total_amount = total_amount
        self.lines = lines or []
        self.payment_terms_id = payment_terms_id
        self.sent_by = sent_by
        self.sent_at = sent_at
        self.viewed_at = viewed_at
        self.accepted_at = accepted_at
        self.rejected_at = rejected_at
        self.rejection_reason = rejection_reason
        self.converted_at = converted_at
        self.converted_to_invoice_id = converted_to_invoice_id
        self.converted_to_so_id = converted_to_so_id
        self.updated_by = updated_by
        self.updated_at = updated_at
        self.customer_notes = customer_notes
        self.internal_notes = internal_notes


class MockQuoteLine:
    """Mock QuoteLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        quote_id: uuid.UUID = None,
        line_number: int = 1,
        item_code: str = "ITEM-001",
        description: str = "Test Item",
        quantity: Decimal = Decimal("10"),
        unit_of_measure: str = "EA",
        unit_price: Decimal = Decimal("100"),
        discount_percent: Decimal = Decimal("0"),
        discount_amount: Decimal = Decimal("0"),
        tax_code_id: uuid.UUID = None,
        tax_amount: Decimal = Decimal("100"),
        line_total: Decimal = None,
        revenue_account_id: uuid.UUID = None,
        project_id: uuid.UUID = None,
        cost_center_id: uuid.UUID = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.quote_id = quote_id or uuid.uuid4()
        self.line_number = line_number
        self.item_code = item_code
        self.description = description
        self.quantity = quantity
        self.unit_of_measure = unit_of_measure
        self.unit_price = unit_price
        self.discount_percent = discount_percent
        self.discount_amount = discount_amount
        self.tax_code_id = tax_code_id
        self.tax_amount = tax_amount
        self.line_total = line_total or (quantity * unit_price - discount_amount + tax_amount)
        self.revenue_account_id = revenue_account_id
        self.project_id = project_id
        self.cost_center_id = cost_center_id


class TestGenerateQuoteNumber:
    """Tests for generate_quote_number method."""

    @patch("app.services.ifrs.ar.quote.SyncNumberingService")
    def test_generate_quote_number(self, mock_numbering_class):
        """Test quote number generation."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "QT-2024-001"
        mock_numbering_class.return_value = mock_numbering

        result = QuoteService.generate_quote_number(mock_db, org_id)

        assert result == "QT-2024-001"
        mock_numbering.generate_next_number.assert_called_once()


class TestCreate:
    """Tests for create method."""

    @patch("app.services.ifrs.ar.quote.QuoteService.generate_quote_number")
    @patch("app.services.ifrs.ar.quote.Quote")
    def test_create_basic_quote(self, mock_quote_class, mock_generate):
        """Test creating a basic quote."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_generate.return_value = "QT-001"
        mock_quote = MockQuote()
        mock_quote.lines = []
        mock_quote_class.return_value = mock_quote

        result = QuoteService.create(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
            quote_date=date.today(),
            valid_until=date.today() + timedelta(days=30),
            created_by=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    @patch("app.services.ifrs.ar.quote.QuoteService.generate_quote_number")
    @patch("app.services.ifrs.ar.quote.QuoteService._add_lines")
    @patch("app.services.ifrs.ar.quote.QuoteService._recalculate_totals")
    @patch("app.services.ifrs.ar.quote.Quote")
    def test_create_quote_with_lines(self, mock_quote_class, mock_recalc, mock_add_lines, mock_generate):
        """Test creating a quote with lines."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_generate.return_value = "QT-001"
        mock_quote = MockQuote()
        mock_quote.lines = []
        mock_quote_class.return_value = mock_quote

        lines = [
            {
                "description": "Test Item",
                "quantity": 10,
                "unit_price": "100.00",
            }
        ]

        result = QuoteService.create(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
            quote_date=date.today(),
            valid_until=date.today() + timedelta(days=30),
            created_by=user_id,
            lines=lines,
        )

        mock_add_lines.assert_called_once()
        mock_recalc.assert_called_once()


class TestUpdate:
    """Tests for update method."""

    def test_update_draft_quote(self):
        """Test updating a draft quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.update(
            db=mock_db,
            quote_id=str(quote_id),
            updated_by=str(user_id),
            reference="Updated Reference",
        )

        assert result.reference == "Updated Reference"
        mock_db.flush.assert_called_once()

    def test_update_not_found(self):
        """Test updating non-existent quote."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.update(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
                updated_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_update_non_draft_quote(self):
        """Test updating non-draft quote fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.SENT)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.update(
                db=mock_db,
                quote_id=str(quote_id),
                updated_by=str(uuid.uuid4()),
            )

        assert "Cannot update" in str(exc_info.value)


class TestSend:
    """Tests for send method."""

    def test_send_draft_quote(self):
        """Test sending a draft quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.send(
            db=mock_db,
            quote_id=str(quote_id),
            sent_by=str(user_id),
        )

        assert result.status == QuoteStatus.SENT
        assert result.sent_by is not None
        mock_db.flush.assert_called_once()

    def test_send_not_found(self):
        """Test sending non-existent quote."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.send(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
                sent_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_send_wrong_status(self):
        """Test sending quote in wrong status fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.ACCEPTED)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.send(
                db=mock_db,
                quote_id=str(quote_id),
                sent_by=str(uuid.uuid4()),
            )

        assert "Cannot send" in str(exc_info.value)


class TestMarkViewed:
    """Tests for mark_viewed method."""

    def test_mark_viewed_sent_quote(self):
        """Test marking sent quote as viewed."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.SENT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.mark_viewed(
            db=mock_db,
            quote_id=str(quote_id),
        )

        assert result.status == QuoteStatus.VIEWED
        mock_db.flush.assert_called_once()

    def test_mark_viewed_not_found(self):
        """Test marking non-existent quote as viewed."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.mark_viewed(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_mark_viewed_non_sent_quote(self):
        """Test marking non-sent quote as viewed does nothing."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.mark_viewed(
            db=mock_db,
            quote_id=str(quote_id),
        )

        assert result.status == QuoteStatus.DRAFT  # Status unchanged


class TestAccept:
    """Tests for accept method."""

    def test_accept_sent_quote(self):
        """Test accepting a sent quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(
            quote_id=quote_id,
            status=QuoteStatus.SENT,
            valid_until=date.today() + timedelta(days=10),  # Not expired
        )
        mock_db.get.return_value = mock_quote

        result = QuoteService.accept(
            db=mock_db,
            quote_id=str(quote_id),
        )

        assert result.status == QuoteStatus.ACCEPTED
        mock_db.flush.assert_called_once()

    def test_accept_viewed_quote(self):
        """Test accepting a viewed quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(
            quote_id=quote_id,
            status=QuoteStatus.VIEWED,
            valid_until=date.today() + timedelta(days=10),
        )
        mock_db.get.return_value = mock_quote

        result = QuoteService.accept(
            db=mock_db,
            quote_id=str(quote_id),
        )

        assert result.status == QuoteStatus.ACCEPTED

    def test_accept_not_found(self):
        """Test accepting non-existent quote."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.accept(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_accept_wrong_status(self):
        """Test accepting quote in wrong status fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.accept(
                db=mock_db,
                quote_id=str(quote_id),
            )

        assert "Cannot accept" in str(exc_info.value)

    def test_accept_expired_quote(self):
        """Test accepting expired quote fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(
            quote_id=quote_id,
            status=QuoteStatus.SENT,
            valid_until=date.today() - timedelta(days=1),  # Expired
        )
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.accept(
                db=mock_db,
                quote_id=str(quote_id),
            )

        assert "expired" in str(exc_info.value)


class TestReject:
    """Tests for reject method."""

    def test_reject_sent_quote(self):
        """Test rejecting a sent quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.SENT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.reject(
            db=mock_db,
            quote_id=str(quote_id),
            reason="Too expensive",
        )

        assert result.status == QuoteStatus.REJECTED
        assert result.rejection_reason == "Too expensive"
        mock_db.flush.assert_called_once()

    def test_reject_not_found(self):
        """Test rejecting non-existent quote."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.reject(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_reject_wrong_status(self):
        """Test rejecting quote in wrong status fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.reject(
                db=mock_db,
                quote_id=str(quote_id),
            )

        assert "Cannot reject" in str(exc_info.value)


class TestConvertToInvoice:
    """Tests for convert_to_invoice method."""

    @patch("app.services.ifrs.ar.quote.InvoiceLine")
    @patch("app.services.ifrs.ar.quote.Invoice")
    @patch("app.services.ifrs.ar.quote.SyncNumberingService")
    def test_convert_to_invoice_success(self, mock_numbering_class, mock_invoice_class, mock_inv_line_class):
        """Test converting accepted quote to invoice."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_quote_line = MockQuoteLine(quote_id=quote_id)
        mock_quote = MockQuote(
            quote_id=quote_id,
            status=QuoteStatus.ACCEPTED,
            lines=[mock_quote_line],
        )

        mock_invoice = MagicMock()
        mock_invoice.invoice_id = uuid.uuid4()
        mock_invoice_class.return_value = mock_invoice

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "INV-001"
        mock_numbering_class.return_value = mock_numbering

        mock_db.get.return_value = mock_quote

        result = QuoteService.convert_to_invoice(
            db=mock_db,
            quote_id=str(quote_id),
            created_by=str(user_id),
        )

        assert mock_db.add.called
        assert mock_quote.status == QuoteStatus.CONVERTED
        mock_db.flush.assert_called()

    def test_convert_to_invoice_not_found(self):
        """Test converting non-existent quote to invoice."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.convert_to_invoice(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
                created_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_convert_to_invoice_wrong_status(self):
        """Test converting non-accepted quote to invoice fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.convert_to_invoice(
                db=mock_db,
                quote_id=str(quote_id),
                created_by=str(uuid.uuid4()),
            )

        assert "only convert accepted" in str(exc_info.value).lower()


class TestConvertToSalesOrder:
    """Tests for convert_to_sales_order method."""

    @patch("app.services.ifrs.ar.quote.SalesOrderLine")
    @patch("app.services.ifrs.ar.quote.SalesOrder")
    @patch("app.services.ifrs.ar.quote.SyncNumberingService")
    def test_convert_to_sales_order_success(self, mock_numbering_class, mock_so_class, mock_so_line_class):
        """Test converting accepted quote to sales order."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_quote_line = MockQuoteLine(quote_id=quote_id)
        mock_quote = MockQuote(
            quote_id=quote_id,
            status=QuoteStatus.ACCEPTED,
            lines=[mock_quote_line],
        )

        mock_so = MagicMock()
        mock_so.so_id = uuid.uuid4()
        mock_so_class.return_value = mock_so

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SO-001"
        mock_numbering_class.return_value = mock_numbering

        mock_db.get.return_value = mock_quote

        result = QuoteService.convert_to_sales_order(
            db=mock_db,
            quote_id=str(quote_id),
            created_by=str(user_id),
            customer_po_number="PO-12345",
        )

        assert mock_db.add.called
        assert mock_quote.status == QuoteStatus.CONVERTED
        mock_db.flush.assert_called()

    def test_convert_to_sales_order_not_found(self):
        """Test converting non-existent quote to sales order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.convert_to_sales_order(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
                created_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_convert_to_sales_order_wrong_status(self):
        """Test converting non-accepted quote to sales order fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.SENT)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.convert_to_sales_order(
                db=mock_db,
                quote_id=str(quote_id),
                created_by=str(uuid.uuid4()),
            )

        assert "only convert accepted" in str(exc_info.value).lower()


class TestVoid:
    """Tests for void method."""

    def test_void_draft_quote(self):
        """Test voiding a draft quote."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.DRAFT)
        mock_db.get.return_value = mock_quote

        result = QuoteService.void(
            db=mock_db,
            quote_id=str(quote_id),
            voided_by=str(user_id),
        )

        assert result.status == QuoteStatus.VOID
        mock_db.flush.assert_called_once()

    def test_void_not_found(self):
        """Test voiding non-existent quote."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            QuoteService.void(
                db=mock_db,
                quote_id=str(uuid.uuid4()),
                voided_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_void_converted_quote_fails(self):
        """Test voiding converted quote fails."""
        mock_db = MagicMock()
        quote_id = uuid.uuid4()

        mock_quote = MockQuote(quote_id=quote_id, status=QuoteStatus.CONVERTED)
        mock_db.get.return_value = mock_quote

        with pytest.raises(ValueError) as exc_info:
            QuoteService.void(
                db=mock_db,
                quote_id=str(quote_id),
                voided_by=str(uuid.uuid4()),
            )

        assert "Cannot void a converted" in str(exc_info.value)


class TestExpireQuotes:
    """Tests for expire_quotes method."""

    def test_expire_quotes(self):
        """Test expiring quotes."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.update.return_value = 5

        result = QuoteService.expire_quotes(mock_db)

        assert result == 5
        mock_db.flush.assert_called_once()


class TestListQuotes:
    """Tests for list_quotes method."""

    def test_list_quotes_basic(self):
        """Test listing quotes with no filters."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_quotes = [MockQuote(), MockQuote()]
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_quotes

        result = QuoteService.list_quotes(
            db=mock_db,
            organization_id=org_id,
        )

        assert len(result) == 2

    def test_list_quotes_with_customer_filter(self):
        """Test listing quotes with customer filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())

        mock_quotes = [MockQuote()]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_quotes

        result = QuoteService.list_quotes(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
        )

        assert len(result) == 1

    def test_list_quotes_with_status_filter(self):
        """Test listing quotes with status filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_quotes = [MockQuote()]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_quotes

        result = QuoteService.list_quotes(
            db=mock_db,
            organization_id=org_id,
            status=QuoteStatus.DRAFT,
        )

        assert len(result) == 1

    def test_list_quotes_with_date_range(self):
        """Test listing quotes with date range filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_quotes = [MockQuote()]
        mock_db.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_quotes

        result = QuoteService.list_quotes(
            db=mock_db,
            organization_id=org_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert len(result) == 1

    def test_list_quotes_with_pagination(self):
        """Test listing quotes with pagination."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_quotes = [MockQuote()]
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_quotes

        result = QuoteService.list_quotes(
            db=mock_db,
            organization_id=org_id,
            limit=10,
            offset=5,
        )

        assert len(result) == 1
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.assert_called_with(5)
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.assert_called_with(10)
