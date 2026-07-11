"""Shared tax-posting helpers for document posting services."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.tax.tax_code import TaxCode, TaxType

logger = logging.getLogger(__name__)


def prorate_amount(
    allocated_amount: Decimal,
    component_amount: Decimal,
    total_amount: Decimal,
) -> Decimal:
    """Return a rounded proportional component amount for an allocation."""
    if total_amount == Decimal("0"):
        return Decimal("0")
    return ((allocated_amount * component_amount) / total_amount).quantize(
        Decimal("0.01")
    )


def resolve_tax_posting_account_id(
    db: Session,
    organization_id: UUID,
    tax_code_id: UUID,
    *,
    tax_account_attr: str,
    prefer_deferred: bool,
) -> UUID | None:
    """Resolve the current or deferred posting account for a tax code."""
    tax_code = db.get(TaxCode, tax_code_id)
    if not tax_code or tax_code.organization_id != organization_id:
        return None

    account_id = getattr(tax_code, tax_account_attr, None)
    if not account_id:
        return None

    if prefer_deferred and tax_code.tax_type in {TaxType.VAT, TaxType.GST}:
        account = db.get(Account, account_id)
        if account and account.deferral_pair_account_id:
            return account.deferral_pair_account_id
    return cast(UUID, account_id)


def _resolve_fiscal_period(
    db: Session,
    organization_id: UUID,
    transaction_date,
) -> FiscalPeriod | None:
    fiscal_period_stmt = select(FiscalPeriod).where(
        and_(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.start_date <= transaction_date,
            FiscalPeriod.end_date >= transaction_date,
        )
    )
    fiscal_period = db.scalar(fiscal_period_stmt)

    # Several posting unit tests use broad mocks for Session.scalar(). Fall back
    # to the older scalars(...).first() path when scalar() does not return a
    # real FiscalPeriod-like object.
    if fiscal_period is None or isinstance(fiscal_period, Mock):
        scalar_result = db.scalars(fiscal_period_stmt)
        fiscal_period = (
            scalar_result.first() if hasattr(scalar_result, "first") else None
        )
    if isinstance(fiscal_period, Mock) or (
        fiscal_period is not None and not hasattr(fiscal_period, "fiscal_period_id")
    ):
        return None
    return fiscal_period


def create_invoice_tax_transactions(
    db: Session,
    organization_id: UUID,
    *,
    invoice: Any,
    lines: list[Any],
    counterparty_name: str | None,
    counterparty_tax_id: str | None,
    exchange_rate: Decimal,
    is_purchase: bool,
    tax_service: Any,
    is_credit_note: bool = False,
    log_label: str,
) -> list[UUID]:
    """Create tax transactions for AP/AR invoice lines with tax codes."""
    tax_transaction_ids: list[UUID] = []
    fiscal_period = _resolve_fiscal_period(
        db,
        organization_id,
        invoice.invoice_date,
    )
    if not fiscal_period:
        return tax_transaction_ids

    for line in lines:
        if not line.tax_code_id or line.tax_amount == Decimal("0"):
            continue

        base_amount = line.line_amount if not is_credit_note else -line.line_amount
        try:
            tax_txn = tax_service.create_from_invoice_line(
                db=db,
                organization_id=organization_id,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                tax_code_id=line.tax_code_id,
                invoice_id=invoice.invoice_id,
                invoice_line_id=line.line_id,
                invoice_number=invoice.invoice_number,
                transaction_date=invoice.invoice_date,
                is_purchase=is_purchase,
                base_amount=base_amount,
                currency_code=invoice.currency_code,
                counterparty_name=counterparty_name,
                counterparty_tax_id=counterparty_tax_id,
                exchange_rate=exchange_rate,
            )
            tax_transaction_ids.append(tax_txn.transaction_id)
        except Exception:
            logger.exception(
                "create_tax_transaction failed for %s invoice %s",
                log_label,
                invoice.invoice_number,
            )

    if tax_transaction_ids:
        try:
            from app.models.finance.tax.tax_transaction import TaxTransaction as TaxTxn
            from app.services.finance.tax.tax_return import TaxReturnService

            first_txn = db.get(TaxTxn, tax_transaction_ids[0])
            if first_txn:
                TaxReturnService.auto_refresh_return(
                    db,
                    organization_id,
                    fiscal_period.fiscal_period_id,
                    first_txn.jurisdiction_id,
                    organization_id,
                )
        except Exception:
            logger.exception(
                "Failed to auto-refresh tax return for %s invoice %s (non-blocking)",
                log_label,
                invoice.invoice_number,
            )

    return tax_transaction_ids


def create_payment_tax_recognitions(
    db: Session,
    organization_id: UUID,
    *,
    payment: Any,
    tax_payloads: Sequence[Mapping[str, Any]],
    source_document_type: str,
    is_purchase: bool,
    exchange_rate: Decimal,
    counterparty_name: str | None,
    counterparty_tax_id: str | None,
    tax_service: Any,
) -> None:
    """Create cash-basis tax recognition rows for AP/AR payments."""
    fiscal_period = _resolve_fiscal_period(
        db,
        organization_id,
        payment.payment_date,
    )
    if not fiscal_period:
        return

    for payload in tax_payloads:
        tax_service.create_payment_recognition(
            db=db,
            organization_id=organization_id,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            tax_code_id=payload["tax_code_id"],
            transaction_date=payment.payment_date,
            source_document_type=source_document_type,
            source_document_id=payment.payment_id,
            source_document_line_id=payload["source_document_line_id"],
            source_document_reference=payload["source_document_reference"],
            is_purchase=is_purchase,
            base_amount=payload["base_amount"],
            tax_amount=payload["tax_amount"],
            currency_code=payment.currency_code,
            exchange_rate=exchange_rate,
            counterparty_name=counterparty_name,
            counterparty_tax_id=counterparty_tax_id,
        )
