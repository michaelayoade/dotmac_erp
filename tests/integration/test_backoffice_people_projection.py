"""PostgreSQL proof for ERP's tenant-bound People replacement projection."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.services.people.hr.replacement_projection import (
    BackofficePeopleProjectionService,
    PeopleProjectionEntity,
)


def _organization(db: Session, suffix: str) -> Organization:
    organization = Organization(
        organization_code=f"PEOPLE-{suffix}-{uuid4().hex[:6].upper()}",
        legal_name=f"People projection {suffix}",
        functional_currency_code="NGN",
        presentation_currency_code="NGN",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )
    db.add(organization)
    db.flush()
    return organization


def test_postgres_projection_never_returns_another_organization(db: Session) -> None:
    first_org = _organization(db, "A")
    second_org = _organization(db, "B")
    first_id = UUID("80000000-0000-0000-0000-000000000008")
    second_id = UUID("90000000-0000-0000-0000-000000000009")
    db.add_all(
        [
            Person(
                id=first_id,
                organization_id=first_org.organization_id,
                first_name="First",
                last_name="Tenant",
                email=f"first-{uuid4().hex}@example.com",
            ),
            Person(
                id=second_id,
                organization_id=second_org.organization_id,
                first_name="Second",
                last_name="Tenant",
                email=f"second-{uuid4().hex}@example.com",
            ),
        ]
    )
    db.flush()

    page = BackofficePeopleProjectionService(db).page(
        organization_id=first_org.organization_id,
        entity=PeopleProjectionEntity.PARTY_PERSON,
        after=None,
        limit=500,
    )

    assert [item.source_id for item in page.items] == [first_id]
    assert second_id not in {item.source_id for item in page.items}
