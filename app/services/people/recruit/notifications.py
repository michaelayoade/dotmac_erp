"""Recruitment notification helpers."""

from __future__ import annotations

import logging
import os
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_profile import EmailModule
from app.models.people.recruit import JobApplicant, JobOffer, JobOpening
from app.models.person import Person, PersonStatus
from app.models.rbac import PersonRole, Role
from app.services.email import send_email

logger = logging.getLogger(__name__)

_ROLE_NAMES = ["hr_director", "hr_manager", "admin"]


def _recipient_emails(db: Session, org_id: UUID) -> list[str]:
    stmt = (
        select(Person.email)
        .join(PersonRole, PersonRole.person_id == Person.id)
        .join(Role, PersonRole.role_id == Role.id)
        .where(Person.organization_id == org_id)
        .where(Person.status == PersonStatus.active)
        .where(Person.is_active.is_(True))
        .where(Role.name.in_(_ROLE_NAMES))
        .distinct()
    )
    return [email for email in db.scalars(stmt).all() if email]


def send_new_applicant_notification(
    db: Session,
    org_id: UUID,
    applicant: JobApplicant,
    opening: JobOpening,
) -> None:
    """Send applicant notification email to HR/admin roles."""
    recipients = _recipient_emails(db, org_id)
    if not recipients:
        logger.warning(
            "No HR/admin recipients found for applicant notification "
            "(org_id=%s, roles=%s).",
            org_id,
            ",".join(_ROLE_NAMES),
        )
        return

    app_url = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
    applicant_url = f"{app_url}/people/recruit/applicants/{applicant.applicant_id}"
    applicant_name = f"{applicant.first_name} {applicant.last_name}".strip()
    subject = f"New job application: {applicant_name}"
    body_text = (
        "A new job application has been submitted.\n\n"
        f"Applicant: {applicant_name}\n"
        f"Email: {applicant.email}\n"
        f"Application #: {applicant.application_number}\n"
        f"Job: {opening.job_title} ({opening.job_code})\n"
        f"Source: {applicant.source or '-'}\n"
        f"View: {applicant_url}\n"
    )
    body_html = (
        "<p>A new job application has been submitted.</p>"
        "<ul>"
        f"<li><strong>Applicant:</strong> {applicant_name}</li>"
        f"<li><strong>Email:</strong> {applicant.email}</li>"
        f"<li><strong>Application #:</strong> {applicant.application_number}</li>"
        f"<li><strong>Job:</strong> {opening.job_title} ({opening.job_code})</li>"
        f"<li><strong>Source:</strong> {applicant.source or '-'}</li>"
        f'<li><strong>View:</strong> <a href="{applicant_url}">Open applicant</a></li>'
        "</ul>"
    )

    for email in recipients:
        try:
            sent = send_email(
                db,
                email,
                subject,
                body_html,
                body_text,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=org_id,
            )
            if not sent:
                logger.error(
                    "Applicant notification failed to send to %s (org_id=%s).",
                    email,
                    org_id,
                )
        except Exception:
            logger.exception("Failed to send applicant notification for %s", email)


def send_offer_response_notification(
    db: Session,
    org_id: UUID,
    applicant: JobApplicant,
    offer: JobOffer,
    response: str,
) -> None:
    """Send offer response notification to HR/admin roles."""
    recipients = _recipient_emails(db, org_id)
    if not recipients:
        logger.warning(
            "No HR/admin recipients found for offer response notification "
            "(org_id=%s, roles=%s).",
            org_id,
            ",".join(_ROLE_NAMES),
        )
        return

    app_url = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
    offer_url = f"{app_url}/people/recruit/offers/{offer.offer_id}"
    applicant_name = f"{applicant.first_name} {applicant.last_name}".strip()
    response_label = response.replace("_", " ").title()
    subject = f"Offer {response_label}: {applicant_name}"
    decline_reason = offer.decline_reason or "-"
    job_title = offer.job_opening.job_title if offer.job_opening else "Position"

    body_text = (
        f"The candidate has {response_label.lower()} the offer.\n\n"
        f"Applicant: {applicant_name}\n"
        f"Email: {applicant.email}\n"
        f"Offer #: {offer.offer_number}\n"
        f"Job: {job_title}\n"
        f"Response: {response_label}\n"
        f"Decline Reason: {decline_reason}\n"
        f"View: {offer_url}\n"
    )
    body_html = (
        f"<p>The candidate has <strong>{response_label.lower()}</strong> the offer.</p>"
        "<ul>"
        f"<li><strong>Applicant:</strong> {applicant_name}</li>"
        f"<li><strong>Email:</strong> {applicant.email}</li>"
        f"<li><strong>Offer #:</strong> {offer.offer_number}</li>"
        f"<li><strong>Job:</strong> {job_title}</li>"
        f"<li><strong>Response:</strong> {response_label}</li>"
        f"<li><strong>Decline Reason:</strong> {decline_reason}</li>"
        f'<li><strong>View:</strong> <a href="{offer_url}">Open offer</a></li>'
        "</ul>"
    )

    for email in recipients:
        try:
            sent = send_email(
                db,
                email,
                subject,
                body_html,
                body_text,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=org_id,
            )
            if not sent:
                logger.error(
                    "Offer response notification failed to send to %s (org_id=%s).",
                    email,
                    org_id,
                )
        except Exception:
            logger.exception("Failed to send offer response notification for %s", email)
