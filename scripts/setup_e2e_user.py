#!/usr/bin/env python3
"""
Setup E2E Test User.

Creates a test user for running E2E tests. Run this before running e2e tests.

Usage:
    poetry run python scripts/setup_e2e_user.py

Environment variables (optional):
    E2E_TEST_USERNAME - Username for the test user (default: e2e_testuser)
    E2E_TEST_PASSWORD - Password for the test user (default: e2e_testpassword123)
"""

import os
import sys
import uuid

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.auth_flow import hash_password

# Default test credentials
DEFAULT_USERNAME = "e2e_testuser"
DEFAULT_PASSWORD = "e2e_testpassword123"
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def assign_admin_role(db, person_id):
    """Assign admin role to a person."""
    # Find or create admin role
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(
            id=uuid.uuid4(),
            name="admin",
            description="Administrator role with full access",
            is_active=True,
        )
        db.add(admin_role)
        db.flush()
        print(f"Created admin role: {admin_role.id}")

    # Check if person already has admin role
    existing_assignment = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person_id)
        .filter(PersonRole.role_id == admin_role.id)
        .first()
    )

    if existing_assignment:
        print("  Admin role already assigned")
        return

    # Assign admin role to person
    person_role = PersonRole(
        id=uuid.uuid4(),
        person_id=person_id,
        role_id=admin_role.id,
    )
    db.add(person_role)
    db.commit()
    print("  Assigned admin role to user")


def setup_e2e_user():
    """Create or update the E2E test user."""
    username = os.environ.get("E2E_TEST_USERNAME", DEFAULT_USERNAME)
    password = os.environ.get("E2E_TEST_PASSWORD", DEFAULT_PASSWORD)

    db = SessionLocal()
    try:
        # Check if user already exists
        existing_cred = (
            db.query(UserCredential).filter(UserCredential.username == username).first()
        )

        if existing_cred:
            # Update password if user exists
            existing_cred.password_hash = hash_password(password)
            existing_cred.is_active = True
            existing_cred.must_change_password = False
            db.commit()
            print(f"Updated existing E2E test user: {username}")
            # Assign admin role
            assign_admin_role(db, existing_cred.person_id)
            return existing_cred.person_id

        # Check if person with this email exists
        email = f"{username}@e2e-test.local"
        existing_person = db.query(Person).filter(Person.email == email).first()

        if existing_person:
            person = existing_person
            print(f"Found existing person with email: {email}")
        else:
            # Create new person
            person = Person(
                id=uuid.uuid4(),
                organization_id=DEFAULT_ORG_ID,
                first_name="E2E",
                last_name="Test User",
                email=email,
                email_verified=True,
                is_active=True,
                status="active",
            )
            db.add(person)
            db.flush()
            print(f"Created new person: {person.id}")

        # Create credential
        credential = UserCredential(
            id=uuid.uuid4(),
            person_id=person.id,
            username=username,
            password_hash=hash_password(password),
            is_active=True,
            must_change_password=False,
        )
        db.add(credential)
        db.commit()

        print("Created E2E test user successfully!")
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        print(f"  Person ID: {person.id}")
        print(f"  Organization ID: {DEFAULT_ORG_ID}")

        # Assign admin role
        assign_admin_role(db, person.id)

        return person.id

    except Exception as e:
        db.rollback()
        print(f"Error creating E2E test user: {e}")
        raise
    finally:
        db.close()


def check_organization_exists():
    """Ensure the default organization exists."""
    db = SessionLocal()
    try:
        # Check if we need to create the organization
        # This depends on your Organization model location
        from sqlalchemy import text

        result = db.execute(
            text("SELECT 1 FROM core_org.organization WHERE organization_id = :org_id"),
            {"org_id": str(DEFAULT_ORG_ID)},
        ).fetchone()

        if not result:
            print(f"Warning: Default organization {DEFAULT_ORG_ID} does not exist.")
            print(
                "Please run database migrations first: poetry run alembic upgrade head"
            )
            return False
        return True
    except Exception as e:
        print(f"Could not check organization: {e}")
        print("Make sure the database is running and migrations are applied.")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("Setting up E2E test user...")
    print()

    if not check_organization_exists():
        sys.exit(1)

    setup_e2e_user()
    print()
    print("E2E test user setup complete!")
    print()
    print("To run E2E tests:")
    print("  1. Start the server: poetry run uvicorn app.main:app --port 8000")
    print("  2. Run tests: poetry run pytest tests/e2e/ -v")
