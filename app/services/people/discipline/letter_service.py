"""
Discipline Letter Service.

Service for generating disciplinary letters (queries, warnings, decisions, terminations).
Wraps DocumentGeneratorService with discipline-specific context building.
"""

import logging
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.finance.automation.document_template import TemplateType
from app.models.finance.automation.generated_document import GeneratedDocument
from app.models.people.discipline import (
    ActionType,
    CaseAction,
    CaseStatus,
    DisciplinaryCase,
    DocumentType,
)
from app.schemas.document_context import (
    DecisionLetterContext,
    DisciplineTerminationLetterContext,
    QueryLetterContext,
    WarningLetterContext,
)
from app.services.automation.document_generator import (
    DocumentGeneratorService,
    TemplateNotFoundError,
)

logger = logging.getLogger(__name__)


class DisciplineLetterServiceError(Exception):
    """Base error for discipline letter service."""

    pass


class CaseNotFoundError(DisciplineLetterServiceError):
    """Disciplinary case not found."""

    pass


class InvalidCaseStateError(DisciplineLetterServiceError):
    """Case is not in a valid state for this operation."""

    pass


class ActionNotFoundError(DisciplineLetterServiceError):
    """Case action not found."""

    pass


class DisciplineLetterService:
    """
    Service for generating discipline-related letters.

    Wraps DocumentGeneratorService with discipline-specific logic:
    - Builds context from DisciplinaryCase and related entities
    - Validates case state before generation
    - Tracks generated documents with entity references
    """

    def __init__(self, db: Session):
        self.db = db
        self._doc_service = DocumentGeneratorService(db)

    def get_case_with_relations(self, case_id: UUID) -> Optional[DisciplinaryCase]:
        """
        Get a disciplinary case with all related data loaded.

        Loads employee, actions, witnesses for context building.
        """
        stmt = (
            select(DisciplinaryCase)
            .options(
                joinedload(DisciplinaryCase.employee),
                joinedload(DisciplinaryCase.reported_by),
                joinedload(DisciplinaryCase.actions),
                joinedload(DisciplinaryCase.witnesses),
            )
            .where(DisciplinaryCase.case_id == case_id)
        )
        return self.db.scalar(stmt)

    def generate_query_letter(
        self,
        case_id: UUID,
        user_id: UUID,
        *,
        signatory_name: str,
        signatory_title: str,
        template_name: Optional[str] = None,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        response_instructions: Optional[str] = None,
        policy_violated: Optional[str] = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate a query (show cause) letter PDF.

        Args:
            case_id: UUID of the DisciplinaryCase
            user_id: UUID of the user generating the letter
            signatory_name: Name of person signing
            signatory_title: Title of signatory
            template_name: Optional specific template name
            organization_name: Override organization name
            organization_address: Organization address for letterhead
            organization_logo_url: URL to company logo
            response_instructions: Custom instructions for employee response
            policy_violated: Specific policy reference

        Returns:
            Tuple of (pdf_bytes, generated_document_record)

        Raises:
            CaseNotFoundError: If case not found
            InvalidCaseStateError: If case is not in valid state for query letter
            TemplateNotFoundError: If no query letter template exists
        """
        case = self.get_case_with_relations(case_id)
        if not case:
            raise CaseNotFoundError(f"Disciplinary case {case_id} not found")

        # Query letter is typically generated when issuing query (DRAFT -> QUERY_ISSUED)
        # or when case already has query issued
        if case.status not in (CaseStatus.DRAFT, CaseStatus.QUERY_ISSUED):
            raise InvalidCaseStateError(
                f"Cannot generate query letter for case in {case.status.value} status. "
                "Query letter should be generated when issuing the query."
            )

        if not case.query_text:
            raise InvalidCaseStateError(
                "Cannot generate query letter without query text. "
                "Please set the query text first."
            )

        if not case.response_due_date:
            raise InvalidCaseStateError(
                "Cannot generate query letter without response due date."
            )

        # Build context
        context = self._build_query_context(
            case,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            organization_name=organization_name,
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            response_instructions=response_instructions,
            policy_violated=policy_violated,
        )

        # Generate PDF
        pdf_bytes, doc_record = self._doc_service.generate_pdf(
            organization_id=case.organization_id,
            template_type=TemplateType.SHOW_CAUSE_NOTICE,
            context=context.model_dump(),
            template_name=template_name,
            entity_type="DISCIPLINE_CASE",
            entity_id=case.case_id,
            document_number=f"QUERY-{case.case_number}",
            document_title=f"Query Letter - {case.case_number}",
            created_by=user_id,
            save_record=True,
            use_base_template=True,
        )

        if doc_record is None:
            raise DisciplineLetterServiceError("Failed to create document record")

        # Also store reference in case documents
        from app.models.people.discipline import CaseDocument

        case_doc = CaseDocument(
            case_id=case.case_id,
            document_type=DocumentType.QUERY_LETTER,
            title=f"Query Letter - {case.case_number}",
            description="Auto-generated query letter",
            file_path=doc_record.file_path or "",
            file_name=f"query_letter_{case.case_number}.pdf",
            file_size=doc_record.file_size_bytes,
            mime_type="application/pdf",
            uploaded_by_id=user_id,
        )
        self.db.add(case_doc)

        logger.info(
            "Generated query letter for case %s",
            case.case_number,
        )

        return pdf_bytes, doc_record

    def generate_warning_letter(
        self,
        case_id: UUID,
        action_id: UUID,
        user_id: UUID,
        *,
        signatory_name: str,
        signatory_title: str,
        expected_improvement: str,
        consequences_if_repeated: str,
        template_name: Optional[str] = None,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        improvement_deadline: Optional[date] = None,
        appeal_instructions: Optional[str] = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate a warning letter PDF for a specific action.

        Args:
            case_id: UUID of the DisciplinaryCase
            action_id: UUID of the CaseAction (warning)
            user_id: UUID of the user generating the letter
            signatory_name: Name of person signing
            signatory_title: Title of signatory
            expected_improvement: What improvement is expected
            consequences_if_repeated: What happens if violation repeats
            template_name: Optional specific template name
            organization_name: Override organization name
            organization_address: Organization address for letterhead
            organization_logo_url: URL to company logo
            improvement_deadline: Deadline for improvement
            appeal_instructions: Custom appeal instructions

        Returns:
            Tuple of (pdf_bytes, generated_document_record)

        Raises:
            CaseNotFoundError: If case not found
            ActionNotFoundError: If action not found
            InvalidCaseStateError: If action is not a warning type
        """
        case = self.get_case_with_relations(case_id)
        if not case:
            raise CaseNotFoundError(f"Disciplinary case {case_id} not found")

        # Find the action
        action = self.db.get(CaseAction, action_id)
        if not action or action.case_id != case.case_id:
            raise ActionNotFoundError(f"Action {action_id} not found in case {case_id}")

        # Verify it's a warning action
        warning_types = {
            ActionType.VERBAL_WARNING,
            ActionType.WRITTEN_WARNING,
            ActionType.FINAL_WARNING,
        }
        if action.action_type not in warning_types:
            raise InvalidCaseStateError(
                f"Action type {action.action_type.value} is not a warning. "
                "Use generate_decision_letter for other action types."
            )

        # Build context
        context = self._build_warning_context(
            case,
            action,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            expected_improvement=expected_improvement,
            consequences_if_repeated=consequences_if_repeated,
            organization_name=organization_name,
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            improvement_deadline=improvement_deadline,
            appeal_instructions=appeal_instructions,
        )

        # Generate PDF
        pdf_bytes, doc_record = self._doc_service.generate_pdf(
            organization_id=case.organization_id,
            template_type=TemplateType.WARNING_LETTER,
            context=context.model_dump(),
            template_name=template_name,
            entity_type="DISCIPLINE_CASE",
            entity_id=case.case_id,
            document_number=f"WARNING-{case.case_number}-{action.action_type.value}",
            document_title=f"Warning Letter - {case.case_number}",
            created_by=user_id,
            save_record=True,
            use_base_template=True,
        )

        if doc_record is None:
            raise DisciplineLetterServiceError("Failed to create document record")

        # Also store reference in case documents
        from app.models.people.discipline import CaseDocument

        case_doc = CaseDocument(
            case_id=case.case_id,
            document_type=DocumentType.WARNING_LETTER,
            title=f"Warning Letter - {action.action_type.value}",
            description=f"Auto-generated {action.action_type.value.lower().replace('_', ' ')}",
            file_path=doc_record.file_path or "",
            file_name=f"warning_letter_{case.case_number}.pdf",
            file_size=doc_record.file_size_bytes,
            mime_type="application/pdf",
            uploaded_by_id=user_id,
        )
        self.db.add(case_doc)

        logger.info(
            "Generated warning letter (%s) for case %s",
            action.action_type.value,
            case.case_number,
        )

        return pdf_bytes, doc_record

    def generate_decision_letter(
        self,
        case_id: UUID,
        user_id: UUID,
        *,
        signatory_name: str,
        signatory_title: str,
        template_name: Optional[str] = None,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        appeal_instructions: Optional[str] = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate a decision letter PDF for a case.

        Args:
            case_id: UUID of the DisciplinaryCase
            user_id: UUID of the user generating the letter
            signatory_name: Name of person signing
            signatory_title: Title of signatory
            template_name: Optional specific template name
            organization_name: Override organization name
            organization_address: Organization address for letterhead
            organization_logo_url: URL to company logo
            appeal_instructions: Custom appeal instructions

        Returns:
            Tuple of (pdf_bytes, generated_document_record)

        Raises:
            CaseNotFoundError: If case not found
            InvalidCaseStateError: If decision has not been made
        """
        case = self.get_case_with_relations(case_id)
        if not case:
            raise CaseNotFoundError(f"Disciplinary case {case_id} not found")

        # Decision letter should be generated after decision is made
        if case.status not in (
            CaseStatus.DECISION_MADE,
            CaseStatus.APPEAL_FILED,
            CaseStatus.APPEAL_DECIDED,
            CaseStatus.CLOSED,
        ):
            raise InvalidCaseStateError(
                f"Cannot generate decision letter for case in {case.status.value} status. "
                "Decision must be made first."
            )

        if not case.decision_summary:
            raise InvalidCaseStateError(
                "Cannot generate decision letter without decision summary."
            )

        # Build context
        context = self._build_decision_context(
            case,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            organization_name=organization_name,
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            appeal_instructions=appeal_instructions,
        )

        # Use a general template for decision letters
        # (Could be added as a new TemplateType if needed)
        pdf_bytes, doc_record = self._doc_service.generate_pdf(
            organization_id=case.organization_id,
            template_type=TemplateType.WARNING_LETTER,  # Reuse for now
            context=context.model_dump(),
            template_name=template_name or "decision_letter",
            entity_type="DISCIPLINE_CASE",
            entity_id=case.case_id,
            document_number=f"DECISION-{case.case_number}",
            document_title=f"Decision Letter - {case.case_number}",
            created_by=user_id,
            save_record=True,
            use_base_template=True,
        )

        if doc_record is None:
            raise DisciplineLetterServiceError("Failed to create document record")

        # Also store reference in case documents
        from app.models.people.discipline import CaseDocument

        case_doc = CaseDocument(
            case_id=case.case_id,
            document_type=DocumentType.DECISION_LETTER,
            title=f"Decision Letter - {case.case_number}",
            description="Auto-generated decision letter",
            file_path=doc_record.file_path or "",
            file_name=f"decision_letter_{case.case_number}.pdf",
            file_size=doc_record.file_size_bytes,
            mime_type="application/pdf",
            uploaded_by_id=user_id,
        )
        self.db.add(case_doc)

        logger.info(
            "Generated decision letter for case %s",
            case.case_number,
        )

        return pdf_bytes, doc_record

    def generate_termination_letter(
        self,
        case_id: UUID,
        action_id: UUID,
        user_id: UUID,
        *,
        signatory_name: str,
        signatory_title: str,
        case_summary: str,
        template_name: Optional[str] = None,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        items_to_return: Optional[list[str]] = None,
        return_deadline: Optional[date] = None,
        appeal_instructions: Optional[str] = None,
    ) -> tuple[bytes, GeneratedDocument]:
        """
        Generate a termination letter PDF for a disciplinary termination.

        Args:
            case_id: UUID of the DisciplinaryCase
            action_id: UUID of the CaseAction (termination)
            user_id: UUID of the user generating the letter
            signatory_name: Name of person signing
            signatory_title: Title of signatory
            case_summary: Brief history of the disciplinary case
            template_name: Optional specific template name
            organization_name: Override organization name
            organization_address: Organization address for letterhead
            organization_logo_url: URL to company logo
            items_to_return: List of company property to return
            return_deadline: Deadline for returning property
            appeal_instructions: Custom appeal instructions

        Returns:
            Tuple of (pdf_bytes, generated_document_record)

        Raises:
            CaseNotFoundError: If case not found
            ActionNotFoundError: If action not found
            InvalidCaseStateError: If action is not a termination
        """
        case = self.get_case_with_relations(case_id)
        if not case:
            raise CaseNotFoundError(f"Disciplinary case {case_id} not found")

        # Find the action
        action = self.db.get(CaseAction, action_id)
        if not action or action.case_id != case.case_id:
            raise ActionNotFoundError(f"Action {action_id} not found in case {case_id}")

        # Verify it's a termination action
        if action.action_type != ActionType.TERMINATION:
            raise InvalidCaseStateError(
                f"Action type {action.action_type.value} is not a termination."
            )

        # Build context
        context = self._build_termination_context(
            case,
            action,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            case_summary=case_summary,
            organization_name=organization_name,
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            items_to_return=items_to_return,
            return_deadline=return_deadline,
            appeal_instructions=appeal_instructions,
        )

        # Generate PDF
        pdf_bytes, doc_record = self._doc_service.generate_pdf(
            organization_id=case.organization_id,
            template_type=TemplateType.TERMINATION_LETTER,
            context=context.model_dump(),
            template_name=template_name,
            entity_type="DISCIPLINE_CASE",
            entity_id=case.case_id,
            document_number=f"TERM-{case.case_number}",
            document_title=f"Termination Letter - {case.case_number}",
            created_by=user_id,
            save_record=True,
            use_base_template=True,
        )

        if doc_record is None:
            raise DisciplineLetterServiceError("Failed to create document record")

        # Also store reference in case documents
        from app.models.people.discipline import CaseDocument

        case_doc = CaseDocument(
            case_id=case.case_id,
            document_type=DocumentType.TERMINATION_LETTER,
            title=f"Termination Letter - {case.case_number}",
            description="Auto-generated termination letter",
            file_path=doc_record.file_path or "",
            file_name=f"termination_letter_{case.case_number}.pdf",
            file_size=doc_record.file_size_bytes,
            mime_type="application/pdf",
            uploaded_by_id=user_id,
        )
        self.db.add(case_doc)

        logger.info(
            "Generated termination letter for case %s",
            case.case_number,
        )

        return pdf_bytes, doc_record

    def get_case_letters(self, case_id: UUID) -> list[GeneratedDocument]:
        """Get all generated letters for a disciplinary case."""
        case = self.get_case_with_relations(case_id)
        if not case:
            return []
        return self._doc_service.get_documents_for_entity(
            organization_id=case.organization_id,
            entity_type="DISCIPLINE_CASE",
            entity_id=case_id,
        )

    # =========================================================================
    # Context Building Methods
    # =========================================================================

    def _build_query_context(
        self,
        case: DisciplinaryCase,
        *,
        signatory_name: str,
        signatory_title: str,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        response_instructions: Optional[str] = None,
        policy_violated: Optional[str] = None,
    ) -> QueryLetterContext:
        """Build query letter context from case data."""
        employee = case.employee

        return QueryLetterContext(
            # Case info
            case_number=case.case_number,
            case_date=case.reported_date or date.today(),
            # Employee info
            employee_name=employee.full_name if employee else "Employee",
            employee_code=employee.employee_code if employee else "",
            employee_address=None,  # Could be added from employee record
            job_title=employee.designation.designation_name
            if employee and employee.designation
            else None,
            department_name=employee.department.department_name
            if employee and employee.department
            else None,
            # Violation details
            violation_type=case.violation_type.value if case.violation_type else "",
            violation_severity=case.severity.value if case.severity else "",
            incident_date=case.incident_date or date.today(),
            incident_description=case.description or "",
            policy_violated=policy_violated,
            # Query details
            query_text=case.query_text or "",
            response_due_date=case.response_due_date or date.today(),
            response_instructions=response_instructions,
            # Organization
            organization_name=organization_name or "Organization",
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            # Signatory
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            # Reference
            letter_date=date.today(),
        )

    def _build_warning_context(
        self,
        case: DisciplinaryCase,
        action: CaseAction,
        *,
        signatory_name: str,
        signatory_title: str,
        expected_improvement: str,
        consequences_if_repeated: str,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        improvement_deadline: Optional[date] = None,
        appeal_instructions: Optional[str] = None,
    ) -> WarningLetterContext:
        """Build warning letter context from case and action data."""
        employee = case.employee

        # Get previous warnings for context
        previous_warnings = []
        for prev_action in case.actions:
            if prev_action.action_id != action.action_id:
                if prev_action.action_type in (
                    ActionType.VERBAL_WARNING,
                    ActionType.WRITTEN_WARNING,
                    ActionType.FINAL_WARNING,
                ):
                    previous_warnings.append(
                        {
                            "type": prev_action.action_type.value,
                            "date": prev_action.effective_date.isoformat()
                            if prev_action.effective_date
                            else None,
                            "summary": prev_action.description,
                        }
                    )

        return WarningLetterContext(
            # Case info
            case_number=case.case_number,
            # Employee info
            employee_name=employee.full_name if employee else "Employee",
            employee_code=employee.employee_code if employee else "",
            employee_address=None,
            job_title=employee.designation.designation_name
            if employee and employee.designation
            else None,
            department_name=employee.department.department_name
            if employee and employee.department
            else None,
            # Warning details
            warning_type=action.action_type.value,
            warning_description=action.description or "",
            violation_type=case.violation_type.value if case.violation_type else "",
            incident_date=case.incident_date or date.today(),
            incident_summary=case.description or "",
            # Previous warnings
            previous_warnings=previous_warnings if previous_warnings else None,
            total_warnings_count=len(previous_warnings),
            # Expected improvement
            expected_improvement=expected_improvement,
            improvement_deadline=improvement_deadline,
            consequences_if_repeated=consequences_if_repeated,
            # Appeal rights
            appeal_period_days=14,
            appeal_deadline=case.appeal_deadline,
            appeal_instructions=appeal_instructions,
            # Effective dates
            effective_date=action.effective_date or date.today(),
            warning_expiry_date=action.warning_expiry_date,
            # Organization
            organization_name=organization_name or "Organization",
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            # Signatory
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            # Reference
            letter_date=date.today(),
        )

    def _build_decision_context(
        self,
        case: DisciplinaryCase,
        *,
        signatory_name: str,
        signatory_title: str,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        appeal_instructions: Optional[str] = None,
    ) -> DecisionLetterContext:
        """Build decision letter context from case data."""
        employee = case.employee

        # Build actions list
        actions = []
        for action in case.actions:
            if action.is_active:
                actions.append(
                    {
                        "type": action.action_type.value,
                        "description": action.description,
                        "effective_date": action.effective_date.isoformat()
                        if action.effective_date
                        else None,
                        "end_date": action.end_date.isoformat()
                        if action.end_date
                        else None,
                    }
                )

        return DecisionLetterContext(
            # Case info
            case_number=case.case_number,
            # Employee info
            employee_name=employee.full_name if employee else "Employee",
            employee_code=employee.employee_code if employee else "",
            employee_address=None,
            job_title=employee.designation.designation_name
            if employee and employee.designation
            else None,
            department_name=employee.department.department_name
            if employee and employee.department
            else None,
            # Investigation summary
            investigation_summary=case.description or "",
            hearing_date=case.hearing_date.date() if case.hearing_date else None,
            hearing_outcome=case.hearing_notes,
            # Decision
            decision_summary=case.decision_summary or "",
            decision_date=case.decision_date or date.today(),
            # Actions
            actions=actions,
            # Appeal rights
            appeal_period_days=14,
            appeal_deadline=case.appeal_deadline or date.today(),
            appeal_instructions=appeal_instructions,
            # Organization
            organization_name=organization_name or "Organization",
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            # Signatory
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            # Reference
            letter_date=date.today(),
        )

    def _build_termination_context(
        self,
        case: DisciplinaryCase,
        action: CaseAction,
        *,
        signatory_name: str,
        signatory_title: str,
        case_summary: str,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        organization_logo_url: Optional[str] = None,
        items_to_return: Optional[list[str]] = None,
        return_deadline: Optional[date] = None,
        appeal_instructions: Optional[str] = None,
    ) -> DisciplineTerminationLetterContext:
        """Build termination letter context from case and action data."""
        employee = case.employee

        # Get previous actions for history
        previous_actions = []
        for prev_action in case.actions:
            if prev_action.action_id != action.action_id:
                previous_actions.append(
                    {
                        "type": prev_action.action_type.value,
                        "date": prev_action.effective_date.isoformat()
                        if prev_action.effective_date
                        else None,
                        "description": prev_action.description,
                    }
                )

        return DisciplineTerminationLetterContext(
            # Case info
            case_number=case.case_number,
            # Employee info
            employee_name=employee.full_name if employee else "Employee",
            employee_code=employee.employee_code if employee else "",
            employee_address=None,
            job_title=employee.designation.designation_name
            if employee and employee.designation
            else None,
            department_name=employee.department.department_name
            if employee and employee.department
            else None,
            # Termination details
            termination_date=action.effective_date or date.today(),
            last_working_day=action.effective_date or date.today(),
            termination_reason=case.decision_summary or "",
            violation_type=case.violation_type.value if case.violation_type else "",
            incident_date=case.incident_date or date.today(),
            # Case history
            case_summary=case_summary,
            previous_actions=previous_actions if previous_actions else None,
            # Settlement (would need integration with payroll)
            final_settlement_items=None,
            total_settlement=None,
            currency_code="NGN",
            # Appeal rights
            appeal_period_days=14,
            appeal_deadline=case.appeal_deadline,
            appeal_instructions=appeal_instructions,
            # Return of property
            items_to_return=items_to_return,
            return_deadline=return_deadline,
            # Organization
            organization_name=organization_name or "Organization",
            organization_address=organization_address,
            organization_logo_url=organization_logo_url,
            # Signatory
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            # Reference
            letter_date=date.today(),
        )
