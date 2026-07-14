"""Specialized programmatic reconciliation strategies."""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts.base import (
    Any,
    BankAccount,
    BankStatement,
    BankStatementLine,
    Decimal,
    MatchStrategy,
    ReconciliationRunContext,
    UUID,
    dataclass,
    re,
    select,
)
from app.services.finance.banking.programmatic_parts.helpers import _perform_match


@dataclass(frozen=True)
class BankFeeStrategy(MatchStrategy):
    strategy_id: str = "fee_classification"
    provider_key: str = "bank_fee"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type("bank_fee")
            or not ctx.policy.allows_provider(self.provider_key)
        ):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        account_code = ctx.policy.gl_mappings.get(
            "fee_expense_account_code",
            ctx.config.finance_cost_account_code if ctx.config else "6080",
        )
        finance_cost_account = ctx.db.scalar(
            select(Account).where(
                Account.organization_id == ctx.organization_id,
                Account.account_code == account_code,
            )
        )
        if not finance_cost_account:
            return

        fee_lines = [
            line
            for line in still_unmatched
            if line.description
            and any(
                keyword in line.description.lower()
                for keyword in ctx.policy.fee_keywords
            )
        ]

        for line in fee_lines:
            try:
                amount = abs(line.amount)
                correlation_id = f"bank-fee-{line.line_id}"
                journal_input = JournalInput(
                    journal_type=JournalType.STANDARD,
                    entry_date=line.transaction_date,
                    posting_date=line.transaction_date,
                    description=f"Bank charge - {line.description}",
                    reference=line.reference,
                    source_module="BANKING",
                    source_document_type="BANK_FEE",
                    correlation_id=correlation_id,
                    lines=[
                        JournalLineInput(
                            account_id=finance_cost_account.account_id,
                            debit_amount=amount,
                            description=line.description,
                        ),
                        JournalLineInput(
                            account_id=ctx.bank_account.gl_account_id,
                            credit_amount=amount,
                            description=line.description,
                        ),
                    ],
                )
                journal, create_error = BasePostingAdapter.create_and_approve_journal(
                    ctx.db,
                    ctx.organization_id,
                    journal_input,
                    service.SYSTEM_USER_ID,
                    error_prefix="Fee journal creation failed",
                )
                if create_error:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {create_error.message}"
                    )
                    continue

                idempotency_key = BasePostingAdapter.make_idempotency_key(
                    ctx.organization_id,
                    "BANKING",
                    line.line_id,
                    action="bank-fee",
                )
                posting_result = service._post_with_period_fallback(
                    ctx.db,
                    organization_id=ctx.organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    posting_date=line.transaction_date,
                    idempotency_key=idempotency_key,
                    source_module="BANKING",
                    correlation_id=correlation_id,
                    posted_by_user_id=service.SYSTEM_USER_ID,
                    success_message="Bank fee posted",
                    error_prefix="Fee journal posting failed",
                )
                if not posting_result.success:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {posting_result.message}"
                    )
                    continue

                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                )
            except Exception as exc:
                service.logger.exception(
                    "Error matching fee line %s: %s", line.line_id, exc
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class InterbankCounterpartStrategy(MatchStrategy):
    strategy_id: str = "counterpart_transfer"
    provider_key: str = "bank_transfer"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type("interbank_transfer")
            or not ctx.policy.allows_provider(self.provider_key)
        ):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        from datetime import timedelta

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        window_days = ctx.policy.settlement_window_days
        date_window = timedelta(days=window_days)
        settlement_lines = [
            line
            for line in still_unmatched
            if line.description
            and any(
                keyword in line.description.lower()
                for keyword in ctx.policy.transfer_keywords
            )
            and not any(
                keyword in line.description.lower()
                for keyword in ctx.policy.fee_keywords
            )
        ]
        if not settlement_lines:
            return

        dedup_groups: dict[tuple[object, str | None, int], list[BankStatementLine]] = {}
        unique_settlements: list[BankStatementLine] = []
        for line in settlement_lines:
            key = (line.transaction_date, line.reference, int(line.amount * 100))
            group = dedup_groups.setdefault(key, [])
            group.append(line)
            if len(group) == 1:
                unique_settlements.append(line)

        min_date = min(line.transaction_date for line in unique_settlements)
        max_date = (
            max(line.transaction_date for line in unique_settlements) + date_window
        )

        other_bank_ids = list(
            ctx.db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == ctx.organization_id,
                    BankAccount.bank_account_id != ctx.bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_bank_ids:
            return

        deposit_lines = list(
            ctx.db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )
        deposit_lines = [
            dep
            for dep in deposit_lines
            if dep.description
            and (
                not ctx.policy.deposit_keywords
                or any(
                    keyword in dep.description.lower()
                    for keyword in ctx.policy.deposit_keywords
                )
            )
        ]
        if not deposit_lines:
            return

        target_accounts = {
            bank.bank_account_id: bank
            for bank in ctx.db.scalars(
                select(BankAccount).where(
                    BankAccount.bank_account_id.in_(other_bank_ids)
                )
            ).all()
        }
        deposits_by_date: dict[object, list[BankStatementLine]] = {}
        for dep in deposit_lines:
            deposits_by_date.setdefault(dep.transaction_date, []).append(dep)

        matched_deposit_ids: set[UUID] = set()
        for settlement_line in unique_settlements:
            try:
                candidates: list[BankStatementLine] = []
                for day_offset in range(window_days + 1):
                    check_date = settlement_line.transaction_date + timedelta(
                        days=day_offset
                    )
                    for dep in deposits_by_date.get(check_date, []):
                        if dep.line_id not in matched_deposit_ids:
                            candidates.append(dep)
                if not candidates:
                    continue

                best_deposit = min(
                    candidates, key=lambda dep: abs(dep.amount - settlement_line.amount)
                )
                dep_statement = ctx.db.get(BankStatement, best_deposit.statement_id)
                if not dep_statement:
                    continue
                dest_bank = target_accounts.get(dep_statement.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"settlement-{settlement_line.line_id}"
                credit_jl = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                debit_jl = None
                if credit_jl:
                    debit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )
                else:
                    amount = abs(settlement_line.amount)
                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=settlement_line.transaction_date,
                        posting_date=settlement_line.transaction_date,
                        description=f"Bank transfer - {settlement_line.reference}",
                        reference=settlement_line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=f"Transfer deposit - {settlement_line.reference}",
                            ),
                            JournalLineInput(
                                account_id=ctx.bank_account.gl_account_id,
                                credit_amount=amount,
                                description=f"Settlement transfer - {settlement_line.reference}",
                            ),
                        ],
                    )
                    journal, create_error = (
                        BasePostingAdapter.create_and_approve_journal(
                            ctx.db,
                            ctx.organization_id,
                            journal_input,
                            service.SYSTEM_USER_ID,
                            error_prefix="Settlement journal creation failed",
                        )
                    )
                    if create_error:
                        ctx.result.errors.append(
                            f"Line {settlement_line.line_number}: {create_error.message}"
                        )
                        continue
                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        ctx.organization_id,
                        "BANKING",
                        settlement_line.line_id,
                        action="settlement",
                    )
                    posting_result = service._post_with_period_fallback(
                        ctx.db,
                        organization_id=ctx.organization_id,
                        journal_entry_id=journal.journal_entry_id,
                        posting_date=settlement_line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=correlation_id,
                        posted_by_user_id=service.SYSTEM_USER_ID,
                        success_message="Settlement transfer posted",
                        error_prefix="Settlement journal posting failed",
                    )
                    if not posting_result.success:
                        ctx.result.errors.append(
                            f"Line {settlement_line.line_number}: {posting_result.message}"
                        )
                        continue
                    credit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        ctx.bank_account.gl_account_id,
                    )
                    debit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )

                dedup_key = (
                    settlement_line.transaction_date,
                    settlement_line.reference,
                    int(settlement_line.amount * 100),
                )
                if credit_jl:
                    for dup_line in dedup_groups.get(dedup_key, [settlement_line]):
                        if dup_line.line_id in ctx.matched_line_ids:
                            continue
                        try:
                            _perform_match(
                                service,
                                ctx,
                                dup_line,
                                credit_jl,
                                source_type="INTER_BANK",
                                source_id=None,
                                confidence=95,
                                explanation=f"Settlement transfer: {settlement_line.reference}",
                            )
                        except Exception:
                            service.logger.debug(
                                "Settlement line %s match skipped",
                                dup_line.line_id,
                                exc_info=True,
                            )

                if debit_jl and best_deposit.line_id not in matched_deposit_ids:
                    try:
                        service._perform_match(
                            ctx.db,
                            ctx.organization_id,
                            best_deposit,
                            debit_jl,
                            source_type="INTER_BANK",
                            source_id=None,
                        )
                        matched_deposit_ids.add(best_deposit.line_id)
                    except Exception:
                        service.logger.debug(
                            "Deposit line %s match skipped",
                            best_deposit.line_id,
                            exc_info=True,
                        )
            except Exception as exc:
                service.logger.exception(
                    "Error matching settlement line %s: %s",
                    settlement_line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {settlement_line.line_number}: {exc}")


_PAYROLL_RE = re.compile(r"(?i)\b(?:payroll|salary)\b")


@dataclass(frozen=True)
class PayrollEntryStrategy(MatchStrategy):
    """Match bank lines to payroll run GL journals.

    Looks for "payroll" or "salary" in the bank line description, then
    loads payroll entries for the statement's bank account and date range.
    Matches by amount (total_net_pay) with date proximity.
    """

    strategy_id: str = "payroll_entry"
    provider_key: str = "payroll_entry"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if not ctx.policy.allows_strategy(self.strategy_id):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return

        from datetime import timedelta

        from sqlalchemy import select as sa_select

        from app.models.people.payroll.payroll_entry import (
            PayrollEntry,
            PayrollEntryStatus,
        )

        # Collect candidate bank lines that mention payroll/salary
        candidates: list[BankStatementLine] = []
        for line in still_unmatched:
            text = (line.description or "") + " " + (line.reference or "")
            if _PAYROLL_RE.search(text):
                candidates.append(line)
        if not candidates:
            return

        # Load POSTED payroll entries for this bank account + date range
        buffer = timedelta(days=7)
        conditions = [
            PayrollEntry.organization_id == ctx.organization_id,
            PayrollEntry.status == PayrollEntryStatus.POSTED,
            PayrollEntry.journal_entry_id.isnot(None),
        ]
        # Filter by bank account if set on the entry
        # (entries without bank_account_id still eligible — matched by amount)
        if ctx.statement.period_start and ctx.statement.period_end:
            conditions.append(
                PayrollEntry.posting_date >= ctx.statement.period_start - buffer
            )
            conditions.append(
                PayrollEntry.posting_date <= ctx.statement.period_end + buffer
            )

        entries = list(ctx.db.scalars(sa_select(PayrollEntry).where(*conditions)).all())
        if not entries:
            return

        matched_entry_ids = ctx.tracker(self.provider_key)

        # Index entries by net pay amount (in cents) for matching
        def _to_cents(val: Any) -> int:
            return int(Decimal(str(val)).quantize(Decimal("0.01")) * 100)

        entry_by_amount: dict[int, list[Any]] = {}
        for entry in entries:
            if entry.entry_id in matched_entry_ids:
                continue
            if not entry.total_net_pay or entry.total_net_pay <= 0:
                continue
            key = _to_cents(entry.total_net_pay)
            entry_by_amount.setdefault(key, []).append(entry)

        for line in candidates:
            if line.line_id in ctx.matched_line_ids:
                continue
            try:
                line_cents = _to_cents(abs(line.amount))
                matching_entries = [
                    e
                    for e in entry_by_amount.get(line_cents, [])
                    if e.entry_id not in matched_entry_ids
                ]
                if len(matching_entries) != 1:
                    continue

                entry = matching_entries[0]
                correlation = str(entry.entry_id)

                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation,
                    ctx.bank_account.gl_account_id,
                    extra_gl_account_ids=ctx.extra_gl_account_ids,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="PAYROLL_ENTRY",
                    source_id=entry.entry_id,
                    confidence=95,
                    explanation=(
                        f"Payroll {entry.entry_number} "
                        f"({entry.payroll_month}/{entry.payroll_year})"
                    ),
                )
                matched_entry_ids.add(entry.entry_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via payroll: %s",
                    line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


_ACC_PAY_RE = re.compile(r"ACC-PAY-\d{4}-\d+")


@dataclass(frozen=True)
class ExpenseReimbursementStrategy(MatchStrategy):
    """Match bank lines to expense claim reimbursements.

    Looks for ``ACC-PAY-YYYY-NNNNN`` in the bank line description, matches
    it against ``expense_claim.payment_reference``, then creates the
    reimbursement journal via ``ExpensePostingAdapter`` and matches the
    bank line to the resulting bank-GL journal line.
    """

    strategy_id: str = "expense_reimbursement"
    provider_key: str = "expense_claim"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:  # noqa: C901
        if not ctx.policy.allows_strategy(self.strategy_id):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return

        import logging

        from sqlalchemy import select as sa_select

        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
        from app.services.expense.expense_posting_adapter import (
            ExpensePostingAdapter,
        )

        logger = logging.getLogger(__name__)

        # Collect candidate lines that mention ACC-PAY references
        candidates: list[tuple[BankStatementLine, str]] = []
        for line in still_unmatched:
            text = line.description or ""
            m = _ACC_PAY_RE.search(text)
            if not m:
                # Also check the reference field
                text = line.reference or ""
                m = _ACC_PAY_RE.search(text)
            if m:
                candidates.append((line, m.group(0)))

        if not candidates:
            return

        logger.info(
            "Expense reimbursement pass: %d candidate lines",
            len(candidates),
        )

        for line, acc_pay_ref in candidates:
            try:
                # Look up expense claim by payment_reference
                claim = ctx.db.scalar(
                    sa_select(ExpenseClaim).where(
                        ExpenseClaim.organization_id == ctx.organization_id,
                        ExpenseClaim.payment_reference == acc_pay_ref,
                        ExpenseClaim.status == ExpenseClaimStatus.PAID,
                    )
                )
                if not claim:
                    logger.debug(
                        "No PAID expense claim for ref %s",
                        acc_pay_ref,
                    )
                    continue

                # If reimbursement journal already exists, try to match
                # against the existing journal line directly.
                if claim.reimbursement_journal_id:
                    journal_line = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        f"exp-reimb-{claim.claim_id}",
                        ctx.bank_account.gl_account_id,
                    )
                    if not journal_line:
                        # Try finding by journal entry directly
                        from app.models.finance.gl.journal_entry_line import (
                            JournalEntryLine,
                        )

                        journal_line = ctx.db.scalar(
                            sa_select(JournalEntryLine).where(
                                JournalEntryLine.journal_entry_id
                                == claim.reimbursement_journal_id,
                                JournalEntryLine.account_id
                                == ctx.bank_account.gl_account_id,
                            )
                        )
                    if journal_line:
                        _perform_match(
                            service,
                            ctx,
                            line,
                            journal_line,
                            source_type="EXPENSE_REIMBURSEMENT",
                            source_id=claim.claim_id,
                            confidence=95,
                            explanation=(
                                f"Expense reimbursement {claim.claim_number} "
                                f"({acc_pay_ref})"
                            ),
                        )
                        logger.info(
                            "Matched line %s to existing reimbursement "
                            "journal for claim %s",
                            line.line_id,
                            claim.claim_number,
                        )
                    continue

                # Skip claims with no payable amount — these need manual
                # data correction (e.g. ERPNext-synced claims with null
                # net_payable_amount). Avoids repeated warnings each run.
                payable = claim.net_payable_amount or Decimal("0")
                if payable <= Decimal("0"):
                    logger.debug(
                        "Skipping reimbursement posting for claim %s — "
                        "net_payable_amount is %s (needs data fix)",
                        claim.claim_number,
                        payable,
                    )
                    continue

                if self.strategy_id not in ctx.policy.journal_creation_strategy_keys:
                    logger.debug(
                        "Skipping reimbursement journal creation for claim %s; "
                        "journal creation is disabled by reconciliation policy",
                        claim.claim_number,
                    )
                    continue

                # Create reimbursement journal via the posting adapter
                correlation_id = f"exp-reimb-{claim.claim_id}"
                posting_result = ExpensePostingAdapter.post_expense_reimbursement(
                    ctx.db,
                    ctx.organization_id,
                    claim.claim_id,
                    posting_date=line.transaction_date,
                    posted_by_user_id=service.SYSTEM_USER_ID,
                    bank_account_id=ctx.bank_account.bank_account_id,
                    payment_reference=acc_pay_ref,
                    correlation_id=correlation_id,
                )

                if not posting_result.success:
                    logger.warning(
                        "Failed to post reimbursement for claim %s: %s",
                        claim.claim_number,
                        posting_result.message,
                    )
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {posting_result.message}"
                    )
                    continue

                # Find the bank-side journal line and match
                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                if not journal_line:
                    # Fallback: find by journal_entry_id + account
                    from app.models.finance.gl.journal_entry_line import (
                        JournalEntryLine,
                    )

                    journal_line = ctx.db.scalar(
                        sa_select(JournalEntryLine).where(
                            JournalEntryLine.journal_entry_id
                            == posting_result.journal_entry_id,
                            JournalEntryLine.account_id
                            == ctx.bank_account.gl_account_id,
                        )
                    )
                if not journal_line:
                    logger.warning(
                        "Created reimbursement journal %s for claim %s but "
                        "couldn't find bank GL line",
                        posting_result.journal_entry_id,
                        claim.claim_number,
                    )
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="EXPENSE_REIMBURSEMENT",
                    source_id=claim.claim_id,
                    confidence=95,
                    explanation=(
                        f"Expense reimbursement {claim.claim_number} ({acc_pay_ref})"
                    ),
                )
                logger.info(
                    "Matched line %s → claim %s via reimbursement journal %s",
                    line.line_id,
                    claim.claim_number,
                    posting_result.journal_entry_id,
                )
            except Exception as exc:
                service.logger.exception(
                    "Error matching expense line %s: %s",
                    line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class LegacyCustomRuleStrategy(MatchStrategy):
    strategy_id: str = "legacy_custom_rules"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if not ctx.policy.allows_strategy(self.strategy_id):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        try:
            from app.services.finance.banking.reconciliation_engine import (
                ReconciliationEngine,
            )

            engine = ReconciliationEngine(ctx.db)
            engine_result = engine.run_custom_rules(
                ctx.organization_id,
                ctx.statement,
                ctx.bank_account,
                ctx.unmatched_lines,
                ctx.matched_line_ids,
                amount_tolerance=ctx.policy.amount_tolerance,
                date_buffer_days=ctx.policy.date_buffer_days,
                extra_gl_account_ids=ctx.extra_gl_account_ids,
            )
            ctx.result.matched += engine_result.matched
            ctx.result.errors.extend(engine_result.errors)
        except Exception:
            service.logger.warning(
                "Programmatic core fallback (legacy custom rules) failed",
                exc_info=True,
            )
