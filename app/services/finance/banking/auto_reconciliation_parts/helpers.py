"""AutoReconciliationHelperService component."""

from __future__ import annotations

from app.services.finance.banking.auto_reconciliation_parts.base import (
    AMOUNT_TOLERANCE,
    BankAccount,
    BankStatementLine,
    Decimal,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    Mapping,
    MultipleResultsFound,
    Session,
    UUID,
    _PAYSTACK_OPEX_RE,
    _T,
    joinedload,
    logger,
    select,
)


class AutoReconciliationHelperService:
    """Auto-reconciliation methods for helpers."""

    @staticmethod
    def _find_ref_in_line(
        line: BankStatementLine,
        ref_lookup: Mapping[str, _T],
    ) -> _T | None:
        """Search statement line text fields for a known reference string.

        Checks ``reference``, ``description``, and ``bank_reference`` for a
        substring match against any key in *ref_lookup*.

        Works for both PaymentIntent (keyed by paystack_reference) and
        CustomerPayment (keyed by reference) lookups.
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
            for text_field in search_fields:
                if ref_lower in text_field.lower():
                    return entity

        return None

    @staticmethod
    def _amounts_match(
        line_amount: Decimal,
        expected_amount: Decimal,
        tolerance: Decimal | None = None,
    ) -> bool:
        """Check if two amounts match within tolerance (default AMOUNT_TOLERANCE)."""
        return abs(line_amount - expected_amount) <= (
            tolerance if tolerance is not None else AMOUNT_TOLERANCE
        )

    @staticmethod
    def _is_paystack_opex_account(bank_account: BankAccount) -> bool:
        """True when account/bank name denotes Paystack OPEX."""
        account_name = (bank_account.account_name or "").strip()
        bank_name = (bank_account.bank_name or "").strip()
        return bool(
            _PAYSTACK_OPEX_RE.search(account_name)
            or _PAYSTACK_OPEX_RE.search(bank_name)
        )

    def _find_journal_line(
        self,
        db: Session,
        organization_id: UUID,
        correlation_id: str,
        gl_account_id: UUID,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> JournalEntryLine | None:
        """Find a GL journal entry line by correlation_id and GL account.

        Looks up the journal entry by ``correlation_id`` and then finds the
        line that hits the specified GL account (typically the bank account).

        If *extra_gl_account_ids* is provided, also checks those accounts as
        fallbacks — this handles cases where payments were reassigned to a
        different bank account but the GL journals still reference the old
        account.

        Works for both PaymentIntent (``correlation_id=str(intent_id)``)
        and Splynx payments (``correlation_id="splynx-pmt-{id}"``).
        """
        # unique() is required because joinedload on a collection produces
        # duplicate parent rows in the SQL JOIN result set.
        stmt = (
            select(JournalEntry)
            .options(joinedload(JournalEntry.lines))
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
        try:
            journal = db.execute(stmt).unique().scalar_one_or_none()
        except MultipleResultsFound:
            # Two POSTED journals share this correlation_id — a data condition
            # we can't safely auto-resolve. Skip the candidate so the bank line
            # stays unmatched and surfaces for manual review.
            dup_ids = list(
                db.execute(
                    select(JournalEntry.journal_entry_id).where(
                        JournalEntry.organization_id == organization_id,
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

        # Prefer the primary GL account
        for jl in journal.lines:
            if jl.account_id == gl_account_id:
                return jl

        # Fall back to extra GL accounts (e.g. old bank account GL)
        if extra_gl_account_ids:
            for jl in journal.lines:
                if jl.account_id in extra_gl_account_ids:
                    return jl

        return None

    def _perform_match(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
        *,
        source_type: str | None = None,
        source_id: UUID | None = None,
    ) -> None:
        """Delegate to BankReconciliationService.match_statement_line().

        Exceptions (e.g. HTTPException for already-matched lines) propagate
        to the caller's per-line try/except.
        """
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        recon_svc = BankReconciliationService()
        # Suggest-only: the auto-engine records a SUGGESTION, never a confirmed
        # match. A human confirms it in the reconciliation workspace.
        recon_svc.match_statement_line(
            db=db,
            organization_id=organization_id,
            statement_line_id=line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,  # System-suggested, no user
            force_match=True,
            source_type=source_type,
            source_id=source_id,
            match_state="suggested",
        )

    @staticmethod
    def _log_match(
        db: Session,
        organization_id: UUID,
        *,
        line: BankStatementLine,
        source_type: str,
        source_id: UUID | None,
        journal_line_id: UUID | None,
        confidence: int,
        explanation: str,
        action: str = "MATCHED",
    ) -> None:
        """Record a match in the reconciliation match log."""
        from app.services.finance.banking.reconciliation_rule_service import (
            ReconciliationRuleService,
        )

        rule_svc = ReconciliationRuleService(db)
        rule_svc.log_match(
            organization_id,
            rule_id=None,  # System passes don't use rule_id
            line_id=line.line_id,
            source_doc_type=source_type,
            source_doc_id=source_id,
            journal_line_id=journal_line_id,
            confidence=confidence,
            explanation=explanation,
            action=action,
        )
