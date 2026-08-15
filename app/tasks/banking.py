"""
Banking Background Tasks - Celery tasks for banking reconciliation workflows.

Handles:
- Periodic auto-matching of unreconciled bank statement lines
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

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

    Session shape (fan out over tenants, then one session per statement):

    1. Enumerate organizations through :func:`app.tenant_catalog
       .organization_ids` — the narrow ``SECURITY DEFINER`` catalogue.
       The listing SELECT then runs inside that tenant's own session, so it
       is filtered by the ORM listener *and* by PostgreSQL RLS.
    2. Process each statement under its own :func:`session_for_org` —
       per-statement session, single commit, isolated failure. This avoids
       (a) the SET LOCAL clearing that would silently de-prime a
       shared session after the first commit, and (b) identity-map
       contamination between tenants on the same session.

    The listing previously ran under :func:`cross_org_session`, which lifts
    only the SQLAlchemy listener and never PostgreSQL RLS. Under ``app_user``
    that SELECT returns zero rows, so the job matched nothing and logged a
    successful run — indistinguishable from a fleet with nothing to match.

    ``include_inactive=True``: the old SELECT had no ``Organization`` predicate,
    so it saw deactivated tenants' statements. A deactivated tenant's bank
    statements still have to reconcile.

    Returns:
        Dict with processing statistics.
    """
    from sqlalchemy import select

    from app.db.session_context import session_for_org
    from app.models.finance.banking.bank_statement import BankStatement
    from app.services.finance.banking.auto_reconciliation import (
        AutoReconciliationService,
    )
    from app.tenant_catalog import organization_ids

    logger.info("Starting periodic auto-match of unreconciled statements")

    results: dict[str, Any] = {
        "statements_processed": 0,
        "total_matched": 0,
        "errors": [],
    }

    auto_svc = AutoReconciliationService()

    for org_id in organization_ids(include_inactive=True):
        # Step 1 — this tenant's unmatched statements, read inside its own
        # session. The org column no longer has to be selected: the session
        # already is the organization.
        with session_for_org(org_id) as listing_db:
            statement_ids = list(
                listing_db.scalars(
                    select(BankStatement.statement_id).where(
                        BankStatement.unmatched_lines > 0
                    )
                ).all()
            )

        # Step 2 — per-statement matching, fresh session + single commit.
        for stmt_id in statement_ids:
            try:
                with session_for_org(org_id) as db:
                    match_result = auto_svc.auto_match_statement(db, org_id, stmt_id)
                    db.commit()

                    if match_result.matched > 0:
                        results["total_matched"] += match_result.matched
                        logger.info(
                            "Auto-matched %d lines for statement %s (org %s)",
                            match_result.matched,
                            stmt_id,
                            org_id,
                        )

                    if match_result.errors:
                        for err in match_result.errors:
                            results["errors"].append(f"Statement {stmt_id}: {err}")

                    results["statements_processed"] += 1

            except Exception as e:
                logger.exception("Failed to auto-match statement %s", stmt_id)
                results["errors"].append(f"Statement {stmt_id}: {e}")

    logger.info(
        "Periodic auto-match complete: %d statements, %d lines matched",
        results["statements_processed"],
        results["total_matched"],
    )

    return results
