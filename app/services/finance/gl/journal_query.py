"""
Shared GL journal query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalEntry
from app.services.common import coerce_uuid
from app.services.finance.gl.web.base import parse_date, parse_status


def build_journal_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Select:
    """
    Build the base GL journal query with filters applied.
    """
    org_id = coerce_uuid(organization_id)
    status_value = parse_status(status)
    from_date = parse_date(start_date)
    to_date = parse_date(end_date)

    query = select(JournalEntry).where(
        JournalEntry.organization_id == org_id
    )

    if status_value:
        query = query.where(JournalEntry.status == status_value)
    if from_date:
        query = query.where(JournalEntry.posting_date >= from_date)
    if to_date:
        query = query.where(JournalEntry.posting_date <= to_date)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (JournalEntry.journal_number.ilike(search_pattern))
            | (JournalEntry.description.ilike(search_pattern))
            | (JournalEntry.reference.ilike(search_pattern))
        )

    return query
