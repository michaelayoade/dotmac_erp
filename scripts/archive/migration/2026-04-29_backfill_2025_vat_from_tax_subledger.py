#!/usr/bin/env python3
"""
Backfill historical 2025 VAT from tax.tax_transaction accrual rows.

Why this exists:
- 2025 VAT accrual rows exist in tax.tax_transaction.
- 2025 invoice tax-detail tables are largely empty, so the normal deferred-VAT
  backfill runner cannot reconstruct history reliably from them.

What this script does:
1. AR invoices:
   Rebuild invoice-date deferred VAT journals from AR VAT/GST accrual rows.
2. AP invoices:
   Rebuild invoice-date deferred VAT journals from AP VAT/GST accrual rows.
3. AR payments:
   Rebuild cash-basis VAT reclass journals and cash tax_transaction rows from
   AR payment allocations plus AR invoice accrual rows.
4. AP payments:
   Report-only. 2025 AP payment allocations are missing historically, so cash
   VAT cannot be reconstructed safely from current data.

This script is intentionally conservative. If data required for a phase is not
present, it skips and reports rather than guessing.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import TypeVar
from uuid import UUID

sys.path.insert(0, ".")

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.core_org.organization import Organization
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_transaction import (
    TaxRecognitionBasis,
    TaxTransaction,
    TaxTransactionType,
)
from app.services.common import coerce_uuid
from app.services.finance.ap.posting.helpers import determine_debit_account
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter
from app.services.finance.tax.tax_transaction import tax_transaction_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_2025_vat_from_tax_subledger")

PHASE_AR_INVOICES = "ar-invoices"
PHASE_AP_INVOICES = "ap-invoices"
PHASE_AR_PAYMENTS = "ar-payments"
PHASE_AP_PAYMENTS = "ap-payments"
ALL_PHASES = (
    PHASE_AR_INVOICES,
    PHASE_AP_INVOICES,
    PHASE_AR_PAYMENTS,
    PHASE_AP_PAYMENTS,
)
T = TypeVar("T")


@dataclass
class BackfillStats:
    ar_invoice_candidates: int = 0
    ar_invoice_posted: int = 0
    ar_invoice_skipped: int = 0
    ap_invoice_candidates: int = 0
    ap_invoice_posted: int = 0
    ap_invoice_skipped: int = 0
    ar_payment_candidates: int = 0
    ar_payment_posted: int = 0
    ar_payment_skipped: int = 0
    ap_payment_candidates: int = 0
    ap_payment_skipped: int = 0
    failures: int = 0


@dataclass(frozen=True)
class RunOptions:
    limit: int | None
    offset: int
    from_date: date | None
    to_date: date | None
    verbose: bool


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _prorate(
    allocated_amount: Decimal,
    component_amount: Decimal,
    total_amount: Decimal,
) -> Decimal:
    if total_amount == Decimal("0"):
        return Decimal("0")
    return _quantize((allocated_amount * component_amount) / total_amount)


def _date_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year + 1, 1, 1)


def _effective_bounds(year: int, options: RunOptions) -> tuple[date, date]:
    start_date, end_date = _date_bounds(year)
    if options.from_date and options.from_date > start_date:
        start_date = options.from_date
    if options.to_date:
        bounded_end = options.to_date + timedelta(days=1)
        if bounded_end < end_date:
            end_date = bounded_end
    return start_date, end_date


def _normalize_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _apply_window(rows: list[T], options: RunOptions) -> list[T]:
    start = options.offset
    if options.limit is None:
        return rows[start:]
    return rows[start : start + options.limit]


def _get_org_id(db: Session, org_id_arg: str | None) -> UUID:
    if org_id_arg:
        return coerce_uuid(org_id_arg)
    org = db.scalar(select(Organization))
    if not org:
        raise RuntimeError("No organization found")
    return org.organization_id


def _journal_exists(
    db: Session,
    *,
    organization_id: UUID,
    source_module: str,
    source_document_type: str,
    source_document_id: UUID,
) -> bool:
    existing = db.scalar(
        select(JournalEntry.journal_entry_id).where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.source_module == source_module,
            JournalEntry.source_document_type == source_document_type,
            JournalEntry.source_document_id == source_document_id,
            JournalEntry.status.notin_([JournalStatus.VOID, JournalStatus.REVERSED]),
        )
    )
    return existing is not None


def _cash_rows_exist(
    db: Session,
    *,
    organization_id: UUID,
    source_document_type: str,
    source_document_id: UUID,
) -> bool:
    existing = db.scalar(
        select(TaxTransaction.transaction_id).where(
            TaxTransaction.organization_id == organization_id,
            TaxTransaction.source_document_type == source_document_type,
            TaxTransaction.source_document_id == source_document_id,
            TaxTransaction.recognition_basis == TaxRecognitionBasis.CASH,
        )
    )
    return existing is not None


def _post_journal(
    db: Session,
    *,
    organization_id: UUID,
    source_module: str,
    source_document_type: str,
    source_document_id: UUID,
    entry_date: date,
    description: str,
    reference: str,
    currency_code: str,
    exchange_rate: Decimal,
    lines: list[JournalLineInput],
    correlation_id: str | None,
    user_id: UUID,
) -> None:
    journal_input = JournalInput(
        journal_type=JournalType.ADJUSTMENT,
        entry_date=entry_date,
        posting_date=entry_date,
        description=description,
        reference=reference,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        lines=lines,
        source_module=source_module,
        source_document_type=source_document_type,
        source_document_id=source_document_id,
        correlation_id=correlation_id,
    )
    journal, error = BasePostingAdapter.create_and_approve_journal(
        db,
        organization_id,
        journal_input,
        user_id,
        error_prefix="Historical VAT journal creation failed",
    )
    if error:
        raise RuntimeError(error.message)

    result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=organization_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=entry_date,
        idempotency_key=BasePostingAdapter.make_idempotency_key(
            organization_id,
            f"{source_module}:{source_document_type}",
            source_document_id,
            action="post",
        ),
        source_module=source_module,
        correlation_id=correlation_id,
        posted_by_user_id=user_id,
        success_message="Historical VAT journal posted successfully",
    )
    if not result.success:
        raise RuntimeError(result.message)


def _get_fiscal_period_id(
    db: Session, organization_id: UUID, txn_date: date
) -> UUID | None:
    period = db.scalar(
        select(FiscalPeriod).where(
            and_(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= txn_date,
                FiscalPeriod.end_date >= txn_date,
            )
        )
    )
    return period.fiscal_period_id if period else None


def _fetch_vat_tax_transactions(
    db: Session,
    *,
    organization_id: UUID,
    start_date: date,
    end_date: date,
    transaction_type: TaxTransactionType,
) -> list[tuple[TaxTransaction, TaxCode]]:
    return list(
        db.execute(
            select(TaxTransaction, TaxCode)
            .join(TaxCode, TaxCode.tax_code_id == TaxTransaction.tax_code_id)
            .where(
                TaxTransaction.organization_id == organization_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date < end_date,
                TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
                TaxTransaction.transaction_type == transaction_type,
                TaxCode.tax_type.in_((TaxType.VAT, TaxType.GST)),
            )
            .order_by(
                TaxTransaction.transaction_date,
                TaxTransaction.source_document_id,
                TaxTransaction.source_document_line_id,
            )
        )
    )


def _build_ar_resolution_context(
    db: Session,
    *,
    organization_id: UUID,
    rows: list[tuple[TaxTransaction, TaxCode]],
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Customer], dict[UUID, list[Invoice]]]:
    normalized_names = {
        _normalize_name(txn.counterparty_name)
        for txn, _ in rows
        if txn.counterparty_name
    }

    customers = list(
        db.scalars(
            select(Customer).where(Customer.organization_id == organization_id)
        ).all()
    )
    customer_by_name = {
        _normalize_name(customer.legal_name): customer
        for customer in customers
        if _normalize_name(customer.legal_name) in normalized_names
    }

    candidate_customer_ids = {
        customer.customer_id for customer in customer_by_name.values()
    }
    if not candidate_customer_ids:
        return customer_by_name, {}

    invoices = list(
        db.scalars(
            select(Invoice).where(
                Invoice.organization_id == organization_id,
                Invoice.customer_id.in_(candidate_customer_ids),
                Invoice.invoice_date >= start_date - timedelta(days=3),
                Invoice.invoice_date < end_date + timedelta(days=3),
            )
        ).all()
    )
    invoices_by_customer: dict[UUID, list[Invoice]] = {}
    for invoice in invoices:
        invoices_by_customer.setdefault(invoice.customer_id, []).append(invoice)
    return customer_by_name, invoices_by_customer


def _resolve_ar_invoice_from_tax_txn(
    db: Session,
    *,
    organization_id: UUID,
    txn: TaxTransaction,
    customer_cache: dict[str, Customer | None] | None = None,
    invoices_by_customer: dict[UUID, list[Invoice]] | None = None,
    resolution_cache: dict[
        tuple[UUID | None, str | None, str | None, date, Decimal, Decimal],
        tuple[Invoice | None, str],
    ]
    | None = None,
) -> tuple[Invoice | None, str]:
    cache_key = (
        txn.source_document_id,
        txn.source_document_reference,
        _normalize_name(txn.counterparty_name),
        txn.transaction_date,
        txn.base_amount,
        txn.tax_amount,
    )
    if resolution_cache is not None and cache_key in resolution_cache:
        return resolution_cache[cache_key]

    invoice = db.get(Invoice, txn.source_document_id)
    if invoice and invoice.organization_id == organization_id:
        result = (invoice, "direct-id")
        if resolution_cache is not None:
            resolution_cache[cache_key] = result
        return result

    if not txn.counterparty_name:
        result = (None, "missing-counterparty")
        if resolution_cache is not None:
            resolution_cache[cache_key] = result
        return result

    normalized_name = _normalize_name(txn.counterparty_name)
    if customer_cache is not None and normalized_name in customer_cache:
        customer = customer_cache[normalized_name]
    else:
        customer = db.scalar(
            select(Customer).where(
                Customer.organization_id == organization_id,
                func.lower(
                    func.regexp_replace(
                        func.trim(Customer.legal_name), r"\s+", " ", "g"
                    )
                )
                == normalized_name,
            )
        )
        if customer_cache is not None:
            customer_cache[normalized_name] = customer
    if not customer:
        result = (None, "customer-not-found")
        if resolution_cache is not None:
            resolution_cache[cache_key] = result
        return result

    candidate_pool = (
        invoices_by_customer.get(customer.customer_id, [])
        if invoices_by_customer is not None
        else list(
            db.scalars(
                select(Invoice).where(
                    Invoice.organization_id == organization_id,
                    Invoice.customer_id == customer.customer_id,
                    Invoice.invoice_date >= txn.transaction_date - timedelta(days=3),
                    Invoice.invoice_date <= txn.transaction_date + timedelta(days=3),
                    Invoice.subtotal == txn.base_amount,
                    Invoice.tax_amount == txn.tax_amount,
                )
            ).all()
        )
    )
    if invoices_by_customer is not None:
        candidates = [
            invoice
            for invoice in candidate_pool
            if invoice.invoice_date >= txn.transaction_date - timedelta(days=3)
            and invoice.invoice_date <= txn.transaction_date + timedelta(days=3)
            and invoice.subtotal == txn.base_amount
            and invoice.tax_amount == txn.tax_amount
        ]
    else:
        candidates = candidate_pool
    if len(candidates) == 1:
        result = (candidates[0], "fuzzy-customer-date-amount")
        if resolution_cache is not None:
            resolution_cache[cache_key] = result
        return result
    if len(candidates) > 1:
        result = (None, "ambiguous-fuzzy-match")
        if resolution_cache is not None:
            resolution_cache[cache_key] = result
        return result
    result = (None, "no-fuzzy-match")
    if resolution_cache is not None:
        resolution_cache[cache_key] = result
    return result


def _backfill_ar_invoices(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    year: int,
    apply: bool,
    stats: BackfillStats,
    options: RunOptions,
) -> None:
    start_date, end_date = _effective_bounds(year, options)
    rows = _fetch_vat_tax_transactions(
        db,
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date,
        transaction_type=TaxTransactionType.OUTPUT,
    )

    grouped: dict[UUID, dict[str, object]] = {}
    resolution_counts: dict[str, int] = {}
    customer_cache, invoices_by_customer = _build_ar_resolution_context(
        db,
        organization_id=organization_id,
        rows=rows,
        start_date=start_date,
        end_date=end_date,
    )
    resolution_cache: dict[
        tuple[UUID | None, str | None, str | None, date, Decimal, Decimal],
        tuple[Invoice | None, str],
    ] = {}
    for txn, code in rows:
        invoice, resolution = _resolve_ar_invoice_from_tax_txn(
            db,
            organization_id=organization_id,
            txn=txn,
            customer_cache=customer_cache,
            invoices_by_customer=invoices_by_customer,
            resolution_cache=resolution_cache,
        )
        resolution_counts[resolution] = resolution_counts.get(resolution, 0) + 1
        if not invoice or not invoice.journal_entry_id:
            continue
        current_account_id = code.tax_collected_account_id
        if not current_account_id:
            continue
        current_account = db.get(Account, current_account_id)
        if not current_account or not current_account.deferral_pair_account_id:
            continue

        bucket = grouped.setdefault(
            invoice.invoice_id,
            {
                "invoice": invoice,
                "lines": {},
            },
        )
        key = (current_account_id, current_account.deferral_pair_account_id)
        lines = bucket["lines"]
        assert isinstance(lines, dict)
        lines[key] = lines.get(key, Decimal("0")) + txn.tax_amount

    if rows and not grouped:
        logger.warning(
            "AR accrual VAT rows exist for %s, but none resolve to current ar.invoice records. "
            "Historical tax_transaction.source_document_id/reference appears to be on a legacy document identity scheme.",
            year,
        )
    elif resolution_counts:
        logger.info("AR resolution paths: %s", resolution_counts)

    grouped_items = list(grouped.items())
    for invoice_id, payload in _apply_window(grouped_items, options):
        invoice = payload["invoice"]
        assert isinstance(invoice, Invoice)
        lines_map = payload["lines"]
        assert isinstance(lines_map, dict)

        stats.ar_invoice_candidates += 1
        if _journal_exists(
            db,
            organization_id=organization_id,
            source_module="AR",
            source_document_type="AR_INVOICE_VAT_DEFERRAL",
            source_document_id=invoice_id,
        ):
            stats.ar_invoice_skipped += 1
            continue

        if not apply:
            if options.verbose:
                logger.info(
                    "DRY RUN AR invoice %s: would defer %s VAT bucket(s)",
                    invoice.invoice_number,
                    len(lines_map),
                )
            continue

        journal_lines: list[JournalLineInput] = []
        for (current_account_id, deferred_account_id), amount in lines_map.items():
            journal_lines.append(
                JournalLineInput(
                    account_id=current_account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    description=f"Historical deferred VAT for invoice {invoice.invoice_number}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=deferred_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    description=f"Historical deferred VAT for invoice {invoice.invoice_number}",
                )
            )

        _post_journal(
            db,
            organization_id=organization_id,
            source_module="AR",
            source_document_type="AR_INVOICE_VAT_DEFERRAL",
            source_document_id=invoice.invoice_id,
            entry_date=invoice.invoice_date,
            description=f"Historical deferred VAT for AR invoice {invoice.invoice_number}",
            reference=f"AR-VAT-HIST-{invoice.invoice_number}",
            currency_code=invoice.currency_code,
            exchange_rate=invoice.exchange_rate or Decimal("1.0"),
            lines=journal_lines,
            correlation_id=invoice.correlation_id,
            user_id=user_id,
        )
        stats.ar_invoice_posted += 1
        db.commit()


def _backfill_ap_invoices(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    year: int,
    apply: bool,
    stats: BackfillStats,
    options: RunOptions,
) -> None:
    start_date, end_date = _effective_bounds(year, options)
    rows = _fetch_vat_tax_transactions(
        db,
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date,
        transaction_type=TaxTransactionType.INPUT,
    )

    grouped: dict[UUID, dict[str, object]] = {}
    for txn, code in rows:
        invoice = db.get(SupplierInvoice, txn.source_document_id)
        if not invoice or not invoice.journal_entry_id:
            continue
        supplier = db.get(Supplier, invoice.supplier_id)
        line = (
            db.get(SupplierInvoiceLine, txn.source_document_line_id)
            if txn.source_document_line_id
            else None
        )
        if not supplier or not line:
            continue
        current_account_id = code.tax_paid_account_id
        if not current_account_id:
            continue
        current_account = db.get(Account, current_account_id)
        if not current_account or not current_account.deferral_pair_account_id:
            continue
        source_debit_account_id = determine_debit_account(
            db,
            organization_id,
            line,
            supplier,
        )
        if not source_debit_account_id:
            continue

        bucket = grouped.setdefault(
            invoice.invoice_id,
            {
                "invoice": invoice,
                "lines": {},
            },
        )
        key = (current_account.deferral_pair_account_id, source_debit_account_id)
        lines = bucket["lines"]
        assert isinstance(lines, dict)
        lines[key] = lines.get(key, Decimal("0")) + txn.tax_amount

    if rows and not grouped:
        logger.warning(
            "AP accrual VAT rows exist for %s, but none resolve to current ap.supplier_invoice records.",
            year,
        )

    grouped_items = list(grouped.items())
    for invoice_id, payload in _apply_window(grouped_items, options):
        invoice = payload["invoice"]
        assert isinstance(invoice, SupplierInvoice)
        lines_map = payload["lines"]
        assert isinstance(lines_map, dict)

        stats.ap_invoice_candidates += 1
        if _journal_exists(
            db,
            organization_id=organization_id,
            source_module="AP",
            source_document_type="SUPPLIER_INVOICE_VAT_DEFERRAL",
            source_document_id=invoice_id,
        ):
            stats.ap_invoice_skipped += 1
            continue

        if not apply:
            if options.verbose:
                logger.info(
                    "DRY RUN AP invoice %s: would defer %s VAT bucket(s)",
                    invoice.invoice_number,
                    len(lines_map),
                )
            continue

        journal_lines: list[JournalLineInput] = []
        for (deferred_account_id, source_debit_account_id), amount in lines_map.items():
            journal_lines.append(
                JournalLineInput(
                    account_id=deferred_account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    description=f"Historical deferred VAT for supplier invoice {invoice.invoice_number}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=source_debit_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    description=f"Historical deferred VAT for supplier invoice {invoice.invoice_number}",
                )
            )

        _post_journal(
            db,
            organization_id=organization_id,
            source_module="AP",
            source_document_type="SUPPLIER_INVOICE_VAT_DEFERRAL",
            source_document_id=invoice.invoice_id,
            entry_date=invoice.invoice_date,
            description=f"Historical deferred VAT for supplier invoice {invoice.invoice_number}",
            reference=f"AP-VAT-HIST-{invoice.invoice_number}",
            currency_code=invoice.currency_code,
            exchange_rate=invoice.exchange_rate or Decimal("1.0"),
            lines=journal_lines,
            correlation_id=invoice.invoice_number,
            user_id=user_id,
        )
        stats.ap_invoice_posted += 1
        db.commit()


def _backfill_ar_payments(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    year: int,
    apply: bool,
    stats: BackfillStats,
    options: RunOptions,
) -> None:
    start_date, end_date = _effective_bounds(year, options)
    accrual_rows = _fetch_vat_tax_transactions(
        db,
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date,
        transaction_type=TaxTransactionType.OUTPUT,
    )

    vat_by_invoice: dict[UUID, list[tuple[TaxTransaction, TaxCode]]] = {}
    resolution_counts: dict[str, int] = {}
    customer_cache, invoices_by_customer = _build_ar_resolution_context(
        db,
        organization_id=organization_id,
        rows=accrual_rows,
        start_date=start_date,
        end_date=end_date,
    )
    resolution_cache: dict[
        tuple[UUID | None, str | None, str | None, date, Decimal, Decimal],
        tuple[Invoice | None, str],
    ] = {}
    for txn, code in accrual_rows:
        invoice, resolution = _resolve_ar_invoice_from_tax_txn(
            db,
            organization_id=organization_id,
            txn=txn,
            customer_cache=customer_cache,
            invoices_by_customer=invoices_by_customer,
            resolution_cache=resolution_cache,
        )
        resolution_counts[resolution] = resolution_counts.get(resolution, 0) + 1
        if not invoice:
            continue
        vat_by_invoice.setdefault(invoice.invoice_id, []).append((txn, code))

    if accrual_rows and not vat_by_invoice:
        logger.warning("No AR VAT accrual rows grouped for %s", year)
    elif resolution_counts:
        logger.info("AR payment resolution paths: %s", resolution_counts)

    payments = list(
        db.scalars(
            select(CustomerPayment)
            .where(
                CustomerPayment.organization_id == organization_id,
                CustomerPayment.payment_date >= start_date,
                CustomerPayment.payment_date < end_date,
                CustomerPayment.status == PaymentStatus.CLEARED,
            )
            .order_by(CustomerPayment.payment_date, CustomerPayment.payment_number)
        ).all()
    )

    for payment in _apply_window(payments, options):
        stats.ar_payment_candidates += 1
        if _journal_exists(
            db,
            organization_id=organization_id,
            source_module="AR",
            source_document_type="CUSTOMER_PAYMENT_VAT_RECLASS",
            source_document_id=payment.payment_id,
        ) or _cash_rows_exist(
            db,
            organization_id=organization_id,
            source_document_type="CUSTOMER_PAYMENT",
            source_document_id=payment.payment_id,
        ):
            stats.ar_payment_skipped += 1
            continue

        allocations = list(
            db.scalars(
                select(PaymentAllocation).where(
                    PaymentAllocation.payment_id == payment.payment_id
                )
            ).all()
        )
        if not allocations:
            stats.ar_payment_skipped += 1
            continue

        customer = db.get(Customer, payment.customer_id)
        if not customer:
            stats.ar_payment_skipped += 1
            continue

        exchange_rate = payment.exchange_rate or Decimal("1.0")
        journal_grouped: dict[tuple[UUID, UUID], Decimal] = {}
        tax_payloads: list[dict[str, object]] = []

        for allocation in allocations:
            invoice = db.get(Invoice, allocation.invoice_id)
            if (
                not invoice
                or invoice.organization_id != organization_id
                or invoice.total_amount == Decimal("0")
            ):
                continue
            invoice_rows = vat_by_invoice.get(invoice.invoice_id, [])
            settled_amount = (
                allocation.allocated_amount
                + allocation.discount_taken
                + allocation.write_off_amount
            )
            for txn, code in invoice_rows:
                current_account_id = code.tax_collected_account_id
                if not current_account_id:
                    continue
                current_account = db.get(Account, current_account_id)
                if not current_account or not current_account.deferral_pair_account_id:
                    continue
                tax_amount = _prorate(
                    settled_amount,
                    txn.tax_amount,
                    invoice.total_amount,
                )
                base_amount = _prorate(
                    settled_amount,
                    txn.base_amount,
                    invoice.total_amount,
                )
                if tax_amount == Decimal("0"):
                    continue
                key = (
                    current_account.deferral_pair_account_id,
                    current_account_id,
                )
                journal_grouped[key] = (
                    journal_grouped.get(key, Decimal("0")) + tax_amount
                )
                tax_payloads.append(
                    {
                        "tax_code_id": txn.tax_code_id,
                        "source_document_line_id": allocation.allocation_id,
                        "source_document_reference": invoice.invoice_number,
                        "base_amount": base_amount,
                        "tax_amount": tax_amount,
                    }
                )

        if not journal_grouped:
            stats.ar_payment_skipped += 1
            continue

        if not apply:
            if options.verbose:
                logger.info(
                    "DRY RUN AR payment %s: would recognize %s VAT bucket(s)",
                    payment.payment_number,
                    len(journal_grouped),
                )
            continue

        journal_lines: list[JournalLineInput] = []
        for (
            deferred_account_id,
            current_account_id,
        ), amount in journal_grouped.items():
            functional_tax = _quantize(amount * exchange_rate)
            journal_lines.append(
                JournalLineInput(
                    account_id=deferred_account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=functional_tax,
                    credit_amount_functional=Decimal("0"),
                    description=f"Historical VAT recognized on receipt {payment.payment_number}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=current_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=functional_tax,
                    description=f"Historical VAT payable recognized on receipt {payment.payment_number}",
                )
            )

        _post_journal(
            db,
            organization_id=organization_id,
            source_module="AR",
            source_document_type="CUSTOMER_PAYMENT_VAT_RECLASS",
            source_document_id=payment.payment_id,
            entry_date=payment.payment_date,
            description=f"Historical AR VAT reclass {payment.payment_number} - {customer.legal_name}",
            reference=payment.reference or payment.payment_number,
            currency_code=payment.currency_code,
            exchange_rate=exchange_rate,
            lines=journal_lines,
            correlation_id=payment.correlation_id,
            user_id=user_id,
        )

        fiscal_period_id = _get_fiscal_period_id(
            db, organization_id, payment.payment_date
        )
        if fiscal_period_id:
            for payload in tax_payloads:
                tax_transaction_service.create_payment_recognition(
                    db=db,
                    organization_id=organization_id,
                    fiscal_period_id=fiscal_period_id,
                    tax_code_id=payload["tax_code_id"],
                    transaction_date=payment.payment_date,
                    source_document_type="CUSTOMER_PAYMENT",
                    source_document_id=payment.payment_id,
                    source_document_line_id=payload["source_document_line_id"],
                    source_document_reference=payload["source_document_reference"],
                    is_purchase=False,
                    base_amount=payload["base_amount"],
                    tax_amount=payload["tax_amount"],
                    currency_code=payment.currency_code,
                    exchange_rate=exchange_rate,
                    counterparty_name=customer.legal_name,
                    counterparty_tax_id=customer.tax_identification_number,
                )

        stats.ar_payment_posted += 1
        db.commit()


def _review_ap_payments(
    db: Session,
    *,
    organization_id: UUID,
    year: int,
    stats: BackfillStats,
    options: RunOptions,
) -> None:
    start_date, end_date = _effective_bounds(year, options)
    payments = list(
        db.scalars(
            select(SupplierPayment).where(
                SupplierPayment.organization_id == organization_id,
                SupplierPayment.payment_date >= start_date,
                SupplierPayment.payment_date < end_date,
                SupplierPayment.status.in_(
                    {APPaymentStatus.SENT, APPaymentStatus.CLEARED}
                ),
            )
        ).all()
    )
    for payment in _apply_window(payments, options):
        stats.ap_payment_candidates += 1
        allocation_count = db.scalar(
            select(func.count(APPaymentAllocation.allocation_id)).where(
                APPaymentAllocation.payment_id == payment.payment_id
            )
        )
        if allocation_count:
            logger.info(
                "AP payment %s has %s allocation row(s); historical AP cash VAT may be recoverable for this payment",
                payment.payment_number,
                allocation_count,
            )
        stats.ap_payment_skipped += 1


def _log_summary(stats: BackfillStats, *, apply: bool) -> None:
    logger.info("=" * 72)
    logger.info(
        "Historical 2025 VAT remediation summary [%s]",
        "APPLY" if apply else "DRY RUN",
    )
    logger.info(
        "AR invoices: candidates=%d posted=%d skipped=%d",
        stats.ar_invoice_candidates,
        stats.ar_invoice_posted,
        stats.ar_invoice_skipped,
    )
    logger.info(
        "AP invoices: candidates=%d posted=%d skipped=%d",
        stats.ap_invoice_candidates,
        stats.ap_invoice_posted,
        stats.ap_invoice_skipped,
    )
    logger.info(
        "AR payments: candidates=%d posted=%d skipped=%d",
        stats.ar_payment_candidates,
        stats.ar_payment_posted,
        stats.ar_payment_skipped,
    )
    logger.info(
        "AP payments: candidates=%d skipped=%d (review only: historical allocations unavailable)",
        stats.ap_payment_candidates,
        stats.ap_payment_skipped,
    )
    logger.info("Failures: %d", stats.failures)
    logger.info("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical 2025 VAT from tax subledger accrual rows."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview only")
    mode.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--year", type=int, default=2025, help="Target calendar year")
    parser.add_argument("--org-id", help="Organization UUID (defaults to first org)")
    parser.add_argument(
        "--user-id",
        help="User UUID to attribute postings to (defaults to org_id/system user)",
    )
    parser.add_argument(
        "--phase",
        action="append",
        choices=ALL_PHASES,
        help="Run only the selected phase(s); may be provided multiple times",
    )
    parser.add_argument("--limit", type=int, help="Limit rows per selected phase")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset rows per selected phase after grouping/filtering",
    )
    parser.add_argument(
        "--from-date",
        type=date.fromisoformat,
        help="Inclusive lower date bound in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--to-date",
        type=date.fromisoformat,
        help="Inclusive upper date bound in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log each candidate document instead of summary-only output",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be a positive integer")
    if args.offset < 0:
        raise SystemExit("--offset must be zero or greater")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        raise SystemExit("--from-date cannot be later than --to-date")

    apply = bool(args.apply)

    with SessionLocal() as db:
        org_id = _get_org_id(db, args.org_id)
        user_id = coerce_uuid(args.user_id) if args.user_id else org_id
        phases = tuple(args.phase or ALL_PHASES)
        options = RunOptions(
            limit=args.limit,
            offset=args.offset,
            from_date=args.from_date,
            to_date=args.to_date,
            verbose=args.verbose,
        )
        stats = BackfillStats()

        handlers = {
            PHASE_AR_INVOICES: lambda: _backfill_ar_invoices(
                db,
                organization_id=org_id,
                user_id=user_id,
                year=args.year,
                apply=apply,
                stats=stats,
                options=options,
            ),
            PHASE_AP_INVOICES: lambda: _backfill_ap_invoices(
                db,
                organization_id=org_id,
                user_id=user_id,
                year=args.year,
                apply=apply,
                stats=stats,
                options=options,
            ),
            PHASE_AR_PAYMENTS: lambda: _backfill_ar_payments(
                db,
                organization_id=org_id,
                user_id=user_id,
                year=args.year,
                apply=apply,
                stats=stats,
                options=options,
            ),
            PHASE_AP_PAYMENTS: lambda: _review_ap_payments(
                db,
                organization_id=org_id,
                year=args.year,
                stats=stats,
                options=options,
            ),
        }

        for phase in phases:
            logger.info(
                "Running phase=%s year=%s mode=%s limit=%s offset=%s from_date=%s to_date=%s",
                phase,
                args.year,
                "APPLY" if apply else "DRY RUN",
                options.limit if options.limit is not None else "ALL",
                options.offset,
                options.from_date.isoformat() if options.from_date else "NONE",
                options.to_date.isoformat() if options.to_date else "NONE",
            )
            try:
                handlers[phase]()
            except Exception:
                stats.failures += 1
                db.rollback()
                logger.exception("Phase %s failed", phase)

        _log_summary(stats, apply=apply)


if __name__ == "__main__":
    main()
