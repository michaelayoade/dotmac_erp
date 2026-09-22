"""Correct query-issued emails with the former broken self-service link.

Dry-run by default. Run --send only after reviewing the exact candidates.
The new notification is the delivery/audit record; the original stays intact.
"""

from __future__ import annotations

import argparse
import html
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select

from app.config import settings
from app.db.session_context import session_for_org
from app.models.email_profile import EmailModule
from app.models.notification import (
    EntityType,
    Notification,
    NotificationChannel,
    NotificationType,
)
from app.models.people.discipline.case import DisciplinaryCase
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.services.email import person_can_receive_email, send_email
from app.tenant_catalog import active_organization_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send corrected emails")
    parser.add_argument("--hours", type=int, default=72)
    args = parser.parse_args()
    if args.hours <= 0 or args.hours > 72:
        parser.error("--hours must be between 1 and 72")

    base_url = settings.app_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.netloc != "erp.dotmac.io":
        raise RuntimeError(f"Refusing unexpected production APP_URL: {base_url}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=args.hours)
    planned = sent = skipped = failed = 0
    print(f"mode={'SEND' if args.send else 'DRY-RUN'} cutoff_utc={cutoff.isoformat()}")
    for org_id in active_organization_ids():
        with session_for_org(org_id) as db:
            rows = db.execute(
                select(Notification, DisciplinaryCase, Person)
                .join(
                    DisciplinaryCase,
                    (DisciplinaryCase.case_id == Notification.entity_id)
                    & (
                        DisciplinaryCase.organization_id == Notification.organization_id
                    ),
                )
                .join(
                    Employee,
                    (Employee.employee_id == DisciplinaryCase.employee_id)
                    & (Employee.organization_id == Notification.organization_id),
                )
                .join(
                    Person,
                    (Person.id == Notification.recipient_id)
                    & (Person.id == Employee.person_id)
                    & (Person.organization_id == Notification.organization_id),
                )
                .where(
                    Notification.organization_id == org_id,
                    Notification.entity_type == EntityType.DISCIPLINE,
                    Notification.title.like("Disciplinary Query Issued - %"),
                    Notification.action_url.like("/people/self-service/discipline/%"),
                    Notification.email_sent.is_(True),
                    Notification.email_sent_at >= cutoff,
                )
                .order_by(Notification.email_sent_at, Notification.notification_id)
            ).all()
            for source, case, person in rows:
                title = (
                    f"Corrected link: Disciplinary Query Issued - {case.case_number}"
                )
                existing = db.scalar(
                    select(Notification.notification_id).where(
                        Notification.organization_id == org_id,
                        Notification.recipient_id == person.id,
                        Notification.entity_type == EntityType.DISCIPLINE,
                        Notification.entity_id == case.case_id,
                        Notification.title == title,
                    )
                )
                if existing or not person_can_receive_email(person):
                    skipped += 1
                    print(
                        f"SKIP source={source.notification_id} duplicate_or_ineligible"
                    )
                    continue

                action_url = f"/people/self/discipline/{case.case_id}"
                url = f"{base_url}{action_url}"
                message = (
                    f"The link in an earlier email about disciplinary case "
                    f"{case.case_number} was incorrect. Please open the query in "
                    "Dotmac ERP using the corrected link below. If you have "
                    "already responded, no further action is needed."
                )
                body_text = f"{message}\n\nView query: {url}"
                body_html = (
                    f"<p>{html.escape(message)}</p>"
                    f'<p><a href="{html.escape(url, quote=True)}">View query</a></p>'
                )
                planned += 1
                print(
                    f"{'SEND' if args.send else 'PLAN'} source={source.notification_id} case={case.case_number}"
                )
                if not args.send:
                    continue

                correction = Notification(
                    organization_id=org_id,
                    recipient_id=person.id,
                    entity_type=EntityType.DISCIPLINE,
                    entity_id=case.case_id,
                    notification_type=NotificationType.INFO,
                    channel=NotificationChannel.BOTH,
                    title=title,
                    message=message,
                    action_url=action_url,
                )
                db.add(correction)
                db.flush()
                try:
                    ok = send_email(
                        db=db,
                        to_email=person.email.strip(),
                        subject=title,
                        body_html=body_html,
                        body_text=body_text,
                        module=EmailModule.PEOPLE_PAYROLL,
                        organization_id=org_id,
                    )
                    if not ok:
                        raise RuntimeError("SMTP send returned false")
                    correction.email_sent = True
                    correction.email_sent_at = datetime.now(UTC)
                    db.commit()
                    sent += 1
                    print(
                        f"SENT source={source.notification_id} correction={correction.notification_id}"
                    )
                except Exception as exc:
                    db.rollback()
                    failed += 1
                    print(
                        f"FAILED source={source.notification_id} error={type(exc).__name__}: {exc}"
                    )

    print(f"summary planned={planned} sent={sent} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
