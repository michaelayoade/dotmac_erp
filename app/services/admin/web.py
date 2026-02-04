"""
Admin web view service.

Provides view-focused data for admin web routes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.auth import AuthProvider, Session as AuthSession, SessionStatus, UserCredential
from app.models.audit import AuditActorType, AuditEvent
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.config import settings
from app.models.finance.core_org.organization import Organization
from app.models.person import Person, PersonStatus
from app.models.people.hr.employee import Employee
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.models.scheduler import ScheduleType, ScheduledTask
from app.services.auth_flow import hash_password
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import brand_context, WebAuthContext


DEFAULT_PAGE_SIZE = 20


class Pagination(TypedDict):
    page: int
    total_pages: int
    total: int
    per_page: int
    start: int
    end: int
    has_prev: bool
    has_next: bool
    pages: list[int | str]


def _format_datetime(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _truncate(value: str, max_length: int = 120) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _safe_json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _build_pagination(page: int, total_pages: int, total: int, per_page: int) -> Pagination:
    start = (page - 1) * per_page + 1 if total > 0 else 0
    end = min(page * per_page, total)

    pages: list[int | str] = []
    if total_pages <= 7:
        pages = list(range(1, total_pages + 1))
    else:
        if page <= 3:
            pages = [
                1,
                2,
                3,
                4,
                "...",
                total_pages,
            ]
        elif page >= total_pages - 2:
            pages = [
                1,
                "...",
                total_pages - 3,
                total_pages - 2,
                total_pages - 1,
                total_pages,
            ]
        else:
            pages = [
                1,
                "...",
                page - 1,
                page,
                page + 1,
                "...",
                total_pages,
            ]

    return {
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "per_page": per_page,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "pages": pages,
    }


def _parse_status_filter(value: Optional[str]) -> Optional[bool]:
    if value == "active":
        return True
    if value == "inactive":
        return False
    return None


def _parse_domain(value: Optional[str]) -> Optional[SettingDomain]:
    if not value:
        return None
    try:
        return SettingDomain(value)
    except ValueError:
        return None


def _parse_actor_type(value: Optional[str]) -> Optional[AuditActorType]:
    if not value:
        return None
    try:
        return AuditActorType(value)
    except ValueError:
        return None


def _parse_success_filter(value: Optional[str]) -> Optional[bool]:
    if value == "success":
        return True
    if value == "failed":
        return False
    return None


def _setting_value_display(setting: DomainSetting) -> str:
    if setting.is_secret:
        return "Hidden"
    if setting.value_type == SettingValueType.json:
        if setting.value_json is None:
            return ""
        return _truncate(_safe_json_dump(setting.value_json))
    if setting.value_text is None:
        return ""
    return _truncate(str(setting.value_text))


def _format_interval(seconds: Optional[int]) -> str:
    if not seconds:
        return "-"
    units = [
        ("day", 86400),
        ("hour", 3600),
        ("min", 60),
        ("sec", 1),
    ]
    for label, size in units:
        if seconds >= size and seconds % size == 0:
            value = seconds // size
            suffix = "s" if value != 1 else ""
            return f"Every {value} {label}{suffix}"
    return f"Every {seconds} sec"


def _format_relative_time(dt: datetime) -> str:
    """Format datetime as relative time string."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "Just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days}d ago"
    else:
        return dt.strftime("%b %d, %Y")


def _parse_flag(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if not value:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_name(value: str | None) -> str:
    cleaned = (value or "").strip()
    return "" if cleaned.lower() in {"none", "null"} else cleaned


def _derive_display_name(first_name: str | None, last_name: str | None, display_name: str | None) -> str | None:
    display = _clean_name(display_name)
    if display:
        return display
    base_name = f"{_clean_name(first_name)} {_clean_name(last_name)}".strip()
    return base_name or None


def _parse_person_status(value: Optional[str]) -> Optional[PersonStatus]:
    if not value:
        return None
    try:
        return PersonStatus(value)
    except ValueError:
        return None


class AdminWebService:
    """View service for admin web routes."""

    @staticmethod
    def dashboard_context(db: Session) -> dict:
        """Get context for admin dashboard."""
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)

        # Total users
        total_users = db.query(func.count(Person.id)).scalar() or 0
        new_users_week = (
            db.query(func.count(Person.id))
            .filter(Person.created_at >= week_start)
            .scalar()
            or 0
        )

        # Active sessions
        active_sessions = (
            db.query(func.count(AuthSession.id))
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .scalar() or 0
        )
        unique_users_today = (
            db.query(func.count(func.distinct(AuthSession.person_id)))
            .filter(AuthSession.last_seen_at.isnot(None))
            .filter(AuthSession.last_seen_at >= start_of_day)
            .scalar()
            or 0
        )

        total_organizations = (
            db.query(func.count(Organization.organization_id)).scalar() or 0
        )
        active_organizations = (
            db.query(func.count(Organization.organization_id))
            .filter(Organization.is_active.is_(True))
            .scalar()
            or 0
        )

        # Recent users (last 5)
        recent_users_query = (
            db.query(Person)
            .order_by(Person.created_at.desc())
            .limit(5)
            .all()
        )

        recent_users = []
        for person in recent_users_query:
            name = person.name or person.email or "Unknown"
            initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
            recent_users.append({
                "id": str(person.id),
                "name": name,
                "email": person.email,
                "initials": initials,
                "status": "active" if person.is_active else "inactive",
            })

        stats = {
            "total_users": total_users,
            "new_users_week": new_users_week,
            "active_sessions": active_sessions,
            "unique_users_today": unique_users_today,
            "total_organizations": total_organizations,
            "active_organizations": active_organizations,
            "system_health": "Good",
        }

        return {
            "stats": stats,
            "recent_users": recent_users,
            "recent_activity": [],
        }

    @staticmethod
    def users_context(
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        """Get context for users list page."""
        offset = (page - 1) * limit

        query = db.query(Person)

        # Apply search filter
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            query = query.filter(
                or_(
                    Person.first_name.ilike(search_pattern),
                    Person.last_name.ilike(search_pattern),
                    Person.email.ilike(search_pattern),
                    Person.phone.ilike(search_pattern),
                )
            )

        status_enum = _parse_person_status(status)
        if status_enum:
            query = query.filter(Person.status == status_enum)

        # Get total count
        total = query.with_entities(func.count(Person.id)).scalar() or 0
        total_pages = max(1, (total + limit - 1) // limit)

        # Get paginated results
        persons = (
            query.order_by(Person.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Get roles and last active for each user
        person_ids = [p.id for p in persons]
        person_roles_map: dict = {}
        last_active_map: dict = {}

        if person_ids:
            # Get roles
            person_roles = (
                db.query(PersonRole.person_id, Role.name)
                .join(Role, PersonRole.role_id == Role.id)
                .filter(PersonRole.person_id.in_(person_ids))
                .filter(Role.is_active.is_(True))
                .all()
            )
            for person_id, role_name in person_roles:
                if person_id not in person_roles_map:
                    person_roles_map[person_id] = []
                person_roles_map[person_id].append(role_name)

            # Get last active sessions
            last_sessions = (
                db.query(AuthSession.person_id, func.max(AuthSession.last_seen_at))
                .filter(AuthSession.person_id.in_(person_ids))
                .group_by(AuthSession.person_id)
                .all()
            )
            for person_id, last_seen in last_sessions:
                if last_seen:
                    last_active_map[person_id] = _format_relative_time(last_seen)

        # Format users
        users = []
        for person in persons:
            name = person.name or person.email or "Unknown"
            initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
            users.append({
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
            })

        pagination = _build_pagination(page, total_pages, total, limit)

        return {
            "users": users,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
        }

    @staticmethod
    def user_form_context(db: Session, user_id: Optional[str] = None) -> dict:
        """Get context for user create/edit form."""
        # Get organizations
        organizations = db.query(Organization).filter(Organization.is_active.is_(True)).all()
        org_list = [
            {"id": str(org.organization_id), "name": org.legal_name or org.trading_name or org.organization_code}
            for org in organizations
        ]

        # Get roles
        roles = db.query(Role).filter(Role.is_active.is_(True)).order_by(Role.name).all()
        role_list = [
            {"id": str(role.id), "name": role.name, "description": role.description}
            for role in roles
        ]

        user_data = None
        if user_id:
            person = db.get(Person, coerce_uuid(user_id))
            if not person:
                raise HTTPException(status_code=404, detail="User not found")

            # Get user credential
            credential = (
                db.query(UserCredential)
                .filter(UserCredential.person_id == person.id)
                .filter(UserCredential.provider == AuthProvider.local)
                .first()
            )

            # Get user roles
            user_roles = (
                db.query(Role)
                .join(PersonRole, PersonRole.role_id == Role.id)
                .filter(PersonRole.person_id == person.id)
                .all()
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

        return {
            "user_data": user_data,
            "organizations": org_list,
            "roles": role_list,
        }

    @staticmethod
    def user_data_from_payload(payload: dict, user_id: Optional[str] = None) -> dict:
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
        role_ids: list[str] = None,
    ) -> tuple[Optional[Person], Optional[str]]:
        """Create a new user. Returns (person, error)."""
        role_ids = role_ids or []
        if isinstance(role_ids, str):
            role_ids = [role_ids]
        must_change_password = _parse_flag(must_change_password)

        # Validate passwords
        if password != password_confirm:
            return None, "Passwords do not match"
        if len(password) < 8:
            return None, "Password must be at least 8 characters"

        # Check if email exists
        if db.query(Person).filter(Person.email == email).first():
            return None, "A user with this email already exists"

        # Check if username exists
        existing_username = (
            db.query(UserCredential)
            .filter(UserCredential.username == username)
            .filter(UserCredential.provider == AuthProvider.local)
            .first()
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

            # Create credential
            credential = UserCredential(
                person_id=person.id,
                provider=AuthProvider.local,
                username=username,
                password_hash=hash_password(password),
                must_change_password=must_change_password,
                is_active=True,
            )
            db.add(credential)

            # Assign roles
            for role_id in role_ids:
                if role_id:
                    db.add(PersonRole(person_id=person.id, role_id=coerce_uuid(role_id)))

            db.commit()
            return person, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create user: {str(e)}"

    @staticmethod
    def update_user(
        db: Session,
        user_id: str,
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        organization_id: str,
        password: str = "",
        password_confirm: str = "",
        display_name: str = "",
        phone: str = "",
        status: str = "active",
        must_change_password: bool | str = False,
        email_verified: bool | str = False,
        role_ids: list[str] = None,
    ) -> tuple[Optional[Person], Optional[str]]:
        """Update an existing user. Returns (person, error)."""
        role_ids = role_ids or []
        if isinstance(role_ids, str):
            role_ids = [role_ids]
        normalized_role_ids = {
            str(coerce_uuid(role_id)) for role_id in role_ids if role_id
        }
        must_change_password = _parse_flag(must_change_password)
        email_verified = _parse_flag(email_verified)

        person = db.get(Person, coerce_uuid(user_id))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        # Validate passwords if provided
        if password:
            if password != password_confirm:
                return None, "Passwords do not match"
            if len(password) < 8:
                return None, "Password must be at least 8 characters"

        # Check email uniqueness
        existing_email = (
            db.query(Person)
            .filter(Person.email == email)
            .filter(Person.id != person.id)
            .first()
        )
        if existing_email:
            return None, "A user with this email already exists"

        # Check username uniqueness
        existing_username = (
            db.query(UserCredential)
            .filter(UserCredential.username == username)
            .filter(UserCredential.provider == AuthProvider.local)
            .filter(UserCredential.person_id != person.id)
            .first()
        )
        if existing_username:
            return None, "A user with this username already exists"

        try:
            person_status = PersonStatus(status) if status else PersonStatus.active
            derived_display_name = _derive_display_name(first_name, last_name, display_name)

            person.first_name = first_name
            person.last_name = last_name
            person.display_name = derived_display_name
            person.email = email
            person.phone = phone if phone else None
            person.organization_id = coerce_uuid(organization_id)
            person.status = person_status
            person.is_active = person_status == PersonStatus.active
            person.email_verified = email_verified

            # Update credential
            credential = (
                db.query(UserCredential)
                .filter(UserCredential.person_id == person.id)
                .filter(UserCredential.provider == AuthProvider.local)
                .first()
            )

            if credential:
                credential.username = username
                credential.must_change_password = must_change_password
                if password:
                    credential.password_hash = hash_password(password)
                    credential.password_updated_at = datetime.now(timezone.utc)
            elif password:
                credential = UserCredential(
                    person_id=person.id,
                    provider=AuthProvider.local,
                    username=username,
                    password_hash=hash_password(password),
                    must_change_password=must_change_password,
                    is_active=True,
                )
                db.add(credential)

            # Update roles
            current_role_ids = {
                str(role_id)
                for (role_id,) in db.query(PersonRole.role_id)
                .filter(PersonRole.person_id == person.id)
                .all()
            }
            db.query(PersonRole).filter(PersonRole.person_id == person.id).delete()
            for role_id in normalized_role_ids:
                db.add(PersonRole(person_id=person.id, role_id=coerce_uuid(role_id)))

            roles_changed = current_role_ids != normalized_role_ids
            session_ids_to_invalidate: list[UUID] = []
            if roles_changed:
                active_sessions = (
                    db.query(AuthSession)
                    .filter(AuthSession.person_id == person.id)
                    .filter(AuthSession.status == SessionStatus.active)
                    .filter(AuthSession.revoked_at.is_(None))
                    .all()
                )
                session_ids_to_invalidate = [session.id for session in active_sessions]
                for session in active_sessions:
                    session.status = SessionStatus.revoked
                    session.revoked_at = datetime.now(timezone.utc)

            db.commit()

            if roles_changed and session_ids_to_invalidate:
                from app.services.auth_dependencies import invalidate_session_cache
                for session_id in session_ids_to_invalidate:
                    invalidate_session_cache(session_id)

            return person, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update user: {str(e)}"

    @staticmethod
    def delete_user(db: Session, user_id: str) -> Optional[str]:
        """Delete a user. Returns error message or None on success."""
        person = db.get(Person, coerce_uuid(user_id))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        try:
            employee = (
                db.query(Employee)
                .filter(Employee.person_id == person.id)
                .filter(Employee.is_deleted.is_(False))
                .first()
            )
            if employee:
                return (
                    "Cannot delete user linked to an employee. "
                    "Delete the employee record first."
                )

            # Delete related records
            db.query(PersonRole).filter(PersonRole.person_id == person.id).delete()
            db.query(UserCredential).filter(UserCredential.person_id == person.id).delete()
            db.query(AuthSession).filter(AuthSession.person_id == person.id).delete()

            db.delete(person)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete user: {str(e)}"

    @staticmethod
    def roles_context(
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit

        base_query = db.query(Role)
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            base_query = base_query.filter(
                or_(
                    Role.name.ilike(search_pattern),
                    Role.description.ilike(search_pattern),
                )
            )

        active_count = (
            base_query.filter(Role.is_active.is_(True))
            .with_entities(func.count(Role.id))
            .scalar()
            or 0
        )
        inactive_count = (
            base_query.filter(Role.is_active.is_(False))
            .with_entities(func.count(Role.id))
            .scalar()
            or 0
        )

        status_flag = _parse_status_filter(status)
        query = base_query
        if status_flag is not None:
            query = query.filter(Role.is_active == status_flag)

        total_count = query.with_entities(func.count(Role.id)).scalar() or 0
        roles = (
            query.order_by(Role.name)
            .limit(limit)
            .offset(offset)
            .all()
        )

        role_ids = [role.id for role in roles]
        permission_counts: dict[UUID, int] = {}
        member_counts: dict[UUID, int] = {}
        if role_ids:
            permission_counts = {
                role_id: count
                for role_id, count in db.query(
                    RolePermission.role_id, func.count(RolePermission.id)
                )
                .filter(RolePermission.role_id.in_(role_ids))
                .group_by(RolePermission.role_id)
                .all()
            }
            member_counts = {
                role_id: count
                for role_id, count in db.query(
                    PersonRole.role_id, func.count(PersonRole.id)
                )
                .filter(PersonRole.role_id.in_(role_ids))
                .group_by(PersonRole.role_id)
                .all()
            }

        roles_view = [
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
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "roles": roles_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
            "stats": {
                "active": active_count,
                "inactive": inactive_count,
                "total": active_count + inactive_count,
            },
        }

    @staticmethod
    def role_form_context(
        db: Session,
        role_id: Optional[str] = None,
    ) -> dict:
        """Get context for role create/edit form."""
        from app.services.common import coerce_uuid

        role_data = None
        if role_id:
            role = db.get(Role, coerce_uuid(role_id))
            if role:
                # Get role permissions
                role_permission_ids = [
                    rp.permission_id
                    for rp in db.query(RolePermission)
                    .filter(RolePermission.role_id == role.id)
                    .all()
                ]

                # Get role members with details
                members_query = (
                    db.query(Person)
                    .join(PersonRole, PersonRole.person_id == Person.id)
                    .filter(PersonRole.role_id == role.id)
                    .limit(20)
                    .all()
                )

                members = []
                for person in members_query:
                    name = person.name or person.email or "Unknown"
                    initials = (
                        "".join(word[0].upper() for word in name.split()[:2])
                        if name
                        else "?"
                    )
                    members.append({"name": name, "initials": initials})

                role_data = {
                    "id": str(role.id),
                    "name": role.name,
                    "description": role.description or "",
                    "is_active": role.is_active,
                    "permission_ids": role_permission_ids,
                    "members": members,
                }

        # Get all permissions grouped by category
        permissions = (
            db.query(Permission)
            .filter(Permission.is_active.is_(True))
            .order_by(Permission.key)
            .all()
        )

        # Group permissions by category (first part of key before colon or underscore)
        permissions_by_category: dict[str, list[dict]] = {}
        for perm in permissions:
            # Extract category from key (e.g., "users:read" -> "users")
            key_parts = perm.key.replace("_", ":").split(":")
            category = key_parts[0] if key_parts else "general"

            if category not in permissions_by_category:
                permissions_by_category[category] = []

            permissions_by_category[category].append(
                {
                    "id": perm.id,
                    "key": perm.key,
                    "description": perm.description,
                }
            )

        return {
            "role_data": role_data,
            "permissions_by_category": permissions_by_category,
        }

    @staticmethod
    def role_profile_context(
        db: Session,
        role_id: str,
    ) -> dict:
        """Get context for role profile/detail view."""
        from app.services.common import coerce_uuid

        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return {"role": None}

        # Get role permissions with details
        role_permissions = (
            db.query(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .filter(RolePermission.role_id == role.id)
            .filter(Permission.is_active.is_(True))
            .order_by(Permission.key)
            .all()
        )

        # Group permissions by module (first part of key before colon)
        permissions_by_module: dict[str, list[dict]] = {}
        for perm in role_permissions:
            key_parts = perm.key.split(":")
            module = key_parts[0] if key_parts else "general"

            if module not in permissions_by_module:
                permissions_by_module[module] = []

            permissions_by_module[module].append({
                "key": perm.key,
                "description": perm.description,
                "action": key_parts[-1] if len(key_parts) > 1 else "access",
            })

        # Sort modules by name
        permissions_by_module = dict(sorted(permissions_by_module.items()))

        # Get member count
        member_count = (
            db.query(func.count(PersonRole.id))
            .filter(PersonRole.role_id == role.id)
            .scalar() or 0
        )

        # Get role members with details (up to 50)
        members_query = (
            db.query(Person)
            .join(PersonRole, PersonRole.person_id == Person.id)
            .filter(PersonRole.role_id == role.id)
            .order_by(Person.display_name, Person.first_name, Person.email)
            .limit(50)
            .all()
        )

        members = []
        for person in members_query:
            name = person.name or person.email or "Unknown"
            initials = (
                "".join(word[0].upper() for word in name.split()[:2])
                if name
                else "?"
            )
            members.append({
                "id": str(person.id),
                "name": name,
                "email": person.email,
                "initials": initials,
            })

        # Module display names
        module_names = {
            "audit": "Audit & Compliance",
            "auth": "Authentication",
            "rbac": "Roles & Permissions",
            "scheduler": "Job Scheduler",
            "settings": "System Settings",
            "integrations": "Integrations",
            "finance": "Finance Module",
            "gl": "General Ledger",
            "ar": "Accounts Receivable",
            "ap": "Accounts Payable",
            "fa": "Fixed Assets",
            "banking": "Banking",
            "inv": "Inventory",
            "inventory": "Inventory",
            "tax": "Tax Management",
            "lease": "Lease Accounting",
            "cons": "Consolidation",
            "fx": "Foreign Exchange",
            "reports": "Financial Reports",
            "rpt": "Reporting API",
            "payments": "Payment Gateway",
            "automation": "Automation",
            "org": "Organization Setup",
            "import": "Data Import",
            "hr": "Human Resources",
            "payroll": "Payroll",
            "leave": "Leave Management",
            "attendance": "Attendance",
            "perf": "Performance Management",
            "recruit": "Recruitment",
            "training": "Training & Development",
            "selfservice": "Self-Service Portal",
            "expense": "Expense Management",
            "fleet": "Fleet Management",
            "procurement": "Procurement",
            "projects": "Projects",
            "settings": "Settings",
            "support": "Support & Ticketing",
            "tasks": "Task Management",
        }

        return {
            "role": {
                "id": str(role.id),
                "name": role.name,
                "description": role.description or "",
                "is_active": role.is_active,
                "created_at": _format_datetime(role.created_at),
                "updated_at": _format_datetime(role.updated_at),
            },
            "permissions_by_module": permissions_by_module,
            "permission_count": len(role_permissions),
            "member_count": member_count,
            "members": members,
            "module_names": module_names,
        }

    @staticmethod
    def create_role(
        db: Session,
        name: str,
        description: str,
        is_active: bool,
        permission_ids: list[str],
    ) -> tuple[Optional[Role], Optional[str]]:
        """Create a new role. Returns (role, error)."""
        from app.services.common import coerce_uuid

        # Check if role name already exists
        existing = db.query(Role).filter(Role.name == name).first()
        if existing:
            return None, "A role with this name already exists"

        try:
            role = Role(
                name=name,
                description=description if description else None,
                is_active=is_active,
            )
            db.add(role)
            db.flush()

            # Add permissions
            for perm_id in permission_ids:
                if perm_id:
                    role_permission = RolePermission(
                        role_id=role.id,
                        permission_id=coerce_uuid(perm_id),
                    )
                    db.add(role_permission)

            db.commit()
            return role, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create role: {str(e)}"

    @staticmethod
    def update_role(
        db: Session,
        role_id: str,
        name: str,
        description: str,
        is_active: bool,
        permission_ids: list[str],
    ) -> tuple[Optional[Role], Optional[str]]:
        """Update an existing role. Returns (role, error)."""
        from app.services.common import coerce_uuid

        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return None, "Role not found"

        # Check if name already exists for another role
        existing = (
            db.query(Role)
            .filter(Role.name == name)
            .filter(Role.id != role.id)
            .first()
        )
        if existing:
            return None, "A role with this name already exists"

        try:
            role.name = name
            role.description = description if description else None
            role.is_active = is_active

            # Update permissions - remove old, add new
            db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()

            for perm_id in permission_ids:
                if perm_id:
                    role_permission = RolePermission(
                        role_id=role.id,
                        permission_id=coerce_uuid(perm_id),
                    )
                    db.add(role_permission)

            db.commit()
            return role, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update role: {str(e)}"

    @staticmethod
    def delete_role(
        db: Session,
        role_id: str,
    ) -> Optional[str]:
        """Delete a role. Returns error message or None on success."""
        from app.services.common import coerce_uuid

        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            return "Role not found"

        try:
            # Remove role permissions
            db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()

            # Remove person-role assignments
            db.query(PersonRole).filter(PersonRole.role_id == role.id).delete()

            # Delete role
            db.delete(role)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete role: {str(e)}"

    @staticmethod
    def permissions_context(
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        """Get context for permissions list page."""
        offset = (page - 1) * limit

        base_query = db.query(Permission)
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            base_query = base_query.filter(
                or_(
                    Permission.key.ilike(search_pattern),
                    Permission.description.ilike(search_pattern),
                )
            )

        active_count = (
            base_query.filter(Permission.is_active.is_(True))
            .with_entities(func.count(Permission.id))
            .scalar()
            or 0
        )
        inactive_count = (
            base_query.filter(Permission.is_active.is_(False))
            .with_entities(func.count(Permission.id))
            .scalar()
            or 0
        )

        status_flag = _parse_status_filter(status)
        query = base_query
        if status_flag is not None:
            query = query.filter(Permission.is_active == status_flag)

        total_count = query.with_entities(func.count(Permission.id)).scalar() or 0
        permissions = (
            query.order_by(Permission.key)
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Get role counts for each permission
        perm_ids = [p.id for p in permissions]
        role_counts: dict[UUID, int] = {}
        if perm_ids:
            role_counts = {
                perm_id: count
                for perm_id, count in db.query(
                    RolePermission.permission_id, func.count(RolePermission.id)
                )
                .filter(RolePermission.permission_id.in_(perm_ids))
                .group_by(RolePermission.permission_id)
                .all()
            }

        permissions_view = [
            {
                "permission_id": perm.id,
                "key": perm.key,
                "description": perm.description,
                "is_active": perm.is_active,
                "role_count": role_counts.get(perm.id, 0),
                "created_at": _format_datetime(perm.created_at),
            }
            for perm in permissions
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "permissions": permissions_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
            "stats": {
                "active": active_count,
                "inactive": inactive_count,
                "total": active_count + inactive_count,
            },
        }

    @staticmethod
    def permission_form_context(
        db: Session,
        permission_id: Optional[str] = None,
    ) -> dict:
        """Get context for permission create/edit form."""
        permission_data = None
        if permission_id:
            perm = db.get(Permission, coerce_uuid(permission_id))
            if perm:
                # Get roles that have this permission
                roles_with_permission = (
                    db.query(Role.name)
                    .join(RolePermission, RolePermission.role_id == Role.id)
                    .filter(RolePermission.permission_id == perm.id)
                    .all()
                )

                permission_data = {
                    "id": str(perm.id),
                    "key": perm.key,
                    "description": perm.description or "",
                    "is_active": perm.is_active,
                    "roles": [r[0] for r in roles_with_permission],
                }

        return {
            "permission_data": permission_data,
        }

    @staticmethod
    def create_permission(
        db: Session,
        key: str,
        description: str,
        is_active: bool,
    ) -> tuple[Optional[Permission], Optional[str]]:
        """Create a new permission. Returns (permission, error)."""
        # Check if key already exists
        existing = db.query(Permission).filter(Permission.key == key).first()
        if existing:
            return None, "A permission with this key already exists"

        try:
            permission = Permission(
                key=key,
                description=description if description else None,
                is_active=is_active,
            )
            db.add(permission)
            db.commit()
            return permission, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create permission: {str(e)}"

    @staticmethod
    def update_permission(
        db: Session,
        permission_id: str,
        key: str,
        description: str,
        is_active: bool,
    ) -> tuple[Optional[Permission], Optional[str]]:
        """Update an existing permission. Returns (permission, error)."""
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            return None, "Permission not found"

        # Check if key already exists for another permission
        existing = (
            db.query(Permission)
            .filter(Permission.key == key)
            .filter(Permission.id != permission.id)
            .first()
        )
        if existing:
            return None, "A permission with this key already exists"

        try:
            permission.key = key
            permission.description = description if description else None
            permission.is_active = is_active

            db.commit()
            return permission, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update permission: {str(e)}"

    @staticmethod
    def delete_permission(
        db: Session,
        permission_id: str,
    ) -> Optional[str]:
        """Delete a permission. Returns error message or None on success."""
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            return "Permission not found"

        try:
            # Remove role-permission assignments
            db.query(RolePermission).filter(
                RolePermission.permission_id == permission.id
            ).delete()

            db.delete(permission)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete permission: {str(e)}"

    @staticmethod
    def organizations_context(
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit

        base_query = db.query(Organization)
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            base_query = base_query.filter(
                or_(
                    Organization.organization_code.ilike(search_pattern),
                    Organization.legal_name.ilike(search_pattern),
                    Organization.trading_name.ilike(search_pattern),
                )
            )

        active_count = (
            base_query.filter(Organization.is_active.is_(True))
            .with_entities(func.count(Organization.organization_id))
            .scalar()
            or 0
        )
        inactive_count = (
            base_query.filter(Organization.is_active.is_(False))
            .with_entities(func.count(Organization.organization_id))
            .scalar()
            or 0
        )

        status_flag = _parse_status_filter(status)
        query = base_query
        if status_flag is not None:
            query = query.filter(Organization.is_active == status_flag)

        total_count = (
            query.with_entities(func.count(Organization.organization_id)).scalar() or 0
        )
        organizations = (
            query.order_by(Organization.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        org_ids = [org.organization_id for org in organizations]
        user_counts: dict[UUID, int] = {}
        active_user_counts: dict[UUID, int] = {}
        if org_ids:
            user_counts = {
                org_id: count
                for org_id, count in db.query(
                    Person.organization_id, func.count(Person.id)
                )
                .filter(Person.organization_id.in_(org_ids))
                .group_by(Person.organization_id)
                .all()
            }
            active_user_counts = {
                org_id: count
                for org_id, count in db.query(
                    Person.organization_id, func.count(Person.id)
                )
                .filter(Person.organization_id.in_(org_ids))
                .filter(Person.is_active.is_(True))
                .group_by(Person.organization_id)
                .all()
            }

        organizations_view = [
            {
                "organization_id": org.organization_id,
                "organization_code": org.organization_code,
                "legal_name": org.legal_name,
                "trading_name": org.trading_name,
                "country_code": org.jurisdiction_country_code,
                "functional_currency": org.functional_currency_code,
                "presentation_currency": org.presentation_currency_code,
                "is_active": org.is_active,
                "total_users": user_counts.get(org.organization_id, 0),
                "active_users": active_user_counts.get(org.organization_id, 0),
                "created_at": _format_datetime(org.created_at),
            }
            for org in organizations
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "organizations": organizations_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
            "stats": {
                "active": active_count,
                "inactive": inactive_count,
                "total": active_count + inactive_count,
            },
        }

    @staticmethod
    def organization_form_context(
        db: Session,
        organization_id: Optional[str] = None,
        default_currency_org_id: Optional[str] = None,
    ) -> dict:
        """Get context for organization create/edit form."""
        from app.models.finance.core_org.organization import ConsolidationMethod

        # Get parent organizations for dropdown
        parent_orgs = (
            db.query(Organization)
            .filter(Organization.is_active.is_(True))
            .order_by(Organization.legal_name)
            .all()
        )

        parent_org_list = [
            {
                "id": str(org.organization_id),
                "code": org.organization_code,
                "name": org.legal_name or org.trading_name or org.organization_code,
            }
            for org in parent_orgs
        ]

        # Consolidation method options
        consolidation_methods = [
            {"value": cm.value, "label": cm.value.replace("_", " ").title()}
            for cm in ConsolidationMethod
        ]

        organization_data = None
        default_functional_currency_code = None
        default_presentation_currency_code = None

        default_org_id = organization_id or default_currency_org_id
        if default_org_id:
            default_org = db.get(Organization, coerce_uuid(default_org_id))
            if default_org:
                default_functional_currency_code = default_org.functional_currency_code
                default_presentation_currency_code = default_org.presentation_currency_code
        if not default_functional_currency_code:
            default_functional_currency_code = settings.default_functional_currency_code
        if not default_presentation_currency_code:
            default_presentation_currency_code = settings.default_presentation_currency_code
        if organization_id:
            org = db.get(Organization, coerce_uuid(organization_id))
            if org:
                # Get user count
                user_count = (
                    db.query(func.count(Person.id))
                    .filter(Person.organization_id == org.organization_id)
                    .scalar()
                    or 0
                )

                # Get subsidiaries count
                subsidiaries_count = (
                    db.query(func.count(Organization.organization_id))
                    .filter(Organization.parent_organization_id == org.organization_id)
                    .scalar()
                    or 0
                )

                organization_data = {
                    "id": str(org.organization_id),
                    "organization_code": org.organization_code,
                    "legal_name": org.legal_name,
                    "trading_name": org.trading_name or "",
                    "registration_number": org.registration_number or "",
                    "tax_identification_number": org.tax_identification_number or "",
                    "incorporation_date": (
                        org.incorporation_date.isoformat() if org.incorporation_date else ""
                    ),
                    "jurisdiction_country_code": org.jurisdiction_country_code or "",
                    "functional_currency_code": org.functional_currency_code,
                    "presentation_currency_code": org.presentation_currency_code,
                    "fiscal_year_end_month": org.fiscal_year_end_month,
                    "fiscal_year_end_day": org.fiscal_year_end_day,
                    "parent_organization_id": (
                        str(org.parent_organization_id) if org.parent_organization_id else ""
                    ),
                    "consolidation_method": (
                        org.consolidation_method.value if org.consolidation_method else ""
                    ),
                    "ownership_percentage": (
                        str(org.ownership_percentage) if org.ownership_percentage else ""
                    ),
                    "is_active": org.is_active,
                    "user_count": user_count,
                    "subsidiaries_count": subsidiaries_count,
                    # Payroll GL account settings
                    "salaries_expense_account_id": (
                        str(org.salaries_expense_account_id) if org.salaries_expense_account_id else ""
                    ),
                    "salary_payable_account_id": (
                        str(org.salary_payable_account_id) if org.salary_payable_account_id else ""
                    ),
                }

                # Remove current org from parent list to prevent self-reference
                parent_org_list = [
                    p for p in parent_org_list if p["id"] != str(org.organization_id)
                ]

        # Load GL accounts for payroll settings (expense and liability accounts)
        expense_accounts = []
        liability_accounts = []
        if organization_id:
            from app.models.finance.gl.account import Account
            from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

            org_uuid = coerce_uuid(organization_id)

            # Get expense accounts (IFRS category = EXPENSES)
            expense_accts = (
                db.query(Account)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    Account.organization_id == org_uuid,
                    Account.is_active.is_(True),
                    Account.is_posting_allowed.is_(True),
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                )
                .order_by(Account.account_code)
                .all()
            )
            expense_accounts = [
                {
                    "account_id": str(a.account_id),
                    "account_code": a.account_code,
                    "account_name": a.account_name,
                }
                for a in expense_accts
            ]

            # Get liability accounts (IFRS category = LIABILITIES)
            liability_accts = (
                db.query(Account)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    Account.organization_id == org_uuid,
                    Account.is_active.is_(True),
                    Account.is_posting_allowed.is_(True),
                    AccountCategory.ifrs_category == IFRSCategory.LIABILITIES,
                )
                .order_by(Account.account_code)
                .all()
            )
            liability_accounts = [
                {
                    "account_id": str(a.account_id),
                    "account_code": a.account_code,
                    "account_name": a.account_name,
                }
                for a in liability_accts
            ]

        return {
            "organization_data": organization_data,
            "parent_organizations": parent_org_list,
            "consolidation_methods": consolidation_methods,
            "default_functional_currency_code": default_functional_currency_code,
            "default_presentation_currency_code": default_presentation_currency_code,
            "expense_accounts": expense_accounts,
            "liability_accounts": liability_accounts,
        }

    @staticmethod
    def create_organization(
        db: Session,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str = "",
        registration_number: str = "",
        tax_identification_number: str = "",
        incorporation_date: str = "",
        jurisdiction_country_code: str = "",
        parent_organization_id: str = "",
        consolidation_method: str = "",
        ownership_percentage: str = "",
        is_active: bool = True,
    ) -> tuple[Optional[Organization], Optional[str]]:
        """Create a new organization. Returns (organization, error)."""
        from datetime import date as date_type

        from app.models.finance.core_org.organization import ConsolidationMethod

        # Check if organization code already exists
        existing = (
            db.query(Organization)
            .filter(Organization.organization_code == organization_code)
            .first()
        )
        if existing:
            return None, "An organization with this code already exists"

        try:
            # Parse incorporation date
            incorp_date = None
            if incorporation_date:
                incorp_date = date_type.fromisoformat(incorporation_date)

            # Parse consolidation method
            consol_method = None
            if consolidation_method:
                consol_method = ConsolidationMethod(consolidation_method)

            # Parse ownership percentage
            ownership_pct = None
            if ownership_percentage:
                from decimal import Decimal

                ownership_pct = Decimal(ownership_percentage)

            # Parse parent org id
            parent_org_id = None
            if parent_organization_id:
                parent_org_id = coerce_uuid(parent_organization_id)

            org = Organization(
                organization_code=organization_code,
                legal_name=legal_name,
                trading_name=trading_name if trading_name else None,
                registration_number=registration_number if registration_number else None,
                tax_identification_number=(
                    tax_identification_number if tax_identification_number else None
                ),
                incorporation_date=incorp_date,
                jurisdiction_country_code=(
                    jurisdiction_country_code if jurisdiction_country_code else None
                ),
                functional_currency_code=functional_currency_code,
                presentation_currency_code=presentation_currency_code,
                fiscal_year_end_month=fiscal_year_end_month,
                fiscal_year_end_day=fiscal_year_end_day,
                parent_organization_id=parent_org_id,
                consolidation_method=consol_method,
                ownership_percentage=ownership_pct,
                is_active=is_active,
            )
            db.add(org)
            db.commit()
            return org, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create organization: {str(e)}"

    @staticmethod
    def update_organization(
        db: Session,
        organization_id: str,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str = "",
        registration_number: str = "",
        tax_identification_number: str = "",
        incorporation_date: str = "",
        jurisdiction_country_code: str = "",
        parent_organization_id: str = "",
        consolidation_method: str = "",
        ownership_percentage: str = "",
        is_active: bool = True,
        salaries_expense_account_id: str = "",
        salary_payable_account_id: str = "",
    ) -> tuple[Optional[Organization], Optional[str]]:
        """Update an existing organization. Returns (organization, error)."""
        from datetime import date as date_type

        from app.models.finance.core_org.organization import ConsolidationMethod

        org = db.get(Organization, coerce_uuid(organization_id))
        if not org:
            return None, "Organization not found"

        # Check if code already exists for another org
        existing = (
            db.query(Organization)
            .filter(Organization.organization_code == organization_code)
            .filter(Organization.organization_id != org.organization_id)
            .first()
        )
        if existing:
            return None, "An organization with this code already exists"

        # Prevent setting self as parent
        if parent_organization_id and coerce_uuid(parent_organization_id) == org.organization_id:
            return None, "An organization cannot be its own parent"

        try:
            # Parse incorporation date
            incorp_date = None
            if incorporation_date:
                incorp_date = date_type.fromisoformat(incorporation_date)

            # Parse consolidation method
            consol_method = None
            if consolidation_method:
                consol_method = ConsolidationMethod(consolidation_method)

            # Parse ownership percentage
            ownership_pct = None
            if ownership_percentage:
                from decimal import Decimal

                ownership_pct = Decimal(ownership_percentage)

            # Parse parent org id
            parent_org_id = None
            if parent_organization_id:
                parent_org_id = coerce_uuid(parent_organization_id)

            # Parse payroll GL account IDs
            salaries_exp_acc_id = None
            if salaries_expense_account_id:
                salaries_exp_acc_id = coerce_uuid(salaries_expense_account_id)

            salary_pay_acc_id = None
            if salary_payable_account_id:
                salary_pay_acc_id = coerce_uuid(salary_payable_account_id)

            org.organization_code = organization_code
            org.legal_name = legal_name
            org.trading_name = trading_name if trading_name else None
            org.registration_number = registration_number if registration_number else None
            org.tax_identification_number = (
                tax_identification_number if tax_identification_number else None
            )
            org.incorporation_date = incorp_date
            org.jurisdiction_country_code = (
                jurisdiction_country_code if jurisdiction_country_code else None
            )
            org.functional_currency_code = functional_currency_code
            org.presentation_currency_code = presentation_currency_code
            org.fiscal_year_end_month = fiscal_year_end_month
            org.fiscal_year_end_day = fiscal_year_end_day
            org.parent_organization_id = parent_org_id
            org.consolidation_method = consol_method
            org.ownership_percentage = ownership_pct
            org.is_active = is_active
            org.salaries_expense_account_id = salaries_exp_acc_id
            org.salary_payable_account_id = salary_pay_acc_id

            db.commit()
            return org, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update organization: {str(e)}"

    @staticmethod
    def delete_organization(
        db: Session,
        organization_id: str,
    ) -> Optional[str]:
        """Delete an organization. Returns error message or None on success."""
        org = db.get(Organization, coerce_uuid(organization_id))
        if not org:
            return "Organization not found"

        # Check for users
        user_count = (
            db.query(func.count(Person.id))
            .filter(Person.organization_id == org.organization_id)
            .scalar()
            or 0
        )
        if user_count > 0:
            return f"Cannot delete organization with {user_count} user(s). Remove users first."

        # Check for subsidiaries
        subsidiaries_count = (
            db.query(func.count(Organization.organization_id))
            .filter(Organization.parent_organization_id == org.organization_id)
            .scalar()
            or 0
        )
        if subsidiaries_count > 0:
            return f"Cannot delete organization with {subsidiaries_count} subsidiary(ies). Remove subsidiaries first."

        try:
            db.delete(org)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete organization: {str(e)}"

    @staticmethod
    def settings_context(
        db: Session,
        search: Optional[str],
        domain: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit

        domain_value = _parse_domain(domain)

        base_query = db.query(DomainSetting)
        if domain_value:
            base_query = base_query.filter(DomainSetting.domain == domain_value)

        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            base_query = base_query.filter(DomainSetting.key.ilike(search_pattern))

        active_count = (
            base_query.filter(DomainSetting.is_active.is_(True))
            .with_entities(func.count(DomainSetting.id))
            .scalar()
            or 0
        )
        inactive_count = (
            base_query.filter(DomainSetting.is_active.is_(False))
            .with_entities(func.count(DomainSetting.id))
            .scalar()
            or 0
        )

        status_flag = _parse_status_filter(status)
        query = base_query
        if status_flag is not None:
            query = query.filter(DomainSetting.is_active == status_flag)

        total_count = query.with_entities(func.count(DomainSetting.id)).scalar() or 0
        settings = (
            query.order_by(DomainSetting.domain, DomainSetting.key)
            .limit(limit)
            .offset(offset)
            .all()
        )

        settings_view = [
            {
                "setting_id": setting.id,
                "domain": setting.domain.value,
                "key": setting.key,
                "value": _setting_value_display(setting),
                "value_type": setting.value_type.value,
                "is_secret": setting.is_secret,
                "is_active": setting.is_active,
                "updated_at": _format_datetime(setting.updated_at or setting.created_at),
            }
            for setting in settings
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "settings": settings_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
            "domain_filter": domain_value.value if domain_value else "",
            "domain_options": [value.value for value in SettingDomain],
            "stats": {
                "active": active_count,
                "inactive": inactive_count,
                "total": active_count + inactive_count,
            },
        }

    @staticmethod
    def setting_form_context(
        db: Session,
        setting_id: Optional[str] = None,
    ) -> dict:
        """Get context for setting create/edit form."""
        setting_data = None
        if setting_id:
            setting = db.get(DomainSetting, coerce_uuid(setting_id))
            if setting:
                # Get the value based on type
                value = ""
                if setting.value_type == SettingValueType.json:
                    if setting.value_json is not None:
                        value = json.dumps(setting.value_json, indent=2)
                elif setting.value_type == SettingValueType.boolean:
                    value = "true" if setting.value_text == "true" else "false"
                else:
                    value = setting.value_text or ""

                setting_data = {
                    "id": str(setting.id),
                    "domain": setting.domain.value,
                    "key": setting.key,
                    "value_type": setting.value_type.value,
                    "value": value,
                    "is_secret": setting.is_secret,
                    "is_active": setting.is_active,
                }

        return {
            "setting_data": setting_data,
            "domains": [d.value for d in SettingDomain],
            "value_types": [vt.value for vt in SettingValueType],
        }

    @staticmethod
    def create_setting(
        db: Session,
        domain: str,
        key: str,
        value_type: str,
        value: str,
        is_secret: bool = False,
        is_active: bool = True,
    ) -> tuple[Optional[DomainSetting], Optional[str]]:
        """Create a new setting. Returns (setting, error)."""
        # Validate domain
        try:
            domain_enum = SettingDomain(domain)
        except ValueError:
            return None, f"Invalid domain: {domain}"

        # Validate value type
        try:
            value_type_enum = SettingValueType(value_type)
        except ValueError:
            return None, f"Invalid value type: {value_type}"

        # Check if setting already exists
        existing = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == domain_enum)
            .filter(DomainSetting.key == key)
            .first()
        )
        if existing:
            return None, f"A setting with key '{key}' already exists in domain '{domain}'"

        try:
            # Parse and store value based on type
            value_text = None
            value_json = None

            if value_type_enum == SettingValueType.json:
                try:
                    value_json = json.loads(value) if value else None
                except json.JSONDecodeError as e:
                    return None, f"Invalid JSON value: {str(e)}"
            elif value_type_enum == SettingValueType.boolean:
                value_text = "true" if value.lower() in ("true", "1", "yes", "on") else "false"
            elif value_type_enum == SettingValueType.integer:
                try:
                    int(value) if value else 0
                    value_text = value
                except ValueError:
                    return None, "Value must be a valid integer"
            else:
                value_text = value

            setting = DomainSetting(
                domain=domain_enum,
                key=key,
                value_type=value_type_enum,
                value_text=value_text,
                value_json=value_json,
                is_secret=is_secret,
                is_active=is_active,
            )
            db.add(setting)
            db.commit()
            return setting, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create setting: {str(e)}"

    @staticmethod
    def update_setting(
        db: Session,
        setting_id: str,
        domain: str,
        key: str,
        value_type: str,
        value: str,
        is_secret: bool = False,
        is_active: bool = True,
    ) -> tuple[Optional[DomainSetting], Optional[str]]:
        """Update an existing setting. Returns (setting, error)."""
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting:
            return None, "Setting not found"

        # Validate domain
        try:
            domain_enum = SettingDomain(domain)
        except ValueError:
            return None, f"Invalid domain: {domain}"

        # Validate value type
        try:
            value_type_enum = SettingValueType(value_type)
        except ValueError:
            return None, f"Invalid value type: {value_type}"

        # Check if key already exists for another setting in the same domain
        existing = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == domain_enum)
            .filter(DomainSetting.key == key)
            .filter(DomainSetting.id != setting.id)
            .first()
        )
        if existing:
            return None, f"A setting with key '{key}' already exists in domain '{domain}'"

        try:
            # Parse and store value based on type
            value_text = None
            value_json = None

            if value_type_enum == SettingValueType.json:
                try:
                    value_json = json.loads(value) if value else None
                except json.JSONDecodeError as e:
                    return None, f"Invalid JSON value: {str(e)}"
            elif value_type_enum == SettingValueType.boolean:
                value_text = "true" if value.lower() in ("true", "1", "yes", "on") else "false"
            elif value_type_enum == SettingValueType.integer:
                try:
                    int(value) if value else 0
                    value_text = value
                except ValueError:
                    return None, "Value must be a valid integer"
            else:
                value_text = value

            setting.domain = domain_enum
            setting.key = key
            setting.value_type = value_type_enum
            setting.value_text = value_text
            setting.value_json = value_json
            setting.is_secret = is_secret
            setting.is_active = is_active

            db.commit()
            return setting, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update setting: {str(e)}"

    @staticmethod
    def delete_setting(
        db: Session,
        setting_id: str,
    ) -> Optional[str]:
        """Delete a setting. Returns error message or None on success."""
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting:
            return "Setting not found"

        try:
            db.delete(setting)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete setting: {str(e)}"

    @staticmethod
    def audit_logs_context(
        db: Session,
        search: Optional[str],
        actor_type: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit

        query = db.query(AuditEvent)

        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            query = query.filter(
                or_(
                    AuditEvent.action.ilike(search_pattern),
                    AuditEvent.entity_type.ilike(search_pattern),
                    AuditEvent.actor_id.ilike(search_pattern),
                    AuditEvent.request_id.ilike(search_pattern),
                    AuditEvent.ip_address.ilike(search_pattern),
                )
            )

        actor_type_value = _parse_actor_type(actor_type)
        if actor_type_value:
            query = query.filter(AuditEvent.actor_type == actor_type_value)

        success_value = _parse_success_filter(status)
        if success_value is not None:
            query = query.filter(AuditEvent.is_success == success_value)

        total_count = query.with_entities(func.count(AuditEvent.id)).scalar() or 0
        events = (
            query.order_by(AuditEvent.occurred_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        events_view = [
            {
                "event_id": event.id,
                "occurred_at": _format_datetime(event.occurred_at),
                "actor_type": event.actor_type.value,
                "actor_id": event.actor_id,
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "status_code": event.status_code,
                "is_success": event.is_success,
                "request_id": event.request_id,
                "ip_address": event.ip_address,
            }
            for event in events
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "events": events_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
            "actor_type_filter": actor_type_value.value if actor_type_value else "",
            "actor_types": [value.value for value in AuditActorType],
        }

    @staticmethod
    def tasks_context(
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit

        query = db.query(ScheduledTask)
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            query = query.filter(
                or_(
                    ScheduledTask.name.ilike(search_pattern),
                    ScheduledTask.task_name.ilike(search_pattern),
                )
            )

        status_flag = None
        if status == "enabled":
            status_flag = True
        elif status == "disabled":
            status_flag = False
        if status_flag is not None:
            query = query.filter(ScheduledTask.enabled == status_flag)

        total_count = query.with_entities(func.count(ScheduledTask.id)).scalar() or 0
        tasks = (
            query.order_by(ScheduledTask.name)
            .limit(limit)
            .offset(offset)
            .all()
        )

        tasks_view = []
        for task in tasks:
            args_display = ""
            kwargs_display = ""
            if task.args_json:
                args_display = _truncate(_safe_json_dump(task.args_json))
            if task.kwargs_json:
                kwargs_display = _truncate(_safe_json_dump(task.kwargs_json))
            schedule_label = "-"
            if task.schedule_type == ScheduleType.interval:
                schedule_label = _format_interval(task.interval_seconds)

            tasks_view.append(
                {
                    "task_id": task.id,
                    "name": task.name,
                    "task_name": task.task_name,
                    "schedule": schedule_label,
                    "args": args_display,
                    "kwargs": kwargs_display,
                    "enabled": task.enabled,
                    "last_run_at": _format_datetime(task.last_run_at),
                    "updated_at": _format_datetime(task.updated_at or task.created_at),
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)
        pagination = _build_pagination(page, total_pages, total_count, limit)

        return {
            "tasks": tasks_view,
            "pagination": pagination,
            "search": search_value,
            "status_filter": status or "",
        }

    @staticmethod
    def task_form_context(
        db: Session,
        task_id: Optional[str] = None,
    ) -> dict:
        """Get context for task create/edit form."""
        task_data = None
        if task_id:
            task = db.get(ScheduledTask, coerce_uuid(task_id))
            if task:
                task_data = {
                    "id": str(task.id),
                    "name": task.name,
                    "task_name": task.task_name,
                    "schedule_type": task.schedule_type.value,
                    "interval_seconds": task.interval_seconds,
                    "args_json": json.dumps(task.args_json, indent=2) if task.args_json else "",
                    "kwargs_json": json.dumps(task.kwargs_json, indent=2) if task.kwargs_json else "",
                    "enabled": task.enabled,
                    "last_run_at": _format_datetime(task.last_run_at),
                }

        return {
            "task_data": task_data,
            "schedule_types": [st.value for st in ScheduleType],
            "interval_presets": [
                {"value": 60, "label": "Every minute"},
                {"value": 300, "label": "Every 5 minutes"},
                {"value": 600, "label": "Every 10 minutes"},
                {"value": 900, "label": "Every 15 minutes"},
                {"value": 1800, "label": "Every 30 minutes"},
                {"value": 3600, "label": "Every hour"},
                {"value": 7200, "label": "Every 2 hours"},
                {"value": 14400, "label": "Every 4 hours"},
                {"value": 21600, "label": "Every 6 hours"},
                {"value": 43200, "label": "Every 12 hours"},
                {"value": 86400, "label": "Every day"},
                {"value": 604800, "label": "Every week"},
            ],
        }

    @staticmethod
    def create_task(
        db: Session,
        name: str,
        task_name: str,
        schedule_type: str,
        interval_seconds: int,
        args_json: str = "",
        kwargs_json: str = "",
        enabled: bool = True,
    ) -> tuple[Optional[ScheduledTask], Optional[str]]:
        """Create a new scheduled task. Returns (task, error)."""
        # Validate schedule type
        try:
            schedule_type_enum = ScheduleType(schedule_type)
        except ValueError:
            return None, f"Invalid schedule type: {schedule_type}"

        # Check if task name already exists
        existing = db.query(ScheduledTask).filter(ScheduledTask.name == name).first()
        if existing:
            return None, f"A task with name '{name}' already exists"

        # Parse args JSON
        args_list = None
        if args_json and args_json.strip():
            try:
                args_list = json.loads(args_json)
                if not isinstance(args_list, list):
                    return None, "Args must be a JSON array"
            except json.JSONDecodeError as e:
                return None, f"Invalid args JSON: {str(e)}"

        # Parse kwargs JSON
        kwargs_dict = None
        if kwargs_json and kwargs_json.strip():
            try:
                kwargs_dict = json.loads(kwargs_json)
                if not isinstance(kwargs_dict, dict):
                    return None, "Kwargs must be a JSON object"
            except json.JSONDecodeError as e:
                return None, f"Invalid kwargs JSON: {str(e)}"

        try:
            task = ScheduledTask(
                name=name,
                task_name=task_name,
                schedule_type=schedule_type_enum,
                interval_seconds=interval_seconds,
                args_json=args_list,
                kwargs_json=kwargs_dict,
                enabled=enabled,
            )
            db.add(task)
            db.commit()
            return task, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create task: {str(e)}"

    @staticmethod
    def update_task(
        db: Session,
        task_id: str,
        name: str,
        task_name: str,
        schedule_type: str,
        interval_seconds: int,
        args_json: str = "",
        kwargs_json: str = "",
        enabled: bool = True,
    ) -> tuple[Optional[ScheduledTask], Optional[str]]:
        """Update an existing scheduled task. Returns (task, error)."""
        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            return None, "Task not found"

        # Validate schedule type
        try:
            schedule_type_enum = ScheduleType(schedule_type)
        except ValueError:
            return None, f"Invalid schedule type: {schedule_type}"

        # Check if name already exists for another task
        existing = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.name == name)
            .filter(ScheduledTask.id != task.id)
            .first()
        )
        if existing:
            return None, f"A task with name '{name}' already exists"

        # Parse args JSON
        args_list = None
        if args_json and args_json.strip():
            try:
                args_list = json.loads(args_json)
                if not isinstance(args_list, list):
                    return None, "Args must be a JSON array"
            except json.JSONDecodeError as e:
                return None, f"Invalid args JSON: {str(e)}"

        # Parse kwargs JSON
        kwargs_dict = None
        if kwargs_json and kwargs_json.strip():
            try:
                kwargs_dict = json.loads(kwargs_json)
                if not isinstance(kwargs_dict, dict):
                    return None, "Kwargs must be a JSON object"
            except json.JSONDecodeError as e:
                return None, f"Invalid kwargs JSON: {str(e)}"

        try:
            task.name = name
            task.task_name = task_name
            task.schedule_type = schedule_type_enum
            task.interval_seconds = interval_seconds
            task.args_json = args_list
            task.kwargs_json = kwargs_dict
            task.enabled = enabled

            db.commit()
            return task, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update task: {str(e)}"

    @staticmethod
    def delete_task(
        db: Session,
        task_id: str,
    ) -> Optional[str]:
        """Delete a scheduled task. Returns error message or None on success."""
        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            return "Task not found"

        try:
            db.delete(task)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete task: {str(e)}"

    def _request_path_with_query(self, request: Request) -> str:
        if request.url.query:
            return f"{request.url.path}?{request.url.query}"
        return request.url.path

    def _admin_login_redirect(self, next_path: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/admin/login?{urlencode({'next': next_path})}",
            status_code=302,
        )

    def _require_admin_web_auth(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> WebAuthContext | RedirectResponse:
        if not auth.is_authenticated:
            return self._admin_login_redirect(self._request_path_with_query(request))

        if "admin" not in auth.roles:
            raise HTTPException(
                status_code=403,
                detail="Admin access required",
            )

        return auth

    def _render_admin_template(
        self,
        request: Request,
        template_name: str,
        auth: WebAuthContext,
        title: str,
        page_title: str,
        active_page: str,
        context: Optional[dict] = None,
        status_code: Optional[int] = None,
    ) -> HTMLResponse:
        if status_code is None:
            status_code = 200
        payload = {
            "title": title,
            "page_title": page_title,
            "brand": brand_context(),
            "user": auth.user,
            "active_page": active_page,
            "csrf_token": getattr(request.state, "csrf_token", ""),
            **(context or {}),
        }
        return templates.TemplateResponse(
            request,
            template_name,
            payload,
            status_code=status_code,
        )

    def dashboard_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.dashboard_context(db)
        return self._render_admin_template(
            request,
            "admin/dashboard.html",
            auth_or_redirect,
            "Admin Dashboard",
            "Dashboard",
            "dashboard",
            context,
        )

    def users_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.users_context(db, search, status, page)
        return self._render_admin_template(
            request,
            "admin/users.html",
            auth_or_redirect,
            "Users",
            "Users",
            "users",
            context,
        )

    def users_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.user_form_context(db)
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/user_form.html",
            auth_or_redirect,
            "Add New User",
            "Add New User",
            "users",
            context,
        )

    def users_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        organization_id: str,
        password: str,
        password_confirm: str,
        display_name: str,
        phone: str,
        status: str,
        must_change_password: str,
        roles: list[str],
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.create_user(
            db=db,
            first_name=first_name,
            last_name=last_name,
            email=email,
            username=username,
            organization_id=organization_id,
            password=password,
            password_confirm=password_confirm,
            display_name=display_name,
            phone=phone,
            status=status,
            must_change_password=must_change_password,
            role_ids=roles,
        )

        if error:
            context = self.user_form_context(db)
            context["user_data"] = self.user_data_from_payload(
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "display_name": display_name,
                    "email": email,
                    "phone": phone,
                    "status": status,
                    "organization_id": organization_id,
                    "username": username,
                    "must_change_password": must_change_password,
                    "roles": roles,
                }
            )
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/user_form.html",
                auth_or_redirect,
                "Add New User",
                "Add New User",
                "users",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/users?created=1", status_code=302)

    def users_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        user_id: str,
    ) -> HTMLResponse | RedirectResponse:
        return self.users_edit_response(request, db, auth, user_id)

    def users_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        user_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.user_form_context(db, user_id)
        context.update({"error": None, "success": None})
        title = f"Edit User - {context['user_data']['first_name']} {context['user_data']['last_name']}"
        return self._render_admin_template(
            request,
            "admin/user_form.html",
            auth_or_redirect,
            title,
            "Edit User",
            "users",
            context,
        )

    def users_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        user_id: str,
        first_name: str,
        last_name: str,
        email: str,
        username: str,
        organization_id: str,
        password: str,
        password_confirm: str,
        display_name: str,
        phone: str,
        status: str,
        must_change_password: str,
        email_verified: str,
        roles: list[str],
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.update_user(
            db=db,
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            username=username,
            organization_id=organization_id,
            password=password,
            password_confirm=password_confirm,
            display_name=display_name,
            phone=phone,
            status=status,
            must_change_password=must_change_password,
            email_verified=email_verified,
            role_ids=roles,
        )

        context = self.user_form_context(db, user_id)
        if error:
            context["user_data"] = self.user_data_from_payload(
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "display_name": display_name,
                    "email": email,
                    "phone": phone,
                    "status": status,
                    "organization_id": organization_id,
                    "username": username,
                    "must_change_password": must_change_password,
                    "email_verified": email_verified,
                    "roles": roles,
                },
                user_id=user_id,
            )
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/user_form.html",
                auth_or_redirect,
                f"Edit User - {first_name} {last_name}",
                "Edit User",
                "users",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "User updated successfully"})
        return self._render_admin_template(
            request,
            "admin/user_form.html",
            auth_or_redirect,
            f"Edit User - {first_name} {last_name}",
            "Edit User",
            "users",
            context,
        )

    def users_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        user_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_user(db, user_id)
        if error:
            return RedirectResponse(
                url=f"/admin/users?{urlencode({'error': error})}",
                status_code=302,
            )
        return RedirectResponse(url="/admin/users?deleted=1", status_code=302)

    def roles_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.roles_context(db=db, search=search, status=status, page=page)
        return self._render_admin_template(
            request,
            "admin/roles.html",
            auth_or_redirect,
            "Roles",
            "Roles & Permissions",
            "roles",
            context,
        )

    def roles_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.role_form_context(db)
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/role_form.html",
            auth_or_redirect,
            "Create Role",
            "Create Role",
            "roles",
            context,
        )

    def roles_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        name: str,
        description: str,
        is_active: str,
        permissions: list[str],
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.create_role(
            db=db,
            name=name,
            description=description,
            is_active=is_active == "1",
            permission_ids=permissions,
        )

        if error:
            context = self.role_form_context(db)
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/role_form.html",
                auth_or_redirect,
                "Create Role",
                "Create Role",
                "roles",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/roles?created=1", status_code=302)

    def roles_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        role_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.role_profile_context(db, role_id)
        if not context.get("role"):
            raise HTTPException(status_code=404, detail="Role not found")
        title = f"Role Profile - {context['role']['name']}"
        return self._render_admin_template(
            request,
            "admin/role_profile.html",
            auth_or_redirect,
            title,
            "Role Profile",
            "roles",
            context,
        )

    def roles_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        role_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.role_form_context(db, role_id)
        if not context.get("role_data"):
            raise HTTPException(status_code=404, detail="Role not found")
        context.update({"error": None, "success": None})
        title = f"Edit Role - {context['role_data']['name']}"
        return self._render_admin_template(
            request,
            "admin/role_form.html",
            auth_or_redirect,
            title,
            "Edit Role",
            "roles",
            context,
        )

    def roles_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        role_id: str,
        name: str,
        description: str,
        is_active: str,
        permissions: list[str],
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.update_role(
            db=db,
            role_id=role_id,
            name=name,
            description=description,
            is_active=is_active == "1",
            permission_ids=permissions,
        )

        context = self.role_form_context(db, role_id)
        if error:
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/role_form.html",
                auth_or_redirect,
                f"Edit Role - {name}",
                "Edit Role",
                "roles",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "Role updated successfully"})
        return self._render_admin_template(
            request,
            "admin/role_form.html",
            auth_or_redirect,
            f"Edit Role - {name}",
            "Edit Role",
            "roles",
            context,
        )

    def roles_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        role_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_role(db, role_id)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return RedirectResponse(url="/admin/roles?deleted=1", status_code=302)

    def permissions_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.permissions_context(db=db, search=search, status=status, page=page)
        return self._render_admin_template(
            request,
            "admin/permissions.html",
            auth_or_redirect,
            "Permissions",
            "Permissions",
            "permissions",
            context,
        )

    def permissions_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.permission_form_context(db)
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/permission_form.html",
            auth_or_redirect,
            "Create Permission",
            "Create Permission",
            "permissions",
            context,
        )

    def permissions_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        key: str,
        description: str,
        is_active: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        if not key:
            context = self.permission_form_context(db)
            context.update(
                {
                    "error": "Permission key is required.",
                    "success": None,
                    "permission_data": {
                        "key": key,
                        "description": description,
                        "is_active": is_active == "1",
                    },
                }
            )
            return self._render_admin_template(
                request,
                "admin/permission_form.html",
                auth_or_redirect,
                "Create Permission",
                "Create Permission",
                "permissions",
                context,
                status_code=400,
            )
        _, error = self.create_permission(
            db=db,
            key=key,
            description=description,
            is_active=is_active == "1",
        )

        if error:
            context = self.permission_form_context(db)
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/permission_form.html",
                auth_or_redirect,
                "Create Permission",
                "Create Permission",
                "permissions",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/permissions?created=1", status_code=302)

    def permissions_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        permission_id: str,
    ) -> HTMLResponse | RedirectResponse:
        return self.permissions_edit_response(request, db, auth, permission_id)

    def permissions_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        permission_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.permission_form_context(db, permission_id)
        if not context.get("permission_data"):
            raise HTTPException(status_code=404, detail="Permission not found")
        context.update({"error": None, "success": None})
        title = f"Edit Permission - {context['permission_data']['key']}"
        return self._render_admin_template(
            request,
            "admin/permission_form.html",
            auth_or_redirect,
            title,
            "Edit Permission",
            "permissions",
            context,
        )

    def permissions_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        permission_id: str,
        key: str,
        description: str,
        is_active: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        if not key:
            context = self.permission_form_context(db, permission_id)
            context.update(
                {
                    "error": "Permission key is required.",
                    "success": None,
                    "permission_data": {
                        "id": permission_id,
                        "key": key,
                        "description": description,
                        "is_active": is_active == "1",
                    },
                }
            )
            return self._render_admin_template(
                request,
                "admin/permission_form.html",
                auth_or_redirect,
                "Edit Permission",
                "Edit Permission",
                "permissions",
                context,
                status_code=400,
            )
        _, error = self.update_permission(
            db=db,
            permission_id=permission_id,
            key=key,
            description=description,
            is_active=is_active == "1",
        )

        context = self.permission_form_context(db, permission_id)
        if error:
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/permission_form.html",
                auth_or_redirect,
                f"Edit Permission - {key}",
                "Edit Permission",
                "permissions",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "Permission updated successfully"})
        return self._render_admin_template(
            request,
            "admin/permission_form.html",
            auth_or_redirect,
            f"Edit Permission - {key}",
            "Edit Permission",
            "permissions",
            context,
        )

    def permissions_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        permission_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_permission(db, permission_id)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return RedirectResponse(url="/admin/permissions?deleted=1", status_code=302)

    def organizations_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.organizations_context(db=db, search=search, status=status, page=page)
        return self._render_admin_template(
            request,
            "admin/organizations.html",
            auth_or_redirect,
            "Organizations",
            "Organizations",
            "organizations",
            context,
        )

    def organizations_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.organization_form_context(
            db,
            default_currency_org_id=str(auth_or_redirect.organization_id)
            if auth_or_redirect.organization_id
            else None,
        )
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/organization_form.html",
            auth_or_redirect,
            "Create Organization",
            "Create Organization",
            "organizations",
            context,
        )

    def organizations_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str,
        registration_number: str,
        tax_identification_number: str,
        incorporation_date: str,
        jurisdiction_country_code: str,
        parent_organization_id: str,
        consolidation_method: str,
        ownership_percentage: str,
        is_active: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.create_organization(
            db=db,
            organization_code=organization_code,
            legal_name=legal_name,
            functional_currency_code=functional_currency_code,
            presentation_currency_code=presentation_currency_code,
            fiscal_year_end_month=fiscal_year_end_month,
            fiscal_year_end_day=fiscal_year_end_day,
            trading_name=trading_name or "",
            registration_number=registration_number or "",
            tax_identification_number=tax_identification_number or "",
            incorporation_date=incorporation_date or "",
            jurisdiction_country_code=jurisdiction_country_code or "",
            parent_organization_id=parent_organization_id or "",
            consolidation_method=consolidation_method or "",
            ownership_percentage=ownership_percentage or "",
            is_active=is_active == "1",
        )

        if error:
            context = self.organization_form_context(
                db,
                default_currency_org_id=str(auth_or_redirect.organization_id)
                if auth_or_redirect.organization_id
                else None,
            )
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/organization_form.html",
                auth_or_redirect,
                "Create Organization",
                "Create Organization",
                "organizations",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/organizations?created=1", status_code=302)

    def organizations_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        org_id: str,
    ) -> HTMLResponse | RedirectResponse:
        return self.organizations_edit_response(request, db, auth, org_id)

    def organizations_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        org_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.organization_form_context(db, org_id)
        if not context.get("organization_data"):
            raise HTTPException(status_code=404, detail="Organization not found")
        context.update({"error": None, "success": None})
        title = f"Edit Organization - {context['organization_data']['legal_name']}"
        return self._render_admin_template(
            request,
            "admin/organization_form.html",
            auth_or_redirect,
            title,
            "Edit Organization",
            "organizations",
            context,
        )

    def organizations_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        org_id: str,
        organization_code: str,
        legal_name: str,
        functional_currency_code: str,
        presentation_currency_code: str,
        fiscal_year_end_month: int,
        fiscal_year_end_day: int,
        trading_name: str,
        registration_number: str,
        tax_identification_number: str,
        incorporation_date: str,
        jurisdiction_country_code: str,
        parent_organization_id: str,
        consolidation_method: str,
        ownership_percentage: str,
        is_active: str,
        salaries_expense_account_id: str = "",
        salary_payable_account_id: str = "",
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.update_organization(
            db=db,
            organization_id=org_id,
            organization_code=organization_code,
            legal_name=legal_name,
            functional_currency_code=functional_currency_code,
            presentation_currency_code=presentation_currency_code,
            fiscal_year_end_month=fiscal_year_end_month,
            fiscal_year_end_day=fiscal_year_end_day,
            trading_name=trading_name or "",
            registration_number=registration_number or "",
            tax_identification_number=tax_identification_number or "",
            incorporation_date=incorporation_date or "",
            jurisdiction_country_code=jurisdiction_country_code or "",
            parent_organization_id=parent_organization_id or "",
            consolidation_method=consolidation_method or "",
            ownership_percentage=ownership_percentage or "",
            is_active=is_active == "1",
            salaries_expense_account_id=salaries_expense_account_id or "",
            salary_payable_account_id=salary_payable_account_id or "",
        )

        context = self.organization_form_context(db, org_id)
        if error:
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/organization_form.html",
                auth_or_redirect,
                f"Edit Organization - {legal_name}",
                "Edit Organization",
                "organizations",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "Organization updated successfully"})
        return self._render_admin_template(
            request,
            "admin/organization_form.html",
            auth_or_redirect,
            f"Edit Organization - {legal_name}",
            "Edit Organization",
            "organizations",
            context,
        )

    def organizations_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        org_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_organization(db, org_id)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return RedirectResponse(url="/admin/organizations?deleted=1", status_code=302)

    def settings_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
        domain: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.settings_context(db=db, search=search, domain=domain, status=status, page=page)
        return self._render_admin_template(
            request,
            "admin/settings.html",
            auth_or_redirect,
            "Settings",
            "System Settings",
            "settings",
            context,
        )

    def settings_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.setting_form_context(db)
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/setting_form.html",
            auth_or_redirect,
            "Create Setting",
            "Create Setting",
            "settings",
            context,
        )

    def settings_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        domain: str,
        key: str,
        value_type: str,
        value: str,
        is_secret: str,
        is_active: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.create_setting(
            db=db,
            domain=domain,
            key=key,
            value_type=value_type,
            value=value,
            is_secret=is_secret == "1",
            is_active=is_active == "1",
        )

        if error:
            context = self.setting_form_context(db)
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/setting_form.html",
                auth_or_redirect,
                "Create Setting",
                "Create Setting",
                "settings",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/settings?created=1", status_code=302)

    def settings_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        setting_id: str,
    ) -> HTMLResponse | RedirectResponse:
        return self.settings_edit_response(request, db, auth, setting_id)

    def settings_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        setting_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.setting_form_context(db, setting_id)
        if not context.get("setting_data"):
            raise HTTPException(status_code=404, detail="Setting not found")
        context.update({"error": None, "success": None})
        title = f"Edit Setting - {context['setting_data']['key']}"
        return self._render_admin_template(
            request,
            "admin/setting_form.html",
            auth_or_redirect,
            title,
            "Edit Setting",
            "settings",
            context,
        )

    def settings_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        setting_id: str,
        domain: str,
        key: str,
        value_type: str,
        value: str,
        is_secret: str,
        is_active: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.update_setting(
            db=db,
            setting_id=setting_id,
            domain=domain,
            key=key,
            value_type=value_type,
            value=value,
            is_secret=is_secret == "1",
            is_active=is_active == "1",
        )

        context = self.setting_form_context(db, setting_id)
        if error:
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/setting_form.html",
                auth_or_redirect,
                f"Edit Setting - {key}",
                "Edit Setting",
                "settings",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "Setting updated successfully"})
        return self._render_admin_template(
            request,
            "admin/setting_form.html",
            auth_or_redirect,
            f"Edit Setting - {key}",
            "Edit Setting",
            "settings",
            context,
        )

    def settings_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        setting_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_setting(db, setting_id)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return RedirectResponse(url="/admin/settings?deleted=1", status_code=302)

    def audit_logs_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
        actor_type: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.audit_logs_context(
            db=db,
            search=search,
            actor_type=actor_type,
            status=status,
            page=page,
        )
        return self._render_admin_template(
            request,
            "admin/audit_logs.html",
            auth_or_redirect,
            "Audit Logs",
            "Audit Logs",
            "audit",
            context,
        )

    def tasks_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        page: int,
        search: str,
        status: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.tasks_context(db=db, search=search, status=status, page=page)
        return self._render_admin_template(
            request,
            "admin/tasks.html",
            auth_or_redirect,
            "Scheduled Tasks",
            "Scheduled Tasks",
            "tasks",
            context,
        )

    def tasks_new_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.task_form_context(db)
        context.update({"error": None, "success": None})
        return self._render_admin_template(
            request,
            "admin/task_form.html",
            auth_or_redirect,
            "Create Task",
            "Create Task",
            "tasks",
            context,
        )

    def tasks_create_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        name: str,
        task_name: str,
        schedule_type: str,
        interval_seconds: int,
        args_json: str,
        kwargs_json: str,
        enabled: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.create_task(
            db=db,
            name=name,
            task_name=task_name,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            args_json=args_json,
            kwargs_json=kwargs_json,
            enabled=enabled == "1",
        )

        if error:
            context = self.task_form_context(db)
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/task_form.html",
                auth_or_redirect,
                "Create Task",
                "Create Task",
                "tasks",
                context,
                status_code=400,
            )

        return RedirectResponse(url="/admin/tasks?created=1", status_code=302)

    def tasks_view_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        task_id: str,
    ) -> HTMLResponse | RedirectResponse:
        return self.tasks_edit_response(request, db, auth, task_id)

    def tasks_edit_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        task_id: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        context = self.task_form_context(db, task_id)
        if not context.get("task_data"):
            raise HTTPException(status_code=404, detail="Task not found")
        context.update({"error": None, "success": None})
        title = f"Edit Task - {context['task_data']['name']}"
        return self._render_admin_template(
            request,
            "admin/task_form.html",
            auth_or_redirect,
            title,
            "Edit Task",
            "tasks",
            context,
        )

    def tasks_update_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        task_id: str,
        name: str,
        task_name: str,
        schedule_type: str,
        interval_seconds: int,
        args_json: str,
        kwargs_json: str,
        enabled: str,
    ) -> HTMLResponse | RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        _, error = self.update_task(
            db=db,
            task_id=task_id,
            name=name,
            task_name=task_name,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            args_json=args_json,
            kwargs_json=kwargs_json,
            enabled=enabled == "1",
        )

        context = self.task_form_context(db, task_id)
        if error:
            context.update({"error": error, "success": None})
            return self._render_admin_template(
                request,
                "admin/task_form.html",
                auth_or_redirect,
                f"Edit Task - {name}",
                "Edit Task",
                "tasks",
                context,
                status_code=400,
            )

        context.update({"error": None, "success": "Task updated successfully"})
        return self._render_admin_template(
            request,
            "admin/task_form.html",
            auth_or_redirect,
            f"Edit Task - {name}",
            "Edit Task",
            "tasks",
            context,
        )

    def tasks_delete_response(
        self,
        request: Request,
        db: Session,
        auth: WebAuthContext,
        task_id: str,
    ) -> RedirectResponse:
        auth_or_redirect = self._require_admin_web_auth(request, auth)
        if isinstance(auth_or_redirect, RedirectResponse):
            return auth_or_redirect
        error = self.delete_task(db, task_id)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return RedirectResponse(url="/admin/tasks?deleted=1", status_code=302)


admin_web_service = AdminWebService()
