"""Deterministic reconciliation of Paystack customer collections."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus
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
from app.models.finance.gl.account import Account, AccountType
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.services.common import NotFoundError, ValidationError
from app.services.finance.ar.customer_payment import CustomerPaymentService
from app.services.finance.banking.bank_reconciliation import (
    bank_reconciliation_service,
)
from app.services.finance.banking.reconciliation_rule_service import (
    ReconciliationRuleService,
)
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

logger = logging.getLogger(__name__)

_REFERENCE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=/\-]{2,}")
_NON_ALNUM = re.compile(r"[^a-z0-9]")
_FEE_SOURCE_TYPE = "PAYSTACK_COLLECTION_FEE"


class PaystackCustomerAutoMatchError(ValueError):
    """Raised when Paystack customer automation cannot safely run."""


class _SkipCandidate(RuntimeError):
    """Roll back one candidate savepoint without failing the batch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class PaystackCustomerAutoMatchResult:
    """Auditable counters returned by a Paystack customer match run."""

    scanned: int = 0
    matched: int = 0
    fees_posted: int = 0
    skipped_rule_condition: int = 0
    skipped_no_intent_reference: int = 0
    skipped_ambiguous_reference: int = 0
    skipped_duplicate_intent: int = 0
    skipped_transaction_id: int = 0
    skipped_amount: int = 0
    skipped_currency: int = 0
    skipped_payment: int = 0
    skipped_fee_account: int = 0
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
    searchable_values = (
        line.reference,
        line.bank_reference,
        line.description,
    )
    keys = {_normalize_reference(value) for value in searchable_values[:2] if value}
    for value in searchable_values:
        for token in _REFERENCE_TOKEN.findall(value or ""):
            normalized = _normalize_reference(token)
            if any(character.isdigit() for character in normalized):
                keys.add(normalized)
    return {key for key in keys if len(key) >= 4}


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise _SkipCandidate("amount") from exc
    if not amount.is_finite() or amount < Decimal("0"):
        raise _SkipCandidate("amount")
    return amount


class PaystackCustomerAutoMatchService:
    """Post missing receipt/fee effects and confirm exact Paystack matches."""

    @staticmethod
    def validate_configuration(
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
    ) -> tuple[BankAccount, ReconciliationMatchRule]:
        account = db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise PaystackCustomerAutoMatchError("Bank account not found")
        if account.status != BankAccountStatus.active:
            raise PaystackCustomerAutoMatchError(
                "The selected bank account is not active"
            )
        if not account.gl_account_id:
            raise PaystackCustomerAutoMatchError(
                "The selected bank account has no GL account"
            )

        rule = db.get(ReconciliationMatchRule, rule_id)
        if not rule or rule.organization_id != organization_id:
            raise PaystackCustomerAutoMatchError("Automation rule not found")
        if rule.bank_account_id != bank_account_id:
            raise PaystackCustomerAutoMatchError(
                "The automation rule is not assigned to this bank account"
            )
        if not rule.is_active:
            raise PaystackCustomerAutoMatchError("The automation rule is disabled")
        if rule.source_doc_type != SourceDocType.PAYMENT_INTENT.value:
            raise PaystackCustomerAutoMatchError(
                "The selected rule does not match gateway payment intents"
            )
        if rule.action_type != "MATCH" or not rule.match_credit:
            raise PaystackCustomerAutoMatchError(
                "Paystack customer automation requires a credit MATCH rule"
            )
        return account, rule

    def run(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
        actor_user_id: UUID,
    ) -> PaystackCustomerAutoMatchResult:
        account, rule = self.validate_configuration(
            db,
            organization_id,
            bank_account_id,
            rule_id,
        )
        rule_service = ReconciliationRuleService(db)
        result = PaystackCustomerAutoMatchResult()

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
                    BankStatementLine.transaction_type == StatementLineType.credit,
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

        intents = list(
            db.scalars(
                select(PaymentIntent)
                .where(
                    PaymentIntent.organization_id == organization_id,
                    PaymentIntent.bank_account_id == bank_account_id,
                    PaymentIntent.direction == PaymentDirection.INBOUND,
                    PaymentIntent.status == PaymentIntentStatus.COMPLETED,
                    PaymentIntent.source_type == "INVOICE",
                    PaymentIntent.customer_payment_id.is_not(None),
                )
                .with_for_update(of=PaymentIntent)
            ).all()
        )
        intents_by_reference: dict[str, list[PaymentIntent]] = {}
        for intent in intents:
            key = _normalize_reference(intent.paystack_reference)
            if len(key) >= 4:
                intents_by_reference.setdefault(key, []).append(intent)

        candidates: dict[UUID, PaymentIntent] = {}
        for line in eligible_lines:
            matched_intents: dict[UUID, PaymentIntent] = {}
            ambiguous = False
            for key in _statement_reference_keys(line):
                reference_intents = intents_by_reference.get(key, [])
                if len({intent.intent_id for intent in reference_intents}) > 1:
                    ambiguous = True
                for intent in reference_intents:
                    matched_intents[intent.intent_id] = intent
            if ambiguous or len(matched_intents) > 1:
                result.skipped_ambiguous_reference += 1
            elif len(matched_intents) == 1:
                candidates[line.line_id] = next(iter(matched_intents.values()))
            else:
                result.skipped_no_intent_reference += 1

        intent_occurrences = Counter(intent.intent_id for intent in candidates.values())

        for line in eligible_lines:
            intent = candidates.get(line.line_id)
            if intent is None:
                continue
            if intent_occurrences[intent.intent_id] > 1:
                result.skipped_duplicate_intent += 1
                continue
            if (
                line.transaction_id
                and intent.paystack_transaction_id
                and str(line.transaction_id) != str(intent.paystack_transaction_id)
            ):
                result.skipped_transaction_id += 1
                continue

            raw_data = line.raw_data if isinstance(line.raw_data, dict) else {}
            try:
                gross_amount = _money(raw_data.get("gross_amount", line.amount))
                fee_amount = _money(raw_data.get("fees", 0))
            except _SkipCandidate:
                result.skipped_amount += 1
                continue

            if (
                Decimal(line.amount) + fee_amount != gross_amount
                or Decimal(intent.amount) != gross_amount
            ):
                result.skipped_amount += 1
                continue
            if (intent.currency_code or "").upper() != (
                account.currency_code or ""
            ).upper():
                result.skipped_currency += 1
                continue

            try:
                with db.begin_nested():
                    payment = self._resolve_payment(
                        db,
                        organization_id,
                        account,
                        line,
                        intent,
                        gross_amount,
                        actor_user_id,
                    )
                    receipt_line = self._receipt_bank_line(
                        db,
                        organization_id,
                        account,
                        payment,
                        gross_amount,
                    )
                    journal_lines = [receipt_line]
                    fee_created = False
                    if fee_amount > Decimal("0"):
                        fee_line, fee_created = self._resolve_fee_bank_line(
                            db,
                            organization_id,
                            account,
                            rule,
                            line,
                            intent,
                            fee_amount,
                            actor_user_id,
                        )
                        journal_lines.append(fee_line)

                    self._require_unconsumed_journal_lines(
                        db,
                        [journal_line.line_id for journal_line in journal_lines],
                    )
                    if len(journal_lines) == 1:
                        bank_reconciliation_service.match_statement_line(
                            db,
                            organization_id,
                            line.line_id,
                            receipt_line.line_id,
                            matched_by=actor_user_id,
                            force_match=False,
                            source_type=SourceDocType.PAYMENT_INTENT.value,
                            source_id=intent.intent_id,
                            match_state="confirmed",
                        )
                    else:
                        bank_reconciliation_service.multi_match_statement_line(
                            db,
                            organization_id,
                            line.line_id,
                            [journal_line.line_id for journal_line in journal_lines],
                            matched_by=actor_user_id,
                            force_match=False,
                        )

                    rule_service.log_match(
                        organization_id,
                        rule_id=rule.rule_id,
                        line_id=line.line_id,
                        source_doc_type=SourceDocType.PAYMENT_INTENT.value,
                        source_doc_id=intent.intent_id,
                        journal_line_id=receipt_line.line_id,
                        confidence=100,
                        explanation=(
                            f"Exact Paystack reference {intent.paystack_reference}, "
                            "transaction amount, fee, and currency"
                        ),
                        action="MATCH",
                    )
                    result.matched += 1
                    if fee_created:
                        result.fees_posted += 1
            except _SkipCandidate as exc:
                if exc.reason == "amount":
                    result.skipped_amount += 1
                elif exc.reason == "fee_account":
                    result.skipped_fee_account += 1
                elif exc.reason == "consumed_journal_line":
                    result.skipped_consumed_journal_line += 1
                elif exc.reason == "journal":
                    result.skipped_journal += 1
                else:
                    result.skipped_payment += 1
            except Exception:
                result.failed += 1
                logger.exception(
                    "Paystack customer auto-match failed for statement line %s",
                    line.line_id,
                )

        return result

    @staticmethod
    def _resolve_payment(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
        line: BankStatementLine,
        intent: PaymentIntent,
        gross_amount: Decimal,
        actor_user_id: UUID,
    ) -> CustomerPayment:
        if intent.customer_payment_id is None:
            raise _SkipCandidate("payment")
        payment = db.get(CustomerPayment, intent.customer_payment_id)
        if (
            not payment
            or payment.organization_id != organization_id
            or payment.bank_account_id != account.bank_account_id
            or Decimal(payment.amount) != gross_amount
            or Decimal(payment.gross_amount) != gross_amount
            or Decimal(payment.wht_amount) != Decimal("0")
            or (payment.currency_code or "").upper()
            != (intent.currency_code or "").upper()
            or _normalize_reference(payment.reference)
            != _normalize_reference(intent.paystack_reference)
            or payment.correlation_id != str(intent.intent_id)
        ):
            raise _SkipCandidate("payment")
        if intent.paid_at and payment.payment_date != intent.paid_at.date():
            raise _SkipCandidate("payment")
        if payment.status == PaymentStatus.PENDING:
            try:
                payment = CustomerPaymentService.post_payment(
                    db,
                    organization_id,
                    payment.payment_id,
                    actor_user_id,
                    posting_date=line.transaction_date,
                )
            except (NotFoundError, ValidationError) as exc:
                raise _SkipCandidate("payment") from exc
        if payment.status != PaymentStatus.CLEARED or payment.journal_entry_id is None:
            raise _SkipCandidate("payment")
        return payment

    @staticmethod
    def _receipt_bank_line(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
        payment: CustomerPayment,
        gross_amount: Decimal,
    ) -> JournalEntryLine:
        journal = db.get(JournalEntry, payment.journal_entry_id)
        if (
            not journal
            or journal.organization_id != organization_id
            or journal.status != JournalStatus.POSTED
            or journal.source_document_type != "CUSTOMER_PAYMENT"
            or journal.source_document_id != payment.payment_id
        ):
            raise _SkipCandidate("journal")
        lines = list(
            db.scalars(
                select(JournalEntryLine).where(
                    JournalEntryLine.journal_entry_id == payment.journal_entry_id,
                    JournalEntryLine.account_id == account.gl_account_id,
                    JournalEntryLine.debit_amount == gross_amount,
                    JournalEntryLine.credit_amount == Decimal("0"),
                )
            ).all()
        )
        if len(lines) != 1:
            raise _SkipCandidate("journal")
        return lines[0]

    @staticmethod
    def _resolve_fee_bank_line(
        db: Session,
        organization_id: UUID,
        account: BankAccount,
        rule: ReconciliationMatchRule,
        line: BankStatementLine,
        intent: PaymentIntent,
        fee_amount: Decimal,
        actor_user_id: UUID,
    ) -> tuple[JournalEntryLine, bool]:
        if rule.writeoff_account_id is None:
            raise _SkipCandidate("fee_account")
        fee_account = db.get(Account, rule.writeoff_account_id)
        if (
            not fee_account
            or fee_account.organization_id != organization_id
            or not fee_account.is_active
            or not fee_account.is_posting_allowed
            or fee_account.account_type != AccountType.POSTING
        ):
            raise _SkipCandidate("fee_account")

        correlation_id = f"paystack-collection-fee:{intent.intent_id}"
        journals = list(
            db.scalars(
                select(JournalEntry).where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.source_document_type == _FEE_SOURCE_TYPE,
                    JournalEntry.source_document_id == intent.intent_id,
                )
            ).all()
        )
        if len(journals) > 1:
            raise _SkipCandidate("journal")
        created = False
        if journals:
            journal = journals[0]
            if (
                journal.status != JournalStatus.POSTED
                or journal.correlation_id != correlation_id
                or (journal.currency_code or "").upper()
                != (intent.currency_code or "").upper()
            ):
                raise _SkipCandidate("journal")
        else:
            description = f"Paystack collection fee - {intent.paystack_reference}"
            journal_input = JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=line.transaction_date,
                posting_date=line.transaction_date,
                description=description,
                reference=f"FEE-{intent.paystack_reference}",
                currency_code=intent.currency_code,
                exchange_rate=Decimal("1"),
                source_module="PAYMENTS",
                source_document_type=_FEE_SOURCE_TYPE,
                source_document_id=intent.intent_id,
                correlation_id=correlation_id,
                lines=[
                    JournalLineInput(
                        account_id=fee_account.account_id,
                        debit_amount=fee_amount,
                        description=description,
                    ),
                    JournalLineInput(
                        account_id=account.gl_account_id,
                        credit_amount=fee_amount,
                        description=description,
                    ),
                ],
            )
            journal, posting_result = (
                BasePostingAdapter.create_approve_and_post_journal(
                    db,
                    organization_id,
                    journal_input,
                    actor_user_id,
                    posting_date=line.transaction_date,
                    idempotency_key=f"{organization_id}:PAYSTACK_COLLECTION_FEE:{intent.intent_id}:v1",
                    source_module="PAYMENTS",
                    correlation_id=correlation_id,
                    success_message="Paystack collection fee posted",
                    creation_error_prefix="Paystack fee journal creation failed",
                    ledger_error_prefix="Paystack fee journal posting failed",
                )
            )
            if not posting_result.success or journal is None:
                raise _SkipCandidate("journal")
            created = True

        journal_lines = list(
            db.scalars(
                select(JournalEntryLine).where(
                    JournalEntryLine.journal_entry_id == journal.journal_entry_id,
                )
            ).all()
        )
        bank_lines = [
            journal_line
            for journal_line in journal_lines
            if journal_line.account_id == account.gl_account_id
            and Decimal(journal_line.credit_amount) == fee_amount
            and Decimal(journal_line.debit_amount) == Decimal("0")
        ]
        expense_lines = [
            journal_line
            for journal_line in journal_lines
            if journal_line.account_id == fee_account.account_id
            and Decimal(journal_line.debit_amount) == fee_amount
            and Decimal(journal_line.credit_amount) == Decimal("0")
        ]
        if len(journal_lines) != 2 or len(bank_lines) != 1 or len(expense_lines) != 1:
            raise _SkipCandidate("journal")
        return bank_lines[0], created

    @staticmethod
    def _require_unconsumed_journal_lines(
        db: Session,
        journal_line_ids: list[UUID],
    ) -> None:
        consumed = db.scalar(
            select(BankStatementLineMatch.match_id)
            .where(
                BankStatementLineMatch.journal_line_id.in_(journal_line_ids),
                or_(
                    BankStatementLineMatch.match_state.is_(None),
                    BankStatementLineMatch.match_state != "suggested",
                ),
            )
            .limit(1)
        )
        if consumed is not None:
            raise _SkipCandidate("consumed_journal_line")


paystack_customer_auto_match_service = PaystackCustomerAutoMatchService()


__all__ = [
    "PaystackCustomerAutoMatchError",
    "PaystackCustomerAutoMatchResult",
    "PaystackCustomerAutoMatchService",
    "paystack_customer_auto_match_service",
]
