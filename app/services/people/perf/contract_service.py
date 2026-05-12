"""
Performance Contract Service — OHCSF Performance Management System.

Handles creation, signing workflow, amendment, and querying of
PerformanceContract records within the PMS module.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.performance_contract import PerformanceContract
from app.models.people.perf.contract_amendment import ContractAmendmentWorkflow
from app.models.people.perf.pms_enums import ContractStatus, ContractType
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.perf.performance_policy import get_policy_profile
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

__all__ = [
    "ContractAuthorizationError",
    "ContractServiceError",
    "ContractNotFoundError",
    "ContractValidationError",
    "ContractStatusError",
    "PerformanceContractService",
]


# =============================================================================
# Error classes
# =============================================================================


class ContractServiceError(Exception):
    """Base error for PerformanceContractService."""


class ContractNotFoundError(ContractServiceError):
    """Raised when a contract cannot be found."""

    def __init__(self, contract_id: UUID) -> None:
        self.contract_id = contract_id
        super().__init__(f"Performance contract {contract_id} not found")


class ContractValidationError(ContractServiceError):
    """Raised when contract input validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ContractStatusError(ContractServiceError):
    """Raised when a status transition is invalid."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


class ContractAuthorizationError(ContractServiceError):
    """Raised when the current actor cannot sign the contract."""

    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(f"Not authorized to {action} this contract")


# =============================================================================
# Service
# =============================================================================


class PerformanceContractService:
    """Service for managing OHCSF performance contracts."""

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
        policy_profile_name: str = "GOVERNMENT_PMS",
    ) -> None:
        self.db = db
        self.ctx = ctx
        self._policy = get_policy_profile(policy_profile_name)

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private validation helpers
    # ------------------------------------------------------------------

    def _validate_objectives(self, objectives: list[dict]) -> None:
        """Validate performance objectives.

        Rules:
        - Must contain between 3 and 7 objectives (inclusive).
        - Each objective must include SMART objective text, KPI, and target.
        - The sum of ``weight`` values must equal exactly 70.

        Raises:
            ContractValidationError: if either rule is violated.
        """
        count = len(objectives)
        min_count = self._policy.objective.min_count
        max_count = self._policy.objective.max_count
        if count < min_count or count > max_count:
            raise ContractValidationError(
                f"Objectives must be between {min_count} and {max_count} (got {count})"
            )
        total_weight = 0
        for idx, obj in enumerate(objectives, start=1):
            objective_text = str(
                obj.get("objective") or obj.get("kra") or obj.get("title") or ""
            ).strip()
            kpi_text = str(obj.get("kpi") or "").strip()
            target_text = str(obj.get("target") or "").strip()
            if not objective_text:
                raise ContractValidationError(
                    f"Objective {idx} must include objective description"
                )
            if not kpi_text:
                raise ContractValidationError(f"Objective {idx} must include a KPI")
            if not target_text:
                raise ContractValidationError(f"Objective {idx} must include a target")

            weight = int(obj.get("weight", 0))
            if weight <= 0:
                raise ContractValidationError(
                    f"Objective {idx} weight must be greater than zero"
                )
            total_weight += weight
        required_total_weight = self._policy.objective.required_total_weight
        if total_weight != required_total_weight:
            raise ContractValidationError(
                "Objective weights must sum to "
                f"{required_total_weight} (got {total_weight})"
            )

    def _validate_competency_selections(self, competencies: list[dict]) -> None:
        """Validate competency development-focus selections.

        Rules:
        - Exactly 5 competencies must be selected.
        - All selected competency IDs must be unique and non-empty.
        - Exactly 3 competencies must have ``is_development_focus=True``.

        Raises:
            ContractValidationError: if the rule is violated.
        """
        required_count = self._policy.competency.required_count
        if len(competencies) != required_count:
            raise ContractValidationError(
                f"Exactly {required_count} competencies must be selected "
                f"(got {len(competencies)})"
            )
        competency_ids = [
            str(c.get("competency_id") or "").strip() for c in competencies
        ]
        if any(not cid for cid in competency_ids):
            raise ContractValidationError(
                "Each competency entry must include competency_id"
            )
        if len(set(competency_ids)) != required_count:
            raise ContractValidationError("Selected competencies must be unique")

        dev_focus = [c for c in competencies if c.get("is_development_focus")]
        required_focus = self._policy.competency.required_development_focus_count
        if len(dev_focus) != required_focus:
            raise ContractValidationError(
                f"Exactly {required_focus} competencies must be marked as development "
                f"focus (got {len(dev_focus)})"
            )

    def _validate_competencies_payload(self, competency_ids: list | None) -> None:
        """Validate competency payload shape and policy rules."""
        required_count = self._policy.competency.required_count
        if competency_ids is None:
            raise ContractValidationError(
                f"Exactly {required_count} competencies are required for EPMS planning"
            )
        if not isinstance(competency_ids, list):
            raise ContractValidationError("competency_ids must be a list")
        if not competency_ids:
            raise ContractValidationError(
                f"Exactly {required_count} competencies are required for EPMS planning"
            )
        if not isinstance(competency_ids[0], dict):
            raise ContractValidationError(
                "competency_ids must be a list of objects with competency_id and "
                "is_development_focus"
            )
        self._validate_competency_selections(competency_ids)

    def _validate_contract_planning_compliance(
        self,
        contract: PerformanceContract,
    ) -> None:
        """Ensure objective and competency planning is complete before activation."""
        self._validate_objectives(contract.objectives or [])
        self._validate_competencies_payload(contract.competency_ids)

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_contract(self, org_id: UUID, contract_id: UUID) -> PerformanceContract:
        """Return a contract by ID scoped to the organisation.

        Raises:
            ContractNotFoundError: if not found.
        """
        stmt = select(PerformanceContract).where(
            PerformanceContract.organization_id == org_id,
            PerformanceContract.contract_id == contract_id,
        )
        contract = self.db.scalar(stmt)
        if contract is None:
            raise ContractNotFoundError(contract_id)
        return contract

    def list_contracts(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: ContractStatus | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[PerformanceContract]:
        """List performance contracts for an organisation with optional filters."""
        stmt = (
            select(PerformanceContract)
            .where(PerformanceContract.organization_id == org_id)
            .order_by(PerformanceContract.created_at.desc())
        )

        if cycle_id is not None:
            stmt = stmt.where(PerformanceContract.cycle_id == cycle_id)
        if employee_id is not None:
            stmt = stmt.where(PerformanceContract.employee_id == employee_id)
        if status is not None:
            stmt = stmt.where(PerformanceContract.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(PerformanceContract.contract_code.ilike(pattern))

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=PerformanceContract.contract_id,
        )

    # ------------------------------------------------------------------
    # Public mutation methods
    # ------------------------------------------------------------------

    def create_contract(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID,
        employee_id: UUID,
        supervisor_id: UUID,
        contract_code: str,
        contract_type: ContractType,
        objectives: list[dict],
        competency_ids: list | None = None,
        development_plan: str | None = None,
    ) -> PerformanceContract:
        """Create a new performance contract.

        Validates objectives and, when provided, competency selections
        before persisting.

        Raises:
            ContractValidationError: if validation fails.
        """
        self._ensure_pms_write_mode(org_id)
        # Sequencing gate: institutional goals must exist for this cycle/department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.institutional_performance import (
            InstitutionalPerformance,
        )
        from app.models.people.perf.pms_enums import InstitutionalPerfStatus

        employee = self.db.scalar(
            select(Employee).where(Employee.employee_id == employee_id)
        )
        if employee and employee.department_id:
            inst_perf = self.db.scalar(
                select(InstitutionalPerformance).where(
                    InstitutionalPerformance.organization_id == org_id,
                    InstitutionalPerformance.cycle_id == cycle_id,
                    InstitutionalPerformance.department_id == employee.department_id,
                    InstitutionalPerformance.status != InstitutionalPerfStatus.DRAFT,
                )
            )
            if not inst_perf:
                raise ContractValidationError(
                    "Departmental goals must be agreed before individual performance planning. "
                    "Create and approve institutional performance targets for this department first."
                )

        self._validate_objectives(objectives)
        self._validate_competencies_payload(competency_ids)

        contract = PerformanceContract(
            organization_id=org_id,
            cycle_id=cycle_id,
            employee_id=employee_id,
            supervisor_id=supervisor_id,
            contract_code=contract_code,
            contract_type=contract_type,
            objectives=objectives,
            competency_ids=competency_ids,
            development_plan=development_plan,
            status=ContractStatus.DRAFT,
        )
        self.db.add(contract)
        self.db.flush()
        logger.info(
            "Created PerformanceContract %s for employee %s in cycle %s",
            contract.contract_id,
            employee_id,
            cycle_id,
        )
        return contract

    def _get_employee_for_person(self, org_id: UUID, person_id: UUID) -> UUID | None:
        """Resolve the actor's employee ID for the organisation."""
        from app.models.people.hr.employee import Employee, EmployeeStatus

        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.person_id == person_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        return employee.employee_id if employee is not None else None

    def sign_employee(
        self, org_id: UUID, contract_id: UUID, *, actor_person_id: UUID
    ) -> PerformanceContract:
        """Record the employee's signature.

        If the supervisor has already signed, transitions the contract to ACTIVE.
        Otherwise sets status to PENDING_SIGNATURE.
        """
        self._ensure_pms_write_mode(org_id)
        contract = self.get_contract(org_id, contract_id)
        actor_employee_id = self._get_employee_for_person(org_id, actor_person_id)
        if actor_employee_id != contract.employee_id:
            raise ContractAuthorizationError("employee-sign")
        if contract.status not in (
            ContractStatus.DRAFT,
            ContractStatus.PENDING_SIGNATURE,
        ):
            raise ContractStatusError(contract.status.value, "PENDING_SIGNATURE")
        self._validate_contract_planning_compliance(contract)
        contract.employee_signed_date = date.today()

        if contract.supervisor_signed_date is not None:
            contract.status = ContractStatus.ACTIVE
            logger.info("Contract %s activated — both parties have signed", contract_id)
        else:
            contract.status = ContractStatus.PENDING_SIGNATURE
            logger.info("Contract %s pending supervisor signature", contract_id)

        self.db.flush()
        return contract

    def sign_supervisor(
        self, org_id: UUID, contract_id: UUID, *, actor_person_id: UUID
    ) -> PerformanceContract:
        """Record the supervisor's signature.

        If the employee has already signed, transitions the contract to ACTIVE.
        Otherwise sets status to PENDING_SIGNATURE.
        """
        self._ensure_pms_write_mode(org_id)
        contract = self.get_contract(org_id, contract_id)
        actor_employee_id = self._get_employee_for_person(org_id, actor_person_id)
        if actor_employee_id != contract.supervisor_id:
            raise ContractAuthorizationError("supervisor-sign")
        if contract.status not in (
            ContractStatus.DRAFT,
            ContractStatus.PENDING_SIGNATURE,
        ):
            raise ContractStatusError(contract.status.value, "PENDING_SIGNATURE")
        self._validate_contract_planning_compliance(contract)
        contract.supervisor_signed_date = date.today()

        if contract.employee_signed_date is not None:
            contract.status = ContractStatus.ACTIVE
            logger.info("Contract %s activated — both parties have signed", contract_id)
        else:
            contract.status = ContractStatus.PENDING_SIGNATURE
            logger.info("Contract %s pending employee signature", contract_id)

        self.db.flush()
        return contract

    def countersign(
        self,
        org_id: UUID,
        contract_id: UUID,
        countersigner_id: UUID,
    ) -> PerformanceContract:
        """Record the HoD counter-signature and activate the contract.

        Counter-signing makes the contract valid regardless of employee
        signature status.
        """
        self._ensure_pms_write_mode(org_id)
        contract = self.get_contract(org_id, contract_id)

        if contract.status not in (
            ContractStatus.DRAFT,
            ContractStatus.PENDING_SIGNATURE,
        ):
            raise ContractStatusError(contract.status.value, "PENDING_SIGNATURE")
        self._validate_contract_planning_compliance(contract)

        contract.countersigner_id = countersigner_id
        contract.countersigner_date = date.today()
        contract.status = ContractStatus.ACTIVE

        self.db.flush()
        logger.info(
            "Contract %s countersigned and activated by %s",
            contract_id,
            countersigner_id,
        )
        return contract

    def check_30_day_requirement(self, org_id: UUID) -> list[dict]:
        """Find employees without active contracts within 30 days of joining/transfer/promotion."""
        from datetime import timedelta

        from app.models.people.hr.employee import Employee

        cutoff = date.today() - timedelta(days=30)

        # Employees who joined in the last 30 days
        stmt = select(Employee).where(
            Employee.organization_id == org_id,
            Employee.status == "ACTIVE",
            Employee.date_of_joining >= cutoff,
        )
        recent_employees = list(self.db.scalars(stmt).all())

        missing = []
        for emp in recent_employees:
            has_contract = self.db.scalar(
                select(PerformanceContract.contract_id).where(
                    PerformanceContract.organization_id == org_id,
                    PerformanceContract.employee_id == emp.employee_id,
                    PerformanceContract.status.in_(
                        [
                            ContractStatus.ACTIVE,
                            ContractStatus.PENDING_SIGNATURE,
                        ]
                    ),
                )
            )
            if not has_contract:
                missing.append(
                    {
                        "employee_id": emp.employee_id,
                        "employee_code": emp.employee_code,
                        "date_of_joining": emp.date_of_joining,
                        "days_since_joining": (date.today() - emp.date_of_joining).days,
                    }
                )

        return missing

    def amend_contract(
        self,
        org_id: UUID,
        contract_id: UUID,
        *,
        new_objectives: list[dict],
        amendment_reason: str,
        competency_ids: list | None = None,
    ) -> PerformanceContract:
        """Amend an existing contract by creating a replacement contract.

        The original contract status is set to AMENDED.  The new contract
        code has "-A" appended to the original code, and its
        ``amended_from_id`` points to the original.

        Raises:
            ContractValidationError: if new_objectives fail validation.
            ContractNotFoundError: if the original contract is not found.
        """
        self._ensure_pms_write_mode(org_id)
        self._validate_objectives(new_objectives)
        self._validate_competencies_payload(competency_ids)

        original = self.get_contract(org_id, contract_id)

        new_contract = PerformanceContract(
            organization_id=org_id,
            cycle_id=original.cycle_id,
            employee_id=original.employee_id,
            supervisor_id=original.supervisor_id,
            contract_code=f"{original.contract_code}-A",
            contract_type=original.contract_type,
            objectives=new_objectives,
            competency_ids=competency_ids,
            development_plan=original.development_plan,
            status=ContractStatus.DRAFT,
            amended_from_id=original.contract_id,
            amendment_reason=amendment_reason,
        )

        self.db.add(new_contract)
        self.db.flush()
        logger.info(
            "Contract %s amended → new contract %s (%s)",
            contract_id,
            new_contract.contract_id,
            new_contract.contract_code,
        )
        return new_contract

    def create_amendment_workflow(
        self,
        org_id: UUID,
        *,
        original_contract_id: UUID,
        new_contract_id: UUID,
        hod_id: UUID,
        hr_head_id: UUID,
        signoff_note: str | None = None,
    ) -> ContractAmendmentWorkflow:
        """Create a staged signoff workflow for a proposed amendment."""
        self._ensure_pms_write_mode(org_id)
        original = self.get_contract(org_id, original_contract_id)

        workflow = ContractAmendmentWorkflow(
            organization_id=org_id,
            contract_id=new_contract_id,
            original_contract_id=original_contract_id,
            status="PENDING",
            appraisee_id=original.employee_id,
            appraiser_id=original.supervisor_id,
            hod_id=hod_id,
            hr_head_id=hr_head_id,
            signoff_note=signoff_note,
        )
        self.db.add(workflow)
        self.db.flush()
        return workflow

    def get_active_amendment_workflow(
        self, org_id: UUID, contract_id: UUID
    ) -> ContractAmendmentWorkflow | None:
        return self.db.scalar(
            select(ContractAmendmentWorkflow).where(
                ContractAmendmentWorkflow.organization_id == org_id,
                ContractAmendmentWorkflow.contract_id == contract_id,
                ContractAmendmentWorkflow.status == "PENDING",
            )
        )

    def approve_amendment_stage(
        self,
        org_id: UUID,
        contract_id: UUID,
        *,
        stage: str,
        actor_person_id: UUID,
        note: str | None = None,
    ) -> ContractAmendmentWorkflow:
        """Approve one stage in amendment signoff chain.

        Stages are strictly sequential:
        APPRAISEE -> APPRAISER -> HOD -> HR_HEAD.
        Final stage activates amended contract and marks original as AMENDED.
        """
        self._ensure_pms_write_mode(org_id)
        workflow = self.get_active_amendment_workflow(org_id, contract_id)
        if workflow is None:
            raise ContractValidationError(
                "No pending amendment workflow for this contract"
            )

        actor_employee_id = self._get_employee_for_person(org_id, actor_person_id)
        if actor_employee_id is None:
            raise ContractAuthorizationError("amendment-approve")

        stage_order = ["APPRAISEE", "APPRAISER", "HOD", "HR_HEAD"]
        if stage not in stage_order:
            raise ContractValidationError(f"Invalid amendment signoff stage: {stage}")

        # Enforce sequential ordering
        if stage == "APPRAISER" and workflow.appraisee_signed_date is None:
            raise ContractValidationError("Appraisee signoff is required first")
        if stage == "HOD" and workflow.appraiser_signed_date is None:
            raise ContractValidationError("Appraiser signoff is required first")
        if stage == "HR_HEAD" and workflow.hod_signed_date is None:
            raise ContractValidationError("HoD signoff is required first")

        today = date.today()
        if stage == "APPRAISEE":
            if actor_employee_id != workflow.appraisee_id:
                raise ContractAuthorizationError("amendment-approve-appraisee")
            workflow.appraisee_signed_date = today
        elif stage == "APPRAISER":
            if actor_employee_id != workflow.appraiser_id:
                raise ContractAuthorizationError("amendment-approve-appraiser")
            workflow.appraiser_signed_date = today
        elif stage == "HOD":
            if actor_employee_id != workflow.hod_id:
                raise ContractAuthorizationError("amendment-approve-hod")
            workflow.hod_signed_date = today
        elif stage == "HR_HEAD":
            if actor_employee_id != workflow.hr_head_id:
                raise ContractAuthorizationError("amendment-approve-hr-head")
            workflow.hr_head_signed_date = today
            workflow.status = "APPROVED"

            amended = self.get_contract(org_id, workflow.contract_id)
            original = self.get_contract(org_id, workflow.original_contract_id)
            self._validate_contract_planning_compliance(amended)
            amended.status = ContractStatus.ACTIVE
            original.status = ContractStatus.AMENDED

        if note:
            workflow.signoff_note = note

        self.db.flush()
        return workflow

    def reject_amendment(
        self,
        org_id: UUID,
        contract_id: UUID,
        *,
        stage: str,
        actor_person_id: UUID,
        reason: str,
    ) -> ContractAmendmentWorkflow:
        """Reject a pending amendment and cancel proposed amended contract."""
        self._ensure_pms_write_mode(org_id)
        workflow = self.get_active_amendment_workflow(org_id, contract_id)
        if workflow is None:
            raise ContractValidationError(
                "No pending amendment workflow for this contract"
            )

        actor_employee_id = self._get_employee_for_person(org_id, actor_person_id)
        if actor_employee_id is None:
            raise ContractAuthorizationError("amendment-reject")

        expected_by_stage = {
            "APPRAISEE": workflow.appraisee_id,
            "APPRAISER": workflow.appraiser_id,
            "HOD": workflow.hod_id,
            "HR_HEAD": workflow.hr_head_id,
        }
        expected = expected_by_stage.get(stage)
        if expected is None:
            raise ContractValidationError(f"Invalid amendment rejection stage: {stage}")
        if actor_employee_id != expected:
            raise ContractAuthorizationError("amendment-reject-stage")

        workflow.status = "REJECTED"
        workflow.rejected_by_stage = stage
        workflow.rejection_reason = reason

        amended = self.get_contract(org_id, workflow.contract_id)
        amended.status = ContractStatus.CANCELLED

        self.db.flush()
        return workflow
