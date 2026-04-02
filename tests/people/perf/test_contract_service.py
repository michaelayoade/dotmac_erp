"""
Tests for PerformanceContractService — validation logic.

Tests focus on validation methods and public API contracts
using MagicMock for the DB session.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.services.people.perf.contract_service import (
    ContractAuthorizationError,
    ContractNotFoundError,
    ContractServiceError,
    ContractStatusError,
    ContractValidationError,
    PerformanceContractService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_OBJECTIVES = [
    {"objective": "Obj A", "kpi": "KPI A", "target": "Target A", "weight": 20},
    {"objective": "Obj B", "kpi": "KPI B", "target": "Target B", "weight": 20},
    {"objective": "Obj C", "kpi": "KPI C", "target": "Target C", "weight": 30},
]  # 3 objectives, total weight = 70


def make_objectives(count: int, *, total_weight: int = 70) -> list[dict]:
    """Create `count` objectives with weights distributed to sum to total_weight."""
    per = total_weight // count
    remainder = total_weight - per * count
    objs = [
        {
            "objective": f"Obj {i}",
            "kpi": f"KPI {i}",
            "target": f"Target {i}",
            "weight": per,
        }
        for i in range(count)
    ]
    if objs:
        objs[0]["weight"] += remainder
    return objs


def make_competencies(
    total: int = 5,
    *,
    dev_focus_count: int = 3,
) -> list[dict]:
    """Create competencies with `dev_focus_count` marked as development focus."""
    comps = []
    for i in range(total):
        comps.append(
            {
                "competency_id": str(uuid.uuid4()),
                "is_development_focus": i < dev_focus_count,
            }
        )
    return comps


def make_service() -> PerformanceContractService:
    db = MagicMock()
    return PerformanceContractService(db)


# ---------------------------------------------------------------------------
# Error class hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_contract_not_found_is_contract_service_error(self) -> None:
        err = ContractNotFoundError(uuid.uuid4())
        assert isinstance(err, ContractServiceError)

    def test_contract_validation_is_contract_service_error(self) -> None:
        err = ContractValidationError("bad input")
        assert isinstance(err, ContractServiceError)

    def test_contract_status_is_contract_service_error(self) -> None:
        err = ContractStatusError("DRAFT", "ACTIVE")
        assert isinstance(err, ContractServiceError)

    def test_not_found_message_contains_id(self) -> None:
        cid = uuid.uuid4()
        err = ContractNotFoundError(cid)
        assert str(cid) in str(err)

    def test_status_error_message_contains_states(self) -> None:
        err = ContractStatusError("DRAFT", "COMPLETED")
        msg = str(err)
        assert "DRAFT" in msg
        assert "COMPLETED" in msg


# ---------------------------------------------------------------------------
# _validate_objectives
# ---------------------------------------------------------------------------


class TestValidateObjectives:
    """Unit tests for _validate_objectives (private method tested directly)."""

    def setup_method(self) -> None:
        self.service = make_service()

    def test_accepts_valid_objectives(self) -> None:
        """Exactly 3 objectives with weights summing to 70 should not raise."""
        self.service._validate_objectives(VALID_OBJECTIVES)

    def test_accepts_7_objectives(self) -> None:
        objs = make_objectives(7)
        self.service._validate_objectives(objs)

    def test_rejects_fewer_than_3(self) -> None:
        objs = make_objectives(2)
        with pytest.raises(ContractValidationError, match="3"):
            self.service._validate_objectives(objs)

    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ContractValidationError):
            self.service._validate_objectives([])

    def test_rejects_more_than_7(self) -> None:
        objs = make_objectives(8)
        with pytest.raises(ContractValidationError, match="7"):
            self.service._validate_objectives(objs)

    def test_rejects_weights_not_summing_to_70(self) -> None:
        objs = [
            {"objective": "A", "kpi": "KPI A", "target": "T", "weight": 30},
            {"objective": "B", "kpi": "KPI B", "target": "T", "weight": 30},
            {"objective": "C", "kpi": "KPI C", "target": "T", "weight": 30},
        ]
        with pytest.raises(ContractValidationError, match="70"):
            self.service._validate_objectives(objs)

    def test_rejects_weights_summing_to_less_than_70(self) -> None:
        objs = [
            {"objective": "A", "kpi": "KPI A", "target": "T", "weight": 20},
            {"objective": "B", "kpi": "KPI B", "target": "T", "weight": 20},
            {"objective": "C", "kpi": "KPI C", "target": "T", "weight": 20},
        ]
        with pytest.raises(ContractValidationError, match="70"):
            self.service._validate_objectives(objs)

    def test_rejects_missing_kpi(self) -> None:
        objs = make_objectives(3)
        objs[0]["kpi"] = ""
        with pytest.raises(ContractValidationError, match="KPI"):
            self.service._validate_objectives(objs)

    def test_rejects_missing_target(self) -> None:
        objs = make_objectives(3)
        objs[0]["target"] = ""
        with pytest.raises(ContractValidationError, match="target"):
            self.service._validate_objectives(objs)

    def test_boundary_3_objectives_accepted(self) -> None:
        self.service._validate_objectives(make_objectives(3))

    def test_boundary_7_objectives_accepted(self) -> None:
        self.service._validate_objectives(make_objectives(7))


# ---------------------------------------------------------------------------
# _validate_competency_selections
# ---------------------------------------------------------------------------


class TestValidateCompetencies:
    """Unit tests for _validate_competency_selections."""

    def setup_method(self) -> None:
        self.service = make_service()

    def test_accepts_3_development_focus(self) -> None:
        comps = make_competencies(5, dev_focus_count=3)
        self.service._validate_competency_selections(comps)

    def test_accepts_exactly_3_when_all_marked(self) -> None:
        comps = make_competencies(5, dev_focus_count=3)
        self.service._validate_competency_selections(comps)

    def test_rejects_not_exactly_3_development_focus_too_few(self) -> None:
        comps = make_competencies(5, dev_focus_count=2)
        with pytest.raises(ContractValidationError, match="3"):
            self.service._validate_competency_selections(comps)

    def test_rejects_not_exactly_3_development_focus_too_many(self) -> None:
        comps = make_competencies(5, dev_focus_count=4)
        with pytest.raises(ContractValidationError, match="3"):
            self.service._validate_competency_selections(comps)

    def test_rejects_zero_development_focus(self) -> None:
        comps = make_competencies(5, dev_focus_count=0)
        with pytest.raises(ContractValidationError, match="3"):
            self.service._validate_competency_selections(comps)

    def test_rejects_empty_competency_list(self) -> None:
        with pytest.raises(ContractValidationError, match="5"):
            self.service._validate_competency_selections([])

    def test_rejects_not_exactly_five_competencies(self) -> None:
        comps = make_competencies(4, dev_focus_count=3)
        with pytest.raises(ContractValidationError, match="5"):
            self.service._validate_competency_selections(comps)


# ---------------------------------------------------------------------------
# get_contract
# ---------------------------------------------------------------------------


class TestGetContract:
    def setup_method(self) -> None:
        self.db = MagicMock()
        self.service = PerformanceContractService(self.db)

    def test_returns_contract_when_found(self) -> None:
        from app.models.people.perf.performance_contract import PerformanceContract

        org_id = uuid.uuid4()
        contract_id = uuid.uuid4()

        mock_contract = MagicMock(spec=PerformanceContract)
        mock_contract.organization_id = org_id
        mock_contract.contract_id = contract_id

        self.db.scalar.return_value = mock_contract

        result = self.service.get_contract(org_id, contract_id)
        assert result is mock_contract

    def test_raises_not_found_when_missing(self) -> None:
        self.db.scalar.return_value = None
        with pytest.raises(ContractNotFoundError):
            self.service.get_contract(uuid.uuid4(), uuid.uuid4())


# ---------------------------------------------------------------------------
# sign_employee / sign_supervisor — status transitions
# ---------------------------------------------------------------------------


class TestSignEmployee:
    def _make_contract(
        self,
        *,
        employee_signed: bool = False,
        supervisor_signed: bool = False,
    ):
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.employee_signed_date = date.today() if employee_signed else None
        c.supervisor_signed_date = date.today() if supervisor_signed else None
        c.status = ContractStatus.PENDING_SIGNATURE
        c.organization_id = uuid.uuid4()
        c.contract_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.objectives = VALID_OBJECTIVES
        c.competency_ids = make_competencies()
        return c

    def test_sets_employee_signed_date(self) -> None:

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract()
        db.scalar.side_effect = [contract, MagicMock(employee_id=contract.employee_id)]

        service.sign_employee(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.employee_signed_date == date.today()

    def test_both_signed_sets_active(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        # supervisor already signed
        contract = self._make_contract(supervisor_signed=True)
        db.scalar.side_effect = [contract, MagicMock(employee_id=contract.employee_id)]

        service.sign_employee(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.status == ContractStatus.ACTIVE

    def test_only_employee_signed_remains_pending(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract(supervisor_signed=False)
        db.scalar.side_effect = [contract, MagicMock(employee_id=contract.employee_id)]

        service.sign_employee(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.status == ContractStatus.PENDING_SIGNATURE

    def test_rejects_non_employee_signer(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract()
        db.scalar.side_effect = [contract, None]

        with pytest.raises(ContractAuthorizationError, match="employee-sign"):
            service.sign_employee(
                contract.organization_id,
                contract.contract_id,
                actor_person_id=uuid.uuid4(),
            )


class TestSignSupervisor:
    def _make_contract(
        self,
        *,
        employee_signed: bool = False,
        supervisor_signed: bool = False,
    ):
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.employee_signed_date = date.today() if employee_signed else None
        c.supervisor_signed_date = date.today() if supervisor_signed else None
        c.status = ContractStatus.PENDING_SIGNATURE
        c.organization_id = uuid.uuid4()
        c.contract_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.objectives = VALID_OBJECTIVES
        c.competency_ids = make_competencies()
        return c

    def test_sets_supervisor_signed_date(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract()
        db.scalar.side_effect = [
            contract,
            MagicMock(employee_id=contract.supervisor_id),
        ]

        service.sign_supervisor(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.supervisor_signed_date == date.today()

    def test_both_signed_sets_active(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract(employee_signed=True)
        db.scalar.side_effect = [
            contract,
            MagicMock(employee_id=contract.supervisor_id),
        ]

        service.sign_supervisor(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.status == ContractStatus.ACTIVE

    def test_only_supervisor_signed_remains_pending(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract(employee_signed=False)
        db.scalar.side_effect = [
            contract,
            MagicMock(employee_id=contract.supervisor_id),
        ]

        service.sign_supervisor(
            contract.organization_id,
            contract.contract_id,
            actor_person_id=uuid.uuid4(),
        )

        assert contract.status == ContractStatus.PENDING_SIGNATURE

    def test_rejects_non_supervisor_signer(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract()
        db.scalar.side_effect = [contract, None]

        with pytest.raises(ContractAuthorizationError, match="supervisor-sign"):
            service.sign_supervisor(
                contract.organization_id,
                contract.contract_id,
                actor_person_id=uuid.uuid4(),
            )


class TestAmendmentWorkflow:
    def test_rejects_out_of_order_stage_approval(self) -> None:
        from app.models.people.perf.contract_amendment import ContractAmendmentWorkflow

        db = MagicMock()
        service = PerformanceContractService(db)

        workflow = ContractAmendmentWorkflow(
            organization_id=uuid.uuid4(),
            contract_id=uuid.uuid4(),
            original_contract_id=uuid.uuid4(),
            appraisee_id=uuid.uuid4(),
            appraiser_id=uuid.uuid4(),
            hod_id=uuid.uuid4(),
            hr_head_id=uuid.uuid4(),
            status="PENDING",
        )

        db.scalar.side_effect = [
            workflow,
            MagicMock(employee_id=workflow.appraiser_id),
        ]

        with pytest.raises(ContractValidationError, match="Appraisee signoff"):
            service.approve_amendment_stage(
                workflow.organization_id,
                workflow.contract_id,
                stage="APPRAISER",
                actor_person_id=uuid.uuid4(),
            )

    def test_final_hr_head_signoff_activates_amended_and_marks_original_amended(self) -> None:
        from app.models.people.perf.contract_amendment import ContractAmendmentWorkflow
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)

        workflow = ContractAmendmentWorkflow(
            organization_id=uuid.uuid4(),
            contract_id=uuid.uuid4(),
            original_contract_id=uuid.uuid4(),
            appraisee_id=uuid.uuid4(),
            appraiser_id=uuid.uuid4(),
            hod_id=uuid.uuid4(),
            hr_head_id=uuid.uuid4(),
            status="PENDING",
            appraisee_signed_date=date.today(),
            appraiser_signed_date=date.today(),
            hod_signed_date=date.today(),
        )

        amended_contract = MagicMock(spec=PerformanceContract)
        amended_contract.organization_id = workflow.organization_id
        amended_contract.contract_id = workflow.contract_id
        amended_contract.status = ContractStatus.DRAFT
        amended_contract.objectives = VALID_OBJECTIVES
        amended_contract.competency_ids = make_competencies()

        original_contract = MagicMock(spec=PerformanceContract)
        original_contract.organization_id = workflow.organization_id
        original_contract.contract_id = workflow.original_contract_id
        original_contract.status = ContractStatus.ACTIVE

        db.scalar.side_effect = [
            workflow,
            MagicMock(employee_id=workflow.hr_head_id),
            amended_contract,
            original_contract,
        ]

        result = service.approve_amendment_stage(
            workflow.organization_id,
            workflow.contract_id,
            stage="HR_HEAD",
            actor_person_id=uuid.uuid4(),
        )

        assert result.status == "APPROVED"
        assert result.hr_head_signed_date == date.today()
        assert amended_contract.status == ContractStatus.ACTIVE
        assert original_contract.status == ContractStatus.AMENDED


# ---------------------------------------------------------------------------
# countersign
# ---------------------------------------------------------------------------


class TestCountersign:
    def _make_contract(self, *, employee_signed: bool = True):
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.employee_signed_date = date.today() if employee_signed else None
        c.countersigner_id = None
        c.countersigner_date = None
        c.status = ContractStatus.PENDING_SIGNATURE
        c.organization_id = uuid.uuid4()
        c.contract_id = uuid.uuid4()
        c.objectives = VALID_OBJECTIVES
        c.competency_ids = make_competencies()
        return c

    def test_sets_countersigner_id_and_date(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract()
        db.scalar.return_value = contract
        countersigner_id = uuid.uuid4()

        service.countersign(
            contract.organization_id, contract.contract_id, countersigner_id
        )

        assert contract.countersigner_id == countersigner_id
        assert contract.countersigner_date == date.today()

    def test_countersign_makes_contract_active(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract(employee_signed=False)
        db.scalar.return_value = contract

        service.countersign(
            contract.organization_id, contract.contract_id, uuid.uuid4()
        )

        assert contract.status == ContractStatus.ACTIVE


# ---------------------------------------------------------------------------
# amend_contract
# ---------------------------------------------------------------------------


class TestAmendContract:
    def _make_active_contract(self) -> MagicMock:
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.contract_id = uuid.uuid4()
        c.organization_id = uuid.uuid4()
        c.status = ContractStatus.ACTIVE
        c.contract_code = "PC-2026-001"
        c.cycle_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.contract_type = "INDIVIDUAL"
        c.competency_ids = []
        c.development_plan = None
        return c

    def test_original_contract_remains_active_until_signoff_completion(self) -> None:
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        new_objs = make_objectives(3)

        # Mock the db.add so we can inspect the new contract
        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        service.amend_contract(
            original.organization_id,
            original.contract_id,
            new_objectives=new_objs,
            amendment_reason="Restructured targets",
            competency_ids=make_competencies(),
        )

        assert original.status == ContractStatus.ACTIVE

    def test_amended_code_has_suffix(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        service.amend_contract(
            original.organization_id,
            original.contract_id,
            new_objectives=make_objectives(3),
            amendment_reason="Test",
            competency_ids=make_competencies(),
        )

        assert len(added) == 1
        new_contract = added[0]
        assert new_contract.contract_code.endswith("-A")

    def test_new_contract_references_original(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        service.amend_contract(
            original.organization_id,
            original.contract_id,
            new_objectives=make_objectives(3),
            amendment_reason="Restructured",
            competency_ids=make_competencies(),
        )

        new_contract = added[0]
        assert new_contract.amended_from_id == original.contract_id

    def test_amend_validates_new_objectives(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        bad_objectives = make_objectives(2)  # less than 3
        with pytest.raises(ContractValidationError):
            service.amend_contract(
                original.organization_id,
                original.contract_id,
                new_objectives=bad_objectives,
                amendment_reason="Bad",
                competency_ids=make_competencies(),
            )


# ---------------------------------------------------------------------------
# create_contract — sequencing gate and input validation
# ---------------------------------------------------------------------------


class TestCreateContract:
    """Test create_contract including the institutional sequencing gate."""

    _SENTINEL = object()  # distinct from None

    def _make_employee(self, *, department_id=_SENTINEL):
        from types import SimpleNamespace

        dept = uuid.uuid4() if department_id is self._SENTINEL else department_id
        return SimpleNamespace(
            employee_id=uuid.uuid4(),
            department_id=dept,
        )

    def _make_inst_perf(self):
        """Return a minimal institutional performance record (non-DRAFT)."""
        from types import SimpleNamespace

        from app.models.people.perf.pms_enums import InstitutionalPerfStatus

        return SimpleNamespace(
            status=InstitutionalPerfStatus.APPRAISED,
        )

    def _base_kwargs(self, org_id, employee_id):
        return dict(
            org_id=org_id,
            cycle_id=uuid.uuid4(),
            employee_id=employee_id,
            supervisor_id=uuid.uuid4(),
            contract_code="PC-2026-001",
            contract_type="INDIVIDUAL",
            objectives=VALID_OBJECTIVES,
            competency_ids=make_competencies(),
        )

    def test_create_contract_succeeds_with_valid_data(self) -> None:
        """Happy path: institutional goals exist, valid objectives, contract created."""
        db = MagicMock()
        service = PerformanceContractService(db)

        org_id = uuid.uuid4()
        employee = self._make_employee()
        inst_perf = self._make_inst_perf()

        db.scalar.side_effect = [employee, inst_perf]
        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        result = service.create_contract(
            **self._base_kwargs(org_id, employee.employee_id)
        )

        assert len(added) == 1
        db.flush.assert_called_once()
        # Returned contract is the object that was added
        assert result is added[0]

    def test_create_contract_blocked_without_institutional_goals(self) -> None:
        """Sequencing gate: raises when no approved institutional performance exists."""
        db = MagicMock()
        service = PerformanceContractService(db)

        org_id = uuid.uuid4()
        employee = self._make_employee()

        # Second scalar call returns None → no institutional perf record
        db.scalar.side_effect = [employee, None]

        with pytest.raises(
            ContractValidationError, match="Departmental goals must be agreed"
        ):
            service.create_contract(**self._base_kwargs(org_id, employee.employee_id))

        db.add.assert_not_called()

    def test_create_contract_skips_gate_when_no_department(self) -> None:
        """Sequencing gate is skipped when employee has no department_id."""
        db = MagicMock()
        service = PerformanceContractService(db)

        org_id = uuid.uuid4()
        # Employee with no department
        employee = self._make_employee(department_id=None)

        # Provide a sentinel second value so the list is not exhausted if SQLAlchemy
        # model construction triggers any unexpected scalar call; the gate block itself
        # must NOT call scalar because department_id is None.
        db.scalar.side_effect = [employee, None]
        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        result = service.create_contract(
            **self._base_kwargs(org_id, employee.employee_id)
        )

        assert len(added) == 1
        assert result is added[0]
        # Gate must not have fired a second db.scalar call — only 1 call consumed
        assert db.scalar.call_count == 1

    def test_create_contract_validates_objectives_weight(self) -> None:
        """Objective validation runs after the sequencing gate passes."""
        db = MagicMock()
        service = PerformanceContractService(db)

        org_id = uuid.uuid4()
        employee = self._make_employee()
        inst_perf = self._make_inst_perf()

        db.scalar.side_effect = [employee, inst_perf]

        bad_objectives = [
            {"objective": "A", "kpi": "KPI A", "target": "T", "weight": 20},
            {"objective": "B", "kpi": "KPI B", "target": "T", "weight": 20},
            {"objective": "C", "kpi": "KPI C", "target": "T", "weight": 20},
        ]

        with pytest.raises(ContractValidationError, match="70"):
            service.create_contract(
                **{
                    **self._base_kwargs(org_id, employee.employee_id),
                    "objectives": bad_objectives,
                }
            )

        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Status guards on sign_employee / sign_supervisor / countersign
# ---------------------------------------------------------------------------


class TestContractStatusGuards:
    """Test that signing methods reject contracts in terminal/invalid states."""

    def _make_contract_with_status(self, status):
        from app.models.people.perf.performance_contract import PerformanceContract

        c = MagicMock(spec=PerformanceContract)
        c.organization_id = uuid.uuid4()
        c.contract_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.employee_signed_date = None
        c.supervisor_signed_date = None
        c.status = status
        c.objectives = VALID_OBJECTIVES
        c.competency_ids = make_competencies()
        return c

    def _make_pending_contract_with_missing_planning(self):
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.organization_id = uuid.uuid4()
        c.contract_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.employee_signed_date = None
        c.supervisor_signed_date = None
        c.status = ContractStatus.PENDING_SIGNATURE
        c.objectives = make_objectives(3)
        c.competency_ids = None
        return c

    def test_sign_employee_rejects_completed_contract(self) -> None:
        """sign_employee raises ContractStatusError on COMPLETED contracts."""
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract_with_status(ContractStatus.COMPLETED)
        # First scalar → contract (from get_contract), second → employee lookup
        db.scalar.side_effect = [contract, MagicMock(employee_id=contract.employee_id)]

        with pytest.raises(ContractStatusError):
            service.sign_employee(
                contract.organization_id,
                contract.contract_id,
                actor_person_id=uuid.uuid4(),
            )

    def test_sign_supervisor_rejects_cancelled_contract(self) -> None:
        """sign_supervisor raises ContractStatusError on CANCELLED contracts."""
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract_with_status(ContractStatus.CANCELLED)
        db.scalar.side_effect = [
            contract,
            MagicMock(employee_id=contract.supervisor_id),
        ]

        with pytest.raises(ContractStatusError):
            service.sign_supervisor(
                contract.organization_id,
                contract.contract_id,
                actor_person_id=uuid.uuid4(),
            )

    def test_countersign_rejects_active_contract(self) -> None:
        """countersign raises ContractStatusError when contract is already ACTIVE."""
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_contract_with_status(ContractStatus.ACTIVE)
        db.scalar.return_value = contract

        with pytest.raises(ContractStatusError):
            service.countersign(
                contract.organization_id,
                contract.contract_id,
                uuid.uuid4(),
            )

    def test_sign_employee_blocks_when_competencies_missing(self) -> None:
        db = MagicMock()
        service = PerformanceContractService(db)
        contract = self._make_pending_contract_with_missing_planning()
        db.scalar.side_effect = [contract, MagicMock(employee_id=contract.employee_id)]

        with pytest.raises(ContractValidationError, match="competencies"):
            service.sign_employee(
                contract.organization_id,
                contract.contract_id,
                actor_person_id=uuid.uuid4(),
            )


# ---------------------------------------------------------------------------
# amend_contract — additional cases
# ---------------------------------------------------------------------------


class TestAmendContractAdditional:
    """Additional amendment tests beyond those in TestAmendContract."""

    def _make_active_contract(self) -> MagicMock:
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        c = MagicMock(spec=PerformanceContract)
        c.contract_id = uuid.uuid4()
        c.organization_id = uuid.uuid4()
        c.status = ContractStatus.ACTIVE
        c.contract_code = "PC-2026-002"
        c.cycle_id = uuid.uuid4()
        c.employee_id = uuid.uuid4()
        c.supervisor_id = uuid.uuid4()
        c.contract_type = "INDIVIDUAL"
        c.competency_ids = []
        c.development_plan = None
        return c

    def test_amend_creates_new_version_with_draft_status(self) -> None:
        """Amendment creates a new contract with DRAFT status."""
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        service.amend_contract(
            original.organization_id,
            original.contract_id,
            new_objectives=make_objectives(3),
            amendment_reason="Mid-year restructure",
            competency_ids=make_competencies(),
        )

        assert len(added) == 1
        new_contract = added[0]
        assert new_contract.status == ContractStatus.DRAFT

    def test_amend_keeps_original_active_pending_signoff(self) -> None:
        """Amendment request keeps original ACTIVE until final signoff."""
        from app.models.people.perf.pms_enums import ContractStatus

        db = MagicMock()
        service = PerformanceContractService(db)
        original = self._make_active_contract()
        db.scalar.return_value = original

        db.add.side_effect = lambda obj: None  # discard

        service.amend_contract(
            original.organization_id,
            original.contract_id,
            new_objectives=make_objectives(3),
            amendment_reason="Restructure",
            competency_ids=make_competencies(),
        )

        assert original.status == ContractStatus.ACTIVE


# ---------------------------------------------------------------------------
# check_30_day_requirement
# ---------------------------------------------------------------------------


class TestCheck30DayRequirement:
    """Test the 30-day contract requirement check."""

    def _make_employee(self, *, days_ago: int, employee_code: str = "EMP-001"):
        from datetime import timedelta
        from types import SimpleNamespace

        return SimpleNamespace(
            employee_id=uuid.uuid4(),
            employee_code=employee_code,
            date_of_joining=date.today() - timedelta(days=days_ago),
        )

    def _mock_scalars(self, db: MagicMock, employees: list) -> None:
        """Configure db.scalars(stmt).all() to return the given employee list."""
        scalars_result = MagicMock()
        scalars_result.all.return_value = employees
        db.scalars.return_value = scalars_result

    def test_finds_employees_without_contracts(self) -> None:
        """Employees who joined within 30 days and have no active contract are flagged."""
        db = MagicMock()
        service = PerformanceContractService(db)
        org_id = uuid.uuid4()

        emp = self._make_employee(days_ago=10, employee_code="EMP-010")

        self._mock_scalars(db, [emp])
        # scalar() returns None → no active contract found
        db.scalar.return_value = None

        result = service.check_30_day_requirement(org_id)

        assert len(result) == 1
        assert result[0]["employee_id"] == emp.employee_id
        assert result[0]["employee_code"] == "EMP-010"
        assert result[0]["days_since_joining"] == 10

    def test_skips_employees_with_active_contracts(self) -> None:
        """Employees with an active contract are not included in the result."""
        db = MagicMock()
        service = PerformanceContractService(db)
        org_id = uuid.uuid4()

        emp = self._make_employee(days_ago=15, employee_code="EMP-015")

        self._mock_scalars(db, [emp])
        # scalar() returns a truthy contract_id → employee has a contract
        db.scalar.return_value = uuid.uuid4()

        result = service.check_30_day_requirement(org_id)

        assert result == []

    def test_returns_empty_when_no_recent_employees(self) -> None:
        """Returns an empty list when there are no recently joined employees."""
        db = MagicMock()
        service = PerformanceContractService(db)
        org_id = uuid.uuid4()

        self._mock_scalars(db, [])

        result = service.check_30_day_requirement(org_id)

        assert result == []
