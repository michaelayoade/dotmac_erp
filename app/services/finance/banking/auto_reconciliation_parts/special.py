"""AutoReconciliationSpecialService component."""

from __future__ import annotations


from app.services.finance.banking.auto_reconciliation_parts.base import (
    AutoMatchDefaults,
    AutoMatchResult,
    BankAccount,
    BankStatement,
    BankStatementLine,
    FINANCE_COST_ACCOUNT_CODE,
    JournalEntryLine,
    SETTLEMENT_DATE_WINDOW_DAYS,
    SYSTEM_USER_ID,
    Session,
    UUID,
    _BANK_FEE_RE,
    _PAYSTACK_DEPOSIT_RE,
    _SETTLEMENT_RE,
    logger,
    select,
)


class AutoReconciliationSpecialService:
    """Auto-reconciliation methods for special."""

    def _match_bank_fees(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Create GL journals for Paystack fee lines and auto-match them.

        For each unmatched line whose description matches ``Paystack Fee:``:
        1. Creates a balanced journal: Debit Finance Cost (6080),
           Credit bank GL account.
        2. Auto-posts via ``BasePostingAdapter``
           (DRAFT → SUBMITTED → APPROVED → POSTED, with SoD bypass).
        3. Matches the statement line to the credit journal line.
        """
        from app.models.finance.gl.account import Account
        from app.services.finance.banking.bank_fee_posting import (
            BankFeeState,
            post_bank_fee,
        )

        # Look up Finance Cost GL account (configurable, default 6080) once
        account_code = (
            config.finance_cost_account_code if config else FINANCE_COST_ACCOUNT_CODE
        )
        finance_cost_account = db.scalar(
            select(Account).where(
                Account.organization_id == organization_id,
                Account.account_code == account_code,
            )
        )
        if not finance_cost_account:
            logger.warning(
                "Finance Cost account (%s) not found for org %s — skipping fee pass",
                account_code,
                organization_id,
            )
            return

        # Filter to fee lines only
        fee_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.description
            and _BANK_FEE_RE.search(line.description)
        ]

        if not fee_lines:
            return

        logger.info(
            "Pass 6: Processing %d Paystack fee lines for statement on bank %s",
            len(fee_lines),
            bank_account.bank_account_id,
        )

        for line in fee_lines:
            try:
                # Delegated to the one bank-fee owner. It checks BEFORE it
                # creates, records the statement line as typed identity, and is
                # backed by a unique index — see `bank_fee_posting`.
                outcome = post_bank_fee(
                    db,
                    organization_id=organization_id,
                    line=line,
                    bank_gl_account_id=bank_account.gl_account_id,
                    finance_cost_account_id=finance_cost_account.account_id,
                    posted_by_user_id=SYSTEM_USER_ID,
                    poster=self._post_with_period_fallback,  # type: ignore[attr-defined]
                )

                if outcome.needs_attention:
                    # APPROVED-but-unposted, or voided/reversed. Something
                    # exists and the ledger effect does not. Surfaced rather
                    # than counted as done — silently skipping this shape is
                    # what let 12,117 orphans accumulate unnoticed.
                    logger.warning(
                        "Bank fee for line %s needs attention: %s",
                        line.line_id,
                        outcome.message,
                    )
                    result.errors.append(f"Line {line.line_number}: {outcome.message}")
                    continue

                if outcome.state is BankFeeState.LEGACY_BATCH_ONLY:
                    logger.info(
                        "Bank fee for line %s already posted (legacy): %s",
                        line.line_id,
                        outcome.message,
                    )
                    continue

                if not outcome.ok:
                    logger.warning(
                        "Failed to record fee journal for line %s: %s",
                        line.line_id,
                        outcome.message,
                    )
                    result.errors.append(f"Line {line.line_number}: {outcome.message}")
                    continue

                # Falls through to matching even when nothing was created. A
                # crash between posting and matching leaves the statement line
                # unmatched forever otherwise, and the match step below is
                # idempotent — it looks the line up before writing.
                journal = outcome.posted_journal
                if journal is None:
                    # A live effect without a journal to match against. Reported
                    # rather than asserted away: if it ever happens the fee is
                    # silently unmatched, which is how this went wrong before.
                    result.errors.append(
                        f"Line {line.line_number}: fee posted without a journal"
                    )
                    continue

                correlation_id = f"bank-fee-{line.line_id}"

                # Find the credit line on the bank GL account
                journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    correlation_id,
                    bank_account.gl_account_id,
                )
                if not journal_line:
                    logger.warning(
                        "Created fee journal %s but couldn't find bank GL line",
                        journal.journal_entry_id,
                    )
                    continue

                self._perform_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                )
                self._log_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line=line,
                    source_type="BANK_FEE",
                    source_id=None,
                    journal_line_id=journal_line.line_id,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                )
                matched_line_ids.add(line.line_id)
                result.matched += 1
                logger.info(
                    "Auto-matched fee line %s to GL journal %s",
                    line.line_id,
                    journal.journal_number,
                )

            except Exception as e:
                logger.exception("Error matching fee line %s: %s", line.line_id, e)
                result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 7: Settlement matching (cross-bank transfer) ──────────
    def _match_settlements(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Match Paystack settlement debits to deposits on receiving banks.

        For each unmatched line whose description matches ``Settlement``:
        1. Searches UBA, Zenith 523, and Zenith 461 for a Paystack-related
           credit within 0–5 days (closest amount wins).
        2. Creates a balanced transfer journal: Debit destination bank GL,
           Credit source (Paystack) bank GL.
        3. Posts via ``BasePostingAdapter`` (with SoD bypass).
        4. Matches **both** the settlement line and the deposit line.

        Duplicate settlement lines (same date + reference + amount) are all
        matched to the same journal.

        **Idempotent**: if a journal already exists for a settlement line
        (from a previous partial run), it is reused instead of creating a
        duplicate.  Individual match failures (e.g. line already matched)
        are caught and logged without aborting the batch.
        """
        from datetime import timedelta

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        window_days = (
            config.settlement_date_window_days
            if config
            else SETTLEMENT_DATE_WINDOW_DAYS
        )
        date_window = timedelta(days=window_days)

        # Filter to settlement lines only
        settlement_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.description
            and _SETTLEMENT_RE.search(line.description)
        ]

        if not settlement_lines:
            return

        # Deduplicate: group by (date, reference, amount_cents).
        # Import artefacts can produce identical copies.
        _DedupKey = tuple[object, str | None, int]
        dedup_groups: dict[_DedupKey, list[BankStatementLine]] = {}
        unique_settlements: list[BankStatementLine] = []
        for line in settlement_lines:
            key: _DedupKey = (
                line.transaction_date,
                line.reference,
                int(round(line.amount * 100)),
            )
            group = dedup_groups.setdefault(key, [])
            group.append(line)
            if len(group) == 1:
                # First occurrence — representative for this group
                unique_settlements.append(line)

        logger.info(
            "Pass 7: Processing %d unique settlement lines (%d total incl. dupes) "
            "for bank %s",
            len(unique_settlements),
            len(settlement_lines),
            bank_account.bank_account_id,
        )

        # Determine date range for deposit query
        min_date = min(l.transaction_date for l in unique_settlements)
        max_date = max(l.transaction_date for l in unique_settlements) + date_window

        # Load all Paystack-related deposits from other bank accounts
        other_bank_ids = list(
            db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.bank_account_id != bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )

        if not other_bank_ids:
            logger.info("No other bank accounts configured — skipping settlement pass")
            return

        deposit_lines = list(
            db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == organization_id,
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )

        # Filter to Paystack-related deposits only (in Python for flexibility)
        deposit_lines = [
            dep
            for dep in deposit_lines
            if dep.description and _PAYSTACK_DEPOSIT_RE.search(dep.description)
        ]

        if not deposit_lines:
            logger.info("No Paystack-related deposits found on other banks")
            return

        # Pre-load bank account objects for GL lookup
        target_accounts: dict[UUID, BankAccount] = {
            ba.bank_account_id: ba
            for ba in db.scalars(
                select(BankAccount).where(
                    BankAccount.bank_account_id.in_(other_bank_ids)
                )
            ).all()
        }

        # Index deposits by date for fast window lookup
        deposits_by_date: dict[object, list[BankStatementLine]] = {}
        for dep in deposit_lines:
            deposits_by_date.setdefault(dep.transaction_date, []).append(dep)

        matched_deposit_ids: set[UUID] = set()

        for settlement_line in unique_settlements:
            try:
                # Collect deposit candidates within date window
                candidates: list[BankStatementLine] = []
                for day_offset in range(SETTLEMENT_DATE_WINDOW_DAYS + 1):
                    check_date = settlement_line.transaction_date + timedelta(
                        days=day_offset
                    )
                    for dep in deposits_by_date.get(check_date, []):
                        if dep.line_id not in matched_deposit_ids:
                            candidates.append(dep)

                if not candidates:
                    continue

                # Pick best candidate: closest by amount
                best_deposit = min(
                    candidates,
                    key=lambda d: abs(d.amount - settlement_line.amount),
                )

                # Resolve destination bank account
                dep_statement = db.get(BankStatement, best_deposit.statement_id)
                if not dep_statement:
                    continue
                dest_bank = target_accounts.get(dep_statement.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"settlement-{settlement_line.line_id}"

                # ── Idempotent journal lookup / creation ─────────────
                # Check if journal already exists (from a previous partial run).
                # This avoids duplicate journals and idempotency key violations.
                credit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                    db, organization_id, correlation_id, bank_account.gl_account_id
                )
                debit_jl: JournalEntryLine | None = None

                if credit_jl:
                    # Journal already created and posted — reuse it
                    debit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        db, organization_id, correlation_id, dest_bank.gl_account_id
                    )
                    logger.info(
                        "Reusing existing journal for settlement %s (re-run)",
                        settlement_line.reference,
                    )
                else:
                    # Create new inter-bank transfer journal
                    amount = abs(settlement_line.amount)

                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=settlement_line.transaction_date,
                        posting_date=settlement_line.transaction_date,
                        description=(
                            f"Paystack settlement transfer - "
                            f"{settlement_line.reference}"
                        ),
                        reference=settlement_line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=(
                                    f"Settlement deposit from Paystack - "
                                    f"{settlement_line.reference}"
                                ),
                            ),
                            JournalLineInput(
                                account_id=bank_account.gl_account_id,
                                credit_amount=amount,
                                description=(
                                    f"Settlement transfer - {settlement_line.reference}"
                                ),
                            ),
                        ],
                    )

                    # Step 1: Create, submit, approve
                    journal, create_error = (
                        BasePostingAdapter.create_and_approve_journal(
                            db,
                            organization_id,
                            journal_input,
                            SYSTEM_USER_ID,
                            error_prefix="Settlement journal creation failed",
                        )
                    )

                    if create_error:
                        logger.warning(
                            "Failed to create settlement journal for line %s: %s",
                            settlement_line.line_id,
                            create_error.message,
                        )
                        result.errors.append(
                            f"Line {settlement_line.line_number}: "
                            f"{create_error.message}"
                        )
                        continue

                    # Step 2: Post to ledger
                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        organization_id,
                        "BANKING",
                        settlement_line.line_id,
                        action="settlement",
                    )
                    posting_result = self._post_with_period_fallback(  # type: ignore[attr-defined]
                        db,
                        organization_id=organization_id,
                        journal_entry_id=journal.journal_entry_id,
                        posting_date=settlement_line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=correlation_id,
                        posted_by_user_id=SYSTEM_USER_ID,
                        success_message="Settlement transfer posted",
                        error_prefix="Settlement journal posting failed",
                    )

                    if not posting_result.success:
                        logger.warning(
                            "Failed to post settlement journal for line %s: %s",
                            settlement_line.line_id,
                            posting_result.message,
                        )
                        result.errors.append(
                            f"Line {settlement_line.line_number}: "
                            f"{posting_result.message}"
                        )
                        continue

                    # Find journal lines for matching
                    credit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        correlation_id,
                        bank_account.gl_account_id,
                    )
                    debit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )

                # ── Match settlement line(s) to credit side ──────────
                dedup_key: _DedupKey = (
                    settlement_line.transaction_date,
                    settlement_line.reference,
                    int(round(settlement_line.amount * 100)),
                )
                if credit_jl:
                    for dup_line in dedup_groups.get(dedup_key, [settlement_line]):
                        if dup_line.line_id not in matched_line_ids:
                            try:
                                self._perform_match(  # type: ignore[attr-defined]
                                    db,
                                    organization_id,
                                    dup_line,
                                    credit_jl,
                                    source_type="INTER_BANK",
                                    source_id=None,
                                )
                                self._log_match(  # type: ignore[attr-defined]
                                    db,
                                    organization_id,
                                    line=dup_line,
                                    source_type="INTER_BANK",
                                    source_id=None,
                                    journal_line_id=credit_jl.line_id,
                                    confidence=85,
                                    explanation=f"Settlement transfer: {settlement_line.reference}",
                                )
                                matched_line_ids.add(dup_line.line_id)
                                result.matched += 1
                            except Exception as e:
                                logger.debug(
                                    "Settlement line %s match skipped "
                                    "(already matched): %s",
                                    dup_line.line_id,
                                    e,
                                )

                # ── Match deposit line to debit side ─────────────────
                if debit_jl and best_deposit.line_id not in matched_deposit_ids:
                    try:
                        self._perform_match(  # type: ignore[attr-defined]
                            db,
                            organization_id,
                            best_deposit,
                            debit_jl,
                            source_type="INTER_BANK",
                            source_id=None,
                        )
                        matched_deposit_ids.add(best_deposit.line_id)
                    except Exception as e:
                        logger.debug(
                            "Deposit line %s match skipped (already matched): %s",
                            best_deposit.line_id,
                            e,
                        )

                days_diff = (
                    best_deposit.transaction_date - settlement_line.transaction_date
                ).days
                logger.info(
                    "Auto-matched settlement %s (%.2f) to deposit on %s (%.2f)"
                    " — %d day(s) diff",
                    settlement_line.reference,
                    abs(settlement_line.amount),
                    dest_bank.account_name,
                    best_deposit.amount,
                    days_diff,
                )

            except Exception as e:
                logger.exception(
                    "Error matching settlement line %s: %s",
                    settlement_line.line_id,
                    e,
                )
                result.errors.append(f"Line {settlement_line.line_number}: {e}")

    # ── Shared helpers ──────────────────────────────────────────────
