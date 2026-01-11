"""
Document Template Model.

Customizable templates for documents (invoices, quotes, etc.) and emails.
"""
import enum
import uuid
from datetime import datetime
from typing import Any, Optional

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
    # Document templates
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    QUOTE = "QUOTE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    BILL = "BILL"
    RECEIPT = "RECEIPT"
    STATEMENT = "STATEMENT"
    PAYMENT_RECEIPT = "PAYMENT_RECEIPT"
    # Email templates
    EMAIL_INVOICE = "EMAIL_INVOICE"
    EMAIL_QUOTE = "EMAIL_QUOTE"
    EMAIL_REMINDER = "EMAIL_REMINDER"
    EMAIL_OVERDUE = "EMAIL_OVERDUE"
    EMAIL_PAYMENT = "EMAIL_PAYMENT"
    EMAIL_NOTIFICATION = "EMAIL_NOTIFICATION"


class DocumentTemplate(Base):
    """
    Document template for generating PDFs and emails.

    Uses Jinja2 templating for dynamic content.
    """

    __tablename__ = "document_template"
    __table_args__ = (
        UniqueConstraint("organization_id", "template_type", "template_name", name="uq_document_template"),
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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Template content (Jinja2)
    template_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Jinja2 template content",
    )

    # Styling
    css_styles: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="CSS styles for the template",
    )

    # Header/Footer configuration
    header_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Header config: logo, company name, address, etc.",
    )
    footer_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
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
    page_margins: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Margins: top, right, bottom, left",
    )

    # Email-specific settings
    email_subject: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Email subject template (Jinja2)",
    )
    email_from_name: Mapped[Optional[str]] = mapped_column(
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
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    def render(self, context: dict[str, Any]) -> str:
        """Render the template with the given context."""
        from jinja2 import Template

        template = Template(self.template_content)
        return template.render(**context)

    def render_subject(self, context: dict[str, Any]) -> str:
        """Render the email subject with the given context."""
        if not self.email_subject:
            return ""
        from jinja2 import Template

        template = Template(self.email_subject)
        return template.render(**context)
