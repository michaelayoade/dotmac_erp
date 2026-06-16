"""ReconciliationEngineHelpers component."""

from __future__ import annotations

from typing import cast

from app.services.finance.banking.reconciliation_engine_parts.base import (
    Any,
    BankStatementLine,
    Decimal,
    EngineContext,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    MultipleResultsFound,
    ReconciliationMatchRule,
    UUID,
    _HEX_REF_RE,
    date,
    joinedload,
    logger,
    select,
)


class ReconciliationEngineHelpers:
    """Reconciliation engine methods for helpers."""

    @staticmethod
    def _build_payment_ref_lookup(
        payments: list[Any],
    ) -> dict[str, Any]:
        """Build reference lookup from CustomerPayment records.

        Extracts references from multiple fields:
        - payment.reference (receipt number)
        - payment.payment_number
        - Hex IDs from payment.description (Paystack transaction IDs)
        """
        lookup: dict[str, Any] = {}
        for p in payments:
            if getattr(p, "reference", None):
                lookup[p.reference] = p
            if getattr(p, "payment_number", None):
                lookup[p.payment_number] = p
            # Extract hex IDs from description (gateway transaction refs)
            desc = getattr(p, "description", None)
            if desc:
                for match in _HEX_REF_RE.finditer(desc):
                    lookup[match.group()] = p
        return lookup

    @staticmethod
    def _build_supplier_ref_lookup(
        payments: list[Any],
    ) -> dict[str, Any]:
        """Build reference lookup from SupplierPayment records."""
        lookup: dict[str, Any] = {}
        for p in payments:
            if getattr(p, "payment_number", None):
                lookup[p.payment_number] = p
            if getattr(p, "reference", None):
                lookup[p.reference] = p
        return lookup

    @staticmethod
    def _find_ref_in_line(
        line: BankStatementLine,
        ref_lookup: dict[str, Any],
    ) -> Any | None:
        """Search statement line text fields for a known reference.

        Checks reference, description, and bank_reference for a
        case-insensitive substring match against lookup keys.
        """
        search_fields: list[str] = []
        if line.reference:
            search_fields.append(line.reference)
        if line.description:
            search_fields.append(line.description)
        if line.bank_reference:
            search_fields.append(line.bank_reference)

        if not search_fields:
            return None

        for ref, entity in ref_lookup.items():
            ref_lower = ref.lower()
            for text in search_fields:
                if ref_lower in text.lower():
                    return entity

        return None

    @staticmethod
    def _amounts_match(
        line_amount: Decimal,
        expected: Decimal,
        tolerance: Decimal,
    ) -> bool:
        """Check if two amounts match within tolerance."""
        return abs(line_amount - expected) <= tolerance

    @staticmethod
    def _get_correlation_id(source_doc: Any, source_type: str) -> str | None:
        """Derive the GL correlation_id from a source document.

        Each source document type has a known correlation_id pattern
        used when posting its GL journal entry.
        """
        if source_type == "PAYMENT_INTENT":
            return str(source_doc.intent_id)
        if source_type == "CUSTOMER_PAYMENT":
            # Splynx payments use a special prefix
            if getattr(source_doc, "splynx_id", None):
                return f"splynx-pmt-{source_doc.splynx_id}"
            return str(source_doc.payment_id)
        if source_type == "SUPPLIER_PAYMENT":
            return str(source_doc.payment_id)
        return None

    def _find_journal_line(
        self,
        ctx: EngineContext,
        correlation_id: str,
        gl_account_id: UUID,
    ) -> JournalEntryLine | None:
        """Find GL journal entry line by correlation_id + account."""
        stmt = (
            select(JournalEntry)
            .options(joinedload(JournalEntry.lines))
            .where(
                JournalEntry.organization_id == ctx.organization_id,
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
        try:
            journal = self.db.execute(stmt).unique().scalar_one_or_none()  # type: ignore[attr-defined]
        except MultipleResultsFound:
            # Two POSTED journals share this correlation_id — a data condition
            # we can't safely auto-resolve. Skip the candidate so the bank line
            # stays unmatched and surfaces for manual review.
            dup_ids = list(
                self.db.execute(  # type: ignore[attr-defined]
                    select(JournalEntry.journal_entry_id).where(
                        JournalEntry.organization_id == ctx.organization_id,
                        JournalEntry.correlation_id == correlation_id,
                        JournalEntry.status == JournalStatus.POSTED,
                    )
                )
                .scalars()
                .all()
            )
            logger.warning(
                "Skipping match: %d POSTED journals share correlation_id=%s; "
                "journal_entry_ids=%s",
                len(dup_ids),
                correlation_id,
                [str(jid) for jid in dup_ids],
            )
            return None
        if not journal:
            return None

        # Prefer primary GL account
        for jl in journal.lines:
            if jl.account_id == gl_account_id:
                return cast(JournalEntryLine, jl)

        # Fall back to extra GL accounts
        if ctx.extra_gl_account_ids:
            for jl in journal.lines:
                if jl.account_id in ctx.extra_gl_account_ids:
                    return cast(JournalEntryLine, jl)

        return None

    def _date_amount_fallback(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        candidates: list[Any],
        *,
        source_type: str,
        get_id: Any,
        get_amount: Any,
        get_date: Any,
        get_correlation_id: Any,
        rule_service: Any,
    ) -> None:
        """Fallback matching by date + amount (unique pairs only).

        Only matches when exactly one candidate and one line share
        the same date and amount. Prevents false positives.
        """
        _DateAmountKey = tuple[date, int]

        # Index candidates by (date, amount_cents)
        candidate_index: dict[_DateAmountKey, list[Any]] = {}
        for c in candidates:
            cid = get_id(c)
            if cid in ctx.matched_source_ids:
                continue
            c_date = get_date(c)
            if c_date is None:
                continue
            key: _DateAmountKey = (
                c_date,
                int(round(abs(get_amount(c)) * 100)),
            )
            candidate_index.setdefault(key, []).append(c)

        # Index eligible lines by (date, amount_cents)
        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue
            key = (
                line.transaction_date,
                int(round(abs(line.amount) * 100)),
            )
            line_index.setdefault(key, []).append(line)

        # Match unique pairs only
        for key, key_candidates in candidate_index.items():
            key_lines = line_index.get(key, [])
            available_lines = [
                ln for ln in key_lines if ln.line_id not in ctx.matched_line_ids
            ]
            available_candidates = [
                c for c in key_candidates if get_id(c) not in ctx.matched_source_ids
            ]

            if len(available_lines) != 1 or len(available_candidates) != 1:
                continue

            line = available_lines[0]
            candidate = available_candidates[0]

            corr_id = get_correlation_id(candidate)
            if not corr_id:
                continue

            journal_line = self._find_journal_line(
                ctx, corr_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(
                ctx,
                rule,
                line,
                journal_line,
                source_type=source_type,
                source_id=get_id(candidate),
                confidence=80,
                explanation=(
                    f"Date+amount fallback: {line.transaction_date} / {line.amount}"
                ),
                rule_service=rule_service,
            )

    def _execute_match(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
        *,
        source_type: str,
        source_id: UUID | None,
        confidence: int,
        explanation: str,
        rule_service: Any,
    ) -> None:
        """Perform a match and log it."""
        action = "MATCHED"
        if confidence < rule.min_confidence:
            action = "SUGGESTED"
            # Don't actually match — just log the suggestion
            rule_service.log_match(
                ctx.organization_id,
                rule_id=rule.rule_id,
                line_id=line.line_id,
                source_doc_type=source_type,
                source_doc_id=source_id,
                journal_line_id=journal_line.line_id,
                confidence=confidence,
                explanation=explanation,
                action="SUGGESTED",
            )
            return

        # Perform the actual match
        self._perform_match_action(ctx, line, journal_line, source_type, source_id)

        # Log the match
        rule_service.log_match(
            ctx.organization_id,
            rule_id=rule.rule_id,
            line_id=line.line_id,
            source_doc_type=source_type,
            source_doc_id=source_id,
            journal_line_id=journal_line.line_id,
            confidence=confidence,
            explanation=explanation,
            action=action,
        )

        logger.info(
            "Engine matched line %s via rule '%s' (%s, conf=%d)",
            line.line_id,
            rule.name,
            source_type,
            confidence,
        )

    def _perform_match_action(
        self,
        ctx: EngineContext,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
        source_type: str,
        source_id: UUID | None,
    ) -> None:
        """Delegate match to BankReconciliationService."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        recon_svc = BankReconciliationService()
        # Suggest-only: record a SUGGESTION, never a confirmed match.
        recon_svc.match_statement_line(
            db=ctx.db,
            organization_id=ctx.organization_id,
            statement_line_id=line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,
            force_match=True,
            source_type=source_type,
            source_id=source_id,
            match_state="suggested",
        )
        ctx.matched_line_ids.add(line.line_id)
        if source_id:
            ctx.matched_source_ids.add(source_id)
        ctx.result.matched += 1

    @staticmethod
    def _render_label(
        template: str | None,
        line: BankStatementLine,
        *,
        default: str,
    ) -> str:
        """Render a journal label template with line data."""
        if not template:
            return default
        try:
            return template.format(
                date=line.transaction_date,
                description=line.description or "",
                reference=line.reference or "",
                amount=line.amount,
            )
        except (KeyError, IndexError):
            return default
