# Document Template System Architecture

## Overview

This document describes the architecture for extending the existing Document Template system to support HR documents (offer letters, employment contracts, etc.) and other module documents as a cross-cutting feature.

## Current State

### Existing Components

1. **DocumentTemplate Model** (`app/models/finance/automation/document_template.py`)
   - Multi-tenant (organization_id)
   - Jinja2-based template content
   - CSS styling support
   - Header/footer configuration (JSONB)
   - Page settings (size, orientation, margins)
   - Email-specific fields (subject, from_name)
   - Version tracking
   - Built-in `render()` and `render_subject()` methods

2. **TemplateType Enum** (Current values)
   - Finance documents: INVOICE, CREDIT_NOTE, QUOTE, SALES_ORDER, PURCHASE_ORDER, BILL, RECEIPT, STATEMENT, PAYMENT_RECEIPT
   - Email templates: EMAIL_INVOICE, EMAIL_QUOTE, EMAIL_REMINDER, EMAIL_OVERDUE, EMAIL_PAYMENT, EMAIL_NOTIFICATION

3. **PayslipPDFService** (`app/services/people/payroll/payslip_pdf.py`)
   - Uses WeasyPrint for PDF generation
   - Jinja2 Environment with FileSystemLoader
   - Custom filters (format_currency)
   - Hardcoded to file-based template

---

## Proposed Extensions

### 1. Extended TemplateType Enum

Add HR and cross-module template types to the existing enum:

```python
class TemplateType(str, enum.Enum):
    # === Existing Finance Documents ===
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    # ... (existing types)

    # === HR Documents (NEW) ===
    OFFER_LETTER = "OFFER_LETTER"              # Job offer to candidate
    EMPLOYMENT_CONTRACT = "EMPLOYMENT_CONTRACT"  # Employment agreement
    APPOINTMENT_LETTER = "APPOINTMENT_LETTER"   # Formal appointment after contract
    CONFIRMATION_LETTER = "CONFIRMATION_LETTER" # Post-probation confirmation
    PROMOTION_LETTER = "PROMOTION_LETTER"       # Promotion notification
    TRANSFER_LETTER = "TRANSFER_LETTER"         # Transfer notification
    TERMINATION_LETTER = "TERMINATION_LETTER"   # Employment termination
    RESIGNATION_ACCEPTANCE = "RESIGNATION_ACCEPTANCE"  # Resignation acceptance
    EXPERIENCE_LETTER = "EXPERIENCE_LETTER"     # Service/experience certificate
    RELIEVING_LETTER = "RELIEVING_LETTER"       # Final relieving document
    WARNING_LETTER = "WARNING_LETTER"           # Disciplinary warning
    SHOW_CAUSE_NOTICE = "SHOW_CAUSE_NOTICE"     # Disciplinary inquiry
    SALARY_REVISION_LETTER = "SALARY_REVISION_LETTER"  # Salary change notification
    BONUS_LETTER = "BONUS_LETTER"               # Bonus announcement

    # === HR Email Templates (NEW) ===
    EMAIL_OFFER = "EMAIL_OFFER"                 # Email with offer letter
    EMAIL_ONBOARDING = "EMAIL_ONBOARDING"       # Onboarding instructions
    EMAIL_INTERVIEW_INVITE = "EMAIL_INTERVIEW_INVITE"  # Interview scheduling
    EMAIL_APPLICATION_RECEIVED = "EMAIL_APPLICATION_RECEIVED"  # Application confirmation
    EMAIL_APPLICATION_STATUS = "EMAIL_APPLICATION_STATUS"  # Status update
    EMAIL_REJECTION = "EMAIL_REJECTION"         # Application rejection

    # === Payroll Documents (NEW) ===
    PAYSLIP = "PAYSLIP"                         # Salary slip PDF
    TAX_CERTIFICATE = "TAX_CERTIFICATE"         # Annual tax certificate
    BANK_LETTER = "BANK_LETTER"                 # Salary confirmation for bank

    # === Project Management (NEW) ===
    PROJECT_PROPOSAL = "PROJECT_PROPOSAL"       # Project proposal document
    PROJECT_REPORT = "PROJECT_REPORT"           # Project status report
```

**Migration:** Add new enum values via ALTER TYPE (PostgreSQL allows enum value addition)

---

### 2. GeneratedDocument Model (NEW)

Track all generated documents for audit trail, retrieval, and re-generation.

**Location:** `app/models/finance/automation/generated_document.py`

```python
class GeneratedDocument(Base):
    """
    Record of a generated document instance.

    Tracks when documents were generated, for whom, and stores
    the rendered output or file reference.
    """
    __tablename__ = "generated_document"
    __table_args__ = (
        Index("idx_generated_doc_org", "organization_id"),
        Index("idx_generated_doc_entity", "entity_type", "entity_id"),
        Index("idx_generated_doc_template", "template_id"),
        {"schema": "automation"},
    )

    document_id: Mapped[uuid.UUID]  # PK
    organization_id: Mapped[uuid.UUID]  # FK to organization

    # Template used
    template_id: Mapped[uuid.UUID]  # FK to document_template
    template_version: Mapped[int]   # Version at time of generation

    # Entity this document is for
    entity_type: Mapped[str]  # 'JOB_OFFER', 'EMPLOYEE', 'INVOICE', etc.
    entity_id: Mapped[uuid.UUID]  # ID of the entity

    # Document metadata
    document_number: Mapped[Optional[str]]  # e.g., "OFFER-2024-0001"
    document_date: Mapped[date]

    # Output
    output_format: Mapped[str]  # 'PDF', 'HTML', 'EMAIL'
    file_path: Mapped[Optional[str]]  # Path if stored as file
    file_size_bytes: Mapped[Optional[int]]
    content_hash: Mapped[Optional[str]]  # SHA256 for integrity

    # For emails
    sent_to: Mapped[Optional[str]]  # Recipient email
    sent_at: Mapped[Optional[datetime]]

    # Context snapshot (for re-generation debugging)
    context_snapshot: Mapped[Optional[dict]]  # JSONB - key data at generation time

    # Status
    status: Mapped[str]  # 'DRAFT', 'FINAL', 'SENT', 'SUPERSEDED'
    superseded_by: Mapped[Optional[uuid.UUID]]  # If replaced by newer version

    # Audit
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime]
```

---

### 3. DocumentGeneratorService (NEW)

Generic service for generating documents from templates.

**Location:** `app/services/automation/document_generator.py`

```python
class DocumentGeneratorService:
    """
    Cross-cutting service for generating documents from templates.

    Supports:
    - PDF generation (WeasyPrint)
    - HTML generation
    - Email rendering (with subject)
    """

    def __init__(self, db: Session):
        self.db = db

    def get_template(
        self,
        organization_id: UUID,
        template_type: TemplateType,
        template_name: Optional[str] = None,
    ) -> Optional[DocumentTemplate]:
        """Get template by type (default if name not specified)."""
        ...

    def render_html(
        self,
        template: DocumentTemplate,
        context: dict[str, Any],
    ) -> str:
        """Render template to HTML string."""
        ...

    def generate_pdf(
        self,
        organization_id: UUID,
        template_type: TemplateType,
        context: dict[str, Any],
        *,
        template_name: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[UUID] = None,
        document_number: Optional[str] = None,
        save_record: bool = True,
    ) -> tuple[bytes, Optional[GeneratedDocument]]:
        """
        Generate PDF from template.

        Returns:
            Tuple of (pdf_bytes, generated_document_record)
        """
        ...

    def send_email(
        self,
        organization_id: UUID,
        template_type: TemplateType,
        context: dict[str, Any],
        recipient_email: str,
        *,
        template_name: Optional[str] = None,
        attachments: Optional[list[tuple[str, bytes]]] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[UUID] = None,
    ) -> GeneratedDocument:
        """Render and send email from template."""
        ...
```

---

### 4. Template Context Schemas

Define expected context variables for each template type to ensure consistency.

**Location:** `app/schemas/document_context.py`

```python
class OfferLetterContext(BaseModel):
    """Context for OFFER_LETTER template."""
    # Candidate info
    candidate_name: str
    candidate_email: str

    # Position
    job_title: str
    department_name: str
    reporting_to: Optional[str] = None
    location: str
    employment_type: str  # FULL_TIME, PART_TIME, CONTRACT

    # Compensation
    gross_salary: Decimal
    salary_currency: str
    salary_frequency: str  # MONTHLY, ANNUAL
    allowances: Optional[list[dict]] = None  # {name, amount}
    benefits: Optional[list[str]] = None

    # Dates
    offer_date: date
    offer_expiry_date: date
    proposed_start_date: date
    probation_months: Optional[int] = None

    # Organization
    organization_name: str
    organization_address: Optional[str] = None
    hr_contact_name: str
    hr_contact_email: str

    # Custom
    special_conditions: Optional[str] = None


class EmploymentContractContext(BaseModel):
    """Context for EMPLOYMENT_CONTRACT template."""
    # Employee info
    employee_name: str
    employee_address: str
    employee_id_number: Optional[str] = None  # National ID

    # Employment
    job_title: str
    department_name: str
    start_date: date
    employment_type: str
    probation_period_months: int

    # Compensation
    base_salary: Decimal
    salary_currency: str
    payment_frequency: str
    payment_method: str  # BANK_TRANSFER, CHEQUE
    allowances: list[dict]  # {name, amount, taxable}

    # Work hours
    work_hours_per_week: int
    work_days: list[str]  # ['Monday', 'Tuesday', ...]

    # Leave entitlement
    annual_leave_days: int
    sick_leave_days: int

    # Termination
    notice_period_days: int

    # Legal
    governing_law: str  # e.g., "Laws of Nigeria"
    arbitration_location: Optional[str] = None

    # Confidentiality
    has_nda: bool = True
    has_non_compete: bool = False
    non_compete_duration_months: Optional[int] = None

    # Organization
    organization_name: str
    organization_registration_number: Optional[str] = None
    organization_address: str
    signatory_name: str
    signatory_title: str
```

---

### 5. Module-Specific Services

Each module creates facade services that use DocumentGeneratorService with appropriate context.

**Example:** `app/services/people/recruit/offer_letter_service.py`

```python
class OfferLetterService:
    """
    Service for generating offer letters.

    Wraps DocumentGeneratorService with offer-specific logic.
    """

    def __init__(self, db: Session):
        self.db = db
        self.doc_service = DocumentGeneratorService(db)

    def generate_offer_letter(
        self,
        offer: JobOffer,
        *,
        template_name: Optional[str] = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate PDF offer letter for a job offer.

        Args:
            offer: The JobOffer model with related data loaded
            template_name: Optional specific template name

        Returns:
            Tuple of (pdf_bytes, generated_document_record)
        """
        # Build context from offer
        context = self._build_offer_context(offer)

        # Generate PDF
        return self.doc_service.generate_pdf(
            organization_id=offer.organization_id,
            template_type=TemplateType.OFFER_LETTER,
            context=context.model_dump(),
            template_name=template_name,
            entity_type="JOB_OFFER",
            entity_id=offer.offer_id,
            document_number=f"OFFER-{offer.offer_number}",
        )

    def _build_offer_context(self, offer: JobOffer) -> OfferLetterContext:
        """Build context from JobOffer model."""
        applicant = offer.applicant
        job = offer.job_opening
        org = offer.organization

        return OfferLetterContext(
            candidate_name=f"{applicant.first_name} {applicant.last_name}",
            candidate_email=applicant.email,
            job_title=job.job_title,
            department_name=job.department.department_name if job.department else "",
            location=job.location or "",
            employment_type=job.employment_type,
            gross_salary=offer.offered_salary,
            salary_currency=offer.currency_code or "NGN",
            salary_frequency="MONTHLY",
            offer_date=offer.offer_date,
            offer_expiry_date=offer.offer_expiry_date,
            proposed_start_date=offer.proposed_start_date,
            organization_name=org.legal_name,
            hr_contact_name="HR Department",
            hr_contact_email=org.hr_email or "hr@company.com",
        )
```

---

## File Structure

```
app/
├── models/finance/automation/
│   ├── document_template.py      # MODIFY: Add new TemplateType values
│   ├── generated_document.py     # NEW: Track generated documents
│   └── __init__.py               # MODIFY: Export new model
│
├── schemas/
│   └── document_context.py       # NEW: Context schemas for validation
│
├── services/
│   └── automation/
│       ├── __init__.py           # NEW
│       └── document_generator.py # NEW: Generic generator service
│
└── services/people/recruit/
    └── offer_letter_service.py   # NEW: Offer letter facade

templates/
└── documents/                    # NEW: Base document templates
    ├── base_letter.html          # Base layout for letters
    ├── offer_letter_default.html # Default offer letter
    └── employment_contract_default.html
```

---

## Migration Plan

### Alembic Migration: Add TemplateType Values

```python
def upgrade():
    # Add new enum values to document_template_type
    op.execute("""
        ALTER TYPE automation.document_template_type ADD VALUE IF NOT EXISTS 'OFFER_LETTER';
        ALTER TYPE automation.document_template_type ADD VALUE IF NOT EXISTS 'EMPLOYMENT_CONTRACT';
        -- ... add all new types
    """)

def downgrade():
    # PostgreSQL doesn't support removing enum values
    # Would need to recreate the type (not recommended for production)
    pass
```

### Alembic Migration: Create generated_document Table

```python
def upgrade():
    op.create_table(
        'generated_document',
        sa.Column('document_id', sa.UUID(), primary_key=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('template_id', sa.UUID(), nullable=False),
        sa.Column('template_version', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('document_number', sa.String(50), nullable=True),
        sa.Column('document_date', sa.Date(), nullable=False),
        sa.Column('output_format', sa.String(20), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=True),
        sa.Column('sent_to', sa.String(255), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('context_snapshot', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='DRAFT'),
        sa.Column('superseded_by', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='automation'
    )

    op.create_index('idx_generated_doc_org', 'generated_document', ['organization_id'], schema='automation')
    op.create_index('idx_generated_doc_entity', 'generated_document', ['entity_type', 'entity_id'], schema='automation')
    op.create_index('idx_generated_doc_template', 'generated_document', ['template_id'], schema='automation')
```

---

## Usage Flow

### 1. Admin Creates Template
```
Admin → Settings → Document Templates → Create
- Select type: OFFER_LETTER
- Enter template content (Jinja2)
- Configure header/footer (logo, company info)
- Set as default
```

### 2. HR Generates Offer Letter
```
HR → Recruitment → Applicant → Create Offer → Generate PDF
- System loads default OFFER_LETTER template
- Builds context from JobOffer data
- Generates PDF via WeasyPrint
- Creates GeneratedDocument record
- Returns PDF for download/email
```

### 3. Re-generation / Audit
```
HR → Recruitment → Applicant → Documents tab
- View list of generated documents
- See context_snapshot (what data was used)
- Regenerate with current data if needed
```

---

## Security Considerations

1. **Template Content**: Store templates in database, not file system (prevents arbitrary file injection)
2. **Jinja2 Sandboxing**: Use Jinja2 SandboxedEnvironment for template rendering
3. **Context Validation**: Validate context against Pydantic schemas before rendering
4. **File Storage**: Generated files stored in org-specific directories with UUID names
5. **Access Control**: Template management requires `automation.template.manage` permission

---

## Dependencies

- **WeasyPrint**: PDF generation from HTML/CSS
- **Jinja2**: Template rendering (already used)
- **Pydantic**: Context validation (already used)

---

## Implementation Order

1. Database migration for TemplateType enum extension
2. GeneratedDocument model and migration
3. DocumentGeneratorService
4. Context schemas (OfferLetterContext, etc.)
5. OfferLetterService (facade)
6. Default template HTML files
7. Admin UI for template management
8. Integration with recruitment workflow
