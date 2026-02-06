"""Tests for person service.

NOTE: These tests are skipped because they require PostgreSQL for proper
UUID type handling. The Person model uses organization_id (required for
multi-tenancy) which doesn't work with SQLite test database.
"""

import uuid

import pytest

from app.schemas.person import PersonCreate, PersonUpdate
from app.services import person as person_service


# Test organization ID for multi-tenancy
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


# Skip all tests in this module - they require PostgreSQL for UUID handling
pytestmark = pytest.mark.skip(
    reason="Requires PostgreSQL for UUID/organization_id support"
)


def test_create_person(db_session):
    """Test creating a person."""
    email = _unique_email()
    person = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
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


def test_get_person_by_id(db_session):
    """Test getting a person by ID."""
    person = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Jane",
            last_name="Smith",
            email=_unique_email(),
        ),
    )
    fetched = person_service.people.get(db_session, str(person.id))
    assert fetched is not None
    assert fetched.id == person.id
    assert fetched.first_name == "Jane"


def test_list_people_filter_by_email(db_session):
    """Test listing people filtered by email."""
    email = _unique_email()
    person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Alice",
            last_name="Test",
            email=email,
        ),
    )
    person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Bob",
            last_name="Other",
            email=_unique_email(),
        ),
    )

    results = person_service.people.list(
        db_session,
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


def test_list_people_filter_by_status(db_session):
    """Test listing people filtered by status."""
    email1 = _unique_email()
    person1 = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Active",
            last_name="User",
            email=email1,
        ),
    )
    email2 = _unique_email()
    person2 = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Inactive",
            last_name="User",
            email=email2,
        ),
    )
    # Update second person to inactive
    person_service.people.update(
        db_session,
        str(person2.id),
        PersonUpdate(status="inactive"),
    )

    # Query for person1 specifically with active status filter
    active_results = person_service.people.list(
        db_session,
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
        db_session,
        email=email2,
        status="active",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    assert len(inactive_as_active) == 0


def test_list_people_active_only(db_session):
    """Test listing only active people."""
    person = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="ToDelete",
            last_name="User",
            email=_unique_email(),
        ),
    )
    person_service.people.delete(db_session, str(person.id))

    results = person_service.people.list(
        db_session,
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


def test_update_person(db_session):
    """Test updating a person."""
    person = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="Original",
            last_name="Name",
            email=_unique_email(),
        ),
    )
    updated = person_service.people.update(
        db_session,
        str(person.id),
        PersonUpdate(first_name="Updated", last_name="Person"),
    )
    assert updated.first_name == "Updated"
    assert updated.last_name == "Person"


def test_delete_person(db_session):
    """Test deleting a person."""
    person = person_service.people.create(
        db_session,
        PersonCreate(
            organization_id=TEST_ORG_ID,
            first_name="ToDelete",
            last_name="User",
            email=_unique_email(),
        ),
    )
    person_id = person.id
    person_service.people.delete(db_session, str(person_id))

    # Verify person is deleted
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        person_service.people.get(db_session, str(person_id))
    assert exc_info.value.status_code == 404


def test_list_people_pagination(db_session):
    """Test pagination of people list."""
    # Create multiple people
    for i in range(5):
        person_service.people.create(
            db_session,
            PersonCreate(
                organization_id=TEST_ORG_ID,
                first_name=f"Person{i}",
                last_name="Test",
                email=_unique_email(),
            ),
        )

    page1 = person_service.people.list(
        db_session,
        email=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=2,
        offset=0,
    )
    page2 = person_service.people.list(
        db_session,
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
