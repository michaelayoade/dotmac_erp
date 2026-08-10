"""Discovering which organizations have allocation work to do.

`FIFOAllocationService.allocate_for_org` already owns the FIFO decision, so
`scripts/allocate_splynx_fifo.py` was nearly a thin adapter already. What it
was NOT thin about was choosing the organization:

    SELECT DISTINCT organization_id
    FROM ar.customer_payment
    WHERE splynx_id IS NOT NULL
    LIMIT 1

It inferred the tenant from data, took the first row an unordered scan
happened to return, and justified it with a comment — "all Splynx data
belongs to one org". That is a single-tenant assumption embedded in a
multi-tenant system, and it fails quietly in both directions: a second
organization with Splynx payments would be skipped entirely, and *which* one
ran would depend on the plan Postgres chose that day.

Worse than a hardcoded `ORG_ID`, in one respect — a constant is at least
deterministic and greppable.

This module returns **every** such organization, so the caller iterates
rather than guesses. The scope then comes from the caller in the ordinary
way, one `session_for_org` per organization.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def organizations_with_splynx_payments(db: Session) -> list[uuid.UUID]:
    """Every organization holding at least one Splynx-sourced payment.

    Must run on a cross-organization session: it is asking a question ABOUT
    tenants, so it cannot be asked from inside one.
    """
    rows = db.execute(
        text("""
            SELECT DISTINCT organization_id
            FROM ar.customer_payment
            WHERE splynx_id IS NOT NULL
            ORDER BY organization_id
        """)
    ).all()
    return [uuid.UUID(str(row[0])) for row in rows]
