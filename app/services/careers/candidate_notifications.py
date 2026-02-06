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
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_profile import EmailModule
from app.services.email import send_email

logger = logging.getLogger(__name__)


class CandidateNotificationService:
    """
    Service for sending notifications to job applicants.

    All emails use the organization's branding and are sent from the
    configured SMTP settings.
    """

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

        subject = f"Application received - {job_title}"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Thank you for your application!</h2>
            <p>Dear {applicant_name},</p>
            <p>We have received your application for the position of <strong>{job_title}</strong> at {org_name}.</p>
            <p>Your application reference number is: <strong style="font-family: monospace; background: #f1f5f9; padding: 4px 8px; border-radius: 4px;">{application_number}</strong></p>
            <p>Please save this number for your records. You can use it to check your application status at any time.</p>
            <div style="margin: 24px 0;">
                <a href="{status_url}" style="display: inline-block; background: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 500;">
                    Check Application Status
                </a>
            </div>
            <p style="color: #64748b; font-size: 14px;">Our team will review your application and get back to you soon.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                This is an automated message from {org_name}'s careers portal.<br>
                Please do not reply to this email.
            </p>
        </div>
        """

        body_text = f"""
Thank you for your application!

Dear {applicant_name},

We have received your application for the position of {job_title} at {org_name}.

Your application reference number is: {application_number}

Please save this number for your records. You can use it to check your application status at any time.

Check your status at: {status_url}

Our team will review your application and get back to you soon.

---
This is an automated message from {org_name}'s careers portal.
Please do not reply to this email.
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
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
        subject = f"Verify your email to check application status - {org_name}"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Verify Your Email</h2>
            <p>Dear {applicant_name},</p>
            <p>You requested to check your application status at {org_name}.</p>
            <p>Click the button below to verify your email and view your application status:</p>
            <div style="margin: 24px 0;">
                <a href="{verification_url}" style="display: inline-block; background: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 500;">
                    View Application Status
                </a>
            </div>
            <p style="color: #64748b; font-size: 14px;">This link will expire in 24 hours.</p>
            <p style="color: #64748b; font-size: 14px;">If you didn't request this, you can safely ignore this email.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                This is an automated message from {org_name}'s careers portal.<br>
                Please do not reply to this email.
            </p>
        </div>
        """

        body_text = f"""
Verify Your Email

Dear {applicant_name},

You requested to check your application status at {org_name}.

Click the link below to verify your email and view your application status:

{verification_url}

This link will expire in 24 hours.

If you didn't request this, you can safely ignore this email.

---
This is an automated message from {org_name}'s careers portal.
Please do not reply to this email.
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
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
        additional_notes: Optional[str] = None,
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
        subject = f"Interview invitation - {job_title} at {org_name}"

        notes_html = ""
        notes_text = ""
        if additional_notes:
            notes_html = f'<p style="background: #fef3c7; padding: 12px; border-radius: 8px; color: #92400e;"><strong>Note:</strong> {additional_notes}</p>'
            notes_text = f"\nNote: {additional_notes}\n"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Interview Invitation</h2>
            <p>Dear {applicant_name},</p>
            <p>We are pleased to invite you for an interview for the position of <strong>{job_title}</strong> at {org_name}.</p>
            <div style="background: #f1f5f9; padding: 16px; border-radius: 8px; margin: 16px 0;">
                <p style="margin: 0 0 8px 0;"><strong>Date:</strong> {interview_date}</p>
                <p style="margin: 0 0 8px 0;"><strong>Time:</strong> {interview_time}</p>
                <p style="margin: 0 0 8px 0;"><strong>Type:</strong> {interview_type}</p>
                <p style="margin: 0;"><strong>Location:</strong> {location_or_link}</p>
            </div>
            {notes_html}
            <p>Please confirm your attendance by replying to this email.</p>
            <p>We look forward to meeting you!</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                Best regards,<br>
                The {org_name} Recruitment Team
            </p>
        </div>
        """

        body_text = f"""
Interview Invitation

Dear {applicant_name},

We are pleased to invite you for an interview for the position of {job_title} at {org_name}.

Interview Details:
- Date: {interview_date}
- Time: {interview_time}
- Type: {interview_type}
- Location: {location_or_link}
{notes_text}
Please confirm your attendance by replying to this email.

We look forward to meeting you!

---
Best regards,
The {org_name} Recruitment Team
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
            organization_id=organization_id,
        )

    def send_offer_notification(
        self,
        db: Session,
        applicant_email: str,
        applicant_name: str,
        job_title: str,
        org_name: str,
        offer_details: Optional[str] = None,
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
        subject = f"Job offer - {job_title} at {org_name}"

        details_html = ""
        details_text = ""
        if offer_details:
            details_html = f'<p style="background: #ecfdf5; padding: 12px; border-radius: 8px; color: #065f46;">{offer_details}</p>'
            details_text = f"\n{offer_details}\n"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Congratulations!</h2>
            <p>Dear {applicant_name},</p>
            <p>We are delighted to extend an offer for the position of <strong>{job_title}</strong> at {org_name}.</p>
            {details_html}
            <p>Our HR team will be in touch with the formal offer letter and next steps.</p>
            <p>We are excited about the prospect of having you join our team!</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                Best regards,<br>
                The {org_name} Recruitment Team
            </p>
        </div>
        """

        body_text = f"""
Congratulations!

Dear {applicant_name},

We are delighted to extend an offer for the position of {job_title} at {org_name}.
{details_text}
Our HR team will be in touch with the formal offer letter and next steps.

We are excited about the prospect of having you join our team!

---
Best regards,
The {org_name} Recruitment Team
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
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
        subject = f"Your offer for {job_title} at {org_name}"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Your Offer Is Ready</h2>
            <p>Dear {applicant_name},</p>
            <p>We are pleased to extend an offer for the position of <strong>{job_title}</strong> at {org_name}.</p>
            <p>Please review your offer using the link below.</p>
            <p>
                <a href="{portal_url}" style="display: inline-block; padding: 10px 16px; background: #0f766e; color: #fff; text-decoration: none; border-radius: 6px;">
                    View Offer
                </a>
            </p>
            <p style="color: #64748b; font-size: 13px;">
                If the buttons above don’t work, copy and paste this link into your browser:
                <br>
                {portal_url}
            </p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                Best regards,<br>
                The {org_name} Recruitment Team
            </p>
        </div>
        """

        body_text = f"""
Your Offer Is Ready

Dear {applicant_name},

We are pleased to extend an offer for the position of {job_title} at {org_name}.

View Offer: {portal_url}

Best regards,
The {org_name} Recruitment Team
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
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
        subject = f"Update on your application - {job_title}"

        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1e293b;">Application Update</h2>
            <p>Dear {applicant_name},</p>
            <p>Thank you for your interest in the <strong>{job_title}</strong> position at {org_name} and for taking the time to apply.</p>
            <p>After careful consideration, we have decided to move forward with other candidates whose qualifications more closely match our current needs.</p>
            <p>We appreciate your interest in joining our team and encourage you to apply for future positions that match your skills and experience.</p>
            <p>We wish you all the best in your job search and future endeavors.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
            <p style="color: #94a3b8; font-size: 12px;">
                Best regards,<br>
                The {org_name} Recruitment Team
            </p>
        </div>
        """

        body_text = f"""
Application Update

Dear {applicant_name},

Thank you for your interest in the {job_title} position at {org_name} and for taking the time to apply.

After careful consideration, we have decided to move forward with other candidates whose qualifications more closely match our current needs.

We appreciate your interest in joining our team and encourage you to apply for future positions that match your skills and experience.

We wish you all the best in your job search and future endeavors.

---
Best regards,
The {org_name} Recruitment Team
        """

        return send_email(
            db,
            applicant_email,
            subject,
            body_html,
            body_text,
            module=EmailModule.PEOPLE_PAYROLL,
            organization_id=organization_id,
        )
