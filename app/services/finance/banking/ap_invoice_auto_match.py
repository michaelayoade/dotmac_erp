"""Deterministic matching of AP invoices to debit bank statement lines."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import (
    APPaymentMethod,
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    StatementLineType,
)
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
    SourceDocType,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import NotFoundError, ValidationError
from app.services.finance.ap.supplier_payment import (
    PaymentAllocationInput,
    SupplierPaymentInput,
    SupplierPaymentService,
)
from app.services.finance.banking.bank_reconciliation import (
    bank_reconciliation_service,
)
from app.services.finance.banking.reconciliation_rule_service import (
    ReconciliationRuleService,
)

logger = logging.getLogger(__name__)

_REFERENCE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=/\-]{2,}")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


class APInvoiceAutoMatchError(ValueError):
    """Raised when AP invoice automation cannot safely run."""


class _SkipCandidate(RuntimeError):
    """Roll back one candidate savepoint without failing the batch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class APInvoiceAutoMatchResult:
    """Auditable counters returned by an AP invoice match run."""

    scanned: int = 0
    matched: int = 0
    payments_created: int = 0
    payments_reused: int = 0
    bank_accounts_corrected: int = 0
    skipped_rule_condition: int = 0
    skipped_no_invoice_reference: int = 0
    skipped_ambiguous_reference: int = 0
    skipped_duplicate_invoice: int = 0
    skipped_amount: int = 0
    skipped_currency: int = 0
    skipped_withholding_tax: int = 0
    skipped_payment: int = 0
    skipped_journal: int = 0
    skipped_consumed_journal_line: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _normalize_reference(value: str | None) -> str:
    if not value:
        return ""
    return _NON_ALNUM.sub("", value.casefold())


def _statement_reference_keys(line: BankStatementLine) -> set[str]:
    """Extract exact complete reference tokens from a statement line."""
    searchable_values = (
        line.reference,
        line.bank_reference,
        line.transaction_id,
        line.description,
    )
    keys = {_normalize_reference(value) for value in searchable_values[:3] if value}
    for value in searchable_values:
        for token in _REFERENCE_TOKEN.findall(value or ""):
            normalized = _normalize_reference(token)
            if any(character.isdigit() for character in normalized):
                keys.add(normalized)
    return {key for key in keys if len(key) >= 4}


class APInvoiceAutoMatchService:
    """Create or reuse exact supplier payments and reconcile their bank lines."""

    @staticmethod
    def validate_configuration(
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
    ) -> tuple[BankAccount, ReconciliationMatchRule]:
        account = db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise APInvoiceAutoMatchError("Bank account not found")
        if account.status != BankAccountStatus.active:
            raise APInvoiceAutoMatchError("The selected bank account is not active")
        if not account.gl_account_id:
            raise APInvoiceAutoMatchError("The selected bank account has no GL account")

        rule = db.get(ReconciliationMatchRule, rule_id)
        if not rule or rule.organization_id != organization_id:
            raise APInvoiceAutoMatchError("Automation rule not found")
        if rule.bank_account_id != bank_account_id:
            raise APInvoiceAutoMatchError(
                "The automation rule is not assigned to this bank account"
            )
        if not rule.is_active:
            raise APInvoiceAutoMatchError("The automation rule is disabled")
        if rule.source_doc_type != SourceDocType.SUPPLIER_PAYMENT.value:
            raise APInvoiceAutoMatchError(
                "The selected rule does not match supplier payments"
            )
        if rule.action_type != "MATCH" or not rule.match_debit:
            raise APInvoiceAutoMatchError(
                "AP invoice automation requires a debit MATCH rule"
            )
        return account, rule

    def run(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
        actor_user_id: UUID,
    ) -> APInvoiceAutoMatchResult:
        account, rule = self.validate_configuration(
            db,
            organization_id,
            bank_account_id,
            rule_id,
        )
        rule_service = ReconciliationRuleService(db)
        result = APInvoiceAutoMatchResult()

        statement_lines = list(
            db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == organization_id,
                    BankStatement.bank_account_id == bank_account_id,
                    BankStatementLine.transaction_type == StatementLineType.debit,
                    BankStatementLine.is_matched.is_(False),
                )
                .order_by(
                    BankStatementLine.transaction_date,
                    BankStatementLine.line_number,
                )
                .with_for_update(of=BankStatementLine, skip_locked=True)
            ).all()
        )
        result.scanned = len(statement_lines)
        if not statement_lines:
            return result

        eligible_lines = []
        for line in statement_lines:
            if rule_service.evaluate_conditions(rule, line):
                eligible_lines.append(line)
            else:
                result.skipped_rule_condition += 1

        invoices = list(
            db.scalars(
                select(SupplierInvoice).where(
                    SupplierInvoice.organization_id == organization_id,
                    SupplierInvoice.status.in_(
                        [
                            SupplierInvoiceStatus.POSTED,
                            SupplierInvoiceStatus.PARTIALLY_PAID,
                            SupplierInvoiceStatus.PAID,
                        ]
                    ),
                    or_(
                        SupplierInvoice.balance_due > Decimal("0"),
                        SupplierInvoice.status == SupplierInvoiceStatus.PAID,
                    ),
                    SupplierInvoice.journal_entry_id.is_not(None),
                )
            ).all()
        )
        invoices_by_reference: dict[str, list[SupplierInvoice]] = {}
        for invoice in invoices:
            for reference in (
                invoice.invoice_number,
                invoice.supplier_invoice_number,
            ):
                key = _normalize_reference(reference)
                if len(key) >= 4 and any(character.isdigit() for character in key):
                    invoices_by_reference.setdefault(key, []).append(invoice)

        candidates: dict[UUID, SupplierInvoice] = {}
        for line in eligible_lines:
            matched_invoices: dict[UUID, SupplierInvoice] = {}
            ambiguous = False
            for key in _statement_reference_keys(line):
                reference_invoices = invoices_by_reference.get(key, [])
                distinct_ids = {invoice.invoice_id for invoice in reference_invoices}
                if len(distinct_ids) > 1:
                    ambiguous = True
                for invoice in reference_invoices:
                    matched_invoices[invoice.invoice_id] = invoice

            if ambiguous or len(matched_invoices) > 1:
                result.skipped_ambiguous_reference += 1
            elif len(matched_invoices) == 1:
                candidates[line.line_id] = next(iter(matched_invoices.values()))
            else:
                result.skipped_no_invoice_reference += 1

        invoice_occurrences = Counter(
            invoice.invoice_id for invoice in candidates.values()
        )
        payment_occurrences = Counter(
            (candidates[line.line_id].invoice_id, line.transaction_date, line.amount)
            for line in eligible_lines
            if line.line_id in candidates
        )

        for line in eligible_lines:
            candidate_invoice = candidates.get(line.line_id)
            if candidate_invoice is None:
                continue
            paid = candidate_invoice.status == SupplierInvoiceStatus.PAID
            duplicate = (
                payment_occurrences[
                    (candidate_invoice.invoice_id, line.transaction_date, line.amount)
                ]
                if paid
                else invoice_occurrences[candidate_invoice.invoice_id]
            )
            if duplicate > 1:
                result.skipped_duplicate_invoice += 1
                continue
            if not paid and Decimal(line.amount) != Decimal(
                candidate_invoice.balance_due
            ):
                result.skipped_amount += 1
                continue
            if (candidate_invoice.currency_code or "").upper() != (
                account.currency_code or ""
            ).upper():
                result.skipped_currency += 1
                continue
            if not paid and Decimal(
                candidate_invoice.withholding_tax_amount or 0
            ) != Decimal("0"):
                result.skipped_withholding_tax += 1
                continue

            try:
                with db.begin_nested():
                    payment, created_payment = self._resolve_payment(
                        db,
                        organization_id,
                        account,
                        line,
                        candidate_invoice,
                        actor_user_id,
                    )
                    journal_line = self._bank_journal_line(
                        db,
                        organization_id,
                        account,
                        line,
                        payment,
                    )

                    consumed_match = db.scalar(
                        select(BankStatementLineMatch.match_id)
                        .where(
                            BankStatementLineMatch.journal_line_id
                            == journal_line.line_id,
                            or_(
                                BankStatementLineMatch.match_state.is_(None),
                                BankStatementLineMatch.match_state != "suggested",
                            ),
                        )
                        .limit(1)
                    )
                    if consumed_match is not None:
                        raise _SkipCandidate("consumed_journal_line")

                    matched_line = bank_reconciliation_service.match_statement_line(
                        db,
                        organization_id,
                        line.line_id,
                        journal_line.line_id,
                        matched_by=actor_user_id,
                        force_match=False,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=payment.payment_id,
                        match_state="confirmed",
                    )
                    if not matched_line.is_matched:
                        raise _SkipCandidate("journal")
                    corrected_bank = payment.bank_account_id != account.bank_account_id
                    if corrected_bank:
                        # Repair a uniquely resolved legacy GL reference atomically
                        # with the match; never rewrite posted payment journals.
                        payment.bank_account_id = account.bank_account_id
                    if payment.status == APPaymentStatus.SENT:
                        SupplierPaymentService.mark_cleared(
                            db,
                            organization_id,
                            payment.payment_id,
                            line.transaction_date,
                        )
                    rule_service.log_match(
                        organization_id,
                        rule_id=rule.rule_id,
                        line_id=line.line_id,
                        source_doc_type=SourceDocType.SUPPLIER_PAYMENT.value,
                        source_doc_id=payment.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=100,
                        explanation=(
                            "Exact AP invoice reference "
                            f"{candidate_invoice.invoice_number}, "
                            "posted payment amount, bank, date, and currency"
                            + (
                                "; legacy bank GL reference corrected from "
                                f"{account.gl_account_id} to {account.bank_account_id}"
                                if corrected_bank
                                else ""
                            )
                        ),
                        action="MATCH",
                    )
                    result.matched += 1
                    if created_payment:
                        result.payments_created += 1
                    else:
                        result.payments_reused += 1
                    if corrected_bank:
                        result.bank_accounts_corrected += 1
            except _SkipCandidate as exc:
                if exc.reason == "consumed_journal_line":
                    result.skipped_consumed_journal_line += 1
                elif exc.reason == "journal":
                    result.skipped_journal += 1
                elif exc.reason == "amount":
                    result.skipped_amount += 1
                elif exc.reason == "currency":
                    result.skipped_currency += 1
                elif exc.reason == "withholding_tax":
                    result.skipped_withholding_tax += 1
                else:
                    result.skipped_payment += 1
            except Exception:
                result.failed += 1
                logger.exception(
                    "AP invoice auto-match failed for statement line %s",
                    line.line_id,
                )

        return result

    @staticmethod
    def _resolve_payment(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
        line: BankStatementLine,
        invoice: SupplierInvoice,
        actor_user_id: UUID,
    ) -> tuple[SupplierPayment, bool]:
        # Serialize against AP writers and re-read status/balance under the lock.
        # A concurrently settled invoice must never create another payment.
        db.refresh(invoice, with_for_update=True)
        if invoice.organization_id != organization_id or invoice.status not in {
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.PAID,
        }:
            raise _SkipCandidate("payment")
        if (invoice.currency_code or "").upper() != (
            account.currency_code or ""
        ).upper():
            raise _SkipCandidate("currency")
        if invoice.journal_entry_id is None:
            raise _SkipCandidate("journal")
        correlation_id = f"bank-ap:{invoice.invoice_id}:{line.line_id}"
        active_payments = list(
            db.scalars(
                select(SupplierPayment)
                .join(
                    APPaymentAllocation,
                    APPaymentAllocation.payment_id == SupplierPayment.payment_id,
                )
                .where(
                    SupplierPayment.organization_id == organization_id,
                    APPaymentAllocation.invoice_id == invoice.invoice_id,
                    SupplierPayment.status.notin_(
                        [APPaymentStatus.VOID, APPaymentStatus.REJECTED]
                    ),
                )
                .with_for_update(of=SupplierPayment)
            ).all()
        )

        legacy_bank_allowed = False
        if any(
            payment.bank_account_id == account.gl_account_id
            and payment.bank_account_id != account.bank_account_id
            for payment in active_payments
        ):
            legacy_bank_allowed = (
                APInvoiceAutoMatchService._legacy_bank_reference_allowed(
                    db, organization_id, account
                )
            )

        exact_posted = [
            payment
            for payment in active_payments
            if payment.organization_id == organization_id
            and (
                payment.bank_account_id == account.bank_account_id
                or (
                    legacy_bank_allowed
                    and payment.bank_account_id == account.gl_account_id
                )
            )
            and payment.payment_date == line.transaction_date
            and Decimal(payment.amount) == Decimal(line.amount)
            and payment.currency_code.upper() == invoice.currency_code.upper()
            and payment.journal_entry_id is not None
            and payment.status in {APPaymentStatus.SENT, APPaymentStatus.CLEARED}
        ]
        if len(exact_posted) > 1:
            raise _SkipCandidate("payment")
        if len(exact_posted) == 1:
            return exact_posted[0], False

        if invoice.status == SupplierInvoiceStatus.PAID:
            raise _SkipCandidate("payment")
        if Decimal(invoice.balance_due) != Decimal(line.amount):
            raise _SkipCandidate("amount")
        if Decimal(invoice.withholding_tax_amount or 0) != Decimal("0"):
            raise _SkipCandidate("withholding_tax")

        automation_payments = [
            payment
            for payment in active_payments
            if payment.correlation_id == correlation_id
        ]
        if len(automation_payments) > 1:
            raise _SkipCandidate("payment")
        if automation_payments:
            payment = automation_payments[0]
            if (
                payment.bank_account_id != account.bank_account_id
                or payment.payment_date != line.transaction_date
                or Decimal(payment.amount) != Decimal(line.amount)
                or payment.currency_code.upper() != invoice.currency_code.upper()
            ):
                raise _SkipCandidate("payment")
            created_payment = False
        try:
            if not automation_payments:
                if active_payments:
                    raise _SkipCandidate("payment")
                payment = SupplierPaymentService.create_payment(
                    db,
                    organization_id,
                    SupplierPaymentInput(
                        supplier_id=invoice.supplier_id,
                        payment_date=line.transaction_date,
                        payment_method=APPaymentMethod.BANK_TRANSFER,
                        currency_code=invoice.currency_code,
                        amount=Decimal(invoice.balance_due),
                        bank_account_id=account.bank_account_id,
                        allocations=[
                            PaymentAllocationInput(
                                invoice_id=invoice.invoice_id,
                                amount=Decimal(invoice.balance_due),
                            )
                        ],
                        exchange_rate=invoice.exchange_rate,
                        reference=(
                            line.reference
                            or line.bank_reference
                            or invoice.invoice_number
                        ),
                        correlation_id=correlation_id,
                    ),
                    actor_user_id,
                    auto_commit=False,
                )
                created_payment = True
            if payment.status in {APPaymentStatus.DRAFT, APPaymentStatus.PENDING}:
                payment = SupplierPaymentService.approve_payment(
                    db,
                    organization_id,
                    payment.payment_id,
                    actor_user_id,
                )
            if payment.status == APPaymentStatus.APPROVED:
                payment = SupplierPaymentService.post_payment(
                    db,
                    organization_id,
                    payment.payment_id,
                    actor_user_id,
                    posting_date=line.transaction_date,
                )
        except (NotFoundError, ValidationError) as exc:
            logger.info(
                "AP payment workflow rejected invoice %s: %s",
                invoice.invoice_id,
                exc,
            )
            raise _SkipCandidate("payment") from exc

        if payment.status not in {APPaymentStatus.SENT, APPaymentStatus.CLEARED}:
            raise _SkipCandidate("payment")
        if payment.journal_entry_id is None:
            raise _SkipCandidate("journal")
        return payment, created_payment

    @staticmethod
    def _legacy_bank_reference_allowed(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
    ) -> bool:
        """Accept a legacy GL UUID only when it identifies exactly one bank.

        Never reinterpret another physical bank's UUID, or choose arbitrarily
        between active/closed bank accounts sharing the same GL account.
        """
        if account.organization_id != organization_id:
            return False
        if db.get(BankAccount, account.gl_account_id) is not None:
            return False
        bank_ids = list(
            db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.gl_account_id == account.gl_account_id,
                )
            ).all()
        )
        return bank_ids == [account.bank_account_id]

    @staticmethod
    def _bank_journal_line(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
        line: BankStatementLine,
        payment: SupplierPayment,
    ) -> JournalEntryLine:
        journal = db.get(JournalEntry, payment.journal_entry_id)
        if (
            not journal
            or journal.organization_id != organization_id
            or journal.status != JournalStatus.POSTED
            or journal.source_document_type != "SUPPLIER_PAYMENT"
            or journal.source_document_id != payment.payment_id
            or (journal.currency_code or "").upper()
            != (account.currency_code or "").upper()
        ):
            raise _SkipCandidate("journal")

        bank_lines = list(
            db.scalars(
                select(JournalEntryLine)
                .where(
                    JournalEntryLine.journal_entry_id == payment.journal_entry_id,
                    JournalEntryLine.account_id == account.gl_account_id,
                    JournalEntryLine.credit_amount == line.amount,
                    JournalEntryLine.debit_amount == Decimal("0"),
                )
                .with_for_update(of=JournalEntryLine)
            ).all()
        )
        if len(bank_lines) != 1:
            raise _SkipCandidate("journal")
        return bank_lines[0]


ap_invoice_auto_match_service = APInvoiceAutoMatchService()


__all__ = [
    "APInvoiceAutoMatchError",
    "APInvoiceAutoMatchResult",
    "APInvoiceAutoMatchService",
    "ap_invoice_auto_match_service",
]
