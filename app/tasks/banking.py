"""
Banking Background Tasks - Celery tasks for banking reconciliation workflows.

Handles:
- Periodic auto-matching of unreconciled bank statement lines
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def auto_match_unreconciled_statements() -> dict[str, Any]:
    """Periodically auto-match unreconciled statement lines.

    Scans all statements with unmatched lines and runs deterministic
    matching via two strategies:

    1. **PaymentIntent** — DotMac-initiated Paystack transfers
    2. **Splynx CustomerPayment** — Splynx-originated payments

    This catches cases where a GL journal was posted *after* the
    statement was imported, as well as backfilling matches on
    historical statements.

    Each statement is processed in its own savepoint so a failure
    in one does not roll back matches already committed for others.

    Returns:
        Dict with processing statistics.
    """
    from sqlalchemy import select

    from app.models.finance.banking.bank_statement import BankStatement
    from app.services.finance.banking.auto_reconciliation import (
        AutoReconciliationService,
    )

    logger.info("Starting periodic auto-match of unreconciled statements")

    results: dict[str, Any] = {
        "statements_processed": 0,
        "total_matched": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # NOTE: Intentionally queries across all organizations — this is a global
        # periodic task that processes every tenant's unmatched statements.
        statements = list(
            db.scalars(
                select(BankStatement).where(
                    BankStatement.unmatched_lines > 0,
                )
            ).all()
        )

        auto_svc = AutoReconciliationService()

        for statement in statements:
            try:
                with db.begin_nested():  # savepoint
                    match_result = auto_svc.auto_match_statement(
                        db,
                        statement.organization_id,
                        statement.statement_id,
                    )
                    if match_result.matched > 0:
                        results["total_matched"] += match_result.matched
                        logger.info(
                            "Auto-matched %d lines for statement %s (org %s)",
                            match_result.matched,
                            statement.statement_id,
                            statement.organization_id,
                        )

                    if match_result.errors:
                        for err in match_result.errors:
                            results["errors"].append(
                                f"Statement {statement.statement_id}: {err}"
                            )
                # Savepoint auto-commits on successful exit
                results["statements_processed"] += 1

            except Exception as e:
                # Savepoint auto-rolls back on exception
                logger.exception(
                    "Failed to auto-match statement %s", statement.statement_id
                )
                results["errors"].append(f"Statement {statement.statement_id}: {e}")

        # Single commit at the end for all successful savepoints
        db.commit()

    logger.info(
        "Periodic auto-match complete: %d statements, %d lines matched",
        results["statements_processed"],
        results["total_matched"],
    )

    return results
