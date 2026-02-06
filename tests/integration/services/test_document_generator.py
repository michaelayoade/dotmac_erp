"""Tests for the Document Generator Service."""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.finance.automation.document_template import (
    DocumentTemplate,
    TemplateType,
)
from app.models.finance.automation.generated_document import (
    DocumentStatus,
    GeneratedDocument,
    OutputFormat,
)
from app.models.finance.core_org.organization import Organization
from app.services.automation.document_generator import (
    DocumentGeneratorService,
    TemplateNotFoundError,
)


@pytest.fixture
def offer_letter_template(
    db: Session,
    organization: Organization,
    user_id: uuid.UUID,
) -> DocumentTemplate:
    """Create an offer letter template."""
    template = DocumentTemplate(
        organization_id=organization.organization_id,
        template_type=TemplateType.OFFER_LETTER,
        template_name="Standard Offer Letter",
        description="Default offer letter template",
        template_content="""
            <div class="addressee">
                <p class="addressee-name">{{ candidate_name }}</p>
            </div>
            <div class="subject">
                <p class="subject-label">Subject</p>
                <p class="subject-text">Offer of Employment - {{ job_title }}</p>
            </div>
            <div class="salutation">
                <p>Dear {{ candidate_name }},</p>
            </div>
            <div class="content">
                <p>We are pleased to offer you the position of <strong>{{ job_title }}</strong>
                at {{ organization_name }}.</p>

                <h3>Compensation</h3>
                <p>Your starting salary will be {{ currency }} {{ salary | format_currency }}
                per month.</p>

                <h3>Start Date</h3>
                <p>Your proposed start date is {{ start_date | format_date }}.</p>

                {% if benefits %}
                <h3>Benefits</h3>
                <ul>
                    {% for benefit in benefits %}
                    <li>{{ benefit }}</li>
                    {% endfor %}
                </ul>
                {% endif %}
            </div>
            <div class="signature-section">
                <p class="closing">Sincerely,</p>
                <div class="signature-block">
                    <div class="signature-line"></div>
                    <p class="signature-name">{{ signatory_name }}</p>
                    <p class="signature-title">{{ signatory_title }}</p>
                </div>
            </div>
        """,
        css_styles="""
            .addressee { margin-bottom: 20px; }
            .subject-text { font-weight: bold; }
        """,
        header_config={
            "company_name": "Test Company Ltd",
            "address": "123 Test Street, Lagos",
            "phone": "+234 800 123 4567",
        },
        footer_config={
            "notice": "This offer is confidential.",
        },
        page_size="A4",
        page_orientation="portrait",
        is_default=True,
        is_active=True,
        created_by=user_id,
    )
    db.add(template)
    db.flush()
    return template


class TestDocumentGeneratorService:
    """Test cases for DocumentGeneratorService."""

    def test_get_template(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
    ):
        """Test getting a template by type."""
        service = DocumentGeneratorService(db)

        # Get by type (default)
        template = service.get_template(
            organization.organization_id,
            TemplateType.OFFER_LETTER,
        )
        assert template is not None
        assert template.template_id == offer_letter_template.template_id

        # Get by type and name
        template = service.get_template(
            organization.organization_id,
            TemplateType.OFFER_LETTER,
            template_name="Standard Offer Letter",
        )
        assert template is not None

        # Not found
        template = service.get_template(
            organization.organization_id,
            TemplateType.EMPLOYMENT_CONTRACT,  # Doesn't exist
        )
        assert template is None

    def test_get_default_template(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
    ):
        """Test getting the default template for a type."""
        service = DocumentGeneratorService(db)

        template = service.get_default_template(
            organization.organization_id,
            TemplateType.OFFER_LETTER,
        )
        assert template is not None
        assert template.is_default is True

    def test_render_html(
        self,
        db: Session,
        offer_letter_template: DocumentTemplate,
    ):
        """Test rendering template to HTML."""
        service = DocumentGeneratorService(db)

        context = {
            "candidate_name": "John Doe",
            "job_title": "Software Engineer",
            "organization_name": "Test Company Ltd",
            "currency": "NGN",
            "salary": Decimal("500000"),
            "start_date": date(2024, 2, 1),
            "benefits": ["Health Insurance", "Pension", "Annual Bonus"],
            "signatory_name": "Jane Smith",
            "signatory_title": "HR Director",
        }

        html = service.render_html(offer_letter_template, context)

        assert "John Doe" in html
        assert "Software Engineer" in html
        assert "500,000.00" in html  # Formatted currency
        assert "Health Insurance" in html
        assert "Jane Smith" in html

    def test_sanitize_context_for_snapshot(
        self,
        db: Session,
    ):
        """Test context sanitization for storage."""
        service = DocumentGeneratorService(db)

        context = {
            "name": "John Doe",
            "salary": Decimal("500000"),
            "start_date": date(2024, 2, 1),
            "employee_id": uuid.uuid4(),
            "api_secret_key": "should_be_filtered",  # Sensitive
            "password": "also_filtered",  # Sensitive
            "count": 5,
            "is_active": True,
            "tags": ["tag1", "tag2"],
        }

        snapshot = service._sanitize_context_for_snapshot(context)

        # Regular values preserved
        assert snapshot["name"] == "John Doe"
        assert snapshot["salary"] == "500000"  # Converted to string
        assert "2024-02-01" in snapshot["start_date"]  # ISO format
        assert snapshot["count"] == 5
        assert snapshot["is_active"] is True
        assert snapshot["tags"] == ["tag1", "tag2"]

        # Sensitive values filtered
        assert "api_secret_key" not in snapshot
        assert "password" not in snapshot

    @patch("app.services.automation.document_generator.HTML")
    def test_generate_pdf(
        self,
        mock_html_class: MagicMock,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
        user_id: uuid.UUID,
    ):
        """Test PDF generation."""
        # Mock WeasyPrint
        mock_html_instance = MagicMock()
        mock_html_instance.write_pdf.return_value = b"%PDF-1.4 fake pdf content"
        mock_html_class.return_value = mock_html_instance

        service = DocumentGeneratorService(db)

        context = {
            "candidate_name": "John Doe",
            "job_title": "Software Engineer",
            "organization_name": "Test Company Ltd",
            "currency": "NGN",
            "salary": Decimal("500000"),
            "start_date": date(2024, 2, 1),
            "signatory_name": "Jane Smith",
            "signatory_title": "HR Director",
        }

        entity_id = uuid.uuid4()

        pdf_bytes, doc_record = service.generate_pdf(
            organization_id=organization.organization_id,
            template_type=TemplateType.OFFER_LETTER,
            context=context,
            entity_type="JOB_OFFER",
            entity_id=entity_id,
            document_number="OFFER-2024-0001",
            document_title="Offer Letter for John Doe",
            created_by=user_id,
            save_record=True,
            use_base_template=False,  # Skip base template for simpler test
        )

        # Check PDF bytes returned
        assert pdf_bytes == b"%PDF-1.4 fake pdf content"

        # Check GeneratedDocument record created
        assert doc_record is not None
        assert doc_record.document_id is not None
        assert doc_record.organization_id == organization.organization_id
        assert doc_record.template_id == offer_letter_template.template_id
        assert doc_record.entity_type == "JOB_OFFER"
        assert doc_record.entity_id == entity_id
        assert doc_record.document_number == "OFFER-2024-0001"
        assert doc_record.output_format == OutputFormat.PDF
        assert doc_record.status == DocumentStatus.DRAFT
        assert doc_record.content_hash is not None  # SHA256 hash
        assert doc_record.context_snapshot is not None

        # Verify context snapshot
        assert doc_record.context_snapshot["candidate_name"] == "John Doe"
        assert doc_record.context_snapshot["salary"] == "500000"

    def test_generate_pdf_template_not_found(
        self,
        db: Session,
        organization: Organization,
    ):
        """Test PDF generation with missing template."""
        service = DocumentGeneratorService(db)

        with pytest.raises(TemplateNotFoundError):
            service.generate_pdf(
                organization_id=organization.organization_id,
                template_type=TemplateType.EMPLOYMENT_CONTRACT,  # No template
                context={"name": "Test"},
            )

    def test_mark_as_sent(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
        user_id: uuid.UUID,
    ):
        """Test marking document as sent."""
        # Create a document record
        doc = GeneratedDocument(
            organization_id=organization.organization_id,
            template_id=offer_letter_template.template_id,
            template_version=1,
            entity_type="JOB_OFFER",
            entity_id=uuid.uuid4(),
            document_date=date.today(),
            output_format=OutputFormat.PDF,
            status=DocumentStatus.DRAFT,
            created_by=user_id,
        )
        db.add(doc)
        db.flush()

        service = DocumentGeneratorService(db)
        result = service.mark_as_sent(doc.document_id, "john.doe@example.com")

        assert result is not None
        assert result.status == DocumentStatus.SENT
        assert result.sent_to == "john.doe@example.com"
        assert result.sent_at is not None

    def test_mark_as_final(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
        user_id: uuid.UUID,
    ):
        """Test marking document as final."""
        doc = GeneratedDocument(
            organization_id=organization.organization_id,
            template_id=offer_letter_template.template_id,
            template_version=1,
            entity_type="JOB_OFFER",
            entity_id=uuid.uuid4(),
            document_date=date.today(),
            output_format=OutputFormat.PDF,
            status=DocumentStatus.DRAFT,
            created_by=user_id,
        )
        db.add(doc)
        db.flush()

        service = DocumentGeneratorService(db)
        result = service.mark_as_final(doc.document_id)

        assert result is not None
        assert result.status == DocumentStatus.FINAL

    def test_supersede_document(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
        user_id: uuid.UUID,
    ):
        """Test superseding an old document with a new one."""
        # Create old document
        old_doc = GeneratedDocument(
            organization_id=organization.organization_id,
            template_id=offer_letter_template.template_id,
            template_version=1,
            entity_type="JOB_OFFER",
            entity_id=uuid.uuid4(),
            document_number="OFFER-001",
            document_date=date.today(),
            output_format=OutputFormat.PDF,
            status=DocumentStatus.FINAL,
            created_by=user_id,
        )
        db.add(old_doc)
        db.flush()

        # Create new document
        new_doc = GeneratedDocument(
            organization_id=organization.organization_id,
            template_id=offer_letter_template.template_id,
            template_version=1,
            entity_type="JOB_OFFER",
            entity_id=old_doc.entity_id,
            document_number="OFFER-001-R1",
            document_date=date.today(),
            output_format=OutputFormat.PDF,
            status=DocumentStatus.DRAFT,
            created_by=user_id,
        )
        db.add(new_doc)
        db.flush()

        service = DocumentGeneratorService(db)
        service.supersede_document(old_doc.document_id, new_doc.document_id)

        db.refresh(old_doc)
        assert old_doc.status == DocumentStatus.SUPERSEDED
        assert old_doc.superseded_by == new_doc.document_id

    def test_get_documents_for_entity(
        self,
        db: Session,
        organization: Organization,
        offer_letter_template: DocumentTemplate,
        user_id: uuid.UUID,
    ):
        """Test retrieving documents for an entity."""
        entity_id = uuid.uuid4()

        # Create multiple documents for same entity
        for i in range(3):
            doc = GeneratedDocument(
                organization_id=organization.organization_id,
                template_id=offer_letter_template.template_id,
                template_version=1,
                entity_type="JOB_OFFER",
                entity_id=entity_id,
                document_number=f"OFFER-{i}",
                document_date=date.today(),
                output_format=OutputFormat.PDF,
                status=DocumentStatus.DRAFT,
                created_by=user_id,
            )
            db.add(doc)
        db.flush()

        service = DocumentGeneratorService(db)
        docs = service.get_documents_for_entity(
            organization.organization_id,
            "JOB_OFFER",
            entity_id,
        )

        assert len(docs) == 3
