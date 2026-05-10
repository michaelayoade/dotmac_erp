"""
FX Revaluation Service.

Period-end revaluation of foreign-currency monetary items (AR open
invoices, AP open invoices, bank account balances) at the closing spot
rate, with auto-reversing journal posting on day 1 of the next period.

See docs/superpowers/specs/2026-05-09-fx-revaluation-design.md for the
contract and accounting rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4 as _new_uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.banking import BankAccount, BankAccountStatus
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
    JournalService,
)
from app.services.finance.gl.reversal import ReversalService
from app.services.finance.platform.fx import FXService

logger = logging.getLogger(__name__)


@dataclass
class FXRevaluationLine:
    """One revaluation observation: a single (control_account, currency)
    pair's delta. The proposed journal is constructed from these — the
    asset/liability side becomes one journal line per FXRevaluationLine,
    while the gain/loss side aggregates across all observations into two
    summary lines.

    ``is_liability`` records the source orientation (asset vs liability)
    so ``_aggregate_per_account_currency`` can re-derive ``is_gain`` from
    the *aggregated* delta. Without this, summing two opposite-sign lines
    against the same control account would keep a stale ``is_gain`` from
    the first line, producing a backwards journal.
    """

    account_id: UUID
    currency_code: str
    closing_rate: Decimal
    book_value_functional: Decimal  # current carrying amount in NGN
    revalued_value_functional: Decimal  # value at closing rate, in NGN
    delta_functional: Decimal  # revalued - book; signed
    is_gain: bool  # True iff delta increases asset / decreases liability
    is_liability: bool = False  # True iff source is a liability (AP); False for AR/cash


@dataclass
class FXRevaluationPreview:
    """Output of FXRevaluationService.preview() — no DB writes."""

    fiscal_period_id: UUID
    period_end_date: date
    next_period_start_date: date | None
    lines: list[FXRevaluationLine] = field(default_factory=list)
    total_gain_functional: Decimal = Decimal("0")
    total_loss_functional: Decimal = Decimal("0")
    rates_used: dict[str, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    prior_run_exists: bool = False
    prior_journal_ids: list[UUID] = field(default_factory=list)


@dataclass
class FXRevaluationResult:
    """Output of FXRevaluationService.post() — journals have been written."""

    success: bool
    period_end_journal_id: UUID | None = None
    reversal_journal_id: UUID | None = None
    reversed_prior_journal_ids: list[UUID] = field(default_factory=list)
    total_gain_functional: Decimal = Decimal("0")
    total_loss_functional: Decimal = Decimal("0")
    message: str = ""
    errors: list[str] = field(default_factory=list)


class FXRevaluationService:
    """Period-end FX revaluation for AR / AP / cash monetary items."""

    SOURCE_MODULE = "FXR"
    POSTABLE_PERIOD_STATUSES = {PeriodStatus.OPEN, PeriodStatus.REOPENED}

    def __init__(self, db: Session) -> None:
        self.db = db

    def _read_fx_account_ids(self, organization_id: UUID) -> tuple[UUID, UUID]:
        """Read fx_gain_account_id and fx_loss_account_id from DomainSetting.

        Queries the org-specific DomainSetting row directly, filtered by
        organization_id. FX gain/loss accounts post real money to the GL,
        so this is security-critical: an unset org-specific row must mean
        "unconfigured" — we DO NOT fall back to a global row, otherwise
        every tenant would silently share the same accounts.

        Raises HTTPException(400) with admin-actionable detail when either
        is unset — refuse to post to a wrong account silently.
        """
        gain_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == SettingDomain.gl,
                DomainSetting.key == "fx_gain_account_id",
                DomainSetting.organization_id == organization_id,
                DomainSetting.is_active.is_(True),
            )
        )
        loss_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == SettingDomain.gl,
                DomainSetting.key == "fx_loss_account_id",
                DomainSetting.organization_id == organization_id,
                DomainSetting.is_active.is_(True),
            )
        )

        gain_raw = gain_setting.value_text if gain_setting is not None else None
        loss_raw = loss_setting.value_text if loss_setting is not None else None

        if not gain_raw:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FX revaluation is not configured: fx_gain_account_id "
                    "is unset. Visit /admin/settings/gl/fx and set the "
                    "Foreign Exchange Gain account."
                ),
            )
        if not loss_raw:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FX revaluation is not configured: fx_loss_account_id "
                    "is unset. Visit /admin/settings/gl/fx and set the "
                    "Foreign Exchange Loss account."
                ),
            )

        return UUID(gain_raw), UUID(loss_raw)

    def _discover_ar_open_invoices(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal, Decimal]]:
        """List AR invoices in non-functional currency with balance_due > 0.

        Returns tuples of:
          (invoice_id, ar_control_account_id, currency_code,
           posting_exchange_rate, balance_due_in_invoice_currency)

        Note on the "as-of period_end_date" semantics: the invoice's
        amount_paid is read at the current point in time, which is
        accurate for typical close workflows where revaluation runs
        within the period. Strict as-of-period-end accuracy for
        invoices that received late allocations would require walking
        the AR/AP allocation table for postings dated <= period_end_date;
        that's a known limitation deferred to a follow-up if real-world
        usage shows tenants running revaluation well after period close.
        """
        functional = app_settings.default_functional_currency_code

        stmt = select(Invoice).where(
            Invoice.organization_id == organization_id,
            Invoice.currency_code != functional,
            Invoice.status.in_(InvoiceStatus.outstanding()),
        )
        invoices = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal, Decimal]] = []
        for inv in invoices:
            balance_due = (inv.total_amount or Decimal("0")) - (
                inv.amount_paid or Decimal("0")
            )
            if balance_due <= 0:
                continue
            if inv.currency_code == functional:
                # Defense in depth — query already filters this
                continue
            result.append(
                (
                    inv.invoice_id,
                    inv.ar_control_account_id,
                    inv.currency_code,
                    inv.exchange_rate or Decimal("1"),
                    balance_due,
                )
            )
        return result

    def _discover_ap_open_invoices(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal, Decimal]]:
        """List AP supplier invoices in non-functional currency with
        balance_due > 0.

        Returns tuples of:
          (invoice_id, ap_control_account_id, currency_code,
           posting_exchange_rate, balance_due_in_invoice_currency)

        Mirrors ``_discover_ar_open_invoices`` against ``SupplierInvoice``
        + ``ap_control_account_id``. The same as-of caveat applies: late
        payment allocations are not back-dated; we read amount_paid as
        of *now*. See the AR docstring for the deferred follow-up.

        Status filter uses ``gl_impacting()`` (POSTED, PARTIALLY_PAID,
        PAID) rather than ``outstanding()`` because APPROVED supplier
        invoices have NOT yet hit the GL — revaluing them would create
        FX journals against an unbooked exposure. The ``balance_due <= 0``
        Python guard below drops PAID invoices, so the effective scope
        is {POSTED, PARTIALLY_PAID} with non-zero balance.
        """
        functional = app_settings.default_functional_currency_code

        stmt = select(SupplierInvoice).where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.currency_code != functional,
            SupplierInvoice.status.in_(SupplierInvoiceStatus.gl_impacting()),
        )
        invoices = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal, Decimal]] = []
        for inv in invoices:
            balance_due = (inv.total_amount or Decimal("0")) - (
                inv.amount_paid or Decimal("0")
            )
            if balance_due <= 0:
                continue
            if inv.currency_code == functional:
                # Defense in depth — query already filters this
                continue
            result.append(
                (
                    inv.invoice_id,
                    inv.ap_control_account_id,
                    inv.currency_code,
                    inv.exchange_rate or Decimal("1"),
                    balance_due,
                )
            )
        return result

    def _discover_bank_balances(
        self,
        organization_id: UUID,
        period_end_date: date,
    ) -> list[tuple[UUID, UUID, str, Decimal | None, Decimal]]:
        """List active foreign-currency bank accounts with non-zero balance.

        Returns tuples of:
          (bank_account_id, gl_account_id, currency_code,
           None,                  # bank balances have no single posting rate
           balance_in_account_currency)

        Balance source: prefer ``last_statement_balance`` if the linked
        statement date covers period_end_date; otherwise compute from
        posted journal lines on ``gl_account_id``.

        Posting rate is ``None`` because there is no per-account posting
        rate — bank balances are translated at the closing rate against
        their currency, with no comparison to a prior posting rate (unlike
        AR/AP invoices which carry an ``exchange_rate`` per document).
        """
        functional = app_settings.default_functional_currency_code

        stmt = select(BankAccount).where(
            BankAccount.organization_id == organization_id,
            BankAccount.currency_code != functional,
            BankAccount.status == BankAccountStatus.active,
        )
        accounts = self.db.scalars(stmt).all()

        result: list[tuple[UUID, UUID, str, Decimal | None, Decimal]] = []
        for acct in accounts:
            if acct.currency_code == functional:
                # Defense in depth — query already filters this
                continue
            balance = self._resolve_bank_balance(acct, period_end_date)
            if balance == 0:
                continue
            result.append(
                (
                    acct.bank_account_id,
                    acct.gl_account_id,
                    acct.currency_code,
                    None,
                    balance,
                )
            )
        return result

    def _resolve_bank_balance(
        self, account: BankAccount, period_end_date: date
    ) -> Decimal:
        """Resolve account balance as-of ``period_end_date``.

        Prefer ``last_statement_balance`` when its date is at or after
        ``period_end_date``; otherwise compute from POSTED GL journal
        lines on the linked ``gl_account_id``.

        KNOWN LIMITATION (Phase 2 fix tracked separately):
        ``last_statement_balance`` is the **foreign-currency** balance per
        the bank statement, while ``_compute_balance_from_journals``
        returns the **functional-currency** book value. The two will
        diverge for accounts with unreconciled items (in-flight
        deposits, uncleared cheques, bank fees not yet booked). When the
        two paths produce values for different units, the FX delta
        absorbs the reconciliation gap as a fictitious gain/loss.

        Tenants should reconcile bank accounts before relying on FX
        revaluation accuracy. A future Phase 2 fix will normalise both
        paths to the same currency basis (likely by always computing
        the foreign-currency balance from a per-account ledger sum, then
        translating once at the closing rate).
        """
        stmt_date = getattr(account, "last_statement_date", None)
        stmt_balance = getattr(account, "last_statement_balance", None)
        if (
            stmt_balance is not None
            and stmt_date is not None
            and stmt_date >= period_end_date
        ):
            return Decimal(str(stmt_balance))
        return self._compute_balance_from_journals(
            account.gl_account_id,
            period_end_date,
            account.organization_id,
        )

    def _compute_balance_from_journals(
        self,
        gl_account_id: UUID,
        as_of_date: date,
        organization_id: UUID,
    ) -> Decimal:
        """Sum (debits - credits) on ``gl_account_id`` through ``as_of_date``,
        across POSTED journals only, scoped to ``organization_id``.

        Returns ``Decimal("0")`` if there are no qualifying postings.

        The plan's original signature omitted ``organization_id``; that is
        a multi-tenant safety bug. ``JournalEntryLine`` has no direct
        organization_id column — it inherits scoping from its parent
        ``JournalEntry``. We therefore filter the JOINed parent on
        ``organization_id`` so a stale or attacker-supplied ``gl_account_id``
        from another tenant cannot leak balances across orgs.
        """
        stmt = (
            select(
                func.coalesce(func.sum(JournalEntryLine.debit_amount), 0)
                - func.coalesce(func.sum(JournalEntryLine.credit_amount), 0)
            )
            .select_from(JournalEntryLine)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntryLine.account_id == gl_account_id,
                JournalEntry.organization_id == organization_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date <= as_of_date,
            )
        )
        result = self.db.scalar(stmt)
        return Decimal(str(result)) if result is not None else Decimal("0")

    def _compute_revaluation_lines(
        self,
        ar_items: list[tuple[UUID, UUID, str, Decimal, Decimal]],
        ap_items: list[tuple[UUID, UUID, str, Decimal, Decimal]],
        cash_items: list[tuple[UUID, UUID, str, Decimal | None, Decimal]],
        rates: dict[str, Decimal],
        organization_id: UUID,
        period_end_date: date,
    ) -> list[FXRevaluationLine]:
        """Compute one ``FXRevaluationLine`` per item (pre-aggregation).

        Items in currencies without a closing rate are skipped silently —
        the caller has already produced a warning for them via
        ``_lookup_closing_rates``.

        Sign rules (asymmetric on purpose):
          * AR (asset, control_account): ``is_gain = delta > 0``.
            A positive delta means the asset translated up at the closing
            rate → unrealised gain.
          * AP (liability, control_account): ``is_gain = delta < 0``.
            A positive delta means the liability translated up → loss.
            The asymmetry is intentional and reflects accounting convention.
          * Cash (asset, GL bank account): ``is_gain = delta > 0``,
            same as AR.

        Plan deviation: the original plan reads ``period_end_date`` from a
        magic instance attribute (``self._period_end_for_compute``) and calls
        ``_compute_balance_from_journals`` with two args. Both are bugs:
        the instance-state hack is the "pass parameters via mutable self"
        anti-pattern, and ``_compute_balance_from_journals`` was tightened in
        Task 7 to require ``organization_id`` for multi-tenant safety. We
        therefore take both as explicit parameters here and forward them.
        """
        out: list[FXRevaluationLine] = []

        # AR: asset side. Delta positive = gain (asset went up).
        for _id, control, currency, posting_rate, balance in ar_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            book = balance * posting_rate
            revalued = balance * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=control,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta > 0,
                    is_liability=False,
                )
            )

        # AP: liability side. Delta positive (liability up) = loss.
        for _id, control, currency, posting_rate, balance in ap_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            book = balance * posting_rate
            revalued = balance * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=control,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta < 0,
                    # asymmetry: liability up = loss
                    is_liability=True,
                )
            )

        # Cash: asset side. Book value is the current ledger balance in the
        # functional currency (computed from posted journals as-of
        # ``period_end_date``, scoped to ``organization_id``). Revalued
        # value is ``balance_in_currency × closing_rate``.
        for _id, gl_account_id, currency, _no_rate, balance_in_ccy in cash_items:
            rate = rates.get(currency)
            if rate is None:
                continue
            book = self._compute_balance_from_journals(
                gl_account_id, period_end_date, organization_id
            )
            revalued = balance_in_ccy * rate
            delta = revalued - book
            out.append(
                FXRevaluationLine(
                    account_id=gl_account_id,
                    currency_code=currency,
                    closing_rate=rate,
                    book_value_functional=book,
                    revalued_value_functional=revalued,
                    delta_functional=delta,
                    is_gain=delta > 0,
                    is_liability=False,
                )
            )

        return out

    def _aggregate_per_account_currency(
        self, lines: list[FXRevaluationLine]
    ) -> list[FXRevaluationLine]:
        """Sum deltas per ``(account_id, currency_code)`` pair.

        First occurrence of each key creates a fresh ``FXRevaluationLine``
        bucket (copied so the caller's input list is not mutated);
        subsequent occurrences mutate that bucket's running totals.
        Zero-net aggregations are dropped — they would produce a no-op
        journal line.

        ``closing_rate`` is taken from the first occurrence: all lines for
        the same ``(account_id, currency)`` come from the same accounting
        source and the same closing rate, so it is consistent across the
        bucket. ``is_gain`` is **re-derived** after summation from the
        aggregated delta + ``is_liability`` orientation — keeping the
        first-line ``is_gain`` would produce a backwards journal whenever
        opposite-sign lines aggregate to a net delta with the opposite
        sign of the first.
        """
        buckets: dict[tuple[UUID, str], FXRevaluationLine] = {}

        for line in lines:
            key = (line.account_id, line.currency_code)
            existing = buckets.get(key)
            if existing is None:
                buckets[key] = FXRevaluationLine(
                    account_id=line.account_id,
                    currency_code=line.currency_code,
                    closing_rate=line.closing_rate,
                    book_value_functional=line.book_value_functional,
                    revalued_value_functional=line.revalued_value_functional,
                    delta_functional=line.delta_functional,
                    is_gain=line.is_gain,
                    is_liability=line.is_liability,
                )
            else:
                existing.book_value_functional += line.book_value_functional
                existing.revalued_value_functional += line.revalued_value_functional
                existing.delta_functional += line.delta_functional

        # Re-derive is_gain from the aggregated delta + source orientation.
        # Asset (is_liability=False): delta > 0 → gain (asset went up).
        # Liability (is_liability=True): delta < 0 → gain (liability went down).
        for bucket in buckets.values():
            if bucket.is_liability:
                bucket.is_gain = bucket.delta_functional < 0
            else:
                bucket.is_gain = bucket.delta_functional > 0

        # Drop zero-net aggregations
        return [line for line in buckets.values() if line.delta_functional != 0]

    def _lookup_closing_rates(
        self,
        organization_id: UUID,
        currencies: set[str],
        period_end_date: date,
    ) -> tuple[dict[str, Decimal], list[str]]:
        """Look up the closing spot rate for each currency at period_end_date.

        Currencies without a recorded rate are omitted from the result and
        produce a human-readable warning. Items in those currencies are
        skipped at the compute step.

        Iteration is over ``sorted(currencies)`` so the warning list and
        any audit log produced from this method are deterministic.

        NOTE — adaptation from plan: the plan assumed
        ``FXService.lookup_spot_rate`` returns ``Decimal | None``. The
        actual ``@staticmethod`` returns a ``dict`` shaped like
        ``{"rate": str | None, "effective_date": str, "source": str, ...}``.
        We unwrap ``result["rate"]`` and treat a missing/``None`` rate as
        "no rate available". The ``Decimal(str(rate))`` round-trip is kept
        intentionally to avoid float-precision loss if ``rate`` is ever a
        ``float`` rather than a stringified ``Decimal``.
        """
        rates: dict[str, Decimal] = {}
        warnings: list[str] = []

        for currency in sorted(currencies):
            result = FXService.lookup_spot_rate(
                self.db, organization_id, currency, period_end_date
            )
            rate = result.get("rate") if isinstance(result, dict) else result
            if rate is None:
                warnings.append(
                    f"No closing rate available for {currency} on "
                    f"{period_end_date}; items in {currency} will be skipped."
                )
                continue
            rates[currency] = Decimal(str(rate))

        return rates, warnings

    def _detect_prior_run(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[UUID]:
        """Return journal_entry_ids of active prior FXR journals for this
        period — excludes REVERSED and VOID statuses (those are settled).
        """
        stmt = select(JournalEntry).where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.source_module == self.SOURCE_MODULE,
            JournalEntry.fiscal_period_id == fiscal_period_id,
            JournalEntry.status.in_(
                {
                    JournalStatus.POSTED,
                    JournalStatus.DRAFT,
                    JournalStatus.SUBMITTED,
                }
            ),
        )
        rows = self.db.scalars(stmt).all()
        return [row.journal_entry_id for row in rows]

    def _resolve_next_period_start(
        self, organization_id: UUID, period_end_date: date
    ) -> date | None:
        """Find the postable period whose ``start_date == period_end_date + 1 day``.

        Returns ``None`` if no such period exists OR the next period exists
        but is not in ``POSTABLE_PERIOD_STATUSES`` (e.g., already closed).
        Filtering on status here keeps ``preview()`` honest: if it shows a
        ``next_period_start_date``, the auto-reversal is actually postable
        there. Otherwise the post-time reversal would 400 from the
        ``ReversalService`` period guard, which the user couldn't predict
        from the preview.
        """
        target = period_end_date + timedelta(days=1)
        stmt = select(FiscalPeriod).where(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.start_date == target,
            FiscalPeriod.status.in_(self.POSTABLE_PERIOD_STATUSES),
        )
        period = self.db.scalar(stmt)
        return target if period else None

    def preview(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> FXRevaluationPreview:
        """Read-only preview of period-end FX revaluation. Performs no
        DB writes.

        Order of operations (intentional — see the design spec):

        1. Resolve the fiscal period (404 if missing or org mismatch — the
           404 on org mismatch is deliberate: a 403 would leak the fact
           that the ID exists in some other tenant).
        2. Refuse if the period is not currently postable
           (``OPEN`` or ``REOPENED``).
        3. Refuse early if FX gain/loss accounts are unset (raises 400).
        4. Discover AR / AP / cash monetary items in non-functional
           currency as-of ``period.end_date``.
        5. Look up closing rates for the union of currencies in scope —
           items in unrated currencies are silently skipped at compute
           time, but a human-readable warning is propagated.
        6. Compute pre-aggregation lines, then aggregate per
           ``(account_id, currency_code)``.
        7. Sum totals, detect any prior FXR run, resolve the next period's
           start_date, and package the result.

        Plan deviation (intentional, fixed in this implementation):
        the original plan stashed ``period.end_date`` on
        ``self._period_end_for_compute`` before calling
        ``_compute_revaluation_lines`` and deleted it in a ``finally``.
        Task 9 changed ``_compute_revaluation_lines`` to take
        ``organization_id`` and ``period_end_date`` as explicit kwargs;
        passing them via mutable instance state was an anti-pattern that
        fights the type system and breaks under any concurrent use. We
        therefore pass both as explicit kwargs and never touch
        ``self._period_end_for_compute``.
        """
        period = self.db.get(FiscalPeriod, fiscal_period_id)
        if not period or period.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")
        if period.status not in self.POSTABLE_PERIOD_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Period must be open to run FX revaluation; "
                    f"current status is {period.status.value}"
                ),
            )

        # Refuse early if FX accounts unconfigured (raises 400)
        self._read_fx_account_ids(organization_id)

        # Discover monetary items in non-functional currency
        ar_items = self._discover_ar_open_invoices(organization_id, period.end_date)
        ap_items = self._discover_ap_open_invoices(organization_id, period.end_date)
        cash_items = self._discover_bank_balances(organization_id, period.end_date)

        # Collect currencies in scope (item[2] is currency_code in all three)
        currencies: set[str] = set()
        for src in (ar_items, ap_items, cash_items):
            for item in src:
                currencies.add(item[2])

        # Look up closing rates; warn on misses
        rates, warnings = self._lookup_closing_rates(
            organization_id, currencies, period.end_date
        )

        # Compute pre-aggregation lines. NOTE: organization_id and
        # period_end_date are passed explicitly (Task 9 contract) — no
        # ``self._period_end_for_compute`` instance-state hack.
        raw_lines = self._compute_revaluation_lines(
            ar_items=ar_items,
            ap_items=ap_items,
            cash_items=cash_items,
            rates=rates,
            organization_id=organization_id,
            period_end_date=period.end_date,
        )

        aggregated = self._aggregate_per_account_currency(raw_lines)

        # Totals: gains add, losses are reported as positive magnitudes.
        total_gain = sum(
            (line.delta_functional for line in aggregated if line.is_gain),
            Decimal("0"),
        )
        total_loss = sum(
            (-line.delta_functional for line in aggregated if not line.is_gain),
            Decimal("0"),
        )

        # Detect prior FXR runs for this period (active statuses only)
        prior_journal_ids = self._detect_prior_run(organization_id, fiscal_period_id)

        # Resolve next-period start date for the auto-reversing journal
        next_period_start = self._resolve_next_period_start(
            organization_id, period.end_date
        )

        return FXRevaluationPreview(
            fiscal_period_id=fiscal_period_id,
            period_end_date=period.end_date,
            next_period_start_date=next_period_start,
            lines=aggregated,
            total_gain_functional=total_gain,
            total_loss_functional=total_loss,
            rates_used=rates,
            warnings=warnings,
            prior_run_exists=bool(prior_journal_ids),
            prior_journal_ids=prior_journal_ids,
        )

    def _build_journal_input(
        self,
        lines: list[FXRevaluationLine],
        posting_date: date,
        description: str,
        fx_gain_account_id: UUID,
        fx_loss_account_id: UUID,
        correlation_id: str,
    ) -> JournalInput:
        """Build a balanced JournalInput from aggregated FXRevaluationLines.

        - Asset/liability side: one line per FXRevaluationLine (control
          account, debit/credit per gain/loss orientation).
        - Gain/loss side: aggregate summary lines (single debit to
          fx_loss_account_id for total losses, single credit to
          fx_gain_account_id for total gains). Summary lines are emitted
          ONLY when their respective total is non-zero — we never post a
          zero-amount line.

        Note on multi-tenant scoping: ``JournalInput`` itself does not
        carry an ``organization_id``; tenant scoping is enforced at
        ``JournalService.create_journal(db, organization_id, input, …)``
        call time. The caller (Task 13's ``post()``) is responsible for
        passing the correct organization_id when creating the journal.
        """
        journal_lines: list[JournalLineInput] = []

        total_gain = Decimal("0")
        total_loss = Decimal("0")

        for line in lines:
            amount = abs(line.delta_functional)
            if amount == 0:
                continue

            # The is_gain flag was set with the asset/liability orientation
            # already taken into account in _compute_revaluation_lines, so:
            #   is_gain → debit the control account, credit FX Gain
            #   not is_gain → credit the control account, debit FX Loss
            #
            # For AP specifically: is_gain=False means liability went up,
            # and "credit the AP control" matches accounting convention
            # (liabilities increase on credit side).
            if line.is_gain:
                journal_lines.append(
                    JournalLineInput(
                        account_id=line.account_id,
                        debit_amount=amount,
                        credit_amount=Decimal("0"),
                        description=(
                            f"FX revaluation: {line.currency_code} "
                            f"@ {line.closing_rate}"
                        ),
                        currency_code=line.currency_code,
                        exchange_rate=line.closing_rate,
                    )
                )
                total_gain += amount
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=line.account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=amount,
                        description=(
                            f"FX revaluation: {line.currency_code} "
                            f"@ {line.closing_rate}"
                        ),
                        currency_code=line.currency_code,
                        exchange_rate=line.closing_rate,
                    )
                )
                total_loss += amount

        # Gain/loss summary lines (functional currency, no per-currency
        # rate). Skip when zero — never post a zero-amount line.
        if total_gain > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=fx_gain_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=total_gain,
                    description="Total FX revaluation gains for the period",
                )
            )
        if total_loss > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=fx_loss_account_id,
                    debit_amount=total_loss,
                    credit_amount=Decimal("0"),
                    description="Total FX revaluation losses for the period",
                )
            )

        return JournalInput(
            journal_type=JournalType.REVALUATION,
            entry_date=posting_date,
            posting_date=posting_date,
            description=description,
            source_module=self.SOURCE_MODULE,
            correlation_id=correlation_id,
            lines=journal_lines,
        )

    def _build_reversal_input(
        self,
        period_end_input: JournalInput,
        reversal_date: date,
        original_journal_number: str,
    ) -> JournalInput:
        """Mirror the period-end journal — debits become credits and vice
        versa — with ``reversal_date`` as posting_date.

        Used for the day-1-of-next-period auto-reversing journal. NOT
        used for the prior-FXR-run reversal in Task 13's ``post()``,
        which delegates to ``ReversalService.create_reversal``.
        """
        reversed_lines: list[JournalLineInput] = []
        for ln in period_end_input.lines:
            reversed_lines.append(
                JournalLineInput(
                    account_id=ln.account_id,
                    debit_amount=ln.credit_amount,
                    credit_amount=ln.debit_amount,
                    description=ln.description,
                    currency_code=ln.currency_code,
                    exchange_rate=ln.exchange_rate,
                )
            )
        return JournalInput(
            journal_type=JournalType.REVALUATION,
            entry_date=reversal_date,
            posting_date=reversal_date,
            description=(
                f"Reversal of {original_journal_number}: period-end FX revaluation"
            ),
            source_module=self.SOURCE_MODULE,
            correlation_id=period_end_input.correlation_id,
            lines=reversed_lines,
        )

    def post(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        user_id: UUID,
        reason: str | None = None,
    ) -> FXRevaluationResult:
        """Atomic post of period-end FX revaluation pair.

        On re-run (prior pair exists), reverses both prior journals first
        — reason is required.

        Raises HTTPException(400) on:
          - period not OPEN/REOPENED
          - FX accounts unconfigured
          - prior pair exists but reason missing
          - next period is missing or closed
        """
        # Re-run preview inside the transaction so state is current
        preview = self.preview(organization_id, fiscal_period_id)

        # 1. Reason check fires BEFORE any other branching: if a prior run
        # exists, replacing/voiding it always requires a reason, even if
        # the new revaluation would be a no-op (lines could be empty
        # because allocations have since cleared the prior balances —
        # that's still a state change worth justifying).
        if preview.prior_run_exists and not (reason and reason.strip()):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Re-running FX revaluation requires a reason for "
                    "replacing the prior revaluation."
                ),
            )

        # 2. Reverse prior pair if any. This MUST run before the empty-lines
        # short-circuit below — otherwise a re-run with cleared balances
        # (lines=[]) would silently leave the prior pair active even though
        # the user explicitly supplied a reason to replace it.
        reversed_ids: list[UUID] = []
        if preview.prior_run_exists:
            if not reason:
                # Reachable only if the reason guard above is ever moved
                # below this block. Use a runtime exception, not assert —
                # asserts are stripped under ``python -O``.
                raise RuntimeError(
                    "FXRevaluationService.post: reason guard violated — "
                    "internal invariant broken"
                )
            for prior_id in preview.prior_journal_ids:
                ReversalService.create_reversal(
                    db=self.db,
                    organization_id=organization_id,
                    original_journal_id=prior_id,
                    # Reverse on the period_end_date so the reversal
                    # lands within an open period.
                    reversal_date=preview.period_end_date,
                    created_by_user_id=user_id,
                    reason=reason,
                    auto_post=True,
                )
                reversed_ids.append(prior_id)

        # 3. Empty-lines short-circuit. Now safe to return success: any
        # prior pair has already been reversed above.
        if not preview.lines:
            if reversed_ids:
                msg = "Prior revaluation reversed; no new items to revalue."
            else:
                msg = (
                    "Nothing to revalue: no foreign-currency monetary "
                    "items found in scope."
                )
            return FXRevaluationResult(
                success=True,
                reversed_prior_journal_ids=reversed_ids,
                message=msg,
            )

        # 4. Next-period guard. Only matters when we actually have new
        # lines to post — without lines there is nothing to reverse on
        # day-1.
        if preview.next_period_start_date is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"FX revaluation cannot post: no next fiscal period "
                    f"covers the day after {preview.period_end_date}. The "
                    f"day-1 reversal needs a target period."
                ),
            )

        # 5. Read FX accounts (raises 400 if unset; preview already did
        # this but it's cheap and keeps post() self-contained).
        gain_account_id, loss_account_id = self._read_fx_account_ids(organization_id)

        # 6. Build and post the new period-end journal
        correlation_id = str(_new_uuid())
        period_end_input = self._build_journal_input(
            lines=preview.lines,
            posting_date=preview.period_end_date,
            description=(
                f"FX revaluation as at {preview.period_end_date}. "
                f"Rates used: "
                + ", ".join(
                    f"{ccy}={rate}" for ccy, rate in sorted(preview.rates_used.items())
                )
            ),
            fx_gain_account_id=gain_account_id,
            fx_loss_account_id=loss_account_id,
            correlation_id=correlation_id,
        )
        period_end_journal = JournalService.create_journal(
            db=self.db,
            organization_id=organization_id,
            input=period_end_input,
            created_by_user_id=user_id,
        )
        JournalService.post_journal(
            db=self.db,
            organization_id=organization_id,
            journal_entry_id=period_end_journal.journal_entry_id,
            posted_by_user_id=user_id,
        )

        # Build and post the day-1 reversal
        reversal_input = self._build_reversal_input(
            period_end_input=period_end_input,
            reversal_date=preview.next_period_start_date,
            original_journal_number=period_end_journal.journal_number,
        )
        reversal_journal = JournalService.create_journal(
            db=self.db,
            organization_id=organization_id,
            input=reversal_input,
            created_by_user_id=user_id,
        )
        JournalService.post_journal(
            db=self.db,
            organization_id=organization_id,
            journal_entry_id=reversal_journal.journal_entry_id,
            posted_by_user_id=user_id,
        )

        return FXRevaluationResult(
            success=True,
            period_end_journal_id=period_end_journal.journal_entry_id,
            reversal_journal_id=reversal_journal.journal_entry_id,
            reversed_prior_journal_ids=reversed_ids,
            total_gain_functional=preview.total_gain_functional,
            total_loss_functional=preview.total_loss_functional,
            message=(
                f"FX revaluation posted: gain "
                f"{preview.total_gain_functional}, loss "
                f"{preview.total_loss_functional} across "
                f"{len(preview.rates_used)} currencies."
            ),
        )
