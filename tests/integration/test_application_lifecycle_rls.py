"""PostgreSQL canary for the managed lifecycle receipt's tenant boundary."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.schemas.application_lifecycle import (
    ApplicationLifecyclePlanRequest,
    ApplicationLifecycleTarget,
    DesiredApplicationState,
    ExternalSubject,
)
from app.services.application_lifecycle import ManagedApplicationLifecycle

pytestmark = pytest.mark.integration


def _target(organization_id: UUID, person_id: UUID) -> ApplicationLifecycleTarget:
    return ApplicationLifecycleTarget(
        organization_id=organization_id,
        person_id=person_id,
        desired_state=DesiredApplicationState.active,
        external_subject=ExternalSubject(
            provider_binding="rls-canary",
            issuer="https://idp.example.test/realms/rls",
            subject=uuid4().hex,
        ),
    )


def _organization(code: str) -> Organization:
    return Organization(
        organization_code=code,
        legal_name=f"Lifecycle RLS {code}",
        functional_currency_code="NGN",
        presentation_currency_code="NGN",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )


def _person(organization_id: UUID, label: str) -> Person:
    return Person(
        organization_id=organization_id,
        first_name="Lifecycle",
        last_name=label,
        email=f"lifecycle-{label.lower()}-{uuid4().hex}@example.test",
    )


def test_app_user_cannot_observe_or_mutate_another_organization_receipt(db) -> None:
    first_org = _organization(f"LC-{uuid4().hex[:8].upper()}")
    second_org = _organization(f"LC-{uuid4().hex[:8].upper()}")
    db.add_all([first_org, second_org])
    db.flush()
    first_org_id = UUID(str(first_org.organization_id))
    second_org_id = UUID(str(second_org.organization_id))
    first_person = _person(first_org_id, "First")
    second_person = _person(second_org_id, "Second")
    db.add_all([first_person, second_person])
    db.flush()

    owner = ManagedApplicationLifecycle(db)
    first = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key=f"first-{uuid4().hex}",
            target=_target(first_org_id, first_person.id),
        )
    )
    second = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key=f"second-{uuid4().hex}",
            target=_target(second_org_id, second_person.id),
        )
    )
    db.flush()

    db.execute(text("SET LOCAL ROLE app_user"))
    try:
        db.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(first_org_id)},
        )
        visible = (
            db.execute(
                text(
                    "SELECT operation_id FROM public.application_lifecycle_operations "
                    "ORDER BY operation_id"
                )
            )
            .scalars()
            .all()
        )
        hidden_update = db.execute(
            text(
                "UPDATE public.application_lifecycle_operations "
                "SET changed = true WHERE operation_id = :operation_id "
                "RETURNING operation_id"
            ),
            {"operation_id": second.operation_ref},
        ).scalar_one_or_none()
    finally:
        db.execute(text("RESET ROLE"))

    assert visible == [first.operation_ref]
    assert hidden_update is None
