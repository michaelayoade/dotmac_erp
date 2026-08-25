"""Email delivery tasks for submitted weekly meeting reports."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - Python 3.10 compatibility
    UTC = timezone.utc

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.session_context import session_for_org
from app.models.email_profile import EmailModule
from app.models.people.perf.weekly_meeting_report import (
    ReportEmailStatus,
    WeeklyMeetingReport,
    WeeklyMeetingReportStatus,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def send_weekly_meeting_report_hr_email(
    self: Any, report_id: str, organization_id: str
) -> dict[str, Any]:
    """Claim and deliver one report email without duplicate concurrent sends."""
    report_uuid = UUID(report_id)
    org_uuid = UUID(organization_id)

    with session_for_org(org_uuid) as db:
        report = db.scalar(
            select(WeeklyMeetingReport)
            .where(
                WeeklyMeetingReport.organization_id == org_uuid,
                WeeklyMeetingReport.report_id == report_uuid,
            )
            .options(selectinload(WeeklyMeetingReport.participants))
            .with_for_update()
        )
        if report is None:
            return {"success": False, "reason": "report_not_found"}
        if report.status != WeeklyMeetingReportStatus.SUBMITTED:
            return {"success": False, "reason": "report_not_submitted"}
        if report.notification_status == ReportEmailStatus.SENT:
            return {"success": True, "reason": "already_sent"}
        if report.notification_status == ReportEmailStatus.PROCESSING:
            return {"success": False, "reason": "already_processing"}
        if report.notification_status not in {
            ReportEmailStatus.PENDING,
            ReportEmailStatus.FAILED,
        }:
            return {"success": False, "reason": "notification_not_pending"}

        recipient = (report.notification_recipient or "").strip()
        if not recipient or "@" not in recipient:
            report.notification_status = ReportEmailStatus.FAILED
            report.notification_last_error = "Invalid HR notification recipient"
            report.notification_attempted_at = datetime.now(UTC)
            report.notification_attempt_count += 1
            db.commit()
            return {"success": False, "reason": "invalid_recipient"}

        report.notification_status = ReportEmailStatus.PROCESSING
        report.notification_attempted_at = datetime.now(UTC)
        report.notification_attempt_count += 1
        snapshot = _email_snapshot(report)
        db.commit()

    try:
        from app.services.email import send_email

        with session_for_org(org_uuid) as db:
            send_email(
                db=db,
                to_email=recipient,
                subject=(
                    f"Weekly Meeting Report submitted: "
                    f"{snapshot['division']} — {snapshot['week_ending']}"
                ),
                body_html=_body_html(snapshot),
                body_text=_body_text(snapshot),
                raise_on_error=True,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=org_uuid,
            )
            delivered = db.scalar(
                select(WeeklyMeetingReport).where(
                    WeeklyMeetingReport.organization_id == org_uuid,
                    WeeklyMeetingReport.report_id == report_uuid,
                )
            )
            if delivered is not None:
                delivered.notification_status = ReportEmailStatus.SENT
                delivered.notification_sent_at = datetime.now(UTC)
                delivered.notification_last_error = None
            db.commit()
    except Exception as exc:
        with session_for_org(org_uuid) as db:
            failed = db.scalar(
                select(WeeklyMeetingReport).where(
                    WeeklyMeetingReport.organization_id == org_uuid,
                    WeeklyMeetingReport.report_id == report_uuid,
                )
            )
            if failed is not None:
                failed.notification_status = ReportEmailStatus.FAILED
                failed.notification_last_error = str(exc)[:2000]
            db.commit()
        logger.exception("Weekly meeting report email failed for %s", report_id)
        if isinstance(exc, OSError):
            raise
        return {"success": False, "reason": "delivery_failed", "error": str(exc)}

    return {"success": True, "recipient": recipient}


def _email_snapshot(report: WeeklyMeetingReport) -> dict[str, str]:
    action_url = f"/people/perf/weekly-meeting-reports/{report.report_id}"
    return {
        "report_number": report.report_number,
        "division": report.division_name_snapshot,
        "division_head": report.division_head_name_snapshot or "Not specified",
        "week_ending": report.week_ending.strftime("%d %b %Y"),
        "meeting_date": report.meeting_date.strftime("%d %b %Y"),
        "meeting_time": report.meeting_time.strftime("%H:%M"),
        "prepared_by": report.prepared_by_name_snapshot,
        "participant_count": str(len(report.participants)),
        "report_url": f"{settings.app_url.rstrip('/')}{action_url}",
    }


def _body_html(data: dict[str, str]) -> str:
    safe = {key: html.escape(value) for key, value in data.items()}
    return f"""
    <h2>Weekly Meeting Report submitted</h2>
    <p><strong>Report:</strong> {safe["report_number"]}</p>
    <p><strong>Division:</strong> {safe["division"]}</p>
    <p><strong>Division Head:</strong> {safe["division_head"]}</p>
    <p><strong>Week Ending:</strong> {safe["week_ending"]}</p>
    <p><strong>Meeting:</strong> {safe["meeting_date"]} at {safe["meeting_time"]}</p>
    <p><strong>Prepared By:</strong> {safe["prepared_by"]}</p>
    <p><strong>Participants:</strong> {safe["participant_count"]}</p>
    <p><a href="{safe["report_url"]}">Open the report in Dotmac ERP</a></p>
    """.strip()


def _body_text(data: dict[str, str]) -> str:
    return "\n".join(
        [
            "Weekly Meeting Report submitted",
            f"Report: {data['report_number']}",
            f"Division: {data['division']}",
            f"Division Head: {data['division_head']}",
            f"Week Ending: {data['week_ending']}",
            f"Meeting: {data['meeting_date']} at {data['meeting_time']}",
            f"Prepared By: {data['prepared_by']}",
            f"Participants: {data['participant_count']}",
            f"Open report: {data['report_url']}",
        ]
    )
