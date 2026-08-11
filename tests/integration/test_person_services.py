"""Integration tests for person service (PostgreSQL required)."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.finance.core_org.organization import Organization
from app.schemas.person import PersonCreate, PersonUpdate
from app.services import person as person_service

# Test organization ID for multi-tenancy
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


pytestmark = pytest.mark.integration


def test_create_person(db):
    """Test creating a person."""
    email = _unique_email()
    person = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="John",
            last_name="Doe",
            email=email,
        ),
    )
    assert person.first_name == "John"
    assert person.last_name == "Doe"
    assert person.email == email
    assert person.is_active is True
    assert person.organization_id == TEST_ORG_ID


def test_get_person_by_id(db):
    """Test getting a person by ID."""
    person = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Jane",
            last_name="Smith",
            email=_unique_email(),
        ),
    )
    fetched = person_service.people.get(db, TEST_ORG_ID, str(person.id))
    assert fetched is not None
    assert fetched.id == person.id
    assert fetched.first_name == "Jane"


def test_list_people_filter_by_email(db):
    """Test listing people filtered by email."""
    email = _unique_email()
    person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Alice",
            last_name="Test",
            email=email,
        ),
    )
    person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Bob",
            last_name="Other",
            email=_unique_email(),
        ),
    )

    results = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=email,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(results) == 1
    assert results[0].first_name == "Alice"


def test_list_people_filter_by_status(db):
    """Test listing people filtered by status."""
    email1 = _unique_email()
    person1 = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Active",
            last_name="User",
            email=email1,
        ),
    )
    email2 = _unique_email()
    person2 = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Inactive",
            last_name="User",
            email=email2,
        ),
    )
    # Update second person to inactive
    person_service.people.update(
        db,
        TEST_ORG_ID,
        str(person2.id),
        PersonUpdate(status="inactive"),
    )

    # Query for person1 specifically with active status filter
    active_results = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=email1,
        status="active",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    assert len(active_results) == 1
    assert active_results[0].id == person1.id

    # Verify person2 is not returned when filtering for active
    inactive_as_active = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=email2,
        status="active",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    assert len(inactive_as_active) == 0


def test_list_people_active_only(db):
    """Test listing only active people."""
    person = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="ToDelete",
            last_name="User",
            email=_unique_email(),
        ),
    )
    person_service.people.delete(db, TEST_ORG_ID, str(person.id))

    results = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    ids = {p.id for p in results}
    assert person.id not in ids


def test_update_person(db):
    """Test updating a person."""
    person = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="Original",
            last_name="Name",
            email=_unique_email(),
        ),
    )
    updated = person_service.people.update(
        db,
        TEST_ORG_ID,
        str(person.id),
        PersonUpdate(first_name="Updated", last_name="Person"),
    )
    assert updated.first_name == "Updated"
    assert updated.last_name == "Person"


def test_delete_person(db):
    """Test deleting a person."""
    person = person_service.people.create(
        db,
        TEST_ORG_ID,
        PersonCreate(
            first_name="ToDelete",
            last_name="User",
            email=_unique_email(),
        ),
    )
    person_id = person.id
    person_service.people.delete(db, TEST_ORG_ID, str(person_id))

    # Verify person is deleted
    with pytest.raises(HTTPException) as exc_info:
        person_service.people.get(db, TEST_ORG_ID, str(person_id))
    assert exc_info.value.status_code == 404


def test_list_people_pagination(db):
    """Test pagination of people list."""
    # Create multiple people
    for i in range(5):
        person_service.people.create(
            db,
            TEST_ORG_ID,
            PersonCreate(
                first_name=f"Person{i}",
                last_name="Test",
                email=_unique_email(),
            ),
        )

    page1 = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=2,
        offset=0,
    )
    page2 = person_service.people.list(
        db,
        TEST_ORG_ID,
        email=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=2,
        offset=2,
    )

    assert len(page1) == 2
    assert len(page2) == 2
    # Pages should have different people
    page1_ids = {p.id for p in page1}
    page2_ids = {p.id for p in page2}
    assert page1_ids.isdisjoint(page2_ids)


def test_person_crud_cannot_cross_organizations(db, organization):
    """Service predicates remain safe even when the session itself bypasses RLS."""
    organization_id = uuid.UUID(str(organization.organization_id))
    other_organization = Organization(
        organization_code=f"T2-{uuid.uuid4().hex[:8].upper()}",
        legal_name="Other Organization",
        functional_currency_code="USD",
        presentation_currency_code="USD",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )
    db.add(other_organization)
    db.flush()
    other_organization_id = uuid.UUID(str(other_organization.organization_id))

    visible = person_service.people.create(
        db,
        organization_id,
        PersonCreate(
            first_name="Visible",
            last_name="Person",
            email=_unique_email(),
        ),
    )
    hidden = person_service.people.create(
        db,
        other_organization_id,
        PersonCreate(
            first_name="Hidden",
            last_name="Person",
            email=_unique_email(),
        ),
    )

    listed_ids = {
        person.id
        for person in person_service.people.list(
            db,
            organization_id,
            email=None,
            status=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
    }
    assert visible.id in listed_ids
    assert hidden.id not in listed_ids

    with pytest.raises(HTTPException) as get_error:
        person_service.people.get(db, organization_id, str(hidden.id))
    assert get_error.value.status_code == 404

    with pytest.raises(HTTPException) as update_error:
        person_service.people.update(
            db,
            organization_id,
            str(hidden.id),
            PersonUpdate(first_name="Compromised"),
        )
    assert update_error.value.status_code == 404

    with pytest.raises(HTTPException) as delete_error:
        person_service.people.delete(db, organization_id, str(hidden.id))
    assert delete_error.value.status_code == 404

    db.refresh(hidden)
    assert hidden.first_name == "Hidden"
