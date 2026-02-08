"""
Document Template Model.

Customizable templates for documents (invoices, quotes, etc.) and emails.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TemplateType(str, enum.Enum):
    """Types of document templates."""

    # === Finance Documents ===
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    QUOTE = "QUOTE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    BILL = "BILL"
    RECEIPT = "RECEIPT"
    STATEMENT = "STATEMENT"
    PAYMENT_RECEIPT = "PAYMENT_RECEIPT"

    # === Finance Email Templates ===
    EMAIL_INVOICE = "EMAIL_INVOICE"
    EMAIL_QUOTE = "EMAIL_QUOTE"
    EMAIL_REMINDER = "EMAIL_REMINDER"
    EMAIL_OVERDUE = "EMAIL_OVERDUE"
    EMAIL_PAYMENT = "EMAIL_PAYMENT"
    EMAIL_NOTIFICATION = "EMAIL_NOTIFICATION"

    # === HR Documents ===
    OFFER_LETTER = "OFFER_LETTER"
    EMPLOYMENT_CONTRACT = "EMPLOYMENT_CONTRACT"
    APPOINTMENT_LETTER = "APPOINTMENT_LETTER"
    CONFIRMATION_LETTER = "CONFIRMATION_LETTER"
    PROMOTION_LETTER = "PROMOTION_LETTER"
    TRANSFER_LETTER = "TRANSFER_LETTER"
    TERMINATION_LETTER = "TERMINATION_LETTER"
    RESIGNATION_ACCEPTANCE = "RESIGNATION_ACCEPTANCE"
    EXPERIENCE_LETTER = "EXPERIENCE_LETTER"
    RELIEVING_LETTER = "RELIEVING_LETTER"
    WARNING_LETTER = "WARNING_LETTER"
    SHOW_CAUSE_NOTICE = "SHOW_CAUSE_NOTICE"
    SALARY_REVISION_LETTER = "SALARY_REVISION_LETTER"
    BONUS_LETTER = "BONUS_LETTER"

    # === HR Email Templates ===
    EMAIL_OFFER = "EMAIL_OFFER"
    EMAIL_ONBOARDING = "EMAIL_ONBOARDING"
    EMAIL_INTERVIEW_INVITE = "EMAIL_INTERVIEW_INVITE"
    EMAIL_APPLICATION_RECEIVED = "EMAIL_APPLICATION_RECEIVED"
    EMAIL_APPLICATION_STATUS = "EMAIL_APPLICATION_STATUS"
    EMAIL_REJECTION = "EMAIL_REJECTION"

    # === Payroll Documents ===
    PAYSLIP = "PAYSLIP"
    TAX_CERTIFICATE = "TAX_CERTIFICATE"
    BANK_LETTER = "BANK_LETTER"

    # === Project Management Documents ===
    PROJECT_PROPOSAL = "PROJECT_PROPOSAL"
    PROJECT_REPORT = "PROJECT_REPORT"


class DocumentTemplate(Base):
    """
    Document template for generating PDFs and emails.

    Uses Jinja2 templating for dynamic content.
    """

    __tablename__ = "document_template"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "template_type",
            "template_name",
            name="uq_document_template",
        ),
        Index("idx_document_template_org", "organization_id"),
        Index("idx_document_template_type", "template_type"),
        Index("idx_document_template_default", "is_default"),
        {"schema": "automation"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Template identification
    template_type: Mapped[TemplateType] = mapped_column(
        Enum(TemplateType, name="document_template_type"),
        nullable=False,
    )
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Template content (Jinja2)
    template_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Jinja2 template content",
    )

    # Styling
    css_styles: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="CSS styles for the template",
    )

    # Header/Footer configuration
    header_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Header config: logo, company name, address, etc.",
    )
    footer_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Footer config: terms, bank details, signature, etc.",
    )

    # Page settings
    page_size: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="A4",
        comment="Page size: A4, Letter, Legal",
    )
    page_orientation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="portrait",
        comment="portrait or landscape",
    )
    page_margins: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Margins: top, right, bottom, left",
    )

    # Email-specific settings
    email_subject: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Email subject template (Jinja2)",
    )
    email_from_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Version tracking
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # Settings
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Default template for this type",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    def render(self, context: dict[str, Any]) -> str:
        """
        Render the template with the given context.

        Uses a sandboxed Jinja2 environment to prevent template injection attacks.
        """
        from app.services.automation.safe_template import get_sandboxed_environment

        env = get_sandboxed_environment()
        template = env.from_string(self.template_content)
        return template.render(**context)

    def render_subject(self, context: dict[str, Any]) -> str:
        """
        Render the email subject with the given context.

        Uses a sandboxed Jinja2 environment to prevent template injection attacks.
        """
        if not self.email_subject:
            return ""
        from app.services.automation.safe_template import get_sandboxed_environment

        env = get_sandboxed_environment()
        template = env.from_string(self.email_subject)
        return template.render(**context)
