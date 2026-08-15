"""Golden money-path characterization pins (platform adoption, Phase 1).

Pins the DB-row outcomes of three critical money paths so later refactors —
and eventually kernel adoption — cannot drift them silently:

1. Posting-adapter idempotency: the deterministic idempotency-key format,
   the posting_batch + posted_ledger_line rows a first post writes, and the
   exact no-op behavior of a replay with the same key.
2. AR customer-payment settlement: the journal + posted_ledger_line rows
   ``post_payment`` writes — bank-debit / AR-control-credit pairing,
   functional amounts under a non-1 exchange rate, and the
   source/correlation provenance columns.
3. dotmac_sub reverse-and-repost: the reversal journal + swapped ledger
   lines written when a posted Sub invoice's accounting changes
   (``InvoiceSyncMixin._reverse_posted_invoice_gl``), the invoice's cleared
   GL pointers, and the repost — the posting service's idempotent-replay
   match is reversal-aware, so the repost genuinely posts: new journal
   POSTED under a fresh batch that takes over the deterministic key (the
   reversed original batch's key is retired with a ``:superseded:`` suffix),
   and the document's GL nets to the invoice totals again.

Deliberately complements — never duplicates — the existing mock-based
service suites (tests/finance/test_gl_ledger_posting_service.py,
tests/ifrs/gl/test_reversal_service.py, tests/ifrs/ar/, and
tests/services/test_dotmac_sub_*): those pin decision logic against mocked
sessions; this module pins the ROW SHAPES the real services write, on a
real (SQLite) engine, using the throwaway-engine pattern established by
tests/services/test_dotmac_sub_payment_idempotency.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.domain_settings import DomainSetting, DomainSettingHistory
from app.models.feature_flag import FeatureFlagRegistry
from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.core_config.numbering_sequence import NumberingSequence
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_balance import AccountBalance
from app.models.finance.gl.balance_refresh_queue import BalanceRefreshQueue
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch
from app.models.finance.platform.event_outbox import EventOutbox
from app.models.finance.tax.tax_code import TaxCode
from app.services.dotmac_sub.sync._invoices import InvoiceSyncMixin
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.gl.ledger_posting import (
    LedgerPostingService,
    PostingRequest,
)
from app.services.finance.posting.base import BasePostingAdapter
from app.services.finance.posting.idempotency import PostingIdempotencyService

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

# Every table the three flows touch. ``schema_translate_map`` folds the
# Postgres schemas into SQLite's single namespace; the shared conftest has
# already patched pg UUID/JSONB into SQLite-safe types at import time.
_TABLES = [
    Account.__table__,
    FiscalYear.__table__,
    FiscalPeriod.__table__,
    JournalEntry.__table__,
    JournalEntryLine.__table__,
    PostingBatch.__table__,
    PostedLedgerLine.__table__,
    AccountBalance.__table__,
    BalanceRefreshQueue.__table__,
    NumberingSequence.__table__,
    EventOutbox.__table__,
    TaxCode.__table__,
    Customer.__table__,
    CustomerPayment.__table__,
    Invoice.__table__,
    InvoiceLine.__table__,
    InvoiceLineTax.__table__,
    PaymentAllocation.__table__,
    FeatureFlagRegistry.__table__,
    DomainSetting.__table__,
    DomainSettingHistory.__table__,
]

_SCHEMAS = {"gl", "ar", "tax", "core_config", "platform", "audit", "banking"}

_TODAY = date(2026, 7, 15)


class _FrozenToday(date):
    """`date` with `today()` pinned to the scenario date.

    A subclass rather than a Mock so the patched modules keep constructing real
    dates (`date(...)`) and every `isinstance(x, date)` check still holds — the
    only altered behaviour is what "today" means.
    """

    @classmethod
    def today(cls) -> date:
        return _TODAY


_USER = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _strip_server_defaults(tables) -> None:
    """Make Postgres server defaults SQLite-safe — mirrors the shared
    harness. ``gen_random_uuid()`` defaults are dropped (Python-side
    defaults supply the PKs); ``now()`` timestamps become
    ``CURRENT_TIMESTAMP`` so services that rely on them (e.g. the
    numbering-sequence auto-init) still insert cleanly."""
    from sqlalchemy import DefaultClause
    from sqlalchemy import text as sa_text

    for table in tables:
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
            elif "now()" in default_text:
                column.server_default = DefaultClause(sa_text("CURRENT_TIMESTAMP"))


@dataclass
class _World:
    """Seeded golden world: one org, GL accounts, an open period, a customer."""

    db: Session
    org_id: uuid.UUID
    bank: Account = field(init=False)
    ar_control: Account = field(init=False)
    revenue: Account = field(init=False)
    customer: Customer = field(init=False)


class _SubSyncHarness(InvoiceSyncMixin):
    """Minimal concrete sync host — mirrors the WHT lifecycle test harness."""

    def __init__(self, db: Session, organization_id: uuid.UUID) -> None:
        self.db = db
        self.organization_id = organization_id


_LEAKABLE_SESSION_LISTENERS = (
    ("app.db.org_listener", "do_orm_execute", "_add_org_filter"),
    ("app.services.audit_listener", "before_flush", "_on_before_flush"),
    ("app.services.audit_listener", "after_flush", "_on_after_flush"),
    ("app.services.audit.field_tracker", "before_flush", "_on_before_flush"),
)


def _detach_global_session_listeners():
    """Strip app-global SQLAlchemy Session listeners for this fixture's life.

    Building the real app (some earlier test does) registers ``do_orm_execute``
    (org filter) and ``before_flush``/``after_flush`` (auto-audit) hooks on the
    global ``Session`` class. This world flushes real rows on an unprimed SQLite
    session during SETUP; a leaked org filter raises MissingOrgContextError and
    a leaked audit hook fails mid-flush, detaching the journal
    ("not persistent within this Session"). conftest strips these AFTER each
    test — too late for a fixture that works during setup — so strip them here,
    before we flush, and restore what was present on teardown. Order-independent.
    """
    import importlib

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    restored = []
    for modname, evt, fname in _LEAKABLE_SESSION_LISTENERS:
        try:
            fn = getattr(importlib.import_module(modname), fname, None)
        except Exception:
            fn = None
        if fn is not None and event.contains(Session, evt, fn):
            event.remove(Session, evt, fn)
            restored.append((evt, fn))

    def _restore():
        for evt, fn in restored:
            if not event.contains(Session, evt, fn):
                event.listen(Session, evt, fn)

    return _restore


@pytest.fixture()
def world():
    _restore_listeners = _detach_global_session_listeners()
    # The posting path calls fire_audit_event -> AuditLogService.log_change,
    # which writes to audit.audit_log on THIS session. That table has a
    # Postgres ARRAY column SQLite cannot create, and a failed write aborts
    # the SQLite transaction, stranding the journal INSERT that follows
    # ('not persistent within this Session') — order-dependent because
    # whether the write is attempted turns on global audit state an earlier
    # test may seed. The audit row is fire-and-forget and orthogonal to the
    # GL rows these tests pin, so stub the single DB-writing method for the
    # world's lifetime.
    from unittest.mock import patch as _patch

    _audit_stub = _patch(
        "app.services.finance.platform.audit_log.AuditLogService.log_change",
        return_value=None,
    )
    _audit_stub.start()

    # Freeze the sync module's clock to the scenario's date.
    #
    # The world seeds exactly ONE fiscal period (2026-07), while
    # `_reverse_posted_invoice_gl` correctly reversal-dates with `date.today()`.
    # So from 2026-08-01 onward the reversal asks for a period that this world
    # never created and `ReversalService` refuses it — the tests began failing
    # on a calendar rollover, not on a code change, which is why a historically
    # green `main` was stale rather than correct.
    #
    # Freezing here (rather than seeding an August period) is the fix that does
    # not expire: adding the next month would simply move the failure to the
    # next rollover. Production behaviour is untouched — only the test's notion
    # of "today" is pinned, matching every other date in this world.
    # Only `_invoices` is patched: it is the module holding
    # `_reverse_posted_invoice_gl`, and it binds `date` at module scope.
    # `_credit_notes` imports `date` INSIDE a function, so it has no module
    # attribute to patch — and its `today()` feeds a credit-note date, not a
    # reversal date, so it is not on the path these tests pin. Left alone
    # deliberately rather than reached for with a broader `datetime` patch.
    _clock_stubs = [
        _patch("app.services.dotmac_sub.sync._invoices.date", _FrozenToday),
    ]
    for _stub in _clock_stubs:
        _stub.start()

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={
            "schema_translate_map": dict.fromkeys(_SCHEMAS, None),
        },
    )
    _strip_server_defaults(_TABLES)
    for table in _TABLES:
        table.create(engine, checkfirst=True)

    db = sessionmaker(bind=engine, autoflush=False)()
    org_id = uuid.uuid4()
    now = datetime.now(UTC)

    w = _World(db=db, org_id=org_id)

    def _account(code: str, name: str, normal: NormalBalance) -> Account:
        account = Account(
            organization_id=org_id,
            category_id=uuid.uuid4(),
            account_code=code,
            account_name=name,
            account_type=AccountType.POSTING,
            normal_balance=normal,
            is_active=True,
            is_posting_allowed=True,
            created_at=now,
        )
        db.add(account)
        return account

    w.bank = _account("1000", "Bank", NormalBalance.DEBIT)
    w.ar_control = _account("1100", "AR Control", NormalBalance.DEBIT)
    w.revenue = _account("4000", "Service Revenue", NormalBalance.CREDIT)

    year = FiscalYear(
        organization_id=org_id,
        year_code="FY2026",
        year_name="FY 2026",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        created_at=now,
    )
    db.add(year)
    db.flush()
    db.add(
        FiscalPeriod(
            organization_id=org_id,
            fiscal_year_id=year.fiscal_year_id,
            period_number=7,
            period_name="2026",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
            created_at=now,
        )
    )

    # NOTE: this world deliberately seeds ONE period. An earlier fix additionally
    # seeded a period covering the real current date, because the reversal path
    # posts at `date.today()`; freezing that clock (above) addresses the same
    # failure at its source, so the extra period is no longer reached — and a
    # world whose SHAPE varied with the wall clock (one period in July, two
    # otherwise, plus a fiscal year across a year boundary) is precisely what a
    # golden-pin fixture exists to avoid.

    w.customer = Customer(
        organization_id=org_id,
        customer_code="CUS-GOLD",
        customer_type=CustomerType.COMPANY,
        legal_name="Golden Pin Ltd",
        ar_control_account_id=None,  # set after flush below
        created_at=now,
    )
    db.flush()
    w.customer.ar_control_account_id = w.ar_control.account_id
    db.add(w.customer)
    db.flush()

    try:
        yield w
    finally:
        db.close()
        engine.dispose()
        for _stub in reversed(_clock_stubs):
            _stub.stop()
        _audit_stub.stop()
        _restore_listeners()


def _posted_lines(db: Session, journal_id: uuid.UUID) -> list[PostedLedgerLine]:
    return list(
        db.scalars(
            select(PostedLedgerLine)
            .where(PostedLedgerLine.journal_entry_id == journal_id)
            .order_by(PostedLedgerLine.debit_amount.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# 1. Posting-adapter idempotency
# ---------------------------------------------------------------------------


def test_posting_adapter_idempotency_key_and_replay_rows(world: _World) -> None:
    """First post writes exactly one batch + two ledger lines; a replay with
    the same idempotency key is a row-level no-op that returns the same batch."""
    db, org = world.db, world.org_id
    doc_id = uuid.uuid4()

    key = BasePostingAdapter.make_idempotency_key(org, "AR", doc_id, action="post")
    # The key format is a contract: batches created under it must survive
    # refactors, or replays will double-post.
    assert key == f"{org}:AR:{doc_id}:post:v1"

    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=_TODAY,
        posting_date=_TODAY,
        description="Golden idempotency pin",
        reference="GOLD-1",
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        lines=[
            JournalLineInput(
                account_id=world.bank.account_id,
                debit_amount=Decimal("150.00"),
                credit_amount=Decimal("0"),
                debit_amount_functional=Decimal("150.00"),
                credit_amount_functional=Decimal("0"),
                description="Cash in",
            ),
            JournalLineInput(
                account_id=world.ar_control.account_id,
                debit_amount=Decimal("0"),
                credit_amount=Decimal("150.00"),
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=Decimal("150.00"),
                description="Settle receivable",
            ),
        ],
        source_module="AR",
        source_document_type="CUSTOMER_PAYMENT",
        source_document_id=doc_id,
        correlation_id="corr-golden-1",
    )

    journal, result = BasePostingAdapter.create_approve_and_post_journal(
        db,
        org,
        journal_input,
        _USER,
        posting_date=_TODAY,
        idempotency_key=key,
        source_module="AR",
        correlation_id="corr-golden-1",
        success_message="posted",
    )
    assert result.success is True
    assert journal is not None

    # Journal row shape.
    assert journal.status == JournalStatus.POSTED
    assert journal.source_module == "AR"
    assert journal.source_document_type == "CUSTOMER_PAYMENT"
    assert journal.source_document_id == doc_id
    assert journal.total_debit == Decimal("150.00")
    assert journal.total_credit == Decimal("150.00")
    assert journal.posting_batch_id == result.posting_batch_id

    # Batch row shape.
    batch = db.scalar(select(PostingBatch).where(PostingBatch.idempotency_key == key))
    assert batch is not None
    assert batch.status == BatchStatus.POSTED
    assert batch.posted_entries == 2
    assert batch.source_module == "AR"
    assert batch.correlation_id == "corr-golden-1"

    # Ledger rows: exactly two, provenance columns filled, traceable to the
    # journal lines that produced them.
    lines = _posted_lines(db, journal.journal_entry_id)
    assert [(line.debit_amount, line.credit_amount) for line in lines] == [
        (Decimal("150.00"), Decimal("0")),
        (Decimal("0"), Decimal("150.00")),
    ]
    journal_line_ids = set(
        db.scalars(
            select(JournalEntryLine.line_id).where(
                JournalEntryLine.journal_entry_id == journal.journal_entry_id
            )
        ).all()
    )
    for line in lines:
        assert line.source_module == "AR"
        assert line.source_document_type == "CUSTOMER_PAYMENT"
        assert line.source_document_id == doc_id
        assert line.correlation_id == "corr-golden-1"
        assert line.posting_batch_id == batch.batch_id
        assert line.journal_line_id in journal_line_ids
    assert {line.account_code for line in lines} == {"1000", "1100"}

    # The row-level guard callers consult before posting again.
    assert (
        PostingIdempotencyService.source_journal_exists(
            db,
            source_module="AR",
            source_document_type="CUSTOMER_PAYMENT",
            source_document_id=doc_id,
        )
        is True
    )

    # Replay with the same idempotency key: same batch, no new rows.
    replay = LedgerPostingService.post_journal_entry(
        db,
        PostingRequest(
            organization_id=org,
            journal_entry_id=journal.journal_entry_id,
            posting_date=_TODAY,
            idempotency_key=key,
            source_module="AR",
            correlation_id="corr-golden-1",
            posted_by_user_id=_USER,
        ),
    )
    assert replay.success is True
    assert replay.batch_id == batch.batch_id
    assert replay.message == "Already posted (idempotent replay)"
    assert len(_posted_lines(db, journal.journal_entry_id)) == 2
    assert (
        db.scalar(
            select(PostingBatch.batch_id).where(
                PostingBatch.idempotency_key == key,
                PostingBatch.batch_id != batch.batch_id,
            )
        )
        is None
    )


# ---------------------------------------------------------------------------
# 2. AR customer-payment settlement rows
# ---------------------------------------------------------------------------


def test_ar_payment_settlement_ledger_rows(world: _World) -> None:
    """post_payment writes a bank-debit / AR-control-credit pair with exact
    functional amounts and CUSTOMER_PAYMENT provenance; a second call no-ops."""
    from app.services.finance.ar.posting.payment import post_payment

    db, org = world.db, world.org_id
    payment = CustomerPayment(
        organization_id=org,
        customer_id=world.customer.customer_id,
        payment_number="PMT-GOLD-1",
        payment_date=_TODAY,
        payment_method=PaymentMethod.BANK_TRANSFER,
        currency_code="USD",
        exchange_rate=Decimal("1.50"),
        gross_amount=Decimal("250.00"),
        amount=Decimal("250.00"),
        functional_currency_amount=Decimal("375.00"),
        status=PaymentStatus.APPROVED,
        reference="SUB-PMT-1",
        correlation_id="sub-pay-golden-1",
        bank_account_id=world.bank.account_id,  # direct GL-account mapping path
        created_by_user_id=_USER,
        created_at=datetime.now(UTC),
    )
    db.add(payment)
    db.flush()

    result = post_payment(
        db,
        organization_id=org,
        payment_id=payment.payment_id,
        posting_date=_TODAY,
        posted_by_user_id=_USER,
    )
    assert result.success is True
    assert result.journal_entry_id is not None

    journal = db.get(JournalEntry, result.journal_entry_id)
    assert journal is not None
    assert journal.status == JournalStatus.POSTED
    assert journal.source_module == "AR"
    assert journal.source_document_type == "CUSTOMER_PAYMENT"
    assert journal.source_document_id == payment.payment_id
    assert journal.correlation_id == "sub-pay-golden-1"
    assert journal.exchange_rate == Decimal("1.50")
    # Transaction currency on the journal header...
    assert journal.total_debit == Decimal("250.00")
    assert journal.total_credit == Decimal("250.00")
    # ...functional currency on the header functional totals.
    assert journal.total_debit_functional == Decimal("375.00")
    assert journal.total_credit_functional == Decimal("375.00")

    # Ledger rows carry FUNCTIONAL amounts — this is the balance that feeds
    # reporting, so the conversion must be pinned.
    lines = _posted_lines(db, journal.journal_entry_id)
    assert len(lines) == 2
    bank_line, control_line = lines
    assert bank_line.account_id == world.bank.account_id
    assert bank_line.account_code == "1000"
    assert bank_line.debit_amount == Decimal("375.00")
    assert bank_line.credit_amount == Decimal("0")
    # Control-account linkage: the credit lands on the CUSTOMER's configured
    # AR control account.
    assert control_line.account_id == world.customer.ar_control_account_id
    assert control_line.account_code == "1100"
    assert control_line.debit_amount == Decimal("0")
    assert control_line.credit_amount == Decimal("375.00")
    for line in lines:
        assert line.source_module == "AR"
        assert line.source_document_type == "CUSTOMER_PAYMENT"
        assert line.source_document_id == payment.payment_id
        assert line.correlation_id == "sub-pay-golden-1"

    # Second call is the adapter-level idempotency no-op: same journal id,
    # no new ledger rows anywhere for this payment.
    again = post_payment(
        db,
        organization_id=org,
        payment_id=payment.payment_id,
        posting_date=_TODAY,
        posted_by_user_id=_USER,
    )
    assert again.success is True
    assert again.journal_entry_id == journal.journal_entry_id
    all_rows = db.scalars(
        select(PostedLedgerLine).where(
            PostedLedgerLine.source_document_id == payment.payment_id
        )
    ).all()
    assert len(all_rows) == 2


# ---------------------------------------------------------------------------
# 3. dotmac_sub reverse-and-repost
# ---------------------------------------------------------------------------


def test_sub_sync_reverse_and_repost_row_pairing(world: _World) -> None:
    """When a posted Sub invoice's accounting changes, the sync path reverses
    the original journal (swapped-line REVERSAL journal, original REVERSED,
    invoice pointers cleared); the subsequent repost then genuinely posts —
    the posting service's idempotent-replay match is reversal-aware, so the
    unchanged deterministic key is actionable again and the fresh journal
    reaches the ledger under a new batch."""
    db, org = world.db, world.org_id
    now = datetime.now(UTC)
    invoice = Invoice(
        organization_id=org,
        customer_id=world.customer.customer_id,
        invoice_number="SUB-INV-GOLD-1",
        invoice_type=InvoiceType.STANDARD,
        invoice_date=_TODAY,
        due_date=_TODAY,
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("100.00"),
        functional_currency_amount=Decimal("100.00"),
        status=InvoiceStatus.POSTED,
        ar_control_account_id=world.ar_control.account_id,
        source_document_type="dotmac_sub_invoice",
        correlation_id="sub-inv-golden-1",
        created_by_user_id=_USER,
        created_at=now,
    )
    db.add(invoice)
    db.flush()
    db.add(
        InvoiceLine(
            invoice_id=invoice.invoice_id,
            line_number=1,
            description="Internet service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("100.00"),
            tax_amount=Decimal("0"),
            revenue_account_id=world.revenue.account_id,
            created_at=now,
        )
    )
    db.flush()

    harness = _SubSyncHarness(db, org)

    # -- Original post (the sync repair path used for Sub documents). -------
    harness._ensure_synced_invoice_posted(invoice, _USER)
    original_journal_id = invoice.journal_entry_id
    assert original_journal_id is not None
    original = db.get(JournalEntry, original_journal_id)
    assert original is not None
    assert original.status == JournalStatus.POSTED
    assert original.source_module == "AR"
    assert original.source_document_type == "INVOICE"
    assert original.source_document_id == invoice.invoice_id
    original_lines = _posted_lines(db, original_journal_id)
    assert [
        (line.account_code, line.debit_amount, line.credit_amount)
        for line in original_lines
    ] == [
        ("1100", Decimal("100.00"), Decimal("0")),
        ("4000", Decimal("0"), Decimal("100.00")),
    ]

    # -- Reverse (what the sync does when posted accounting changed). -------
    assert (
        harness._reverse_posted_invoice_gl(
            invoice, _USER, reason="sub resync: accounting changed"
        )
        is True
    )

    db.refresh(original)
    assert original.status == JournalStatus.REVERSED
    reversal = db.get(JournalEntry, original.reversal_journal_id)
    assert reversal is not None
    assert reversal.journal_type == JournalType.REVERSAL
    assert reversal.is_reversal is True
    # Pins the frozen clock, not just the reversal: without this the world
    # could drift back to a real `today()` and the suite would once again pass
    # or fail depending on the calendar rather than the code.
    assert reversal.entry_date == _TODAY
    assert reversal.status == JournalStatus.POSTED
    assert reversal.reversed_journal_id == original_journal_id
    # Reversal keeps the ORIGINAL document's provenance.
    assert reversal.source_module == "AR"
    assert reversal.source_document_type == "INVOICE"
    assert reversal.source_document_id == invoice.invoice_id
    # Totals swap sides.
    assert reversal.total_debit == original.total_credit
    assert reversal.total_credit == original.total_debit

    # Reversal ledger rows are the exact debit/credit mirror of the original.
    reversal_lines = _posted_lines(db, reversal.journal_entry_id)
    assert [
        (line.account_code, line.debit_amount, line.credit_amount)
        for line in reversal_lines
    ] == [
        ("4000", Decimal("100.00"), Decimal("0")),
        ("1100", Decimal("0"), Decimal("100.00")),
    ]

    # The reversal batch uses the sync's deterministic resync key.
    resync_key = f"{org}:AR:INV:{invoice.invoice_id}:sub-resync:{original_journal_id}"
    assert (
        db.scalar(
            select(PostingBatch).where(PostingBatch.idempotency_key == resync_key)
        )
        is not None
    )

    # Invoice GL pointers are cleared so the repost can run.
    assert invoice.journal_entry_id is None
    assert invoice.posting_batch_id is None
    assert invoice.posting_status == "NOT_POSTED"
    # And the idempotency guard agrees the document is repostable.
    assert (
        PostingIdempotencyService.source_journal_exists(
            db,
            source_module="AR",
            source_document_type="INVOICE",
            source_document_id=invoice.invoice_id,
            exclude_reversal_journals=True,
        )
        is False
    )

    # -- Repost. -------------------------------------------------------------
    # The repost goes back through ensure_gl_posted, whose idempotency key
    # ("ensure-gl-inv-<invoice_id>") is unchanged from the ORIGINAL post.
    # LedgerPostingService recognizes that the original batch's journal has
    # been REVERSED, retires the dead batch's unique key with a
    # ":superseded:<batch_id>" suffix, and posts the fresh journal under a
    # NEW batch that takes over the deterministic key — AR and revenue are
    # restated, not silently understated.
    ensure_key = f"ensure-gl-inv-{invoice.invoice_id}"
    original_batch_id = original.posting_batch_id
    assert original_batch_id is not None
    harness._ensure_synced_invoice_posted(invoice, _USER)
    repost_journal_id = invoice.journal_entry_id
    assert repost_journal_id is not None
    assert repost_journal_id != original_journal_id
    repost = db.get(JournalEntry, repost_journal_id)
    assert repost is not None
    assert repost.source_document_type == "INVOICE"
    assert repost.source_document_id == invoice.invoice_id
    # The repost journal genuinely reaches the ledger.
    assert repost.status == JournalStatus.POSTED
    assert repost.posting_batch_id is not None
    assert repost.posting_batch_id != original_batch_id
    # Invoice pointers land on the NEW journal and NEW batch.
    assert invoice.posting_status == "POSTED"
    assert invoice.posting_batch_id == repost.posting_batch_id

    # The deterministic key now belongs to the repost batch; the reversed
    # original batch keeps its rows under the retired ":superseded:" key.
    repost_batch = db.scalar(
        select(PostingBatch).where(PostingBatch.idempotency_key == ensure_key)
    )
    assert repost_batch is not None
    assert repost_batch.batch_id == repost.posting_batch_id
    assert repost_batch.status == BatchStatus.POSTED
    superseded = db.get(PostingBatch, original_batch_id)
    assert superseded is not None
    assert superseded.idempotency_key == f"{ensure_key}:superseded:{original_batch_id}"

    # Repost ledger rows mirror the original post exactly, on the new batch.
    repost_lines = _posted_lines(db, repost_journal_id)
    assert [
        (line.account_code, line.debit_amount, line.credit_amount)
        for line in repost_lines
    ] == [
        ("1100", Decimal("100.00"), Decimal("0")),
        ("4000", Decimal("0"), Decimal("100.00")),
    ]
    for line in repost_lines:
        assert line.posting_batch_id == repost_batch.batch_id

    # The idempotency guard once again sees an active (non-reversal) journal.
    assert (
        PostingIdempotencyService.source_journal_exists(
            db,
            source_module="AR",
            source_document_type="INVOICE",
            source_document_id=invoice.invoice_id,
            exclude_reversal_journals=True,
        )
        is True
    )

    # Full ledger trail: original + reversal + repost = 6 lines, and the
    # document's GL nets back to the invoice totals per account.
    trail = db.scalars(
        select(PostedLedgerLine).where(
            PostedLedgerLine.source_document_id == invoice.invoice_id
        )
    ).all()
    assert len(trail) == 6
    net_by_account: dict[str, Decimal] = {}
    for line in trail:
        net_by_account[line.account_code] = net_by_account.get(
            line.account_code, Decimal("0")
        ) + (line.debit_amount - line.credit_amount)
    assert net_by_account == {
        "1100": Decimal("100.00"),  # AR control restated
        "4000": Decimal("-100.00"),  # revenue restated
    }

    # A replay of the SAME key against the now-live repost batch is once
    # again a pure no-op — reversal-awareness did not weaken live-batch
    # idempotency.
    replay = LedgerPostingService.post_journal_entry(
        db,
        PostingRequest(
            organization_id=org,
            journal_entry_id=repost_journal_id,
            posting_date=_TODAY,
            idempotency_key=ensure_key,
            source_module="AR",
            posted_by_user_id=_USER,
        ),
    )
    assert replay.success is True
    assert replay.batch_id == repost_batch.batch_id
    assert replay.message == "Already posted (idempotent replay)"
    assert len(_posted_lines(db, repost_journal_id)) == 2
