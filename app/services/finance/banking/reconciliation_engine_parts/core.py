"""ReconciliationEngineCore component."""

from __future__ import annotations

from app.services.finance.banking.reconciliation_engine_parts.base import (
    Any,
    BankAccount,
    BankStatement,
    BankStatementLine,
    Decimal,
    EngineContext,
    EngineResult,
    Session,
    UUID,
    _DEFAULT_TOLERANCE,
    logger,
)


class ReconciliationEngineCore:
    """Reconciliation engine methods for core."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Public API ──────────────────────────────────────────────────
    def run_custom_rules(
        self,
        organization_id: UUID,
        statement: BankStatement,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        *,
        amount_tolerance: Decimal = _DEFAULT_TOLERANCE,
        date_buffer_days: int = 3,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> EngineResult:
        """Process custom rules against unmatched statement lines.

        Called by ``AutoReconciliationService.auto_match_statement()``
        after system passes have completed.

        Args:
            organization_id: Tenant scope.
            statement: The statement being reconciled.
            bank_account: Associated bank account.
            unmatched_lines: All lines from the statement.
            matched_line_ids: Lines already matched by system passes
                (mutated in place as new matches are made).
            amount_tolerance: Amount matching tolerance.
            date_buffer_days: Date window for candidate loading.
            extra_gl_account_ids: Fallback GL accounts.

        Returns:
            EngineResult with match/error counts for custom rules only.
        """
        from app.services.finance.banking.reconciliation_rule_service import (
            ReconciliationRuleService,
        )

        rule_service = ReconciliationRuleService(self.db)
        result = EngineResult()

        # Load only custom (non-system) active rules
        all_rules = rule_service.get_active_rules(organization_id)
        custom_rules = [r for r in all_rules if not r.is_system]

        if not custom_rules:
            return result

        ctx = EngineContext(
            db=self.db,
            organization_id=organization_id,
            statement=statement,
            bank_account=bank_account,
            amount_tolerance=amount_tolerance,
            date_buffer_days=date_buffer_days,
            matched_line_ids=matched_line_ids,
            matched_source_ids=set(),
            extra_gl_account_ids=extra_gl_account_ids,
            result=result,
        )

        logger.info(
            "Running %d custom rules for statement %s",
            len(custom_rules),
            statement.statement_id,
        )

        for rule in custom_rules:
            still_unmatched = [
                line
                for line in unmatched_lines
                if line.line_id not in ctx.matched_line_ids
            ]
            if not still_unmatched:
                break

            # Filter lines by rule conditions
            eligible = [
                line
                for line in still_unmatched
                if rule_service.evaluate_conditions(rule, line)
            ]
            if not eligible:
                continue

            handler = self._get_handler(rule.source_doc_type)
            if not handler:
                logger.warning(
                    "No handler for source_doc_type=%s (rule=%s)",
                    rule.source_doc_type,
                    rule.name,
                )
                continue

            try:
                handler(ctx, rule, eligible, rule_service)
            except Exception as e:
                logger.exception("Error processing custom rule '%s': %s", rule.name, e)
                result.errors.append(f"Rule '{rule.name}': {e}")

        result.skipped = len(
            [l for l in unmatched_lines if l.line_id not in ctx.matched_line_ids]
        )

        logger.info(
            "Custom rules: %d matched, %d skipped, %d errors",
            result.matched,
            result.skipped,
            len(result.errors),
        )
        return result

    # ── Handler dispatch ────────────────────────────────────────────
    def _get_handler(self, source_doc_type: str) -> Any | None:
        """Return the handler function for a source document type."""
        handlers: dict[str, Any] = {
            "CUSTOMER_PAYMENT": self._handle_customer_payment,  # type: ignore[attr-defined]
            "SUPPLIER_PAYMENT": self._handle_supplier_payment,  # type: ignore[attr-defined]
            "PAYMENT_INTENT": self._handle_payment_intent,  # type: ignore[attr-defined]
            "BANK_FEE": self._handle_bank_fee,  # type: ignore[attr-defined]
            "INTER_BANK": self._handle_inter_bank,  # type: ignore[attr-defined]
        }
        return handlers.get(source_doc_type)

    # ── CUSTOMER_PAYMENT handler ────────────────────────────────────
