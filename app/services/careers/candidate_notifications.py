"""
Candidate notification service for the careers portal.

Sends email notifications to job applicants for:
- Application confirmation
- Status verification emails
- Interview invitations
- Offer notifications
- Rejection notices
"""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_profile import EmailModule
from app.services.email import send_email
from app.services.email_branding import render_branded_email

logger = logging.getLogger(__name__)


class CandidateNotificationService:
    """
    Service for sending notifications to job applicants.

    All emails use the organization's branding and are sent from the
    configured SMTP settings.
    """

    def _send(
        self,
        db: Session,
        template: str,
        context: dict,
        *,
        to_email: str,
        subject: str,
        organization_id: UUID | None,
    ) -> bool:
        """Render a branded email template and send it."""
        try:
            body_html, body_text = render_branded_email(
                template,
                context,
                db,
                organization_id,
            )
            return send_email(
                db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=organization_id,
            )
        except Exception as e:
            logger.error("Failed to send %s: %s", template, e)
            return False

    def send_application_confirmation(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        application_number: str,
        org_name: str,
        org_slug: str,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Send application confirmation email.

        Args:
            db: Database session for SMTP settings
            applicant_email: Recipient email
            applicant_name: Applicant's name
            job_title: Position applied for
            application_number: Application reference number
            org_name: Organization name
            org_slug: Organization slug for status URL

        Returns:
            True if email sent successfully
        """
        status_url = f"{settings.app_url}/careers/{org_slug}/status"

        context = {
            "applicant_name": applicant_name,
            "job_title": job_title,
            "application_number": application_number,
            "org_name": org_name,
            "status_url": status_url,
        }

        return self._send(
            db,
            "emails/careers/application_confirmation.html",
            context,
            to_email=applicant_email,
            subject=f"Application received - {job_title}",
            organization_id=organization_id,
        )

    def send_status_verification_email(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        verification_url: str,
        org_name: str,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Send verification email for status check request.

        Args:
            db: Database session for SMTP settings
            applicant_email: Recipient email
            applicant_name: Applicant's name
            verification_url: Full URL with verification token
            org_name: Organization name

        Returns:
            True if email sent successfully
        """
        context = {
            "applicant_name": applicant_name,
            "verification_url": verification_url,
            "org_name": org_name,
        }

        return self._send(
            db,
            "emails/careers/status_verification.html",
            context,
            to_email=applicant_email,
            subject=f"Verify your email to check application status - {org_name}",
            organization_id=organization_id,
        )

    def send_interview_invitation(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        interview_date: str,
        interview_time: str,
        interview_type: str,
        location_or_link: str,
        org_name: str,
        additional_notes: str | None = None,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Send interview invitation email.

        Args:
            db: Database session for SMTP settings
            applicant_email: Recipient email
            applicant_name: Applicant's name
            job_title: Position applied for
            interview_date: Interview date (formatted string)
            interview_time: Interview time (formatted string)
            interview_type: Type of interview (e.g., "Video Call", "In-Person")
            location_or_link: Physical location or video call link
            org_name: Organization name
            additional_notes: Optional notes for the candidate

        Returns:
            True if email sent successfully
        """
        context = {
            "applicant_name": applicant_name,
            "job_title": job_title,
            "org_name": org_name,
            "interview_date": interview_date,
            "interview_time": interview_time,
            "interview_type": interview_type,
            "location_or_link": location_or_link,
            "additional_notes": additional_notes,
        }

        return self._send(
            db,
            "emails/careers/interview_invite.html",
            context,
            to_email=applicant_email,
            subject=f"Interview invitation - {job_title} at {org_name}",
            organization_id=organization_id,
        )

    def send_offer_notification(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        org_name: str,
        offer_details: str | None = None,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Send job offer notification email.

        Args:
            db: Database session for SMTP settings
            applicant_email: Recipient email
            applicant_name: Applicant's name
            job_title: Position offered
            org_name: Organization name
            offer_details: Optional brief offer summary

        Returns:
            True if email sent successfully
        """
        context = {
            "applicant_name": applicant_name,
            "job_title": job_title,
            "org_name": org_name,
            "offer_details": offer_details,
        }

        return self._send(
            db,
            "emails/careers/offer_notification.html",
            context,
            to_email=applicant_email,
            subject=f"Job offer - {job_title} at {org_name}",
            organization_id=organization_id,
        )

    def send_offer_portal_email(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        org_name: str,
        portal_url: str,
        pdf_url: str,
        accept_url: str,
        decline_url: str,
        organization_id: UUID | None = None,
    ) -> bool:
        """Send offer portal email with view-offer link."""
        context = {
            "applicant_name": applicant_name,
            "job_title": job_title,
            "org_name": org_name,
            "portal_url": portal_url,
            "pdf_url": pdf_url,
            "accept_url": accept_url,
            "decline_url": decline_url,
        }

        return self._send(
            db,
            "emails/careers/offer_portal.html",
            context,
            to_email=applicant_email,
            subject=f"Your offer for {job_title} at {org_name}",
            organization_id=organization_id,
        )

    def send_rejection_notice(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        org_name: str,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Send application rejection email.

        Args:
            db: Database session for SMTP settings
            applicant_email: Recipient email
            applicant_name: Applicant's name
            job_title: Position applied for
            org_name: Organization name

        Returns:
            True if email sent successfully
        """
        context = {
            "applicant_name": applicant_name,
            "job_title": job_title,
            "org_name": org_name,
        }

        return self._send(
            db,
            "emails/careers/rejection_notice.html",
            context,
            to_email=applicant_email,
            subject=f"Update on your application - {job_title}",
            organization_id=organization_id,
        )
