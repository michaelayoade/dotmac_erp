"""AutoReconciliationPaymentService component."""

from __future__ import annotations

from app.services.finance.banking.auto_reconciliation_parts.base import (
    APPaymentStatus,
    AutoMatchDefaults,
    AutoMatchResult,
    BankAccount,
    BankStatement,
    BankStatementLine,
    CustomerPayment,
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentStatus,
    Session,
    StatementLineType,
    SupplierPayment,
    UUID,
    _PAYSTACK_REF_RE,
    logger,
    select,
)


class AutoReconciliationPaymentService:
    """Auto-reconciliation methods for payments."""

    def _match_payment_intents(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Match lines against COMPLETED PaymentIntent records."""
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        intent_query = select(PaymentIntent).where(
            PaymentIntent.organization_id == organization_id,
            PaymentIntent.bank_account_id == statement.bank_account_id,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
        )
        if statement.period_start and statement.period_end:
            intent_query = intent_query.where(
                PaymentIntent.paid_at >= statement.period_start - date_buffer,
                PaymentIntent.paid_at
                < statement.period_end + date_buffer + timedelta(days=1),
            )
        intents = list(db.scalars(intent_query).all())

        if not intents:
            return

        # Build lookup: paystack_reference -> intent
        ref_to_intent: dict[str, PaymentIntent] = {
            intent.paystack_reference: intent for intent in intents
        }
        matched_intent_ids: set[UUID] = set()

        for line in unmatched_lines:
            try:
                intent = self._find_ref_in_line(line, ref_to_intent)  # type: ignore[attr-defined]
                if not intent:
                    continue

                tolerance = config.amount_tolerance if config else None
                if not self._amounts_match(  # type: ignore[attr-defined]
                    line.amount, intent.amount, tolerance=tolerance
                ):
                    logger.debug(
                        "PaymentIntent ref %s in line %s but amount mismatch: "
                        "line=%s, intent=%s",
                        intent.paystack_reference,
                        line.line_id,
                        line.amount,
                        intent.amount,
                    )
                    continue

                journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    str(intent.intent_id),
                    bank_account.gl_account_id,
                    extra_gl_account_ids=extra_gl_account_ids,
                )
                if not journal_line:
                    logger.debug(
                        "No GL journal for intent %s (ref: %s)",
                        intent.intent_id,
                        intent.paystack_reference,
                    )
                    continue

                self._perform_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                )
                self._log_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line=line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                    journal_line_id=journal_line.line_id,
                    confidence=100,
                    explanation=f"Paystack reference {intent.paystack_reference} (exact match)",
                )
                matched_line_ids.add(line.line_id)
                matched_intent_ids.add(intent.intent_id)
                result.matched += 1
                logger.info(
                    "Auto-matched line %s to GL %s via PaymentIntent %s",
                    line.line_id,
                    journal_line.line_id,
                    intent.paystack_reference,
                )
            except Exception as e:
                logger.exception(
                    "Error matching line %s via PaymentIntent: %s",
                    line.line_id,
                    e,
                )
                result.errors.append(f"Line {line.line_number}: {e}")

        # Paystack OPEX fallback: match expense transfers by date+amount when
        # statement references are missing.
        self._match_expense_intents_by_date_amount(
            db,
            organization_id,
            bank_account,
            intents,
            unmatched_lines,
            matched_line_ids,
            matched_intent_ids,
            result,
            extra_gl_account_ids=extra_gl_account_ids,
        )

    def _match_expense_intents_by_date_amount(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        intents: list[PaymentIntent],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_intent_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Fallback match for Paystack OPEX expense transfers by date+amount."""
        from datetime import date

        if not self._is_paystack_opex_account(bank_account):  # type: ignore[attr-defined]
            return

        _DateAmountKey = tuple[date, int]

        eligible_intents = [
            intent
            for intent in intents
            if intent.intent_id not in matched_intent_ids
            and intent.source_type == "EXPENSE_CLAIM"
            and intent.direction == PaymentDirection.OUTBOUND
            and intent.paid_at is not None
        ]
        if not eligible_intents:
            return

        intent_index: dict[_DateAmountKey, list[PaymentIntent]] = {}
        for intent in eligible_intents:
            paid_at = intent.paid_at
            if paid_at is None:
                continue
            key: _DateAmountKey = (
                paid_at.date(),
                int(round(abs(intent.amount) * 100)),
            )
            intent_index.setdefault(key, []).append(intent)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.debit:
                continue
            key = (line.transaction_date, int(round(abs(line.amount) * 100)))
            line_index.setdefault(key, []).append(line)

        for key, key_intents in intent_index.items():
            key_lines = line_index.get(key, [])
            available_lines = [
                ln for ln in key_lines if ln.line_id not in matched_line_ids
            ]
            if not available_lines:
                continue

            pairs = min(len(key_intents), len(available_lines))
            for i in range(pairs):
                intent = key_intents[i]
                line = available_lines[i]
                try:
                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        str(intent.intent_id),
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="PAYMENT_INTENT",
                        source_id=intent.intent_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="PAYMENT_INTENT",
                        source_id=intent.intent_id,
                        journal_line_id=journal_line.line_id,
                        confidence=85,
                        explanation=f"Expense intent {intent.paystack_reference} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_intent_ids.add(intent.intent_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via expense PaymentIntent %s "
                        "(date+amount, Paystack OPEX)",
                        line.line_id,
                        journal_line.line_id,
                        intent.paystack_reference,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via expense date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Splynx payment loader (shared by passes 2 & 3) ─────────────
    def _load_splynx_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        *,
        config: AutoMatchDefaults | None = None,
    ) -> list[CustomerPayment]:
        """Load eligible Splynx payments for the statement's bank account.

        Filters: ``splynx_id IS NOT NULL``, status CLEARED, has GL journal,
        has correlation_id, and matching bank_account_id + date range.
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        pmt_query = select(CustomerPayment).where(
            CustomerPayment.organization_id == organization_id,
            CustomerPayment.bank_account_id == statement.bank_account_id,
            CustomerPayment.splynx_id.isnot(None),
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.isnot(None),
            CustomerPayment.correlation_id.isnot(None),
        )
        if statement.period_start and statement.period_end:
            pmt_query = pmt_query.where(
                CustomerPayment.payment_date >= statement.period_start - date_buffer,
                CustomerPayment.payment_date <= statement.period_end + date_buffer,
            )
        return list(db.scalars(pmt_query).all())

    # ── AP / AR payment loaders (passes 4 & 5) ──────────────────
    def _load_ap_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        *,
        config: AutoMatchDefaults | None = None,
    ) -> list[SupplierPayment]:
        """Load eligible AP supplier payments for the statement's bank account.

        Filters: status CLEARED, has GL journal, has correlation_id, and
        matching bank_account_id + date range.
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        pmt_query = select(SupplierPayment).where(
            SupplierPayment.organization_id == organization_id,
            SupplierPayment.bank_account_id == statement.bank_account_id,
            SupplierPayment.status == APPaymentStatus.CLEARED,
            SupplierPayment.journal_entry_id.isnot(None),
            SupplierPayment.correlation_id.isnot(None),
        )
        if statement.period_start and statement.period_end:
            pmt_query = pmt_query.where(
                SupplierPayment.payment_date >= statement.period_start - date_buffer,
                SupplierPayment.payment_date <= statement.period_end + date_buffer,
            )
        return list(db.scalars(pmt_query).all())

    def _load_ar_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        *,
        config: AutoMatchDefaults | None = None,
    ) -> list[CustomerPayment]:
        """Load eligible non-Splynx AR payments for the statement's bank account.

        Filters: status CLEARED, has GL journal,
        has correlation_id, and matching bank_account_id + date range.
        This catches AR receipts recorded directly in the app (not via
        Paystack or Splynx).
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        pmt_query = select(CustomerPayment).where(
            CustomerPayment.organization_id == organization_id,
            CustomerPayment.bank_account_id == statement.bank_account_id,
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.isnot(None),
            CustomerPayment.correlation_id.isnot(None),
        )
        if statement.period_start and statement.period_end:
            pmt_query = pmt_query.where(
                CustomerPayment.payment_date >= statement.period_start - date_buffer,
                CustomerPayment.payment_date <= statement.period_end + date_buffer,
            )
        return list(db.scalars(pmt_query).all())

    # ── Pass 2: Splynx CustomerPayment by reference ──────────────
    @staticmethod
    def _extract_paystack_ref(description: str | None) -> str | None:
        """Extract Paystack transaction ID from a payment description.

        Paystack refs are 12-14 lowercase hex characters, e.g.
        ``69871fd7d9178``.  Returns lowercase for case-insensitive matching.
        """
        if not description:
            return None
        match = _PAYSTACK_REF_RE.search(description)
        return match.group(0).lower() if match else None

    def _match_splynx_payments(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[CustomerPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Match lines against Splynx-originated CustomerPayments by reference.

        Builds two lookup dicts:
        1. Paystack ref extracted from ``description`` (primary)
        2. Splynx receipt number from ``reference`` (fallback)
        """
        # Build lookup: paystack_ref_from_description -> payment
        ref_to_payment: dict[str, CustomerPayment] = {}
        for pmt in payments:
            paystack_ref = self._extract_paystack_ref(pmt.description)
            if paystack_ref:
                ref_to_payment[paystack_ref] = pmt

        # Also add Splynx receipt numbers as fallback keys
        for pmt in payments:
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if not ref_to_payment:
            return

        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            try:
                payment = self._find_ref_in_line(line, ref_to_payment)  # type: ignore[attr-defined]
                if not payment:
                    continue

                tolerance = config.amount_tolerance if config else None
                if not self._amounts_match(  # type: ignore[attr-defined]
                    line.amount, payment.amount, tolerance=tolerance
                ):
                    logger.debug(
                        "Splynx ref in line %s but amount mismatch: "
                        "line=%s, payment=%s",
                        line.line_id,
                        line.amount,
                        payment.amount,
                    )
                    continue

                # Query already filters correlation_id IS NOT NULL,
                # but guard for mypy:
                if not payment.correlation_id:
                    continue

                journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    payment.correlation_id,
                    bank_account.gl_account_id,
                    extra_gl_account_ids=extra_gl_account_ids,
                )
                if not journal_line:
                    logger.debug(
                        "No GL journal for Splynx payment %s (ref: %s)",
                        payment.splynx_id,
                        payment.reference,
                    )
                    continue

                self._perform_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=payment.payment_id,
                )
                self._log_match(  # type: ignore[attr-defined]
                    db,
                    organization_id,
                    line=line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=payment.payment_id,
                    journal_line_id=journal_line.line_id,
                    confidence=95,
                    explanation=f"Splynx payment {payment.splynx_id} (reference match)",
                )
                matched_line_ids.add(line.line_id)
                matched_payment_ids.add(payment.payment_id)
                result.matched += 1
                logger.info(
                    "Auto-matched line %s to GL %s via Splynx payment %s (ref)",
                    line.line_id,
                    journal_line.line_id,
                    payment.splynx_id,
                )
            except Exception as e:
                logger.exception(
                    "Error matching line %s via Splynx payment: %s",
                    line.line_id,
                    e,
                )
                result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 3: Date + amount fallback matching ──────────────────
    def _match_by_date_amount(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[CustomerPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Match remaining lines by date + amount (greedy pairing).

        Groups both payments and statement lines by ``(date, amount_cents)``.
        For each group, pairs as many as possible — ``min(N_payments, N_lines)``
        — because every pair shares the same date and exact amount, so any
        pairing within the group is equally valid.
        """
        from datetime import date

        # Index payments by (date, amount_cents)
        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[CustomerPayment]] = {}
        for pmt in payments:
            if pmt.payment_id in matched_payment_ids:
                continue
            if not pmt.correlation_id:
                continue
            amount_cents = int(round(pmt.amount * 100))
            key: _DateAmountKey = (pmt.payment_date, amount_cents)
            pmt_index.setdefault(key, []).append(pmt)

        # Index lines by (date, amount_cents)
        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            amount_cents = int(round(line.amount * 100))
            key = (line.transaction_date, amount_cents)
            line_index.setdefault(key, []).append(line)

        # Greedy pairing: match min(payments, lines) for each (date, amount)
        for key, pmts in pmt_index.items():
            lines = line_index.get(key, [])
            available_lines = [ln for ln in lines if ln.line_id not in matched_line_ids]
            if not available_lines:
                continue

            pairs = min(len(pmts), len(available_lines))
            for i in range(pairs):
                pmt = pmts[i]
                line = available_lines[i]

                if pmt.payment_id in matched_payment_ids:
                    continue
                if line.line_id in matched_line_ids:
                    continue

                try:
                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        pmt.correlation_id,  # type: ignore[arg-type]
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for Splynx payment %s (date+amount)",
                            pmt.splynx_id,
                        )
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"Splynx payment {pmt.splynx_id} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via Splynx payment %s "
                        "(date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.splynx_id,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 4: AP supplier payment matching ──────────────────────
    def _match_ap_payments(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[SupplierPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Match debit bank lines against CLEARED AP supplier payments.

        Two-phase matching:
        A. **Reference** — builds lookup from ``payment_number`` and
           ``reference``, searches bank line text fields.
        B. **Date + amount** — greedy pairing fallback for remaining
           unmatched payments.

        Only considers **debit** (outgoing) bank lines, since AP payments
        are money going out.
        """
        from datetime import date

        # Phase A: Reference matching
        ref_to_payment: dict[str, SupplierPayment] = {}
        for pmt in payments:
            if pmt.payment_number:
                ref_to_payment[pmt.payment_number] = pmt
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if ref_to_payment:
            debit_lines = [
                line
                for line in unmatched_lines
                if line.line_id not in matched_line_ids
                and line.transaction_type == StatementLineType.debit
            ]
            for line in debit_lines:
                if line.line_id in matched_line_ids:
                    continue
                try:
                    payment = self._find_ref_in_line(line, ref_to_payment)  # type: ignore[attr-defined]
                    if not payment:
                        continue

                    tolerance = config.amount_tolerance if config else None
                    if not self._amounts_match(  # type: ignore[attr-defined]
                        line.amount, payment.amount, tolerance=tolerance
                    ):
                        logger.debug(
                            "AP ref in line %s but amount mismatch: "
                            "line=%s, payment=%s",
                            line.line_id,
                            line.amount,
                            payment.amount,
                        )
                        continue

                    if not payment.correlation_id:
                        continue

                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        payment.correlation_id,
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for AP payment %s (ref: %s)",
                            payment.payment_id,
                            payment.payment_number,
                        )
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=payment.payment_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=payment.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=100,
                        explanation=f"AP payment {payment.payment_number} (reference match)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(payment.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AP payment %s (ref)",
                        line.line_id,
                        journal_line.line_id,
                        payment.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AP payment ref: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

        # Phase B: Date + amount fallback
        remaining = [
            p
            for p in payments
            if p.payment_id not in matched_payment_ids and p.correlation_id
        ]
        if not remaining:
            return

        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[SupplierPayment]] = {}
        for pmt in remaining:
            key: _DateAmountKey = (pmt.payment_date, int(round(pmt.amount * 100)))
            pmt_index.setdefault(key, []).append(pmt)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.debit:
                continue
            key = (line.transaction_date, int(round(line.amount * 100)))
            line_index.setdefault(key, []).append(line)

        for key, pmts in pmt_index.items():
            lines = line_index.get(key, [])
            available_lines = [ln for ln in lines if ln.line_id not in matched_line_ids]
            if not available_lines:
                continue

            pairs = min(len(pmts), len(available_lines))
            for i in range(pairs):
                pmt = pmts[i]
                line = available_lines[i]
                if pmt.payment_id in matched_payment_ids:
                    continue
                if line.line_id in matched_line_ids:
                    continue
                try:
                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        pmt.correlation_id,  # type: ignore[arg-type]
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for AP payment %s (date+amount)",
                            pmt.payment_number,
                        )
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"AP payment {pmt.payment_number} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AP payment %s (date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AP date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 5: AR customer payment matching ────────────────────
    def _match_ar_payments(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[CustomerPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
        config: AutoMatchDefaults | None = None,
    ) -> None:
        """Match credit bank lines against non-Splynx AR customer payments.

        Two-phase matching:
        A. **Reference** — builds lookup from ``payment_number`` and
           ``reference``, searches bank line text fields.
        B. **Date + amount** — greedy pairing fallback for remaining
           unmatched payments.

        Only considers **credit** (incoming) bank lines, since AR payments
        are money coming in.
        """
        from datetime import date

        # Phase A: Reference matching
        ref_to_payment: dict[str, CustomerPayment] = {}
        for pmt in payments:
            if pmt.payment_number:
                ref_to_payment[pmt.payment_number] = pmt
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if ref_to_payment:
            credit_lines = [
                line
                for line in unmatched_lines
                if line.line_id not in matched_line_ids
                and line.transaction_type == StatementLineType.credit
            ]
            for line in credit_lines:
                if line.line_id in matched_line_ids:
                    continue
                try:
                    payment = self._find_ref_in_line(line, ref_to_payment)  # type: ignore[attr-defined]
                    if not payment:
                        continue

                    tolerance = config.amount_tolerance if config else None
                    if not self._amounts_match(  # type: ignore[attr-defined]
                        line.amount, payment.amount, tolerance=tolerance
                    ):
                        logger.debug(
                            "AR ref in line %s but amount mismatch: "
                            "line=%s, payment=%s",
                            line.line_id,
                            line.amount,
                            payment.amount,
                        )
                        continue

                    if not payment.correlation_id:
                        continue

                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        payment.correlation_id,
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for AR payment %s (ref: %s)",
                            payment.payment_id,
                            payment.payment_number,
                        )
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=payment.payment_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=payment.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=100,
                        explanation=f"AR payment {payment.payment_number} (reference match)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(payment.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AR payment %s (ref)",
                        line.line_id,
                        journal_line.line_id,
                        payment.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AR payment ref: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

        # Phase B: Date + amount fallback
        remaining = [
            p
            for p in payments
            if p.payment_id not in matched_payment_ids and p.correlation_id
        ]
        if not remaining:
            return

        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[CustomerPayment]] = {}
        for pmt in remaining:
            key: _DateAmountKey = (pmt.payment_date, int(round(pmt.amount * 100)))
            pmt_index.setdefault(key, []).append(pmt)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.credit:
                continue
            key = (line.transaction_date, int(round(line.amount * 100)))
            line_index.setdefault(key, []).append(line)

        for key, pmts in pmt_index.items():
            lines = line_index.get(key, [])
            available_lines = [ln for ln in lines if ln.line_id not in matched_line_ids]
            if not available_lines:
                continue

            pairs = min(len(pmts), len(available_lines))
            for i in range(pairs):
                pmt = pmts[i]
                line = available_lines[i]
                if pmt.payment_id in matched_payment_ids:
                    continue
                if line.line_id in matched_line_ids:
                    continue
                try:
                    journal_line = self._find_journal_line(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        pmt.correlation_id,  # type: ignore[arg-type]
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for AR payment %s (date+amount)",
                            pmt.payment_number,
                        )
                        continue

                    self._perform_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(  # type: ignore[attr-defined]
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"AR payment {pmt.payment_number} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AR payment %s (date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AR date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 6: Bank fee matching ─────────────────────────────────
