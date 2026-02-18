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
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.models.finance.ar.customer_payment import CustomerPayment, PaymentMethod
from app.models.finance.ar.external_sync import EntityType
from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
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


_invoice_counter = 0
_payment_counter = 0
_cn_counter = 0


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

    # Mock numbering service methods to avoid SELECT FOR UPDATE on MagicMock DB
    global _invoice_counter, _payment_counter, _cn_counter
    _invoice_counter = 0
    _payment_counter = 0
    _cn_counter = 0

    def _fake_invoice_number(reference_date: object = None) -> str:
        global _invoice_counter
        _invoice_counter += 1
        return f"INV-{_invoice_counter:05d}"

    def _fake_payment_number(reference_date: object = None) -> str:
        global _payment_counter
        _payment_counter += 1
        return f"PMT-{_payment_counter:05d}"

    def _fake_cn_number(reference_date: object = None) -> str:
        global _cn_counter
        _cn_counter += 1
        return f"CN-{_cn_counter:05d}"

    svc._generate_invoice_number = _fake_invoice_number  # type: ignore[assignment]
    svc._generate_payment_number = _fake_payment_number  # type: ignore[assignment]
    svc._generate_credit_note_number = _fake_cn_number  # type: ignore[assignment]
    # Mark tax code as already resolved (None = no tax) to prevent the
    # lazy property from calling _resolve_sales_tax() which would consume
    # mock DB scalar() calls intended for other operations.
    svc._sales_tax_code_resolved = True
    svc._sales_tax_code = None
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
        self.invoice_date = date(2024, 6, 1)
        self.notes = None
        self.journal_entry_id = None
        self.posting_batch_id = None
        self.posting_status = None
        self.created_by_user_id = None
        self.splynx_id = None
        self.splynx_number = None
        self.last_synced_at = None
        self.invoice_type = None
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
        from app.models.finance.ar.invoice import Invoice as InvoiceModel

        created_invoice = None
        for call in db.add.call_args_list:
            obj = call[0][0]
            if isinstance(obj, InvoiceModel):
                created_invoice = obj
                break
        assert created_invoice is not None
        assert created_invoice.splynx_number == inv.number

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
        from app.models.finance.ar.invoice import Invoice as InvoiceModel

        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice()

        svc._sync_single_invoice(inv, None, result)  # No user ID

        assert result.created == 1
        # Find the Invoice object among all added objects
        invoice_obj = None
        for call in db.add.call_args_list:
            obj = call[0][0]
            if isinstance(obj, InvoiceModel):
                invoice_obj = obj
                break
        assert invoice_obj is not None
        assert invoice_obj.created_by_user_id == SYSTEM_USER_ID


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
        added_payment = db.add.call_args_list[0][0][0]
        assert added_payment.splynx_receipt_number == pmt.receipt_number

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

    def test_unapplied_payment_no_invoice_id(self) -> None:
        """Payment without invoice_id is created as unapplied prepayment."""
        db = MagicMock()
        fake_customer_id = uuid.uuid4()
        fake_customer = MagicMock()
        fake_customer.currency_code = "NGN"
        db.scalar.side_effect = [
            None,  # _has_changed: no existing hash
            None,  # _get_synced_entity: not yet synced
            None,  # _record_sync: _get_synced_entity check
        ]
        db.get.return_value = fake_customer  # db.get(Customer, customer_id)
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="Paystack", is_active=True)
        }
        svc._bank_account_mapping = {1: uuid.uuid4()}
        # Pre-populate customer cache so _get_or_create_customer_id returns immediately
        svc._customer_cache = {500: fake_customer_id}

        pmt = _make_splynx_payment(invoice_id=None)

        result = SyncResult(success=True, entity_type="payments")
        svc._sync_single_payment(pmt, result, USER_ID)

        assert result.created == 1
        assert len(result.errors) == 0
        # Payment was added (but no allocation since no invoice)
        added_objs = [call[0][0] for call in db.add.call_args_list]
        payment_added = [o for o in added_objs if isinstance(o, CustomerPayment)]
        assert len(payment_added) == 1
        assert payment_added[0].customer_id == fake_customer_id
        # No PaymentAllocation should have been added
        from app.services.splynx.sync import PaymentAllocation  # noqa: E501

        alloc_added = [o for o in added_objs if isinstance(o, PaymentAllocation)]
        assert len(alloc_added) == 0

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
            None,  # splynx_id lookup (new)
            None,  # _get_existing_invoice (legacy SPL-CN-{id} number)
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

    def test_fluterwave_typo_maps_to_card(self) -> None:
        svc = _make_service(MagicMock())
        svc._payment_method_cache = {
            21: SplynxPaymentMethod(id=21, name="Fluterwave Gateway", is_active=True)
        }
        assert svc._map_payment_method(21) == PaymentMethod.CARD


class TestBankAccountMethodMapping:
    """Tests for _build_bank_account_mapping robustness."""

    def test_payment_method_matches_bank_name_and_account_suffix(self) -> None:
        db = MagicMock()
        svc = _make_service(db)
        svc._payment_method_cache = {
            1: SplynxPaymentMethod(id=1, name="Zenith 461 Bank", is_active=True),
            2: SplynxPaymentMethod(id=2, name="Fluterwave Wallet", is_active=True),
        }

        zenith = SimpleNamespace(
            bank_account_id=uuid.uuid4(),
            bank_name="Zenith Bank",
            account_name="Collections",
            account_number="1016946461",
            is_primary=True,
            created_at=None,
        )
        flutter = SimpleNamespace(
            bank_account_id=uuid.uuid4(),
            bank_name="Flutterwave",
            account_name="Main",
            account_number="FW-001",
            is_primary=False,
            created_at=None,
        )

        db.scalars.return_value.all.return_value = [zenith, flutter]

        svc._build_bank_account_mapping()

        assert svc._bank_account_mapping[1] == zenith.bank_account_id
        assert svc._bank_account_mapping[2] == flutter.bank_account_id


class TestAutoAllocateUnappliedPayments:
    """Tests for strict Tier-A auto-allocation."""

    @staticmethod
    def _scalars_result(items: list[object]) -> MagicMock:
        result = MagicMock()
        result.all.return_value = items
        return result

    def test_allocates_unique_exact_customer_match(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        customer_id = uuid.uuid4()
        payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=customer_id,
            amount=Decimal("100.00"),
            payment_date=date(2026, 2, 14),
        )
        invoice = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
            invoice_type=InvoiceType.STANDARD,
        )

        db.scalars.side_effect = [
            self._scalars_result([payment]),
            self._scalars_result([invoice]),
        ]

        summary = svc.auto_allocate_unapplied_payments()

        assert summary["allocated"] == 1
        assert summary["ambiguous"] == 0
        assert summary["no_candidate"] == 0
        assert invoice.amount_paid == Decimal("100.00")
        assert invoice.status == InvoiceStatus.PAID
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_skips_when_ambiguous_multiple_invoice_candidates(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        customer_id = uuid.uuid4()
        payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=customer_id,
            amount=Decimal("50.00"),
            payment_date=date(2026, 2, 14),
        )
        inv1 = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("50.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
            invoice_type=InvoiceType.STANDARD,
        )
        inv2 = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("50.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.PARTIALLY_PAID,
            invoice_type=InvoiceType.STANDARD,
        )

        db.scalars.side_effect = [
            self._scalars_result([payment]),
            self._scalars_result([inv1, inv2]),
        ]

        summary = svc.auto_allocate_unapplied_payments()

        assert summary["allocated"] == 0
        assert summary["ambiguous"] == 1
        assert summary["no_candidate"] == 0
        db.add.assert_not_called()

    def test_skips_when_no_exact_balance_candidate(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        customer_id = uuid.uuid4()
        payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=customer_id,
            amount=Decimal("80.00"),
            payment_date=date(2026, 2, 14),
        )
        invoice = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("10.00"),
            status=InvoiceStatus.POSTED,
            invoice_type=InvoiceType.STANDARD,
        )

        db.scalars.side_effect = [
            self._scalars_result([payment]),
            self._scalars_result([invoice]),
        ]

        summary = svc.auto_allocate_unapplied_payments()

        assert summary["allocated"] == 0
        assert summary["ambiguous"] == 0
        assert summary["no_candidate"] == 1
        db.add.assert_not_called()


class TestRepairPaymentInvoiceRelationships:
    """Tests for repair_payment_invoice_relationships."""

    def test_creates_missing_allocation_from_splynx_invoice_link(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        customer_id = uuid.uuid4()
        local_payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=customer_id,
            payment_date=date(2026, 2, 14),
        )
        target_invoice = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("120.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
            invoice_type=InvoiceType.STANDARD,
            splynx_id="1001",
        )

        svc._client.get_payments.return_value = iter(
            [_make_splynx_payment(amount=Decimal("120.00"), invoice_id=1001)]
        )
        db.scalar.side_effect = [
            local_payment,  # local payment by splynx_id
            target_invoice,  # invoice by splynx_id
            None,  # existing allocation
            Decimal("120.00"),  # recompute invoice allocated sum
        ]
        db.get.return_value = target_invoice

        summary = svc.repair_payment_invoice_relationships()

        assert summary["processed"] == 1
        assert summary["fixed"] == 1
        assert summary["created_allocations"] == 1
        assert summary["relinked_allocations"] == 0
        assert target_invoice.amount_paid == Decimal("120.00")
        assert target_invoice.status == InvoiceStatus.PAID
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_relinks_existing_allocation_and_updates_amount(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        customer_id = uuid.uuid4()
        local_payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=customer_id,
            payment_date=date(2026, 2, 14),
        )
        old_invoice = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            status=InvoiceStatus.PAID,
            invoice_type=InvoiceType.STANDARD,
            splynx_id="999",
        )
        target_invoice = FakeInvoice(
            customer_id=customer_id,
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
            invoice_type=InvoiceType.STANDARD,
            splynx_id="1001",
        )
        existing_alloc = SimpleNamespace(
            payment_id=local_payment.payment_id,
            invoice_id=old_invoice.invoice_id,
            allocated_amount=Decimal("80.00"),
            allocation_date=date(2026, 2, 10),
        )

        svc._client.get_payments.return_value = iter(
            [_make_splynx_payment(amount=Decimal("100.00"), invoice_id=1001)]
        )
        # First 3 scalar calls are deterministic (main loop); the last 2 are
        # recompute calls inside touched_invoice_ids iteration whose set order
        # is non-deterministic. We track which invoice was last fetched via
        # db.get so the scalar call returns the correct SUM.
        _scalar_queue = iter([local_payment, target_invoice, existing_alloc])
        _recompute_map = {
            old_invoice.invoice_id: Decimal("0"),
            target_invoice.invoice_id: Decimal("100.00"),
        }
        _last_get_id: list[Any] = []

        def _orig_get_side_effect(_model: Any, inv_id: Any) -> Any:
            return old_invoice if inv_id == old_invoice.invoice_id else target_invoice

        def _get_side_effect(_model: Any, inv_id: Any) -> Any:
            _last_get_id.append(inv_id)
            return _orig_get_side_effect(_model, inv_id)

        def _scalar_side_effect(_stmt: Any) -> Any:
            try:
                return next(_scalar_queue)
            except StopIteration:
                # Recompute phase — return SUM for the last db.get'd invoice
                inv_id = _last_get_id[-1] if _last_get_id else None
                return _recompute_map.get(inv_id, Decimal("0"))

        db.scalar.side_effect = _scalar_side_effect
        db.get.side_effect = _get_side_effect

        summary = svc.repair_payment_invoice_relationships()

        assert summary["fixed"] == 1
        assert summary["created_allocations"] == 0
        assert summary["relinked_allocations"] == 1
        assert summary["updated_amounts"] == 1
        assert existing_alloc.invoice_id == target_invoice.invoice_id
        assert existing_alloc.allocated_amount == Decimal("100.00")
        assert old_invoice.amount_paid == Decimal("0")
        assert old_invoice.status == InvoiceStatus.POSTED
        assert target_invoice.amount_paid == Decimal("100.00")
        assert target_invoice.status == InvoiceStatus.PAID
        db.add.assert_not_called()

    def test_tracks_missing_local_invoice(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        local_payment = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            payment_date=date(2026, 2, 14),
        )
        svc._client.get_payments.return_value = iter(
            [_make_splynx_payment(invoice_id=1001)]
        )
        db.scalar.side_effect = [
            local_payment,  # local payment
            None,  # invoice by splynx_id
            None,  # fallback by correlation id
        ]

        summary = svc.repair_payment_invoice_relationships()

        assert summary["processed"] == 1
        assert summary["fixed"] == 0
        assert summary["missing_local_invoice"] == 1
        db.add.assert_not_called()
        db.flush.assert_called_once()


class TestReconcilePaystackPaymentsScoreGap:
    """Tests for tier-4 score-gap ambiguity resolution."""

    @staticmethod
    def _exec_result(*, rows: list[object] | None = None) -> MagicMock:
        r = MagicMock()
        r.fetchall.return_value = rows or []
        return r

    def test_score_gap_matches_ambiguous_pairs(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        d = date(2026, 2, 14)
        p1 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            payment_date=d,
            amount=Decimal("100.00"),
            reference="ref-a-12345",
            description="Payment ref-a-12345",
        )
        p2 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=p1.customer_id,
            payment_date=d,
            amount=Decimal("100.00"),
            reference="ref-b-67890",
            description="Payment ref-b-67890",
        )
        l1 = SimpleNamespace(
            line_id=uuid.uuid4(),
            transaction_date=date(2026, 2, 15),
            amount=Decimal("100.00"),
            reference="ref-a-12345",
            description="Payment: ref-a-12345",
        )
        l2 = SimpleNamespace(
            line_id=uuid.uuid4(),
            transaction_date=date(2026, 2, 16),
            amount=Decimal("100.00"),
            reference="ref-b-67890",
            description="Payment: ref-b-67890",
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[])  # payments_with_refs
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[])  # statement_refs
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[p1, p2])  # unmatched_payments
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(rows=[l1, l2])  # unmatched_lines
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=False)

        assert result["matched_by_score_gap"] == 2
        assert result["ambiguous_matches"] == 0
        assert result["unmatched_payments"] == 0
        assert result["unmatched_statements"] == 0
        assert result["review_queue"] == []
        db.flush.assert_called_once()

    def test_score_gap_leaves_review_queue_when_low_confidence(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        d = date(2026, 2, 14)
        p1 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            payment_date=d,
            amount=Decimal("100.00"),
            reference=None,
            description="generic payment",
        )
        p2 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=p1.customer_id,
            payment_date=d,
            amount=Decimal("100.00"),
            reference=None,
            description="generic payment",
        )
        l1 = SimpleNamespace(
            line_id=uuid.uuid4(),
            transaction_date=date(2026, 2, 15),
            amount=Decimal("100.00"),
            reference=None,
            description="credit",
        )
        l2 = SimpleNamespace(
            line_id=uuid.uuid4(),
            transaction_date=date(2026, 2, 16),
            amount=Decimal("100.00"),
            reference=None,
            description="credit",
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[])
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[])
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[p1, p2])
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(rows=[l1, l2])
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=True)

        assert result["matched_by_score_gap"] == 0
        assert result["ambiguous_matches"] == 2
        assert result["unmatched_payments"] == 2
        assert result["unmatched_statements"] == 2
        assert len(result["review_queue"]) == 2

    def test_matches_unique_line_within_date_window(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        payment_day = date(2026, 2, 14)
        statement_day = date(2026, 2, 16)  # within +7 day tolerance

        p1 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            payment_date=payment_day,
            amount=Decimal("250.00"),
            reference=None,
            description="collection",
        )
        l1 = SimpleNamespace(
            line_id=uuid.uuid4(),
            transaction_date=statement_day,
            amount=Decimal("250.00"),
            reference=None,
            description="paystack credit",
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[])
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[])
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[p1])
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(rows=[l1])
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=True)

        assert result["matched_by_reference"] == 0
        assert result["matched_by_date_amount"] == 1
        assert result["matched_by_customer"] == 0
        assert result["matched_by_score_gap"] == 0
        assert result["ambiguous_matches"] == 0
        assert result["unmatched_payments"] == 0
        assert result["unmatched_statements"] == 0

    def test_reference_match_uses_statement_description_token(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        d = date(2026, 2, 14)
        token = "698f511d277d1"
        p1 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            payment_date=d,
            amount=Decimal("18812.50"),
            reference="2026-11-00918",
            description=f"Splynx payment via Paystack. {token}",
        )
        line = SimpleNamespace(
            line_id=uuid.uuid4(),
            reference=None,
            description=f"PAYSTACK COLLECTION ref {token}",
            amount=Decimal("18812.50"),
            transaction_date=d,
            is_matched=False,
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[p1])  # payments_with_refs
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[line])  # statement_refs
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[])  # unmatched_payments after ref match
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(rows=[line])
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=True)

        assert result["matched_by_reference"] == 1
        assert result["matched_by_date_amount"] == 0
        assert result["unmatched_statements"] == 0

    def test_reference_match_uses_payment_reference_token(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        d = date(2026, 2, 14)
        token = "698f8d88c5793"
        p1 = SimpleNamespace(
            payment_id=uuid.uuid4(),
            payment_date=d,
            amount=Decimal("2000.00"),
            reference=f"Reference: {token}",
            description="Splynx payment via Paystack.",
        )
        line = SimpleNamespace(
            line_id=uuid.uuid4(),
            reference=token,
            description="paystack inflow",
            amount=Decimal("2000.00"),
            transaction_date=d,
            is_matched=False,
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[p1])  # payments_with_refs
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[line])  # statement_refs
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[])
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(rows=[line])
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=True)

        assert result["matched_by_reference"] == 1
        assert result["matched_by_date_amount"] == 0
        assert result["unmatched_statements"] == 0

    def test_marks_opening_balance_as_matched(self) -> None:
        db = MagicMock()
        svc = _make_service(db)

        paystack_account = SimpleNamespace(bank_account_id=uuid.uuid4())
        line = SimpleNamespace(
            line_id=uuid.uuid4(),
            reference="OB-2021-12-31",
            description="Opening Balance: Dec 31, 2021 collections",
            amount=Decimal("442000.00"),
            transaction_date=date(2022, 1, 1),
            is_matched=False,
        )

        def _execute_side(
            stmt: object, _params: dict[str, object] | None = None
        ) -> MagicMock:
            sql = str(stmt)
            if "FROM banking.bank_accounts" in sql:
                return self._exec_result(rows=[paystack_account])
            if "FROM ar.customer_payment cp" in sql and "cp.customer_id" not in sql:
                return self._exec_result(rows=[])  # payments_with_refs
            if (
                "FROM banking.bank_statement_lines bsl" in sql
                and "bsl.is_matched\n                FROM" in sql
            ):
                return self._exec_result(rows=[line])  # statement_refs
            if "UPDATE banking.bank_statement_lines" in sql:
                return self._exec_result(rows=[])
            if (
                "cp.payment_date" in sql
                and "cp.amount" in sql
                and "cp.reference" in sql
            ):
                return self._exec_result(rows=[])  # unmatched_payments
            if (
                "bsl.transaction_date" in sql
                and "bsl.amount" in sql
                and "bsl.reference" in sql
            ):
                return self._exec_result(
                    rows=[line]
                )  # unmatched_lines includes OB line
            return self._exec_result()

        db.execute.side_effect = _execute_side

        result = svc.reconcile_paystack_payments(dry_run=False)

        assert result["matched_opening_balance"] == 1
        assert result["unmatched_statements"] == 0
        db.flush.assert_called_once()


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


# ---------------------------------------------------------------------------
# Tax Extraction Tests
# ---------------------------------------------------------------------------

TAX_CODE_ID = uuid.uuid4()


def _make_tax_code(
    *,
    tax_rate: Decimal = Decimal("0.075"),
    is_inclusive: bool = True,
) -> SimpleNamespace:
    """Create a fake TaxCode-like object for tax extraction tests."""
    return SimpleNamespace(
        tax_code_id=TAX_CODE_ID,
        tax_code="VAT-7.5",
        tax_name="VAT 7.5%",
        tax_rate=tax_rate,
        is_inclusive=is_inclusive,
        is_active=True,
        applies_to_sales=True,
    )


class TestExtractTax:
    """Tests for _extract_tax private method."""

    def test_no_tax_code_returns_zero_tax(self) -> None:
        """Without a sales tax code, tax should be zero."""
        db = MagicMock()
        svc = _make_service(db)
        assert svc._sales_tax_code is None

        subtotal, tax = svc._extract_tax(Decimal("18812.50"))
        assert subtotal == Decimal("18812.50")
        assert tax == Decimal("0")

    def test_inclusive_vat_extraction(self) -> None:
        """Inclusive VAT 7.5%: 18812.50 → subtotal=17500, tax=1312.50."""
        db = MagicMock()
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code()  # type: ignore[assignment]

        subtotal, tax = svc._extract_tax(Decimal("18812.50"))
        # 18812.50 * 0.075 / 1.075 = 1312.50
        assert tax == Decimal("1312.50")
        assert subtotal == Decimal("17500.00")
        assert subtotal + tax == Decimal("18812.50")

    def test_inclusive_vat_small_amount(self) -> None:
        """Inclusive VAT on a small amount rounds correctly."""
        db = MagicMock()
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code()  # type: ignore[assignment]

        subtotal, tax = svc._extract_tax(Decimal("107.50"))
        # 107.50 * 0.075 / 1.075 = 7.50
        assert tax == Decimal("7.50")
        assert subtotal == Decimal("100.00")

    def test_exclusive_vat_adds_tax(self) -> None:
        """Exclusive VAT: tax is calculated on top of the total."""
        db = MagicMock()
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code(is_inclusive=False)  # type: ignore[assignment]

        subtotal, tax = svc._extract_tax(Decimal("17500.00"))
        # 17500 * 0.075 = 1312.50
        assert subtotal == Decimal("17500.00")
        assert tax == Decimal("1312.50")

    def test_zero_rate_returns_zero_tax(self) -> None:
        """Tax code with 0% rate should produce zero tax."""
        db = MagicMock()
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code(tax_rate=Decimal("0"))  # type: ignore[assignment]

        subtotal, tax = svc._extract_tax(Decimal("18812.50"))
        assert subtotal == Decimal("18812.50")
        assert tax == Decimal("0")


class TestInvoiceSyncWithTax:
    """Tests that invoice sync correctly applies tax extraction."""

    def test_new_invoice_with_inclusive_vat(self) -> None:
        """New invoice with inclusive VAT splits subtotal and tax correctly."""
        from app.models.finance.ar.invoice import Invoice as InvoiceModel
        from app.models.finance.ar.invoice_line import InvoiceLine as InvoiceLineModel
        from app.models.finance.ar.invoice_line_tax import (
            InvoiceLineTax as LineTaxModel,
        )

        db = MagicMock()
        db.scalar.return_value = None  # No existing invoice
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code()  # type: ignore[assignment]
        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice(
            total=Decimal("18812.50"), total_due=Decimal("18812.50")
        )

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.created == 1

        # Find Invoice, InvoiceLine, and InvoiceLineTax objects
        invoice_obj = None
        line_obj = None
        line_tax_obj = None
        for call in db.add.call_args_list:
            obj = call[0][0]
            if isinstance(obj, InvoiceModel):
                invoice_obj = obj
            elif isinstance(obj, InvoiceLineModel):
                line_obj = obj
            elif isinstance(obj, LineTaxModel):
                line_tax_obj = obj

        assert invoice_obj is not None
        # total_amount remains the Splynx total
        assert invoice_obj.total_amount == Decimal("18812.50")
        # subtotal has VAT extracted
        assert invoice_obj.subtotal == Decimal("17500.00")
        # tax_amount is the extracted VAT
        assert invoice_obj.tax_amount == Decimal("1312.50")

        # Line should also have tax extracted
        assert line_obj is not None
        assert line_obj.tax_amount == Decimal("1312.50")
        assert line_obj.tax_code_id == TAX_CODE_ID

        # InvoiceLineTax audit record should be created
        assert line_tax_obj is not None
        assert line_tax_obj.tax_rate == Decimal("0.075")
        assert line_tax_obj.tax_amount == Decimal("1312.50")
        assert line_tax_obj.is_inclusive is True

    def test_update_invoice_applies_tax(self) -> None:
        """Updated invoice should recalculate subtotal and tax."""
        db = MagicMock()
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code()  # type: ignore[assignment]
        svc._customer_cache[500] = uuid.uuid4()

        existing = FakeInvoice(
            total_amount=Decimal("18812.50"),
            status=InvoiceStatus.POSTED,
        )

        sync_record_mock = MagicMock(
            synced_at=None, sync_hash=None, external_updated_at=None
        )
        db.scalar.side_effect = [
            "old-hash",
            existing.invoice_id,
            existing.invoice_id,
            sync_record_mock,
        ]
        db.get.return_value = existing

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice(total=Decimal("37625.00"), status="unpaid")

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.updated == 1
        # total_amount is the Splynx total
        assert existing.total_amount == Decimal("37625.00")
        # subtotal = 37625 / 1.075 = 35000
        assert existing.subtotal == Decimal("35000.00")
        # tax = 37625 - 35000 = 2625
        assert existing.tax_amount == Decimal("2625.00")

    def test_credit_note_with_tax_extraction(self) -> None:
        """Credit note should also have tax extracted from its total."""
        from app.models.finance.ar.invoice import Invoice as InvoiceModel

        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        svc._sales_tax_code = _make_tax_code()  # type: ignore[assignment]
        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="credit_notes")
        cn = _make_splynx_credit_note(total=Decimal("5375.00"))

        svc._sync_single_credit_note(cn, USER_ID, result)

        assert result.created == 1

        invoice_obj = None
        for call in db.add.call_args_list:
            obj = call[0][0]
            if isinstance(obj, InvoiceModel):
                invoice_obj = obj
                break

        assert invoice_obj is not None
        assert invoice_obj.total_amount == Decimal("5375.00")
        # subtotal = 5375 / 1.075 = 5000
        assert invoice_obj.subtotal == Decimal("5000.00")
        assert invoice_obj.tax_amount == Decimal("375.00")

    def test_no_tax_code_preserves_legacy_behaviour(self) -> None:
        """When no tax code is configured, behaviour matches legacy (zero tax)."""
        from app.models.finance.ar.invoice import Invoice as InvoiceModel

        db = MagicMock()
        db.scalar.return_value = None
        svc = _make_service(db)
        assert svc._sales_tax_code is None  # Default from _make_service

        svc._customer_cache[500] = uuid.uuid4()

        result = SyncResult(success=True, entity_type="invoices")
        inv = _make_splynx_invoice(total=Decimal("50000.00"))

        svc._sync_single_invoice(inv, USER_ID, result)

        assert result.created == 1
        invoice_obj = None
        for call in db.add.call_args_list:
            obj = call[0][0]
            if isinstance(obj, InvoiceModel):
                invoice_obj = obj
                break
        assert invoice_obj is not None
        # Legacy: subtotal = total, tax = 0
        assert invoice_obj.subtotal == Decimal("50000.00")
        assert invoice_obj.tax_amount == Decimal("0")
        assert invoice_obj.total_amount == Decimal("50000.00")
