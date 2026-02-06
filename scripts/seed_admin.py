"""
Seed Admin User Script

Creates an admin user with full system permissions in one step:
1. Creates Person record (if not exists)
2. Creates UserCredential for local auth (if not exists)
3. Seeds all permissions (idempotent)
4. Creates admin role with all permissions (idempotent)
5. Assigns admin role to the user

Usage:
    python scripts/seed_admin.py \
        --email "admin@example.com" \
        --first-name "Admin" \
        --last-name "User" \
        --username "admin" \
        --password "secure-password"
"""

import argparse

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.auth_flow import hash_password

# Import permissions from seed_rbac
from scripts.seed_rbac import DEFAULT_PERMISSIONS, DEFAULT_ROLES


def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed an admin user with full permissions."
    )
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--first-name", required=True, help="First name")
    parser.add_argument("--last-name", required=True, help="Last name")
    parser.add_argument("--username", required=True, help="Login username")
    parser.add_argument("--password", required=True, help="Login password")
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Require password change on first login",
    )
    parser.add_argument(
        "--skip-rbac",
        action="store_true",
        help="Skip RBAC setup (only create user)",
    )
    return parser.parse_args()


def ensure_permission(db, key: str, description: str) -> Permission:
    """Create or update a permission."""
    permission = db.query(Permission).filter(Permission.key == key).first()
    if not permission:
        permission = Permission(key=key, description=description, is_active=True)
        db.add(permission)
    else:
        if not permission.is_active:
            permission.is_active = True
    return permission


def ensure_role(db, name: str, description: str) -> Role:
    """Create or update a role."""
    role = db.query(Role).filter(Role.name == name).first()
    if not role:
        role = Role(name=name, description=description, is_active=True)
        db.add(role)
    else:
        if not role.is_active:
            role.is_active = True
    return role


def ensure_role_permission(db, role_id, permission_id):
    """Link a permission to a role."""
    link = (
        db.query(RolePermission)
        .filter(RolePermission.role_id == role_id)
        .filter(RolePermission.permission_id == permission_id)
        .first()
    )
    if not link:
        link = RolePermission(role_id=role_id, permission_id=permission_id)
        db.add(link)
    return link


def ensure_person_role(db, person_id, role_id):
    """Assign a role to a person."""
    link = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person_id)
        .filter(PersonRole.role_id == role_id)
        .first()
    )
    if not link:
        link = PersonRole(person_id=person_id, role_id=role_id)
        db.add(link)
    return link


def setup_rbac(db) -> Role:
    """
    Seed all permissions and roles, return the admin role.

    This is idempotent - safe to run multiple times.
    """
    print("Setting up RBAC...")

    # Create all permissions
    for key, description in DEFAULT_PERMISSIONS:
        ensure_permission(db, key, description)
    db.flush()
    print(f"  Permissions: {len(DEFAULT_PERMISSIONS)}")

    # Create all roles
    for name, description in DEFAULT_ROLES:
        ensure_role(db, name, description)
    db.flush()
    print(f"  Roles: {len(DEFAULT_ROLES)}")

    # Get admin role and all permissions
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    all_permissions = db.query(Permission).all()

    # Assign ALL permissions to admin role
    for perm in all_permissions:
        ensure_role_permission(db, admin_role.id, perm.id)
    db.flush()
    print(f"  Admin role: {len(all_permissions)} permissions")

    return admin_role


def main():
    load_dotenv()
    args = parse_args()
    db = SessionLocal()

    try:
        # Step 1: Create or get Person
        person = db.query(Person).filter(Person.email == args.email).first()
        if not person:
            person = Person(
                first_name=args.first_name,
                last_name=args.last_name,
                email=args.email,
            )
            db.add(person)
            db.flush()
            print(f"Created person: {person.email}")
        else:
            print(f"Person exists: {person.email}")

        # Step 2: Create UserCredential if not exists
        credential = (
            db.query(UserCredential)
            .filter(UserCredential.person_id == person.id)
            .filter(UserCredential.provider == AuthProvider.local)
            .first()
        )
        if not credential:
            credential = UserCredential(
                person_id=person.id,
                provider=AuthProvider.local,
                username=args.username,
                password_hash=hash_password(args.password),
                must_change_password=args.force_reset,
            )
            db.add(credential)
            db.flush()
            print(f"Created credential: {args.username}")
        else:
            print(f"Credential exists: {credential.username}")

        # Step 3: Setup RBAC and assign admin role
        if not args.skip_rbac:
            admin_role = setup_rbac(db)
            ensure_person_role(db, person.id, admin_role.id)
            print(f"Assigned admin role to: {person.email}")

        db.commit()
        print("\n✓ Admin user ready with full permissions")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
