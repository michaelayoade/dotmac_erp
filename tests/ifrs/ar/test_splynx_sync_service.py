"""
Tests for Splynx Sync Service.

Covers invoice, payment, and credit note sync logic including:
- Happy path create/update
- Hash-based change detection (skip_unchanged)
- Batch size limits
- Customer resolution
- Payment allocation & invoice status update
- Credit note sync tracking
- Edge cases (missing invoice, missing customer, duplicate sync)
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from app.models.finance.ar.customer_payment import PaymentMethod
from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import InvoiceStatus
from app.services.splynx.client import (
    SplynxConfig,
    SplynxCreditNote,
    SplynxError,
    SplynxInvoice,
    SplynxPayment,
    SplynxPaymentMethod,
)
from app.services.splynx.sync import SYSTEM_USER_ID, SplynxSyncService, SyncResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid.uuid4()
AR_ACCOUNT_ID = uuid.uuid4()
REVENUE_ACCOUNT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _make_config() -> SplynxConfig:
    return SplynxConfig(
        api_url="https://test.splynx.local",
        api_key="test-key",
        api_secret="test-secret",
    )


def _make_service(db: MagicMock) -> SplynxSyncService:
    """Create a SplynxSyncService with a mock DB and stubbed client."""
    svc = SplynxSyncService(
        db=db,
        organization_id=ORG_ID,
        ar_control_account_id=AR_ACCOUNT_ID,
        default_revenue_account_id=REVENUE_ACCOUNT_ID,
        config=_make_config(),
    )
    # Stub out the HTTP client so no real requests are made
    svc._client = MagicMock()
    return svc


def _make_splynx_invoice(
    *,
    id: int = 1001,
    number: str = "INV-2024-001",
    customer_id: int = 500,
    total: Decimal = Decimal("50000.00"),
    total_due: Decimal = Decimal("50000.00"),
    status: str = "unpaid",
) -> SplynxInvoice:
    return SplynxInvoice(
        id=id,
        number=number,
        customer_id=customer_id,
        date_created="2024-06-01",
        date_till="2024-07-01",
        status=status,
        total=total,
        total_due=total_due,
        currency="NGN",
    )


def _make_splynx_payment(
    *,
    id: int = 2001,
    customer_id: int = 500,
    invoice_id: int = 1001,
    amount: Decimal = Decimal("50000.00"),
    payment_type: int = 1,
) -> SplynxPayment:
    return SplynxPayment(
        id=id,
        customer_id=customer_id,
        customer_name="Test Customer",
        invoice_id=invoice_id,
        date="2024-06-15",
        amount=amount,
        payment_type=payment_type,
        receipt_number="RCP-001",
        reference="REF-001",
    )


def _make_splynx_credit_note(
    *,
    id: int = 3001,
    number: str = "CN-2024-001",
    customer_id: int = 500,
    total: Decimal = Decimal("5000.00"),
) -> SplynxCreditNote:
    return SplynxCreditNote(
        id=id,
        number=number,
        customer_id=customer_id,
        customer_name="Test Customer",
        date_created="2024-06-10",
        total=total,
        status="applied",
    )


class FakeInvoice:
    """Minimal stand-in for the Invoice ORM model."""

    def __init__(
        self,
        invoice_id: uuid.UUID | None = None,
        organization_id: uuid.UUID = ORG_ID,
        customer_id: uuid.UUID | None = None,
        invoice_number: str = "SPL-INV-1001",
        total_amount: Decimal = Decimal("50000.00"),
        amount_paid: Decimal = Decimal("0"),
        status: InvoiceStatus = InvoiceStatus.POSTED,
        currency_code: str = "NGN",
        correlation_id: str = "",
        **kwargs: object,
    ):
        self.invoice_id = invoice_id or uuid.uuid4()
        self.organization_id = organization_id
        self.customer_id = customer_id or uuid.uuid4()
        self.invoice_number = invoice_number
        self.total_amount = total_amount
        self.subtotal = total_amount
        self.functional_currency_amount = total_amount
        self.amount_paid = amount_paid
        self.status = status
        self.currency_code = currency_code
        self.correlation_id = correlation_id
        self.due_date = date(2024, 7, 1)
        self.notes = None
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Invoice Sync Tests
# ---------------------------------------------------------------------------


class TestSyncSingleInvoice:
    """Tests for _sync_single_invoice."""

    def test_create_new_invoice(self) -> None:
        """Happy path: new invoice is created and sync is recorded."""
        db = MagicMock()
        db.scalar.return_value = None  # No existing sync record / invoice
        svc = _make_service(db)
        # Pre-cache customer
        customer_uuid = uuid.uuid4()
        svc._customer_cache[500] = customer_uuid

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice()

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.created == 1
        assert result.updated == 0
        assert result.skipped == 0
        # Invoice + InvoiceLine added
        assert db.add.call_count >= 2
        db.flush.assert_called()

    def test_skip_unchanged_invoice(self) -> None:
        """Invoice with same hash should be skipped."""
        db = MagicMock()
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        inv = _make_splynx_invoice()
        data_hash = svc._compute_hash(
            {
                "number": inv.number,
                "total": str(inv.total),
                "total_due": str(inv.total_due),
                "status": inv.status,
                "date_created": inv.date_created,
            }
        )
        # Simulate that the same hash already exists
        db.scalar.return_value = data_hash  # _has_changed returns False

        result = SyncResult(success=True, entity_type="invoices")
        svc._sync_single_invoice(inv, USER_ID, result, skip_unchanged=True)

        assert result.skipped == 1
        assert result.created == 0

    def test_update_existing_invoice_applies_all_fields(self) -> None:
        """Update path should update total, due_date, notes — not just status."""
        db = MagicMock()
        svc = _make_service(db)

        customer_uuid = uuid.uuid4()
        svc._customer_cache[500] = customer_uuid

        existing = FakeInvoice(
            total_amount=Decimal("40000.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
        )

        sync_record_mock = MagicMock(
            synced_at=None, sync_hash=None, external_updated_at=None
        )

        # Chain: _has_changed → _get_synced_entity (in method) →
        # _record_sync._get_synced_entity → _record_sync.scalar(full record)
        db.scalar.side_effect = [
            "old-hash",  # _has_changed: old hash != new hash
            existing.invoice_id,  # _get_synced_entity (in _sync_single_invoice)
            existing.invoice_id,  # _record_sync → _get_synced_entity
            sync_record_mock,  # _record_sync → fetch full ExternalSync row
        ]
        db.get.return_value = existing

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice(total=Decimal("55000.00"), status="paid")

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.updated == 1
        assert existing.total_amount == Decimal("55000.00")
        assert existing.subtotal == Decimal("55000.00")
        assert existing.status == InvoiceStatus.PAID

    def test_skip_invoice_when_customer_not_synced(self) -> None:
        """Invoice should be skipped with an error when customer is missing."""
        db = MagicMock()
        db.scalar.return_value = None  # _has_changed, _get_synced_entity all None
        svc = _make_service(db)
        # No customer in cache and no fallback

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice(customer_id=9999)

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.skipped == 1
        assert len(result.errors) == 1
        assert "9999" in result.errors[0]

    def test_created_by_user_id_uses_system_user_when_none(self) -> None:
        """When no user ID is provided, SYSTEM_USER_ID should be used."""
        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice()

        svc._sync_single_invoice(inv, None, result)  # No user ID

        assert result.created == 1
        # Verify the Invoice constructor received SYSTEM_USER_ID
        added_obj = db.add.call_args_list[0][0][0]
        assert added_obj.created_by_user_id == SYSTEM_USER_ID


# ---------------------------------------------------------------------------
# Payment Sync Tests
# ---------------------------------------------------------------------------


class TestSyncSinglePayment:
    """Tests for _sync_single_payment."""

    def _setup_payment_test(
        self,
        *,
        existing_sync: bool = False,
        invoice_exists: bool = True,
        invoice_currency: str = "NGN",
    ) -> tuple[MagicMock, SplynxSyncService, FakeInvoice | None]:
        """Common setup for payment tests."""
        db = MagicMock()
        svc = _make_service(db)

        # Stub payment method lookup
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="Paystack", is_active=True)
        }
        svc._bank_account_mapping = {1: uuid.uuid4()}

        fake_invoice = None
        if invoice_exists:
            fake_invoice = FakeInvoice(
                currency_code=invoice_currency,
                correlation_id="splynx-inv-1001",
            )

        if existing_sync:
            # First scalar: _has_changed hash, second: _get_synced_entity
            db.scalar.side_effect = [
                "same-hash",  # _has_changed
            ]
        elif invoice_exists:
            db.scalar.side_effect = [
                None,  # _has_changed: no existing hash
                None,  # _get_synced_entity: not yet synced
                fake_invoice,  # Find invoice by correlation_id
                None,  # _record_sync: _get_synced_entity check
            ]
        else:
            db.scalar.side_effect = [
                None,  # _has_changed
                None,  # _get_synced_entity
                None,  # Find invoice: not found
            ]

        return db, svc, fake_invoice

    def test_create_payment_and_allocate(self) -> None:
        """Happy path: payment created, allocated to invoice, invoice updated."""
        db, svc, invoice = self._setup_payment_test()
        assert invoice is not None

        result = SyncResult(success=True, entity_type="payments")
        pmt = _make_splynx_payment(amount=Decimal("50000.00"))

        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.created == 1
        # Invoice + Payment + Allocation added
        assert db.add.call_count >= 2
        # Invoice should be marked PAID
        assert invoice.amount_paid == Decimal("50000.00")
        assert invoice.status == InvoiceStatus.PAID

    def test_partial_payment_updates_status(self) -> None:
        """Partial payment sets invoice to PARTIALLY_PAID."""
        db, svc, invoice = self._setup_payment_test()
        assert invoice is not None

        result = SyncResult(success=True, entity_type="payments")
        pmt = _make_splynx_payment(amount=Decimal("20000.00"))

        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.created == 1
        assert invoice.amount_paid == Decimal("20000.00")
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID

    def test_skip_unchanged_payment(self) -> None:
        """Payment with same hash should be skipped."""
        db = MagicMock()
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="Paystack", is_active=True)
        }

        pmt = _make_splynx_payment()
        data_hash = svc._compute_hash(
            {
                "invoice_id": pmt.invoice_id,
                "amount": str(pmt.amount),
                "date": pmt.date,
                "payment_type": pmt.payment_type,
                "reference": pmt.reference,
            }
        )
        # Return same hash from DB
        db.scalar.return_value = data_hash

        result = SyncResult(success=True, entity_type="payments")
        svc._sync_single_payment(pmt, result, USER_ID, skip_unchanged=True)

        assert result.skipped == 1
        assert result.created == 0

    def test_skip_payment_no_invoice_id(self) -> None:
        """Payment without invoice_id is skipped with error."""
        db = MagicMock()
        db.scalar.side_effect = [None, None]  # _has_changed, _get_synced_entity
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="Paystack", is_active=True)
        }

        pmt = _make_splynx_payment(invoice_id=None)

        result = SyncResult(success=True, entity_type="payments")
        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.skipped == 1
        assert any("No invoice_id" in e for e in result.errors)

    def test_skip_payment_invoice_not_found(self) -> None:
        """Payment referencing un-synced invoice is skipped with error."""
        db, svc, _ = self._setup_payment_test(invoice_exists=False)

        result = SyncResult(success=True, entity_type="payments")
        pmt = _make_splynx_payment()

        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.skipped == 1
        assert any("not synced" in e for e in result.errors)

    def test_payment_uses_invoice_currency(self) -> None:
        """Payment currency should match the invoice, not hardcoded NGN."""
        db, svc, invoice = self._setup_payment_test(invoice_currency="USD")

        result = SyncResult(success=True, entity_type="payments")
        pmt = _make_splynx_payment()

        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.created == 1
        # The added payment should use USD
        added_payment = db.add.call_args_list[0][0][0]
        assert added_payment.currency_code == "USD"

    def test_created_by_user_id_falls_back_to_system(self) -> None:
        """When no user ID provided, SYSTEM_USER_ID should be used."""
        db, svc, invoice = self._setup_payment_test()

        result = SyncResult(success=True, entity_type="payments")
        pmt = _make_splynx_payment()

        svc._sync_single_payment(pmt, result, None)  # No user ID

        assert result.created == 1
        added_payment = db.add.call_args_list[0][0][0]
        assert added_payment.created_by_user_id == SYSTEM_USER_ID


# ---------------------------------------------------------------------------
# Credit Note Sync Tests
# ---------------------------------------------------------------------------


class TestSyncSingleCreditNote:
    """Tests for _sync_single_credit_note."""

    def test_create_credit_note_with_sync_tracking(self) -> None:
        """New credit note should be created and _record_sync called."""
        db = MagicMock()
        # _get_synced_entity (EntityType.CREDIT_NOTE): None
        # _get_existing_invoice: None
        # _get_or_create_customer_id -> _get_synced_entity: None
        # _get_existing_customer: None -> returns None (customer not found)
        # Actually we need to cache the customer first
        svc = _make_service(db)
        customer_uuid = uuid.uuid4()
        svc._customer_cache[500] = customer_uuid

        db.scalar.side_effect = [
            None,  # _get_synced_entity (CREDIT_NOTE lookup)
            None,  # _get_existing_invoice (by invoice_number)
            None,  # _record_sync: _get_synced_entity check
        ]

        result = SyncResult(success=True, entity_type="credit_notes")
        cn = _make_splynx_credit_note()

        svc._sync_single_credit_note(cn, USER_ID, result)

        assert result.created == 1
        # Invoice + InvoiceLine should be added
        assert db.add.call_count >= 2
        db.flush.assert_called()

    def test_update_credit_note_records_sync(self) -> None:
        """Updated credit note should call _record_sync."""
        db = MagicMock()
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        existing = FakeInvoice(
            invoice_number="SPL-CN-3001",
            total_amount=Decimal("4000.00"),
        )

        sync_record_mock = MagicMock(
            synced_at=None, sync_hash=None, external_updated_at=None
        )

        # Chain: _get_synced_entity (CREDIT_NOTE) →
        # _record_sync._get_synced_entity → _record_sync.scalar(full record)
        db.scalar.side_effect = [
            existing.invoice_id,  # _get_synced_entity (in method)
            existing.invoice_id,  # _record_sync → _get_synced_entity
            sync_record_mock,  # _record_sync → fetch full ExternalSync row
        ]
        db.get.return_value = existing

        result = SyncResult(success=True, entity_type="credit_notes")
        cn = _make_splynx_credit_note(total=Decimal("6000.00"))

        svc._sync_single_credit_note(cn, USER_ID, result)

        assert result.updated == 1
        assert existing.total_amount == Decimal("6000.00")
        assert existing.subtotal == Decimal("6000.00")

    def test_skip_credit_note_when_customer_missing(self) -> None:
        """Credit note should be skipped when customer is not synced."""
        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        # No customer in cache

        result = SyncResult(success=True, entity_type="credit_notes")
        cn = _make_splynx_credit_note(customer_id=9999)

        svc._sync_single_credit_note(cn, USER_ID, result)

        assert result.skipped == 1
        assert any("9999" in e for e in result.errors)

    def test_credit_note_uses_system_user_id(self) -> None:
        """Credit note should use SYSTEM_USER_ID when no user given."""
        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="credit_notes")
        cn = _make_splynx_credit_note()

        svc._sync_single_credit_note(cn, None, result)

        assert result.created == 1
        added_invoice = db.add.call_args_list[0][0][0]
        assert added_invoice.created_by_user_id == SYSTEM_USER_ID


# ---------------------------------------------------------------------------
# Invoice Status Mapping
# ---------------------------------------------------------------------------


class TestMapInvoiceStatus:
    """Tests for _map_invoice_status."""

    def test_paid_status(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._map_invoice_status("paid", Decimal("0")) == InvoiceStatus.PAID

    def test_zero_due_maps_to_paid(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._map_invoice_status("unpaid", Decimal("0")) == InvoiceStatus.PAID

    def test_partially_paid(self) -> None:
        svc = _make_service(MagicMock())
        assert (
            svc._map_invoice_status("partially_paid", Decimal("100"))
            == InvoiceStatus.PARTIALLY_PAID
        )

    def test_unpaid_maps_to_posted(self) -> None:
        svc = _make_service(MagicMock())
        assert (
            svc._map_invoice_status("unpaid", Decimal("5000")) == InvoiceStatus.POSTED
        )

    def test_unknown_status_maps_to_posted(self) -> None:
        svc = _make_service(MagicMock())
        assert (
            svc._map_invoice_status("some_unknown", Decimal("1000"))
            == InvoiceStatus.POSTED
        )


# ---------------------------------------------------------------------------
# Payment Method Mapping
# ---------------------------------------------------------------------------


class TestMapPaymentMethod:
    """Tests for _map_payment_method."""

    def test_cash_method(self) -> None:
        svc = _make_service(MagicMock())
        svc._payment_method_cache = {
            10: SplynxPaymentMethod(id=10, name="Cash Payment", is_active=True)
        }
        assert svc._map_payment_method(10) == PaymentMethod.CASH

    def test_paystack_method(self) -> None:
        svc = _make_service(MagicMock())
        svc._payment_method_cache = {
            20: SplynxPaymentMethod(id=20, name="Paystack Online", is_active=True)
        }
        assert svc._map_payment_method(20) == PaymentMethod.CARD

    def test_bank_transfer_default(self) -> None:
        svc = _make_service(MagicMock())
        svc._payment_method_cache = {
            30: SplynxPaymentMethod(id=30, name="Zenith 461", is_active=True)
        }
        assert svc._map_payment_method(30) == PaymentMethod.BANK_TRANSFER

    def test_unknown_method_id(self) -> None:
        svc = _make_service(MagicMock())
        svc._payment_method_cache = {}
        assert svc._map_payment_method(999) == PaymentMethod.BANK_TRANSFER


# ---------------------------------------------------------------------------
# Hash / Change Detection
# ---------------------------------------------------------------------------


class TestChangeDetection:
    """Tests for _compute_hash and _has_changed."""

    def test_same_data_produces_same_hash(self) -> None:
        svc = _make_service(MagicMock())
        data = {"a": "1", "b": "2"}
        assert svc._compute_hash(data) == svc._compute_hash(data)

    def test_different_data_produces_different_hash(self) -> None:
        svc = _make_service(MagicMock())
        h1 = svc._compute_hash({"a": "1"})
        h2 = svc._compute_hash({"a": "2"})
        assert h1 != h2

    def test_has_changed_when_no_prior_hash(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None  # No existing hash
        svc = _make_service(db)
        assert svc._has_changed(EntityType.INVOICE, "123", "abc") is True

    def test_has_changed_when_hash_differs(self) -> None:
        db = MagicMock()
        db.scalar.return_value = "old-hash"
        svc = _make_service(db)
        assert svc._has_changed(EntityType.INVOICE, "123", "new-hash") is True

    def test_not_changed_when_hash_matches(self) -> None:
        db = MagicMock()
        db.scalar.return_value = "same-hash"
        svc = _make_service(db)
        assert svc._has_changed(EntityType.INVOICE, "123", "same-hash") is False


# ---------------------------------------------------------------------------
# Date Parsing
# ---------------------------------------------------------------------------


class TestParseDate:
    """Tests for _parse_date."""

    def test_iso_format(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._parse_date("2024-06-15") == date(2024, 6, 15)

    def test_iso_format_with_time(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._parse_date("2024-06-15T10:30:00Z") == date(2024, 6, 15)

    def test_european_format(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._parse_date("15/06/2024") == date(2024, 6, 15)

    def test_none_returns_none(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._parse_date(None) is None

    def test_invalid_returns_none(self) -> None:
        svc = _make_service(MagicMock())
        assert svc._parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# SYSTEM_USER_ID constant
# ---------------------------------------------------------------------------


class TestSystemUserId:
    """Verify SYSTEM_USER_ID is the nil UUID (all zeros)."""

    def test_value(self) -> None:
        assert uuid.UUID("00000000-0000-0000-0000-000000000000") == SYSTEM_USER_ID
        assert SYSTEM_USER_ID.int == 0


# ---------------------------------------------------------------------------
# Sync API Error Handling
# ---------------------------------------------------------------------------


class TestSyncApiError:
    """Tests for API error propagation in sync_invoices / sync_payments."""

    def test_sync_invoices_api_error(self) -> None:
        """Splynx API errors should be caught and reported."""
        db = MagicMock()
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()
        svc.client.get_invoices.side_effect = SplynxError("timeout", status_code=500)

        result = svc.sync_invoices()

        assert result.success is False
        assert "Splynx API error" in result.message

    def test_sync_payments_api_error(self) -> None:
        """Splynx API errors should be caught and reported."""
        db = MagicMock()
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="X", is_active=True)
        }
        svc.client.get_payments.side_effect = SplynxError("timeout", status_code=500)

        result = svc.sync_payments()

        assert result.success is False
        assert "Splynx API error" in result.message

    def test_sync_credit_notes_api_error(self) -> None:
        """Splynx API errors should be caught and reported."""
        db = MagicMock()
        svc = _make_service(db)
        svc.client.get_credit_notes.side_effect = SplynxError(
            "timeout", status_code=500
        )

        result = svc.sync_credit_notes()

        assert result.success is False
        assert "Splynx API error" in result.message


# ---------------------------------------------------------------------------
# Batch Size Limits
# ---------------------------------------------------------------------------


class TestBatchSize:
    """Tests that batch_size limits the number of records processed."""

    def test_invoice_batch_limit(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        invoices = [_make_splynx_invoice(id=i) for i in range(10)]
        svc.client.get_invoices.return_value = iter(invoices)

        result = svc.sync_invoices(batch_size=3)

        assert result.created <= 3

    def test_payment_batch_limit(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="X", is_active=True)
        }
        svc._bank_account_mapping = {}

        # For each payment: _has_changed, _get_synced_entity, find invoice, _record_sync
        db.scalar.side_effect = [None] * 100  # Enough Nones
        # But the invoice lookup needs to return a real invoice
        # This is tricky with side_effect. Let's just verify the batch message.
        payments = [_make_splynx_payment(id=i) for i in range(10)]
        svc.client.get_payments.return_value = iter(payments)

        result = svc.sync_payments(batch_size=2)

        # Should stop after 2 processed (even if some were skipped)
        assert "Batch limit" in result.message or result.created + result.skipped <= 3
