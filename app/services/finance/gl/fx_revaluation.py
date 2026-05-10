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
from datetime import date
from decimal import Decimal
from uuid import UUID

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
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.platform.fx import FXService

logger = logging.getLogger(__name__)


@dataclass
class FXRevaluationLine:
    """One revaluation observation: a single (control_account, currency)
    pair's delta. The proposed journal is constructed from these — the
    asset/liability side becomes one journal line per FXRevaluationLine,
    while the gain/loss side aggregates across all observations into two
    summary lines."""

    account_id: UUID
    currency_code: str
    closing_rate: Decimal
    book_value_functional: Decimal       # current carrying amount in NGN
    revalued_value_functional: Decimal   # value at closing rate, in NGN
    delta_functional: Decimal            # revalued - book; signed
    is_gain: bool                        # True iff delta increases asset / decreases liability


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
        """
        functional = app_settings.default_functional_currency_code

        stmt = select(SupplierInvoice).where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.currency_code != functional,
            SupplierInvoice.status.in_(SupplierInvoiceStatus.outstanding()),
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

        ``is_gain`` and ``closing_rate`` are taken from the first occurrence:
        all lines for the same ``(account_id, currency)`` come from the same
        accounting source (AR/AP/cash), so ``is_gain`` is consistent across
        the bucket and ``closing_rate`` is identical.
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
                )
            else:
                existing.book_value_functional += line.book_value_functional
                existing.revalued_value_functional += line.revalued_value_functional
                existing.delta_functional += line.delta_functional

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
