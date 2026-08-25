"""ReconciliationEngineHandlers component."""

from __future__ import annotations


from app.services.finance.banking.reconciliation_engine_parts.base import (
    Any,
    BankAccount,
    BankStatement,
    BankStatementLine,
    EngineContext,
    JournalEntryLine,
    ReconciliationMatchRule,
    UUID,
    _SYSTEM_USER_ID,
    logger,
    select,
    timedelta,
)


class ReconciliationEngineHandlers:
    """Reconciliation engine methods for handlers."""

    def _handle_customer_payment(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to CustomerPayment records."""

        candidates = self._load_customer_payments(ctx)
        if not candidates:
            return

        # Build reference lookup: {ref_string → payment}
        ref_lookup = self._build_payment_ref_lookup(candidates)  # type: ignore[attr-defined]

        # Phase 1: Reference matching
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            payment = self._find_ref_in_line(line, ref_lookup)  # type: ignore[attr-defined]
            if not payment:
                continue
            if payment.payment_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(  # type: ignore[attr-defined]
                line.amount, payment.amount, ctx.amount_tolerance
            ):
                continue

            correlation_id = self._get_correlation_id(payment, "CUSTOMER_PAYMENT")  # type: ignore[attr-defined]
            if not correlation_id:
                continue

            journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                ctx, correlation_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(  # type: ignore[attr-defined]
                ctx,
                rule,
                line,
                journal_line,
                source_type="CUSTOMER_PAYMENT",
                source_id=payment.payment_id,
                confidence=100,
                explanation=(
                    f"Reference match: {payment.reference or payment.payment_number}"
                ),
                rule_service=rule_service,
            )

        # Phase 2: Date + amount fallback (unique matches only)
        self._date_amount_fallback(  # type: ignore[attr-defined]
            ctx,
            rule,
            eligible_lines,
            candidates,
            source_type="CUSTOMER_PAYMENT",
            get_id=lambda p: p.payment_id,
            get_amount=lambda p: p.amount,
            get_date=lambda p: (
                p.payment_date.date()
                if hasattr(p.payment_date, "date")
                else p.payment_date
            ),
            get_correlation_id=lambda p: self._get_correlation_id(  # type: ignore[attr-defined]
                p, "CUSTOMER_PAYMENT"
            ),
            rule_service=rule_service,
        )

    def _load_customer_payments(self, ctx: EngineContext) -> list[Any]:
        """Load CLEARED customer payments within statement date range."""
        from app.models.finance.ar.customer_payment import (
            CustomerPayment,
            PaymentStatus,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(CustomerPayment).where(
            CustomerPayment.organization_id == ctx.organization_id,
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.bank_account_id == ctx.statement.bank_account_id,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                CustomerPayment.payment_date >= ctx.statement.period_start - buffer,
                CustomerPayment.payment_date
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        return list(self.db.scalars(stmt).all())  # type: ignore[attr-defined]

    # ── SUPPLIER_PAYMENT handler ────────────────────────────────────
    def _handle_supplier_payment(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to SupplierPayment records."""

        candidates = self._load_supplier_payments(ctx)
        if not candidates:
            return

        ref_lookup = self._build_supplier_ref_lookup(candidates)  # type: ignore[attr-defined]

        # Phase 1: Reference matching
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            payment = self._find_ref_in_line(line, ref_lookup)  # type: ignore[attr-defined]
            if not payment:
                continue
            if payment.payment_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(  # type: ignore[attr-defined]
                line.amount, payment.amount, ctx.amount_tolerance
            ):
                continue

            correlation_id = str(payment.payment_id)
            journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                ctx, correlation_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(  # type: ignore[attr-defined]
                ctx,
                rule,
                line,
                journal_line,
                source_type="SUPPLIER_PAYMENT",
                source_id=payment.payment_id,
                confidence=100,
                explanation=(
                    f"Reference match: {payment.payment_number or payment.reference}"
                ),
                rule_service=rule_service,
            )

        # Phase 2: Date + amount fallback
        self._date_amount_fallback(  # type: ignore[attr-defined]
            ctx,
            rule,
            eligible_lines,
            candidates,
            source_type="SUPPLIER_PAYMENT",
            get_id=lambda p: p.payment_id,
            get_amount=lambda p: p.amount,
            get_date=lambda p: (
                p.payment_date.date()
                if hasattr(p.payment_date, "date")
                else p.payment_date
            ),
            get_correlation_id=lambda p: str(p.payment_id),
            rule_service=rule_service,
        )

    def _load_supplier_payments(self, ctx: EngineContext) -> list[Any]:
        """Load CLEARED supplier payments within statement date range."""
        from app.models.finance.ap.supplier_payment import (
            APPaymentStatus,
            SupplierPayment,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(SupplierPayment).where(
            SupplierPayment.organization_id == ctx.organization_id,
            SupplierPayment.status == APPaymentStatus.CLEARED,
            SupplierPayment.bank_account_id == ctx.statement.bank_account_id,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                SupplierPayment.payment_date >= ctx.statement.period_start - buffer,
                SupplierPayment.payment_date
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        return list(self.db.scalars(stmt).all())  # type: ignore[attr-defined]

    # ── PAYMENT_INTENT handler ──────────────────────────────────────
    def _handle_payment_intent(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to PaymentIntent records."""
        from app.models.finance.payments.payment_intent import (
            PaymentIntent,
            PaymentIntentStatus,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(PaymentIntent).where(
            PaymentIntent.organization_id == ctx.organization_id,
            PaymentIntent.bank_account_id == ctx.statement.bank_account_id,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                PaymentIntent.paid_at >= ctx.statement.period_start - buffer,
                PaymentIntent.paid_at
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        intents = list(self.db.scalars(stmt).all())  # type: ignore[attr-defined]
        if not intents:
            return

        # Build ref lookup from paystack_reference (or any gateway ref)
        ref_lookup: dict[str, Any] = {}
        for intent in intents:
            if intent.paystack_reference:
                ref_lookup[intent.paystack_reference] = intent

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            matched_intent = self._find_ref_in_line(line, ref_lookup)  # type: ignore[attr-defined]
            if not matched_intent:
                continue
            if matched_intent.intent_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(  # type: ignore[attr-defined]
                line.amount, matched_intent.amount, ctx.amount_tolerance
            ):
                continue

            journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                ctx,
                str(matched_intent.intent_id),
                ctx.bank_account.gl_account_id,
            )
            if not journal_line:
                continue

            self._execute_match(  # type: ignore[attr-defined]
                ctx,
                rule,
                line,
                journal_line,
                source_type="PAYMENT_INTENT",
                source_id=matched_intent.intent_id,
                confidence=100,
                explanation=(
                    f"Gateway reference {matched_intent.paystack_reference} (exact match)"
                ),
                rule_service=rule_service,
            )

    # ── BANK_FEE handler ────────────────────────────────────────────
    def _handle_bank_fee(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Create GL journals for bank fee lines and auto-match."""
        from app.models.finance.gl.account import Account
        from app.services.finance.banking.bank_fee_posting import post_bank_fee

        # Determine writeoff account from rule or default
        if rule.writeoff_account_id:
            finance_cost_account = self.db.get(Account, rule.writeoff_account_id)  # type: ignore[attr-defined]
        else:
            finance_cost_account = self.db.scalar(  # type: ignore[attr-defined]
                select(Account).where(
                    Account.organization_id == ctx.organization_id,
                    Account.account_code == "6080",
                )
            )

        if not finance_cost_account:
            logger.warning(
                "No writeoff account for BANK_FEE rule '%s' — skipping",
                rule.name,
            )
            return

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            try:
                correlation_id = f"bank-fee-{line.line_id}"

                # Build journal label from template or default
                label = self._render_label(  # type: ignore[attr-defined]
                    rule.journal_label_template,
                    line,
                    default=f"Bank charge - {line.description}",
                )

                # Delegated to the one bank-fee owner (see `bank_fee_posting`).
                # This adapter posts through the combined create-and-post helper,
                # so it passes no `poster`; what changes is that the CREATE is
                # now guarded by a pre-check and a unique index.
                outcome = post_bank_fee(
                    self.db,  # type: ignore[attr-defined]
                    organization_id=ctx.organization_id,
                    line=line,
                    bank_gl_account_id=ctx.bank_account.gl_account_id,
                    finance_cost_account_id=finance_cost_account.account_id,
                    posted_by_user_id=_SYSTEM_USER_ID,
                    description=label,
                )

                if outcome.needs_attention:
                    # Something exists but no ledger effect does. Surfaced, not
                    # skipped: counting this shape as done is what let 12,117
                    # orphan journals accumulate unnoticed.
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {outcome.message}"
                    )
                    continue

                if not outcome.ok:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {outcome.message}"
                    )
                    continue

                # Matching runs whether or not this call created the journal —
                # see the note in `bank_fee_posting`: a crash between posting and
                # matching leaves the line unmatched, and this lookup is
                # idempotent.
                journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                    ctx,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                if not journal_line:
                    continue

                self._execute_match(  # type: ignore[attr-defined]
                    ctx,
                    rule,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                    rule_service=rule_service,
                )

            except Exception as e:
                logger.exception(
                    "Error in BANK_FEE handler for line %s: %s",
                    line.line_id,
                    e,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {e}")

    # ── INTER_BANK handler ──────────────────────────────────────────
    def _handle_inter_bank(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match settlement/transfer lines across bank accounts."""
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import (
            JournalInput,
            JournalLineInput,
        )
        from app.services.finance.posting.base import BasePostingAdapter

        window_days = rule.date_window_days or 10

        # Load other bank accounts
        other_banks = list(
            self.db.scalars(  # type: ignore[attr-defined]
                select(BankAccount).where(
                    BankAccount.organization_id == ctx.organization_id,
                    BankAccount.bank_account_id != ctx.bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_banks:
            return

        other_bank_ids = [b.bank_account_id for b in other_banks]
        bank_by_id = {b.bank_account_id: b for b in other_banks}

        # Load unmatched lines on other bank accounts in date range
        dates = [l.transaction_date for l in eligible_lines]
        if not dates:
            return
        min_date = min(dates)
        max_date = max(dates) + timedelta(days=window_days)

        deposit_lines = list(
            self.db.scalars(  # type: ignore[attr-defined]
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == ctx.organization_id,
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )
        if not deposit_lines:
            return

        matched_deposit_ids: set[UUID] = set()

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            try:
                # Find best deposit within date window
                candidates = [
                    dep
                    for dep in deposit_lines
                    if dep.line_id not in matched_deposit_ids
                    and 0
                    <= (dep.transaction_date - line.transaction_date).days
                    <= window_days
                ]
                if not candidates:
                    continue

                best = min(
                    candidates,
                    key=lambda d: abs(d.amount - line.amount),
                )

                # Resolve destination bank account
                dep_stmt = self.db.get(BankStatement, best.statement_id)  # type: ignore[attr-defined]
                if not dep_stmt:
                    continue
                dest_bank = bank_by_id.get(dep_stmt.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"interbank-{line.line_id}"
                amount = abs(line.amount)

                # Check for existing journal (idempotent)
                credit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                    ctx,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                debit_jl: JournalEntryLine | None = None

                if credit_jl:
                    debit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        ctx, correlation_id, dest_bank.gl_account_id
                    )
                else:
                    label = self._render_label(  # type: ignore[attr-defined]
                        rule.journal_label_template,
                        line,
                        default=(f"Inter-bank transfer - {line.reference}"),
                    )
                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=line.transaction_date,
                        posting_date=line.transaction_date,
                        description=label,
                        reference=line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=f"Deposit from transfer - {line.reference}",
                            ),
                            JournalLineInput(
                                account_id=ctx.bank_account.gl_account_id,
                                credit_amount=amount,
                                description=f"Transfer out - {line.reference}",
                            ),
                        ],
                    )

                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        ctx.organization_id,
                        "BANKING",
                        line.line_id,
                        action="interbank",
                    )
                    journal, posting = (
                        BasePostingAdapter.create_approve_and_post_journal(
                            self.db,  # type: ignore[attr-defined]
                            ctx.organization_id,
                            journal_input,
                            _SYSTEM_USER_ID,
                            posting_date=line.transaction_date,
                            idempotency_key=idempotency_key,
                            source_module="BANKING",
                            correlation_id=correlation_id,
                            success_message="Inter-bank transfer posted",
                            creation_error_prefix="Inter-bank journal creation failed",
                            ledger_error_prefix="Inter-bank posting failed",
                        )
                    )
                    if not posting.success:
                        ctx.result.errors.append(
                            f"Line {line.line_number}: {posting.message}"
                        )
                        continue

                    credit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        ctx,
                        correlation_id,
                        ctx.bank_account.gl_account_id,
                    )
                    debit_jl = self._find_journal_line(  # type: ignore[attr-defined]
                        ctx, correlation_id, dest_bank.gl_account_id
                    )

                # Match source line
                if credit_jl:
                    self._execute_match(  # type: ignore[attr-defined]
                        ctx,
                        rule,
                        line,
                        credit_jl,
                        source_type="INTER_BANK",
                        source_id=None,
                        confidence=85,
                        explanation=(
                            f"Inter-bank transfer to {dest_bank.account_name}"
                        ),
                        rule_service=rule_service,
                    )

                # Match deposit line
                if debit_jl and best.line_id not in matched_deposit_ids:
                    self._perform_match_action(ctx, best, debit_jl, "INTER_BANK", None)  # type: ignore[attr-defined]
                    matched_deposit_ids.add(best.line_id)

            except Exception as e:
                logger.exception(
                    "Error in INTER_BANK handler for line %s: %s",
                    line.line_id,
                    e,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {e}")

    # ── Generic helpers ─────────────────────────────────────────────
