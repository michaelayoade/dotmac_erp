from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.models.support.comment import CommentType
from app.models.support.ticket import Ticket
from app.services.support.web import (
    _format_system_activity_content,
    _format_ticket_for_list,
    support_web_service,
)


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


def test_format_system_activity_content_renders_machine_payload_readably() -> None:
    raw = (
        '<ticket.created {"channel": "chat", "priority": "medium", "status": "open", '
        '"subject": "Ekele Idachaba (100016041 - 16041)", '
        '"title": "Ekele Idachaba (100016041 - 16041)"}>'
    )

    formatted = _format_system_activity_content(raw)

    assert (
        formatted
        == "Ticket Created: Ekele Idachaba (100016041 - 16041) | Status: Open | Priority: Medium | Channel: Chat"
    )


def test_format_system_activity_content_without_angle_brackets() -> None:
    raw = (
        'ticket.created {"channel": "chat", "priority": "medium", "status": "open", '
        '"subject": "Ekele Idachaba (100016041 - 16041)", '
        '"title": "Ekele Idachaba (100016041 - 16041)"}'
    )

    formatted = _format_system_activity_content(raw)

    assert (
        formatted
        == "Ticket Created: Ekele Idachaba (100016041 - 16041) | Status: Open | Priority: Medium | Channel: Chat"
    )


def test_format_system_activity_content_handles_html_escaped_payload() -> None:
    raw = (
        "&lt;ticket.created {&quot;channel&quot;: &quot;chat&quot;, "
        "&quot;status&quot;: &quot;open&quot;}&gt;"
    )

    formatted = _format_system_activity_content(raw)

    assert formatted == "Ticket Created: Status: Open | Channel: Chat"


def test_format_activity_timeline_applies_system_content_formatter() -> None:
    activity = SimpleNamespace(
        author=None,
        comment_type=CommentType.SYSTEM,
        action="status_change",
        content='<ticket.created {"channel":"chat","status":"open"}>',
        old_value=None,
        new_value=None,
        created_at=datetime(2026, 3, 14, 12, 52),
    )

    timeline = support_web_service._format_activity_timeline([activity])

    assert timeline[0]["type"] == "system"
    assert timeline[0]["content"] == "Ticket Created: Status: Open | Channel: Chat"


def test_format_activity_timeline_treats_string_system_comment_type_as_system() -> None:
    activity = SimpleNamespace(
        author=None,
        comment_type="system",
        action="status_change",
        content='<ticket.created {"channel":"chat","status":"open"}>',
        old_value=None,
        new_value=None,
        created_at=datetime(2026, 3, 14, 12, 52),
    )

    timeline = support_web_service._format_activity_timeline([activity])

    assert timeline[0]["type"] == "system"
    assert timeline[0]["content"] == "Ticket Created: Status: Open | Channel: Chat"
