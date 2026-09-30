"""Deterministic matching of paid Paystack expenses to bank statement lines."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
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
from app.services.expense.expense_posting_adapter import ExpensePostingAdapter
from app.services.finance.banking.bank_reconciliation import (
    bank_reconciliation_service,
)
from app.services.finance.banking.reconciliation_rule_service import (
    ReconciliationRuleService,
)

logger = logging.getLogger(__name__)

_REFERENCE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=/\-]{5,}")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


class PaystackExpenseAutoMatchError(ValueError):
    """Raised when the focused auto-match action cannot safely run."""


class _SkipCandidate(RuntimeError):
    """Roll back one candidate savepoint without failing the batch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class PaystackExpenseAutoMatchResult:
    """Auditable counters returned by one focused auto-match run."""

    scanned: int = 0
    matched: int = 0
    journals_created: int = 0
    skipped_no_reference: int = 0
    skipped_rule_condition: int = 0
    skipped_ambiguous_reference: int = 0
    skipped_duplicate_claim: int = 0
    skipped_amount: int = 0
    skipped_date: int = 0
    skipped_journal: int = 0
    skipped_consumed_journal_line: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _normalize_reference(value: str | None) -> str:
    """Return a comparison key without weakening equality to fuzzy matching."""
    if not value:
        return ""
    return _NON_ALNUM.sub("", value.casefold())


def _statement_reference_keys(line: BankStatementLine) -> set[str]:
    """Extract exact reference fields and complete tokens from narration."""
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
    return {key for key in keys if len(key) >= 6}


class PaystackExpenseAutoMatchService:
    """Safely post and match exact paid-expense statement transactions."""

    @staticmethod
    def validate_configuration(
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
    ) -> tuple[BankAccount, ReconciliationMatchRule]:
        account = db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise PaystackExpenseAutoMatchError("Bank account not found")
        if account.status != BankAccountStatus.active:
            raise PaystackExpenseAutoMatchError(
                "The selected bank account is not active"
            )
        if not account.gl_account_id:
            raise PaystackExpenseAutoMatchError(
                "The selected bank account has no GL account"
            )

        rule = db.get(ReconciliationMatchRule, rule_id)
        if not rule or rule.organization_id != organization_id:
            raise PaystackExpenseAutoMatchError("Automation rule not found")
        if rule.bank_account_id != bank_account_id:
            raise PaystackExpenseAutoMatchError(
                "The automation rule is not assigned to this bank account"
            )
        if not rule.is_active:
            raise PaystackExpenseAutoMatchError("The automation rule is disabled")
        if rule.source_doc_type != SourceDocType.EXPENSE.value:
            raise PaystackExpenseAutoMatchError(
                "The selected rule does not match expenses"
            )
        if rule.action_type != "MATCH" or not rule.match_debit:
            raise PaystackExpenseAutoMatchError(
                "Expense automation requires a debit MATCH rule"
            )
        return account, rule

    def run(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        rule_id: UUID,
        actor_user_id: UUID,
    ) -> PaystackExpenseAutoMatchResult:
        account, rule = self.validate_configuration(
            db,
            organization_id,
            bank_account_id,
            rule_id,
        )
        rule_service = ReconciliationRuleService(db)
        result = PaystackExpenseAutoMatchResult()

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

        claims = list(
            db.scalars(
                select(ExpenseClaim).where(
                    ExpenseClaim.organization_id == organization_id,
                    ExpenseClaim.status == ExpenseClaimStatus.PAID,
                    ExpenseClaim.payment_reference.is_not(None),
                )
            ).all()
        )

        claims_by_reference: dict[str, list[ExpenseClaim]] = {}
        for claim in claims:
            key = _normalize_reference(claim.payment_reference)
            if len(key) >= 6:
                claims_by_reference.setdefault(key, []).append(claim)

        candidates: dict[UUID, ExpenseClaim] = {}
        for line in eligible_lines:
            matched_claims: dict[UUID, ExpenseClaim] = {}
            ambiguous = False
            for key in _statement_reference_keys(line):
                reference_claims = claims_by_reference.get(key, [])
                if len(reference_claims) > 1:
                    ambiguous = True
                for claim in reference_claims:
                    matched_claims[claim.claim_id] = claim

            if ambiguous or len(matched_claims) > 1:
                result.skipped_ambiguous_reference += 1
            elif len(matched_claims) == 1:
                candidates[line.line_id] = next(iter(matched_claims.values()))
            else:
                result.skipped_no_reference += 1

        claim_occurrences = Counter(claim.claim_id for claim in candidates.values())

        for line in eligible_lines:
            candidate_claim = candidates.get(line.line_id)
            if candidate_claim is None:
                continue
            if claim_occurrences[candidate_claim.claim_id] > 1:
                result.skipped_duplicate_claim += 1
                continue
            if Decimal(line.amount) != Decimal(candidate_claim.net_payable_amount or 0):
                result.skipped_amount += 1
                continue
            if (
                candidate_claim.paid_on is None
                or line.transaction_date != candidate_claim.paid_on
            ):
                result.skipped_date += 1
                continue

            try:
                with db.begin_nested():
                    created_journal = candidate_claim.reimbursement_journal_id is None
                    journal_id = candidate_claim.reimbursement_journal_id
                    if journal_id is None:
                        posting = ExpensePostingAdapter.post_expense_reimbursement(
                            db,
                            organization_id,
                            candidate_claim.claim_id,
                            candidate_claim.paid_on,
                            actor_user_id,
                            bank_account_id=bank_account_id,
                            payment_reference=candidate_claim.payment_reference,
                            idempotency_key=(
                                f"{organization_id}:EXP:REIMB:"
                                f"{candidate_claim.claim_id}:post:v1"
                            ),
                            correlation_id=(
                                f"expense-reimbursement:{candidate_claim.claim_id}"
                            ),
                        )
                        if not posting.success or posting.journal_entry_id is None:
                            raise _SkipCandidate("journal")
                        journal_id = posting.journal_entry_id

                    journal = db.get(JournalEntry, journal_id)
                    if (
                        not journal
                        or journal.organization_id != organization_id
                        or journal.status != JournalStatus.POSTED
                        or journal.source_document_type != "EXPENSE_REIMBURSEMENT"
                        or journal.source_document_id != candidate_claim.claim_id
                    ):
                        raise _SkipCandidate("journal")

                    bank_lines = list(
                        db.scalars(
                            select(JournalEntryLine).where(
                                JournalEntryLine.journal_entry_id == journal_id,
                                JournalEntryLine.account_id == account.gl_account_id,
                                JournalEntryLine.credit_amount == line.amount,
                                JournalEntryLine.debit_amount == Decimal("0"),
                            )
                        ).all()
                    )
                    if len(bank_lines) != 1:
                        raise _SkipCandidate("journal")

                    bank_line = bank_lines[0]
                    consumed_match = db.scalar(
                        select(BankStatementLineMatch.match_id)
                        .where(
                            BankStatementLineMatch.journal_line_id == bank_line.line_id,
                            or_(
                                BankStatementLineMatch.match_state.is_(None),
                                BankStatementLineMatch.match_state != "suggested",
                            ),
                        )
                        .limit(1)
                    )
                    if consumed_match is not None:
                        raise _SkipCandidate("consumed_journal_line")

                    bank_reconciliation_service.match_statement_line(
                        db,
                        organization_id,
                        line.line_id,
                        bank_line.line_id,
                        matched_by=actor_user_id,
                        force_match=False,
                        source_type="EXPENSE_REIMBURSEMENT",
                        source_id=candidate_claim.claim_id,
                        match_state="confirmed",
                    )
                    rule_service.log_match(
                        organization_id,
                        rule_id=rule.rule_id,
                        line_id=line.line_id,
                        source_doc_type=SourceDocType.EXPENSE.value,
                        source_doc_id=candidate_claim.claim_id,
                        journal_line_id=bank_line.line_id,
                        confidence=100,
                        explanation=(
                            "Exact expense payment reference, amount, and paid date"
                        ),
                        action="MATCH",
                    )
                    result.matched += 1
                    if created_journal:
                        result.journals_created += 1
            except _SkipCandidate as exc:
                if exc.reason == "consumed_journal_line":
                    result.skipped_consumed_journal_line += 1
                else:
                    result.skipped_journal += 1
            except Exception:
                result.failed += 1
                logger.exception(
                    "Paystack expense auto-match failed for statement line %s",
                    line.line_id,
                )

        return result


paystack_expense_auto_match_service = PaystackExpenseAutoMatchService()


__all__ = [
    "PaystackExpenseAutoMatchError",
    "PaystackExpenseAutoMatchResult",
    "PaystackExpenseAutoMatchService",
    "paystack_expense_auto_match_service",
]
