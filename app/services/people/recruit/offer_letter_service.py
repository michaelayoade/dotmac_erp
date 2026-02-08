"""
Offer Letter Service.

Service for generating offer letter PDFs from job offers.
Wraps DocumentGeneratorService with offer-specific context building.
"""

import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.finance.automation.document_template import (
    DocumentTemplate,
    TemplateType,
)
from app.models.finance.automation.generated_document import GeneratedDocument
from app.models.finance.core_org.organization import Organization
from app.models.people.recruit.job_offer import JobOffer, OfferStatus
from app.schemas.document_context import OfferLetterContext
from app.services.automation.document_generator import (
    DocumentGeneratorService,
)

logger = logging.getLogger(__name__)


class OfferLetterServiceError(Exception):
    """Base error for offer letter service."""

    pass


class OfferNotFoundError(OfferLetterServiceError):
    """Job offer not found."""

    pass


class InvalidOfferStateError(OfferLetterServiceError):
    """Offer is not in a valid state for this operation."""

    pass


class OfferLetterService:
    """
    Service for generating offer letters.

    Wraps DocumentGeneratorService with offer-specific logic:
    - Builds context from JobOffer and related entities
    - Validates offer state before generation
    - Tracks generated documents
    """

    def __init__(self, db: Session):
        self.db = db
        self._doc_service = DocumentGeneratorService(db)

    def get_offer_with_relations(self, offer_id: UUID) -> JobOffer | None:
        """
        Get a job offer with all related data loaded.

        Loads applicant, job opening, designation, department for context building.
        """
        return (
            self.db.query(JobOffer)
            .options(
                joinedload(JobOffer.applicant),
                joinedload(JobOffer.job_opening),
                joinedload(JobOffer.designation),
                joinedload(JobOffer.department),
            )
            .filter(JobOffer.offer_id == offer_id)
            .first()
        )

    def generate_offer_letter(
        self,
        offer_id: UUID,
        user_id: UUID,
        *,
        template_name: str | None = None,
        organization_name: str | None = None,
        organization_address: str | None = None,
        organization_logo_url: str | None = None,
        signatory_name: str | None = None,
        signatory_title: str | None = None,
        additional_context: dict | None = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate an offer letter PDF for a job offer.

        Args:
            offer_id: UUID of the JobOffer
            user_id: UUID of the user generating the letter
            template_name: Optional specific template name (uses default if not provided)
            organization_name: Override organization name (uses org legal_name if not provided)
            organization_address: Organization address for letterhead
            organization_logo_url: URL to company logo
            signatory_name: Name of person signing (required if not in template default)
            signatory_title: Title of signatory (e.g., "HR Director")
            additional_context: Additional template variables

        Returns:
            Tuple of (pdf_bytes, generated_document_record)

        Raises:
            OfferNotFoundError: If offer not found
            InvalidOfferStateError: If offer is in invalid state for letter generation
            TemplateNotFoundError: If no offer letter template exists
        """
        # Load offer with relations
        offer = self.get_offer_with_relations(offer_id)
        if not offer:
            raise OfferNotFoundError(f"Job offer {offer_id} not found")

        # Validate offer state - can generate letter for APPROVED or EXTENDED status
        if offer.status not in (
            OfferStatus.DRAFT,
            OfferStatus.PENDING_APPROVAL,
            OfferStatus.APPROVED,
            OfferStatus.EXTENDED,
            OfferStatus.ACCEPTED,
        ):
            raise InvalidOfferStateError(
                f"Cannot generate offer letter for offer in {offer.status.value} status"
            )

        # Build context
        context = self._build_offer_context(
            offer,
            organization_name=organization_name,
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            additional_context=additional_context,
        )

        # Generate PDF
        self._ensure_default_template(offer.organization_id, user_id)
        pdf_bytes, doc_record = self._doc_service.generate_pdf(
            organization_id=offer.organization_id,
            template_type=TemplateType.OFFER_LETTER,
            context=context.model_dump(),
            template_name=template_name,
            entity_type="JOB_OFFER",
            entity_id=offer.offer_id,
            document_number=f"OFFER-{offer.offer_number}",
            document_title=f"Offer Letter - {context.candidate_name}",
            created_by=user_id,
            save_record=True,
            use_base_template=True,
        )

        if doc_record is None:
            raise OfferLetterServiceError("Failed to create document record")

        logger.info(
            "Generated offer letter for offer %s (applicant: %s)",
            offer.offer_number,
            context.candidate_name,
        )

        return pdf_bytes, doc_record

    def _ensure_default_template(self, organization_id: UUID, user_id: UUID) -> None:
        """Ensure a default offer letter template exists for the organization."""
        existing = self._doc_service.get_template(
            organization_id, TemplateType.OFFER_LETTER, None
        )
        if existing:
            return

        template_path = (
            Path(__file__).resolve().parents[4]
            / "templates"
            / "documents"
            / "offer_letter_default.html"
        )
        try:
            template_content = template_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(
                "Default offer letter template file not found at %s", template_path
            )
            raise

        default_template = DocumentTemplate(
            organization_id=organization_id,
            template_type=TemplateType.OFFER_LETTER,
            template_name="Default Offer Letter",
            description="System default offer letter template",
            template_content=template_content,
            css_styles=None,
            header_config=None,
            footer_config=None,
            page_size="A4",
            page_orientation="portrait",
            page_margins=None,
            is_default=True,
            is_active=True,
            version=1,
            created_by=user_id,
        )
        self.db.add(default_template)
        self.db.flush()

    def _build_offer_context(
        self,
        offer: JobOffer,
        *,
        organization_name: str | None = None,
        organization_address: str | None = None,
        organization_logo_url: str | None = None,
        signatory_name: str | None = None,
        signatory_title: str | None = None,
        additional_context: dict | None = None,
    ) -> OfferLetterContext:
        """
        Build offer letter context from JobOffer and related data.

        All fields are extracted from the database models and
        formatted appropriately for template rendering.
        """
        applicant = offer.applicant
        job = offer.job_opening
        designation = offer.designation
        department = offer.department

        # Get organization info
        org = (
            self.db.get(Organization, offer.organization_id)
            if not organization_name
            else None
        )

        # Calculate annual salary if pay frequency is monthly
        annual_salary = None
        if offer.pay_frequency == "MONTHLY":
            annual_salary = offer.base_salary * 12
        elif offer.pay_frequency == "BI_WEEKLY":
            annual_salary = offer.base_salary * 26
        elif offer.pay_frequency == "WEEKLY":
            annual_salary = offer.base_salary * 52
        else:
            annual_salary = offer.base_salary  # Assume annual

        # Parse benefits from other_benefits field
        benefits = None
        if offer.other_benefits:
            # Assume comma-separated or newline-separated
            benefits = [
                b.strip()
                for b in offer.other_benefits.replace("\n", ",").split(",")
                if b.strip()
            ]

        # Build context
        org_name = (
            organization_name
            or (org.trading_name or org.legal_name if org else None)
            or "Your Organization"
        )
        org_address = organization_address
        if not org_address and org:
            address_parts = [
                org.address_line1,
                org.address_line2,
                org.city,
                org.state,
                org.postal_code,
                org.country,
            ]
            org_address = ", ".join([p for p in address_parts if p])

        context = OfferLetterContext(
            # Candidate
            candidate_name=f"{applicant.first_name} {applicant.last_name}",
            candidate_first_name=applicant.first_name,
            candidate_last_name=applicant.last_name,
            candidate_email=applicant.email,
            candidate_phone=getattr(applicant, "phone", None),
            # Position
            job_title=job.job_title,
            designation_name=designation.designation_name
            if designation
            else job.job_title,
            department_name=department.department_name if department else None,
            location=job.location,
            employment_type=offer.employment_type,
            # Compensation
            base_salary=offer.base_salary,
            currency_code=offer.currency_code,
            pay_frequency=offer.pay_frequency,
            annual_salary=annual_salary,
            signing_bonus=offer.signing_bonus,
            relocation_allowance=offer.relocation_allowance,
            benefits=benefits,
            other_benefits=offer.other_benefits,
            # Dates
            offer_date=offer.offer_date,
            offer_expiry_date=offer.valid_until,
            proposed_start_date=offer.expected_joining_date,
            document_date=offer.offer_date,
            # Terms
            probation_months=offer.probation_months,
            notice_period_days=offer.notice_period_days,
            terms_and_conditions=offer.terms_and_conditions,
            # Organization
            organization_name=org_name,
            organization_address=org_address,
            organization_logo_url=organization_logo_url
            or (org.logo_url if org else None),
            # Signatory
            signatory_name=signatory_name or "Human Resources",
            signatory_title=signatory_title or "HR Department",
            # Reference
            offer_number=offer.offer_number,
        )

        # Merge additional context
        if additional_context:
            context_dict = context.model_dump()
            context_dict.update(additional_context)
            context = OfferLetterContext(**context_dict)

        return context

    def get_offer_letters(
        self,
        offer_id: UUID,
    ) -> list[GeneratedDocument]:
        """Get all generated offer letters for a job offer."""
        offer = self.get_offer_with_relations(offer_id)
        if not offer:
            return []
        return self._doc_service.get_documents_for_entity(
            organization_id=offer.organization_id,
            entity_type="JOB_OFFER",
            entity_id=offer_id,
        )

    def regenerate_offer_letter(
        self,
        offer_id: UUID,
        user_id: UUID,
        previous_document_id: UUID,
        **kwargs,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Regenerate an offer letter and mark previous version as superseded.

        Args:
            offer_id: UUID of the JobOffer
            user_id: UUID of the user generating the letter
            previous_document_id: UUID of the document being superseded
            **kwargs: Additional arguments passed to generate_offer_letter

        Returns:
            Tuple of (pdf_bytes, new_generated_document_record)
        """
        # Generate new letter
        pdf_bytes, new_doc = self.generate_offer_letter(offer_id, user_id, **kwargs)

        # Mark previous as superseded
        self._doc_service.supersede_document(previous_document_id, new_doc.document_id)

        logger.info(
            "Regenerated offer letter %s, superseded %s",
            new_doc.document_id,
            previous_document_id,
        )

        return pdf_bytes, new_doc
