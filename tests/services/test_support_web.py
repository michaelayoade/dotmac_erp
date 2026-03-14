from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.models.support.ticket import Ticket
from app.services.support.web import _format_ticket_for_list


def test_format_ticket_for_list_handles_string_status_and_missing_priority() -> None:
    ticket = Ticket(
        ticket_id=uuid4(),
        organization_id=uuid4(),
        ticket_number="TCK-1001",
        subject="Login issue",
        description="User cannot sign in",
        status="open",
        priority=None,
        opening_date=date(2025, 1, 15),
    )

    formatted = _format_ticket_for_list(ticket)

    assert formatted["status"] == "OPEN"
    assert formatted["status_label"] == "Open"
    assert formatted["priority"] == "MEDIUM"
    assert formatted["priority_label"] == "Medium"
