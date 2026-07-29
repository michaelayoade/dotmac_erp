"""
Tests for CRM → ERP expense-claim sync (field-technician expense requests).

DB-backed (SQLite via conftest): exercises DotMacCRMSyncService.create_expense_claim
end-to-end through ExpenseService.create_claim + submit_claim, plus the status
poll and category listing, and the 200/201 endpoint idempotency logic.
"""

import itertools
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Response

from app.models.expense import (
    ExpenseApproverLimit,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseLimitRule,
)
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.position import Position
from app.models.people.hr.position_assignment import PositionAssignment
from app.models.person import Person
from app.schemas.sync.dotmac_crm import (
    CRMExpenseClaimItemPayload,
    CRMExpenseClaimPayload,
)
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService


def _ensure_hr_tables(engine) -> None:
    """Create the HR/limit tables the submit flow touches (SQLite-safe)."""
    for table in (
        Department.__table__,
        Employee.__table__,
        Position.__table__,
        PositionAssignment.__table__,
        ExpenseApproverLimit.__table__,
        ExpenseLimitRule.__table__,
    ):
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


_claim_number_counter = itertools.count(1)


@pytest.fixture()
def org_id():
    """Unique org per test — the SQLite engine is shared across the suite."""
    return uuid.uuid4()


@pytest.fixture()
def service(db_session):
    _ensure_hr_tables(db_session.get_bind())
    return DotMacCRMSyncService(db_session)


@pytest.fixture()
def employee(db_session, org_id):
    person = Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name="Field",
        last_name="Tech",
        email=f"tech-{uuid.uuid4().hex[:10]}@example.com",
    )
    db_session.add(person)
    db_session.flush()
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:6].upper()}",
        date_of_joining=date(2025, 1, 1),
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add(employee)
    db_session.flush()
    return employee


@pytest.fixture()
def fuel_category(db_session, org_id):
    category = ExpenseCategory(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        category_code="FUEL",
        category_name="Fuel & Transport",
        requires_receipt=False,
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()
    return category


@pytest.fixture()
def numbering_patch():
    """Deterministic claim numbers without the core_config numbering tables."""
    with patch(
        "app.services.finance.common.numbering.SyncNumberingService"
        ".generate_next_number",
        side_effect=lambda org_id, seq_type: (
            f"EXP-2026-{next(_claim_number_counter):05d}"
        ),
    ):
        yield


def _payload(employee, omni_id=None, **overrides) -> CRMExpenseClaimPayload:
    items = overrides.pop(
        "items",
        [
            CRMExpenseClaimItemPayload(
                category_code="FUEL",
                description="Fuel for site visit",
                claimed_amount=Decimal("5000.00"),
                expense_date="2026-07-01",
                vendor_name="Total Energies",
            ),
            CRMExpenseClaimItemPayload(
                category_code="FUEL",
                description="Okada to customer premises",
                claimed_amount=Decimal("1500"),
            ),
        ],
    )
    defaults = dict(
        omni_id=omni_id or str(uuid.uuid4()),
        purpose="Site survey expenses",
        claim_date="2026-07-02",
        requested_by_email=employee.person.email,
        currency_code="NGN",
        remarks="Approved by supervisor on site",
        reference_number="EXP-REQ-00042",
    )
    defaults.update(overrides)
    return CRMExpenseClaimPayload(items=items, **defaults)


class TestCreateExpenseClaim:
    def test_happy_path_creates_and_submits_claim(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(employee)

        result = service.create_expense_claim(org_id, payload, employee.person_id)

        assert result.omni_id == payload.omni_id
        assert result.claim_number.startswith("EXP-2026-")
        # No approval chain configured -> plain SUBMITTED (lowercase in response)
        assert result.status in {"submitted", "pending_approval"}

        claim = (
            db_session.query(ExpenseClaim)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == payload.omni_id,
            )
            .one_or_none()
        )
        assert claim is not None
        assert claim.claim_id == result.claim_id
        assert claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }
        assert result.status == claim.status.value.lower()
        assert claim.employee_id == employee.employee_id
        assert claim.purpose == "Site survey expenses"
        assert claim.claim_date == date(2026, 7, 2)
        assert claim.currency_code == "NGN"
        assert claim.last_synced_at is not None
        assert "EXP-REQ-00042" in (claim.notes or "")
        assert "Approved by supervisor on site" in (claim.notes or "")
        assert claim.total_claimed_amount == Decimal("6500.00")

        items = sorted(claim.items, key=lambda item: item.sequence)
        assert len(items) == 2
        assert items[0].expense_date == date(2026, 7, 1)
        assert items[0].vendor_name == "Total Energies"
        # No expense_date on the second line -> falls back to claim_date
        assert items[1].expense_date == date(2026, 7, 2)
        assert items[1].claimed_amount == Decimal("1500")

    def test_identical_resend_returns_existing_claim(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(employee)

        first = service.create_expense_claim(org_id, payload, employee.person_id)
        second = service.create_expense_claim(org_id, payload, employee.person_id)

        assert second.claim_id == first.claim_id
        assert second.claim_number == first.claim_number
        assert second.omni_id == payload.omni_id

        count = (
            db_session.query(ExpenseClaim)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == payload.omni_id,
            )
            .count()
        )
        assert count == 1

    def test_concurrent_create_race_returns_existing_not_500(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        """A concurrent first-send that loses the omni_id race must return the
        winner's claim (via uq_expense_claim_org_crm_id), not a 500."""
        payload = _payload(employee)
        # The race winner creates and persists the claim.
        first = service.create_expense_claim(org_id, payload, employee.person_id)

        # Simulate the losing request: its pre-check misses the row the winner
        # already wrote (the TOCTOU window), so it enters the create path where
        # the unique constraint on (organization_id, crm_id) then fires.
        with patch.object(type(service), "_find_claim_by_omni_id", return_value=None):
            second = service.create_expense_claim(org_id, payload, employee.person_id)

        # It recovered by returning the winner's claim — no exception, no dup.
        assert second.claim_id == first.claim_id
        assert second.claim_number == first.claim_number
        assert second.omni_id == payload.omni_id
        count = (
            db_session.query(ExpenseClaim)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == payload.omni_id,
            )
            .count()
        )
        assert count == 1

    def test_changed_resend_is_rejected_409(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(employee)
        service.create_expense_claim(org_id, payload, employee.person_id)

        changed = _payload(
            employee,
            omni_id=payload.omni_id,
            items=[
                CRMExpenseClaimItemPayload(
                    category_code="FUEL",
                    description="Fuel for site visit",
                    claimed_amount=Decimal("9999.00"),
                    expense_date="2026-07-01",
                    vendor_name="Total Energies",
                ),
                CRMExpenseClaimItemPayload(
                    category_code="FUEL",
                    description="Okada to customer premises",
                    claimed_amount=Decimal("1500"),
                ),
            ],
        )
        with pytest.raises(HTTPException) as exc:
            service.create_expense_claim(org_id, changed, employee.person_id)
        assert exc.value.status_code == 409
        assert "cannot be modified" in exc.value.detail

    def test_unknown_employee_email_raises_422(
        self, service, org_id, fuel_category, numbering_patch, employee
    ):
        payload = _payload(employee, requested_by_email="nobody@nowhere.example.com")
        with pytest.raises(HTTPException) as exc:
            service.create_expense_claim(org_id, payload)
        assert exc.value.status_code == 422
        assert "No ERP employee matches email" in exc.value.detail
        assert "nobody@nowhere.example.com" in exc.value.detail

    def test_unknown_category_code_raises_422(
        self, service, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(
            employee,
            items=[
                CRMExpenseClaimItemPayload(
                    category_code="NOPE",
                    description="Mystery expense",
                    claimed_amount=Decimal("100"),
                )
            ],
        )
        with pytest.raises(HTTPException) as exc:
            service.create_expense_claim(org_id, payload)
        assert exc.value.status_code == 422
        assert "Unknown expense category code(s): NOPE" in exc.value.detail

    def test_missing_required_receipt_surfaces_as_422(
        self, service, db_session, org_id, employee, numbering_patch
    ):
        db_session.add(
            ExpenseCategory(
                category_id=uuid.uuid4(),
                organization_id=org_id,
                category_code="HOTEL",
                category_name="Accommodation",
                requires_receipt=True,
                is_active=True,
            )
        )
        db_session.flush()
        payload = _payload(
            employee,
            items=[
                CRMExpenseClaimItemPayload(
                    category_code="HOTEL",
                    description="Overnight stay",
                    claimed_amount=Decimal("20000"),
                )
            ],
        )
        with pytest.raises(HTTPException) as exc:
            service.create_expense_claim(org_id, payload, employee.person_id)
        assert exc.value.status_code == 422
        assert "receipt" in exc.value.detail.lower()

        # Endpoint contract: caller rolls back on HTTPException — verify the
        # rollback leaves no orphan DRAFT claim behind.
        db_session.rollback()
        count = (
            db_session.query(ExpenseClaim)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == payload.omni_id,
            )
            .count()
        )
        assert count == 0

    def test_unmapped_project_and_ticket_are_ignored(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(
            employee,
            project_crm_id=str(uuid.uuid4()),
            ticket_crm_id=str(uuid.uuid4()),
        )
        with patch.object(service, "_get_mapping", return_value=None):
            result = service.create_expense_claim(org_id, payload, employee.person_id)

        claim = db_session.get(ExpenseClaim, result.claim_id)
        assert claim.project_id is None
        assert claim.ticket_id is None

    def test_invalid_claim_date_raises_value_error(
        self, service, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(employee, claim_date="07/02/2026")
        with pytest.raises(ValueError, match="Invalid claim_date format"):
            service.create_expense_claim(org_id, payload)


class TestExpenseClaimStatusPoll:
    def test_status_found(
        self, service, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        payload = _payload(employee)
        created = service.create_expense_claim(org_id, payload, employee.person_id)

        claim = db_session.get(ExpenseClaim, created.claim_id)
        claim.rejection_reason = None
        claim.total_approved_amount = Decimal("6000.00")
        claim.paid_on = date(2026, 7, 5)
        db_session.flush()

        result = service.get_expense_claim_by_crm_id(org_id, payload.omni_id)
        assert result is not None
        assert result.claim_id == created.claim_id
        assert result.claim_number == created.claim_number
        assert result.status == claim.status.value.lower()
        assert result.total_claimed_amount == Decimal("6500.00")
        assert result.total_approved_amount == Decimal("6000.00")
        assert result.paid_on == date(2026, 7, 5)
        assert result.omni_id == payload.omni_id

    def test_status_missing_returns_none(self, service, org_id):
        assert service.get_expense_claim_by_crm_id(org_id, str(uuid.uuid4())) is None


class TestListExpenseCategories:
    def test_lists_active_categories_ordered_by_code(self, service, db_session, org_id):
        db_session.add_all(
            [
                ExpenseCategory(
                    category_id=uuid.uuid4(),
                    organization_id=org_id,
                    category_code="TRAVEL",
                    category_name="Travel",
                    requires_receipt=True,
                    max_amount_per_claim=Decimal("50000.00"),
                    is_active=True,
                ),
                ExpenseCategory(
                    category_id=uuid.uuid4(),
                    organization_id=org_id,
                    category_code="AIRTIME",
                    category_name="Airtime & Data",
                    requires_receipt=False,
                    is_active=True,
                ),
                ExpenseCategory(
                    category_id=uuid.uuid4(),
                    organization_id=org_id,
                    category_code="OLD",
                    category_name="Retired category",
                    requires_receipt=False,
                    is_active=False,
                ),
            ]
        )
        db_session.flush()

        result = service.list_expense_categories(org_id)
        codes = [item.category_code for item in result.items]
        assert codes == ["AIRTIME", "TRAVEL"]
        travel = result.items[1]
        assert travel.category_name == "Travel"
        assert travel.requires_receipt is True
        assert travel.max_amount_per_claim == Decimal("50000.00")


class TestExpenseClaimEndpoints:
    """Endpoint-level 200/201 idempotency + 404 handling (direct call)."""

    def test_post_returns_201_then_200_for_identical_resend(
        self, db_session, org_id, employee, fuel_category, numbering_patch
    ):
        from app.api.sync.dotmac_crm import create_expense_claim

        _ensure_hr_tables(db_session.get_bind())
        auth = {"organization_id": org_id, "person_id": employee.person_id}
        payload = _payload(employee)

        first_response = Response()
        first = create_expense_claim(payload, first_response, auth=auth, db=db_session)
        assert first_response.status_code == 201

        second_response = Response()
        second = create_expense_claim(
            payload, second_response, auth=auth, db=db_session
        )
        assert second_response.status_code == 200
        assert second.claim_id == first.claim_id

    def test_get_status_404_when_missing(self, db_session, org_id):
        from app.api.sync.dotmac_crm import get_expense_claim_status

        auth = {"organization_id": org_id, "person_id": uuid.uuid4()}
        with pytest.raises(HTTPException) as exc:
            get_expense_claim_status(str(uuid.uuid4()), auth=auth, db=db_session)
        assert exc.value.status_code == 404

    def test_get_categories_endpoint(self, db_session, org_id):
        from app.api.sync.dotmac_crm import list_expense_categories

        db_session.add(
            ExpenseCategory(
                category_id=uuid.uuid4(),
                organization_id=org_id,
                category_code="TOOLS",
                category_name="Tools",
                requires_receipt=False,
                is_active=True,
            )
        )
        db_session.flush()

        auth = {"organization_id": org_id, "person_id": uuid.uuid4()}
        result = list_expense_categories(auth=auth, db=db_session)
        assert [item.category_code for item in result.items] == ["TOOLS"]
