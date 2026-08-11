"""AutoReconciliationCoreService component."""

from __future__ import annotations

import os

from app.services.finance.banking.auto_reconciliation_parts.base import (
    AMOUNT_TOLERANCE,
    AutoMatchDefaults,
    AutoMatchResult,
    BankAccount,
    BankStatement,
    BankStatementLine,
    CONTRA_DATE_WINDOW_DAYS,
    CONTRA_MIN_SCORE,
    PostingResult,
    ProgrammaticReconciliationEngine,
    ReconciliationRunContext,
    Session,
    StatementLineType,
    UUID,
    _CONTRA_TRANSFER_RE,
    build_extra_gl_account_ids,
    date,
    logger,
    reconciliation_policy_service,
    select,
)


class AutoReconciliationCoreService:
    """Auto-reconciliation methods for core."""

    @staticmethod
    def _auto_match_enabled() -> bool:
        """Return whether banking auto-match is enabled for this deployment."""
        return os.getenv("BANKING_AUTO_MATCH_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    @staticmethod
    def _post_with_period_fallback(
        db: Session,
        *,
        organization_id: UUID,
        journal_entry_id: UUID,
        posting_date: date,
        idempotency_key: str,
        source_module: str,
        correlation_id: str | None,
        posted_by_user_id: UUID,
        success_message: str = "Posted successfully",
        error_prefix: str = "Ledger posting failed",
    ) -> PostingResult:
        """Post to ledger, retrying with today's date on closed-period failure.

        Bank fee and settlement journals may reference transaction dates in
        already-closed fiscal periods.  When the first attempt fails with a
        period-related error we retry using ``date.today()`` which should fall
        in the current open period.
        """
        from datetime import date as _date

        from app.services.finance.posting.base import BasePostingAdapter

        result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=organization_id,
            journal_entry_id=journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module=source_module,
            correlation_id=correlation_id,
            posted_by_user_id=posted_by_user_id,
            success_message=success_message,
            error_prefix=error_prefix,
        )
        if result.success:
            return result

        # Detect closed-period failures and retry with today
        msg_lower = result.message.lower()
        if "period" in msg_lower or "closed" in msg_lower:
            today = _date.today()
            if today != posting_date:
                logger.info(
                    "Posting failed for date %s (period closed); retrying with %s",
                    posting_date,
                    today,
                )
                return BasePostingAdapter.post_to_ledger(
                    db,
                    organization_id=organization_id,
                    journal_entry_id=journal_entry_id,
                    posting_date=today,
                    idempotency_key=idempotency_key,
                    source_module=source_module,
                    correlation_id=correlation_id,
                    posted_by_user_id=posted_by_user_id,
                    success_message=success_message,
                    error_prefix=error_prefix,
                )

        return result

    # ── Public API ──────────────────────────────────────────────────
    def auto_match_statement(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        *,
        include_contra_suggestions: bool = False,
    ) -> AutoMatchResult:
        """Match unmatched statement lines against known internal payments.

        Runs seven passes in sequence:
        1. PaymentIntent (Paystack-initiated transfers)
        4. AP supplier payments (by reference, then date + amount)
        3. AR customer payments (by reference, then date + amount)
        6. Bank fees (creates GL journals for Paystack fee lines)
        7. Settlements (cross-bank transfer matching, 0–5 day window)

        Lines matched in earlier passes are excluded from later passes.

        Args:
            db: Database session.
            organization_id: Tenant scope.
            statement_id: The statement to process.

        Returns:
            AutoMatchResult with match/skip/error counts.
        """
        result = AutoMatchResult()
        if not self._auto_match_enabled():
            logger.info(
                "Banking auto-match disabled by BANKING_AUTO_MATCH_ENABLED=false"
            )
            return result

        # Runtime configuration is resolved from ``ReconciliationPolicyProfile``.
        # The transient ``AutoMatchDefaults`` supplies dataclass defaults (all
        # passes enabled, 1¢ tolerance, etc.) so the policy layer's
        # profile-override logic keeps working when individual profile columns
        # are NULL.  No DomainSettings round-trip happens here anymore.
        config = AutoMatchDefaults()
        policy = reconciliation_policy_service.resolve(
            db,
            organization_id,
            legacy_config=config,
        )

        # 1. Load statement + bank account
        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            result.errors.append(f"Statement {statement_id} not found")
            return result

        bank_account = db.get(BankAccount, statement.bank_account_id)
        if not bank_account or not bank_account.gl_account_id:
            result.errors.append("Bank account or GL account not configured")
            return result

        # Build fallback GL accounts: all OTHER bank accounts' GL IDs.
        # This handles payments whose GL journals still reference a
        # previous bank account's GL after a bank_account_id reassignment.
        extra_gl = build_extra_gl_account_ids(db, organization_id, bank_account)

        # 2. Load unmatched lines
        unmatched_lines = list(
            db.scalars(
                select(BankStatementLine).where(
                    BankStatementLine.statement_id == statement_id,
                    BankStatementLine.is_matched.is_(False),
                )
            ).all()
        )

        if not unmatched_lines:
            return result

        # 2b. Ensure system rules are seeded (idempotent, needed for audit trail)
        try:
            from app.services.finance.banking.reconciliation_rule_service import (
                ReconciliationRuleService,
            )

            ReconciliationRuleService.seed_system_rules(db, organization_id)
        except Exception:
            logger.warning("Failed to seed system match rules", exc_info=True)

        matched_line_ids: set[UUID] = set()
        engine_ctx = ReconciliationRunContext(
            db=db,
            organization_id=organization_id,
            statement=statement,
            bank_account=bank_account,
            unmatched_lines=unmatched_lines,
            matched_line_ids=matched_line_ids,
            extra_gl_account_ids=extra_gl,
            config=config,
            policy=policy,
            result=result,
        )
        ProgrammaticReconciliationEngine().run(self, engine_ctx)

        # Recalculate skipped (lines not matched by any pass)
        result.skipped = len(unmatched_lines) - result.matched - len(result.errors)

        # 12. Optional dry-run pass: contra transfer suggestions (no posting)
        if include_contra_suggestions:
            still_unmatched = [
                line for line in unmatched_lines if line.line_id not in matched_line_ids
            ]
            self._suggest_contra_transfers(
                db,
                organization_id,
                bank_account,
                still_unmatched,
                matched_line_ids,
                result,
            )

        return result

    def _suggest_contra_transfers(
        self,
        db: Session,
        organization_id: UUID,
        source_bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
    ) -> None:
        """Suggest contra transfer pairs in dry-run mode (no side effects)."""
        from datetime import timedelta

        from app.services.finance.banking.contra_matching import (
            ContraLineCandidate,
            choose_best_contra_matches,
        )

        source_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.transaction_type == StatementLineType.debit
            and (
                (line.description and _CONTRA_TRANSFER_RE.search(line.description))
                or (line.reference and _CONTRA_TRANSFER_RE.search(line.reference))
            )
        ]
        if not source_lines:
            return

        other_bank_ids = list(
            db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.bank_account_id != source_bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_bank_ids:
            return

        date_window = timedelta(days=CONTRA_DATE_WINDOW_DAYS)
        min_date = min(line.transaction_date for line in source_lines) - date_window
        max_date = max(line.transaction_date for line in source_lines) + date_window

        destination_lines = list(
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
                    BankStatementLine.transaction_type == StatementLineType.credit,
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )
        if not destination_lines:
            return

        source_candidates = [
            ContraLineCandidate(
                line_id=line.line_id,
                bank_account_id=source_bank_account.bank_account_id,
                transaction_date=line.transaction_date,
                amount=line.amount,
                reference=line.reference,
                description=line.description,
            )
            for line in source_lines
        ]
        destination_candidates = []
        for line in destination_lines:
            statement = db.get(BankStatement, line.statement_id)
            if not statement:
                continue
            destination_candidates.append(
                ContraLineCandidate(
                    line_id=line.line_id,
                    bank_account_id=statement.bank_account_id,
                    transaction_date=line.transaction_date,
                    amount=line.amount,
                    reference=line.reference,
                    description=line.description,
                )
            )
        if not destination_candidates:
            return

        matches = choose_best_contra_matches(
            source_candidates,
            destination_candidates,
            amount_tolerance=AMOUNT_TOLERANCE,
            date_window_days=CONTRA_DATE_WINDOW_DAYS,
            min_score=CONTRA_MIN_SCORE,
        )
        for match in matches:
            payload: dict[str, object] = {
                "source_line_id": str(match.source_line_id),
                "destination_line_id": str(match.destination_line_id),
                "score": match.score,
                "date_diff_days": match.date_diff_days,
                "amount_diff": str(match.amount_diff),
                "reasons": match.reasons,
            }
            result.contra_suggestions.append(payload)

    # ── Pass 1: PaymentIntent matching ──────────────────────────────
