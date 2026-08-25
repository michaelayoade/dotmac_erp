from __future__ import annotations

from datetime import date, time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.people.perf.weekly_meeting_report import WeeklyMeetingReport


TASK_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "tasks" / "weekly_meeting_reports.py"
)


def _load_task_module(monkeypatch) -> ModuleType:
    """Load this task without importing the package's unrelated task modules."""
    session_context = ModuleType("app.db.session_context")
    session_context.session_for_org = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.db.session_context", session_context)
    spec = spec_from_file_location(
        f"weekly_meeting_report_task_under_test_{uuid4().hex}", TASK_PATH
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_weekly_report_email_snapshot_links_to_the_submitted_report(
    monkeypatch,
) -> None:
    task_module = _load_task_module(monkeypatch)
    report_id = uuid4()
    report = WeeklyMeetingReport(
        report_id=report_id,
        organization_id=uuid4(),
        report_number="WMR-20260821-OPS",
        department_id=uuid4(),
        division_name_snapshot="Operations",
        division_head_name_snapshot="Division Head",
        week_ending=date(2026, 8, 21),
        meeting_date=date(2026, 8, 20),
        meeting_time=time(9, 30),
        prepared_by_person_id=uuid4(),
        prepared_by_name_snapshot="Report Owner",
    )
    report.participants = []
    monkeypatch.setattr(task_module.settings, "app_url", "https://erp.example.com/")

    snapshot = task_module._email_snapshot(report)

    assert snapshot["report_url"] == (
        f"https://erp.example.com/people/perf/weekly-meeting-reports/{report_id}"
    )
    assert snapshot["participant_count"] == "0"
    assert "WMR-20260821-OPS" in task_module._body_text(snapshot)


def test_weekly_report_html_email_escapes_snapshot_values(monkeypatch) -> None:
    task_module = _load_task_module(monkeypatch)
    body = task_module._body_html(
        {
            "report_number": "WMR-1",
            "division": "<script>alert(1)</script>",
            "division_head": "Head & Lead",
            "week_ending": "21 Aug 2026",
            "meeting_date": "20 Aug 2026",
            "meeting_time": "09:30",
            "prepared_by": "Owner",
            "participant_count": "2",
            "report_url": "https://erp.example.com/report?a=1&b=2",
        }
    )

    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "Head &amp; Lead" in body
    assert "a=1&amp;b=2" in body


def test_weekly_report_email_task_suppresses_an_already_sent_report(
    monkeypatch,
) -> None:
    task_module = _load_task_module(monkeypatch)
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        status=task_module.WeeklyMeetingReportStatus.SUBMITTED,
        notification_status=task_module.ReportEmailStatus.SENT,
    )
    monkeypatch.setattr(task_module, "session_for_org", lambda _org_id: nullcontext(db))

    result = task_module.send_weekly_meeting_report_hr_email.run(
        str(uuid4()), str(uuid4())
    )

    assert result == {"success": True, "reason": "already_sent"}
    db.commit.assert_not_called()
