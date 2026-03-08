from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.auth import AuthProvider, SessionStatus, UserCredential
from app.models.auth import Session as AuthSession
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_org.organization import Organization
from app.models.people.hr.employee import Employee
from app.models.person import Person, PersonStatus
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.auth_flow import hash_password
from app.services.common import coerce_uuid
from app.services.formatters import format_datetime as _format_datetime

from .common import (
    DEFAULT_NEW_LOCAL_PASSWORD,
    DEFAULT_PAGE_SIZE,
    _build_pagination,
    _clean_name,
    _derive_display_name,
    _format_relative_time,
    _parse_flag,
    _parse_person_status,
    _parse_status_filter,
)


def _admin_web_facade():
    return import_module("app.services.admin.web")


class AdminIdentityMixin:
    @staticmethod
    def users_context(
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit
        conditions = []
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    Person.first_name.ilike(search_pattern),
                    Person.last_name.ilike(search_pattern),
                    Person.email.ilike(search_pattern),
                    Person.phone.ilike(search_pattern),
                )
            )

        status_enum = _parse_person_status(status)
        if status_enum:
            conditions.append(Person.status == status_enum)

        total = db.scalar(select(func.count(Person.id)).where(*conditions)) or 0
        total_pages = max(1, (total + limit - 1) // limit)
        persons = list(
            db.scalars(
                select(Person)
                .where(*conditions)
                .order_by(Person.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

        person_ids = [p.id for p in persons]
        person_roles_map: dict[UUID, list[str]] = {}
        last_active_map: dict[UUID, str] = {}
        if person_ids:
            person_roles = db.execute(
                select(PersonRole.person_id, Role.name)
                .join(Role, PersonRole.role_id == Role.id)
                .where(PersonRole.person_id.in_(person_ids), Role.is_active.is_(True))
            ).all()
            for person_id, role_name in person_roles:
                person_roles_map.setdefault(person_id, []).append(role_name)

            last_sessions = db.execute(
                select(AuthSession.person_id, func.max(AuthSession.last_seen_at))
                .where(AuthSession.person_id.in_(person_ids))
                .group_by(AuthSession.person_id)
            ).all()
            for person_id, last_seen in last_sessions:
                if last_seen:
                    last_active_map[person_id] = _format_relative_time(last_seen)

        users = []
        for person in persons:
            name = person.name or person.email or "Unknown"
            initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
            users.append(
                {
                    "id": str(person.id),
                    "name": name,
                    "email": person.email,
                    "phone": person.phone,
                    "initials": initials,
                    "email_verified": person.email_verified,
                    "status": person.status.value if person.status else "active",
                    "roles": person_roles_map.get(person.id, []),
                    "last_active": last_active_map.get(person.id),
                    "created_at": person.created_at.strftime("%b %d, %Y") if person.created_at else "",
                }
            )

        return {
            "users": users,
            "pagination": _build_pagination(page, total_pages, total, limit),
            "search": search_value,
            "status_filter": status or "",
        }

    @staticmethod
    def user_form_context(db: Session, user_id: str | None = None) -> dict:
        organizations = list(
            db.scalars(select(Organization).where(Organization.is_active.is_(True))).all()
        )
        org_list = [
            {"id": str(org.organization_id), "name": org.legal_name or org.trading_name or org.organization_code}
            for org in organizations
        ]
        roles = list(db.scalars(select(Role).where(Role.is_active.is_(True)).order_by(Role.name)).all())
        role_list = [{"id": str(role.id), "name": role.name, "description": role.description} for role in roles]

        user_data = None
        if user_id:
            person = db.get(Person, coerce_uuid(user_id))
            if not person:
                raise HTTPException(status_code=404, detail="User not found")
            credential = db.scalar(
                select(UserCredential).where(
                    UserCredential.person_id == person.id,
                    UserCredential.provider == AuthProvider.local,
                )
            )
            user_roles = list(
                db.scalars(
                    select(Role)
                    .join(PersonRole, PersonRole.role_id == Role.id)
                    .where(PersonRole.person_id == person.id)
                ).all()
            )
            user_data = {
                "id": str(person.id),
                "first_name": person.first_name,
                "last_name": person.last_name,
                "display_name": _clean_name(person.display_name),
                "email": person.email,
                "phone": person.phone,
                "email_verified": person.email_verified,
                "status": person.status.value if person.status else "active",
                "organization_id": str(person.organization_id) if person.organization_id else None,
                "username": credential.username if credential else None,
                "must_change_password": credential.must_change_password if credential else False,
                "role_ids": [str(role.id) for role in user_roles],
            }

        return {"user_data": user_data, "organizations": org_list, "roles": role_list}

    @staticmethod
    def user_data_from_payload(payload: dict, user_id: str | None = None) -> dict:
        role_ids = payload.get("role_ids") or payload.get("roles") or []
        if isinstance(role_ids, str):
            role_ids = [role_ids]
        return {
            "id": user_id,
            "first_name": payload.get("first_name", ""),
            "last_name": payload.get("last_name", ""),
            "display_name": _clean_name(payload.get("display_name")),
            "email": payload.get("email", ""),
            "phone": payload.get("phone", ""),
            "email_verified": _parse_flag(payload.get("email_verified")),
            "status": payload.get("status") or "active",
            "organization_id": payload.get("organization_id"),
            "username": payload.get("username"),
            "must_change_password": _parse_flag(payload.get("must_change_password")),
            "role_ids": role_ids,
        }

    @staticmethod
    def create_user(
        db: Session,
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        organization_id: str,
        password: str,
        password_confirm: str,
        display_name: str = "",
        phone: str = "",
        status: str = "active",
        must_change_password: bool | str = False,
        role_ids: list[str] | None = None,
    ) -> tuple[Person | None, str | None]:
        role_ids = role_ids or []
        if isinstance(role_ids, str):
            role_ids = [role_ids]
        email = (email or "").strip()
        username = ((username or "").strip() or email.lower()).strip()
        password = password or ""
        password_confirm = password_confirm or ""
        if not password and not password_confirm:
            password = DEFAULT_NEW_LOCAL_PASSWORD
            password_confirm = DEFAULT_NEW_LOCAL_PASSWORD
        elif bool(password) != bool(password_confirm):
            return None, "Both password fields are required when setting a custom password"

        must_change_password = True
        if password != password_confirm:
            return None, "Passwords do not match"
        if len(password) < 8:
            return None, "Password must be at least 8 characters"
        if db.scalar(select(Person).where(Person.email == email)):
            return None, "A user with this email already exists"

        existing_username = db.scalar(
            select(UserCredential).where(
                UserCredential.username == username,
                UserCredential.provider == AuthProvider.local,
            )
        )
        if existing_username:
            return None, "A user with this username already exists"

        try:
            person_status = PersonStatus(status) if status else PersonStatus.active
            derived_display_name = _derive_display_name(first_name, last_name, display_name)
            person = Person(
                first_name=first_name,
                last_name=last_name,
                display_name=derived_display_name,
                email=email,
                phone=phone if phone else None,
                organization_id=coerce_uuid(organization_id),
                status=person_status,
                is_active=person_status == PersonStatus.active,
            )
            db.add(person)
            db.flush()

            db.add(
                UserCredential(
                    person_id=person.id,
                    provider=AuthProvider.local,
                    username=username,
                    password_hash=hash_password(password),
                    must_change_password=must_change_password,
                    is_active=True,
                )
            )
            for role_id in role_ids:
                if role_id:
                    db.add(PersonRole(person_id=person.id, role_id=coerce_uuid(role_id)))

            db.commit()
            _admin_web_facade().fire_audit_event(
                db=db,
                organization_id=person.organization_id,
                table_schema="auth",
                table_name="user",
                record_id=str(person.id),
                action=AuditAction.INSERT,
                new_values={"username": username, "email": email, "roles": role_ids},
            )
            return person, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create user: {str(exc)}"

    @staticmethod
    def update_user(
        db: Session,
        user_id: str,
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        organization_id: str,
        password: str | None = None,
        password_confirm: str | None = None,
        display_name: str = "",
        phone: str = "",
        status: str = "active",
        must_change_password: bool | str = False,
        email_verified: bool | str = False,
        role_ids: list[str] | None = None,
    ) -> tuple[Person | None, str | None]:
        role_ids = role_ids or []
        if isinstance(role_ids, str):
            role_ids = [role_ids]
        normalized_role_ids = {str(coerce_uuid(role_id)) for role_id in role_ids if role_id}
        must_change_password = _parse_flag(must_change_password)
        email_verified = _parse_flag(email_verified)

        person = db.get(Person, coerce_uuid(user_id))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        if password:
            if password != password_confirm:
                return None, "Passwords do not match"
            if len(password) < 8:
                return None, "Password must be at least 8 characters"

        existing_email = db.scalar(select(Person).where(Person.email == email, Person.id != person.id))
        if existing_email:
            return None, "A user with this email already exists"

        existing_username = db.scalar(
            select(UserCredential).where(
                UserCredential.username == username,
                UserCredential.provider == AuthProvider.local,
                UserCredential.person_id != person.id,
            )
        )
        if existing_username:
            return None, "A user with this username already exists"

        try:
            person_status = PersonStatus(status) if status else PersonStatus.active
            person.first_name = first_name
            person.last_name = last_name
            person.display_name = _derive_display_name(first_name, last_name, display_name)
            person.email = email
            person.phone = phone if phone else None
            person.organization_id = coerce_uuid(organization_id)
            person.status = person_status
            person.is_active = person_status == PersonStatus.active
            person.email_verified = email_verified

            credential = db.scalar(
                select(UserCredential).where(
                    UserCredential.person_id == person.id,
                    UserCredential.provider == AuthProvider.local,
                )
            )
            if credential:
                credential.username = username
                credential.must_change_password = must_change_password
                if password:
                    credential.password_hash = hash_password(password)
                    credential.password_updated_at = datetime.now(UTC)
            elif password:
                db.add(
                    UserCredential(
                        person_id=person.id,
                        provider=AuthProvider.local,
                        username=username,
                        password_hash=hash_password(password),
                        must_change_password=must_change_password,
                        is_active=True,
                    )
                )

            current_role_ids = {
                str(role_id)
                for (role_id,) in db.execute(select(PersonRole.role_id).where(PersonRole.person_id == person.id)).all()
            }
            db.execute(delete(PersonRole).where(PersonRole.person_id == person.id))
            for role_id in normalized_role_ids:
                db.add(PersonRole(person_id=person.id, role_id=coerce_uuid(role_id)))

            roles_changed = current_role_ids != normalized_role_ids
            session_ids_to_invalidate: list[UUID] = []
            if roles_changed:
                active_sessions = list(
                    db.scalars(
                        select(AuthSession).where(
                            AuthSession.person_id == person.id,
                            AuthSession.status == SessionStatus.active,
                            AuthSession.revoked_at.is_(None),
                        )
                    ).all()
                )
                session_ids_to_invalidate = [session.id for session in active_sessions]
                for session in active_sessions:
                    session.status = SessionStatus.revoked
                    session.revoked_at = datetime.now(UTC)

            db.commit()
            if roles_changed and session_ids_to_invalidate:
                from app.services.auth_dependencies import invalidate_session_cache

                for session_id in session_ids_to_invalidate:
                    invalidate_session_cache(session_id)

            _admin_web_facade().fire_audit_event(
                db=db,
                organization_id=person.organization_id,
                table_schema="auth",
                table_name="user",
                record_id=str(person.id),
                action=AuditAction.UPDATE,
                new_values={"email": email, "username": username, "status": status},
            )
            if roles_changed:
                _admin_web_facade().fire_audit_event(
                    db=db,
                    organization_id=person.organization_id,
                    table_schema="rbac",
                    table_name="role_assignment",
                    record_id=str(person.id),
                    action=AuditAction.UPDATE,
                    old_values={"role_ids": sorted(current_role_ids)},
                    new_values={"role_ids": sorted(normalized_role_ids)},
                )
            return person, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update user: {str(exc)}"

    @staticmethod
    def delete_user(db: Session, user_id: str) -> str | None:
        person = db.get(Person, coerce_uuid(user_id))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            employee = db.scalar(
                select(Employee).where(Employee.person_id == person.id, Employee.is_deleted.is_(False))
            )
            if employee:
                return "Cannot delete user linked to an employee. Delete the employee record first."
            db.execute(delete(PersonRole).where(PersonRole.person_id == person.id))
            db.execute(delete(UserCredential).where(UserCredential.person_id == person.id))
            db.execute(delete(AuthSession).where(AuthSession.person_id == person.id))
            db.delete(person)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete user: {str(exc)}"

    @staticmethod
    def roles_context(db: Session, search: str | None, status: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(or_(Role.name.ilike(search_pattern), Role.description.ilike(search_pattern)))
        active_count = db.scalar(select(func.count(Role.id)).where(*conditions, Role.is_active.is_(True))) or 0
        inactive_count = db.scalar(select(func.count(Role.id)).where(*conditions, Role.is_active.is_(False))) or 0
        role_conditions = list(conditions)
        status_flag = _parse_status_filter(status)
        if status_flag is not None:
            role_conditions.append(Role.is_active == status_flag)
        total_count = db.scalar(select(func.count(Role.id)).where(*role_conditions)) or 0
        roles = list(
            db.scalars(select(Role).where(*role_conditions).order_by(Role.name).limit(limit).offset(offset)).all()
        )
        role_ids = [role.id for role in roles]
        permission_counts: dict[UUID, int] = {}
        member_counts: dict[UUID, int] = {}
        if role_ids:
            permission_counts = {
                role_id: count
                for role_id, count in db.execute(
                    select(RolePermission.role_id, func.count(RolePermission.id))
                    .where(RolePermission.role_id.in_(role_ids))
                    .group_by(RolePermission.role_id)
                ).all()
            }
            member_counts = {
                role_id: count
                for role_id, count in db.execute(
                    select(PersonRole.role_id, func.count(PersonRole.id))
                    .where(PersonRole.role_id.in_(role_ids))
                    .group_by(PersonRole.role_id)
                ).all()
            }
        return {
            "roles": [
                {
                    "role_id": role.id,
                    "name": role.name,
                    "description": role.description,
                    "is_active": role.is_active,
                    "permission_count": permission_counts.get(role.id, 0),
                    "member_count": member_counts.get(role.id, 0),
                    "created_at": _format_datetime(role.created_at),
                }
                for role in roles
            ],
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
            "stats": {"active": active_count, "inactive": inactive_count, "total": active_count + inactive_count},
        }

    @staticmethod
    def role_form_context(db: Session, role_id: str | None = None) -> dict:
        role_data = None
        if role_id:
            role = db.get(Role, coerce_uuid(role_id))
            if role:
                role_permission_ids = [
                    rp.permission_id for rp in db.scalars(select(RolePermission).where(RolePermission.role_id == role.id)).all()
                ]
                members_query = list(
                    db.scalars(
                        select(Person).join(PersonRole, PersonRole.person_id == Person.id).where(PersonRole.role_id == role.id).limit(20)
                    ).all()
                )
                members = []
                for person in members_query:
                    name = person.name or person.email or "Unknown"
                    initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
                    members.append({"name": name, "initials": initials})
                role_data = {
                    "id": str(role.id),
                    "name": role.name,
                    "description": role.description or "",
                    "is_active": role.is_active,
                    "permission_ids": role_permission_ids,
                    "members": members,
                }

        permissions = list(db.scalars(select(Permission).where(Permission.is_active.is_(True)).order_by(Permission.key)).all())
        permissions_by_category: dict[str, list[dict]] = {}
        for perm in permissions:
            category = perm.key.replace("_", ":").split(":")[0] if perm.key else "general"
            permissions_by_category.setdefault(category, []).append(
                {"id": perm.id, "key": perm.key, "description": perm.description}
            )
        return {"role_data": role_data, "permissions_by_category": permissions_by_category}

    @staticmethod
    def role_profile_context(db: Session, role_id: str) -> dict:
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return {"role": None}
        role_permissions = list(
            db.scalars(
                select(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id, Permission.is_active.is_(True))
                .order_by(Permission.key)
            ).all()
        )
        permissions_by_module: dict[str, list[dict]] = {}
        for perm in role_permissions:
            key_parts = perm.key.split(":")
            module = key_parts[0] if key_parts else "general"
            permissions_by_module.setdefault(module, []).append(
                {"key": perm.key, "description": perm.description, "action": key_parts[-1] if len(key_parts) > 1 else "access"}
            )
        member_count = db.scalar(select(func.count(PersonRole.id)).where(PersonRole.role_id == role.id)) or 0
        members_query = list(
            db.scalars(
                select(Person)
                .join(PersonRole, PersonRole.person_id == Person.id)
                .where(PersonRole.role_id == role.id)
                .order_by(Person.display_name, Person.first_name, Person.email)
                .limit(50)
            ).all()
        )
        members = []
        for person in members_query:
            name = person.name or person.email or "Unknown"
            initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
            members.append({"id": str(person.id), "name": name, "email": person.email, "initials": initials})
        module_names = {
            "audit": "Audit & Compliance", "auth": "Authentication", "rbac": "Roles & Permissions", "scheduler": "Job Scheduler",
            "settings": "System Settings", "integrations": "Integrations", "finance": "Finance Module", "gl": "General Ledger",
            "ar": "Accounts Receivable", "ap": "Accounts Payable", "fa": "Fixed Assets", "banking": "Banking",
            "inv": "Inventory", "inventory": "Inventory", "tax": "Tax Management", "lease": "Lease Accounting",
            "cons": "Consolidation", "fx": "Foreign Exchange", "reports": "Financial Reports", "rpt": "Reporting API",
            "payments": "Payment Gateway", "automation": "Automation", "org": "Organization Setup", "import": "Data Import",
            "hr": "Human Resources", "payroll": "Payroll", "leave": "Leave Management", "attendance": "Attendance",
            "perf": "Performance Management", "recruit": "Recruitment", "training": "Training & Development",
            "selfservice": "Self-Service Portal", "expense": "Expense Management", "fleet": "Fleet Management",
            "procurement": "Procurement", "projects": "Projects", "support": "Support & Ticketing", "tasks": "Task Management",
        }
        return {
            "role": {
                "id": str(role.id), "name": role.name, "description": role.description or "", "is_active": role.is_active,
                "created_at": _format_datetime(role.created_at), "updated_at": _format_datetime(role.updated_at),
            },
            "permissions_by_module": dict(sorted(permissions_by_module.items())),
            "permission_count": len(role_permissions),
            "member_count": member_count,
            "members": members,
            "module_names": module_names,
        }

    @staticmethod
    def create_role(db: Session, name: str, description: str, is_active: bool, permission_ids: list[str]) -> tuple[Role | None, str | None]:
        existing = db.scalar(select(Role).where(Role.name == name))
        if existing:
            return None, "A role with this name already exists"
        try:
            role = Role(name=name, description=description if description else None, is_active=is_active)
            db.add(role)
            db.flush()
            for perm_id in permission_ids:
                if perm_id:
                    db.add(RolePermission(role_id=role.id, permission_id=coerce_uuid(perm_id)))
            db.commit()
            return role, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create role: {str(exc)}"

    @staticmethod
    def update_role(db: Session, role_id: str, name: str, description: str, is_active: bool, permission_ids: list[str]) -> tuple[Role | None, str | None]:
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return None, "Role not found"
        existing = db.scalar(select(Role).where(Role.name == name, Role.id != role.id))
        if existing:
            return None, "A role with this name already exists"
        try:
            role.name = name
            role.description = description if description else None
            role.is_active = is_active
            db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            for perm_id in permission_ids:
                if perm_id:
                    db.add(RolePermission(role_id=role.id, permission_id=coerce_uuid(perm_id)))
            db.commit()
            return role, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update role: {str(exc)}"

    @staticmethod
    def delete_role(db: Session, role_id: str) -> str | None:
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return "Role not found"
        try:
            db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            db.execute(delete(PersonRole).where(PersonRole.role_id == role.id))
            db.delete(role)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete role: {str(exc)}"

    @staticmethod
    def permissions_context(db: Session, search: str | None, status: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(or_(Permission.key.ilike(search_pattern), Permission.description.ilike(search_pattern)))
        active_count = db.scalar(select(func.count(Permission.id)).where(*conditions, Permission.is_active.is_(True))) or 0
        inactive_count = db.scalar(select(func.count(Permission.id)).where(*conditions, Permission.is_active.is_(False))) or 0
        permission_conditions = list(conditions)
        status_flag = _parse_status_filter(status)
        if status_flag is not None:
            permission_conditions.append(Permission.is_active == status_flag)
        total_count = db.scalar(select(func.count(Permission.id)).where(*permission_conditions)) or 0
        permissions = list(
            db.scalars(select(Permission).where(*permission_conditions).order_by(Permission.key).limit(limit).offset(offset)).all()
        )
        perm_ids = [p.id for p in permissions]
        role_counts: dict[UUID, int] = {}
        if perm_ids:
            role_counts = {
                perm_id: count
                for perm_id, count in db.execute(
                    select(RolePermission.permission_id, func.count(RolePermission.id))
                    .where(RolePermission.permission_id.in_(perm_ids))
                    .group_by(RolePermission.permission_id)
                ).all()
            }
        return {
            "permissions": [
                {
                    "permission_id": perm.id, "key": perm.key, "description": perm.description, "is_active": perm.is_active,
                    "role_count": role_counts.get(perm.id, 0), "created_at": _format_datetime(perm.created_at),
                }
                for perm in permissions
            ],
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
            "stats": {"active": active_count, "inactive": inactive_count, "total": active_count + inactive_count},
        }

    @staticmethod
    def permission_form_context(db: Session, permission_id: str | None = None) -> dict:
        permission_data = None
        if permission_id:
            perm = db.get(Permission, coerce_uuid(permission_id))
            if perm:
                roles_with_permission = db.execute(
                    select(Role.name).join(RolePermission, RolePermission.role_id == Role.id).where(RolePermission.permission_id == perm.id)
                ).all()
                permission_data = {
                    "id": str(perm.id),
                    "key": perm.key,
                    "description": perm.description or "",
                    "is_active": perm.is_active,
                    "roles": [r[0] for r in roles_with_permission],
                }
        return {"permission_data": permission_data}

    @staticmethod
    def create_permission(db: Session, key: str, description: str, is_active: bool) -> tuple[Permission | None, str | None]:
        existing = db.scalar(select(Permission).where(Permission.key == key))
        if existing:
            return None, "A permission with this key already exists"
        try:
            permission = Permission(key=key, description=description if description else None, is_active=is_active)
            db.add(permission)
            db.commit()
            return permission, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create permission: {str(exc)}"

    @staticmethod
    def update_permission(db: Session, permission_id: str, key: str, description: str, is_active: bool) -> tuple[Permission | None, str | None]:
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            return None, "Permission not found"
        existing = db.scalar(select(Permission).where(Permission.key == key, Permission.id != permission.id))
        if existing:
            return None, "A permission with this key already exists"
        try:
            permission.key = key
            permission.description = description if description else None
            permission.is_active = is_active
            db.commit()
            return permission, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update permission: {str(exc)}"

    @staticmethod
    def delete_permission(db: Session, permission_id: str) -> str | None:
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            return "Permission not found"
        try:
            db.execute(delete(RolePermission).where(RolePermission.permission_id == permission.id))
            db.delete(permission)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete permission: {str(exc)}"
