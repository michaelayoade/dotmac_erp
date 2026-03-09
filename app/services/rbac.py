from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.schemas.rbac import (
    PermissionCreate,
    PermissionUpdate,
    PersonRoleCreate,
    PersonRoleUpdate,
    RoleCreate,
    RolePermissionCreate,
    RolePermissionUpdate,
    RoleUpdate,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

if TYPE_CHECKING:
    from app.models.rbac import (
        Permission as PermissionModel,
        PersonRole as PersonRoleModel,
        Role as RoleModel,
        RolePermission as RolePermissionModel,
    )
else:
    PermissionModel = PersonRoleModel = RoleModel = RolePermissionModel = Any

logger = logging.getLogger(__name__)


def _rbac_models() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    """Resolve RBAC models lazily to avoid stale mocked globals between tests."""
    module = import_module("app.models.rbac")
    return (
        module.Permission,
        module.PersonRole,
        module.Role,
        module.RolePermission,
    )


def _apply_ordering(
    query: Any, order_by: str, order_dir: str, allowed_columns: dict[str, Any]
) -> Any:
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query: Any, limit: int, offset: int) -> Any:
    return query.limit(limit).offset(offset)


class Roles(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: RoleCreate) -> RoleModel:
        _, _, Role, _ = _rbac_models()
        role = Role(**payload.model_dump())
        db.add(role)
        db.commit()
        db.refresh(role)
        return cast(RoleModel, role)

    @staticmethod
    def get(db: Session, role_id: str) -> RoleModel:
        _, _, Role, _ = _rbac_models()
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        return cast(RoleModel, role)

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[RoleModel]:
        _, _, Role, _ = _rbac_models()
        query = select(Role)
        if is_active is None:
            query = query.where(Role.is_active.is_(True))
        else:
            query = query.where(Role.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Role.created_at, "name": Role.name},
        )
        return list(db.scalars(_apply_pagination(query, limit, offset)).all())

    @staticmethod
    def update(db: Session, role_id: str, payload: RoleUpdate) -> RoleModel:
        _, _, Role, _ = _rbac_models()
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(role, key, value)
        db.commit()
        db.refresh(role)
        return cast(RoleModel, role)

    @staticmethod
    def delete(db: Session, role_id: str) -> None:
        _, _, Role, _ = _rbac_models()
        role = db.get(Role, coerce_uuid(role_id))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        role.is_active = False
        db.commit()


class Permissions(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PermissionCreate) -> PermissionModel:
        Permission, _, _, _ = _rbac_models()
        permission = Permission(**payload.model_dump())
        db.add(permission)
        db.commit()
        db.refresh(permission)
        return cast(PermissionModel, permission)

    @staticmethod
    def get(db: Session, permission_id: str) -> PermissionModel:
        Permission, _, _, _ = _rbac_models()
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")
        return cast(PermissionModel, permission)

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[PermissionModel]:
        Permission, _, _, _ = _rbac_models()
        query = select(Permission)
        if is_active is None:
            query = query.where(Permission.is_active.is_(True))
        else:
            query = query.where(Permission.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Permission.created_at, "key": Permission.key},
        )
        return list(db.scalars(_apply_pagination(query, limit, offset)).all())

    @staticmethod
    def update(
        db: Session, permission_id: str, payload: PermissionUpdate
    ) -> PermissionModel:
        Permission, _, _, _ = _rbac_models()
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(permission, key, value)
        db.commit()
        db.refresh(permission)
        return cast(PermissionModel, permission)

    @staticmethod
    def delete(db: Session, permission_id: str) -> None:
        Permission, _, _, _ = _rbac_models()
        permission = db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")
        permission.is_active = False
        db.commit()


class RolePermissions(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: RolePermissionCreate) -> RolePermissionModel:
        Permission, _, Role, RolePermission = _rbac_models()
        role = db.get(Role, coerce_uuid(payload.role_id))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        permission = db.get(Permission, coerce_uuid(payload.permission_id))
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")
        link = RolePermission(**payload.model_dump())
        db.add(link)
        db.commit()
        db.refresh(link)
        return cast(RolePermissionModel, link)

    @staticmethod
    def get(db: Session, link_id: str) -> RolePermissionModel:
        _, _, _, RolePermission = _rbac_models()
        link = db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Role permission not found")
        return cast(RolePermissionModel, link)

    @staticmethod
    def list(
        db: Session,
        role_id: str | None,
        permission_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[RolePermissionModel]:
        _, _, _, RolePermission = _rbac_models()
        query = select(RolePermission)
        if role_id:
            query = query.where(RolePermission.role_id == coerce_uuid(role_id))
        if permission_id:
            query = query.where(
                RolePermission.permission_id == coerce_uuid(permission_id)
            )
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"role_id": RolePermission.role_id},
        )
        return list(db.scalars(_apply_pagination(query, limit, offset)).all())

    @staticmethod
    def update(
        db: Session, link_id: str, payload: RolePermissionUpdate
    ) -> RolePermissionModel:
        Permission, _, Role, RolePermission = _rbac_models()
        link = db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Role permission not found")
        data = payload.model_dump(exclude_unset=True)
        if "role_id" in data:
            role = db.get(Role, data["role_id"])
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")
        if "permission_id" in data:
            permission = db.get(Permission, data["permission_id"])
            if not permission:
                raise HTTPException(status_code=404, detail="Permission not found")
        for key, value in data.items():
            setattr(link, key, value)
        db.commit()
        db.refresh(link)
        return cast(RolePermissionModel, link)

    @staticmethod
    def delete(db: Session, link_id: str) -> None:
        _, _, _, RolePermission = _rbac_models()
        link = db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Role permission not found")
        db.delete(link)
        db.commit()


def get_users_with_permission(
    db: Session,
    organization_id: UUID,
    permission_key: str,
) -> list[PersonRoleModel]:
    """
    Get users in an organization with a given permission.

    Returns PersonRole records (use .person_id for recipient ids).
    """
    Permission, PersonRole, Role, RolePermission = _rbac_models()
    org_id = coerce_uuid(organization_id)
    stmt = (
        select(PersonRole)
        .join(Role, PersonRole.role_id == Role.id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, RolePermission.permission_id == Permission.id)
        .join(Person, PersonRole.person_id == Person.id)
        .where(
            Person.organization_id == org_id,
            Permission.key == permission_key,
            Role.is_active.is_(True),
            Permission.is_active.is_(True),
            Person.is_active.is_(True),
        )
    )
    return list(db.scalars(stmt).all())


class PersonRoles(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PersonRoleCreate) -> PersonRoleModel:
        _, PersonRole, Role, _ = _rbac_models()
        person = db.get(Person, coerce_uuid(payload.person_id))
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        role = db.get(Role, coerce_uuid(payload.role_id))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        link = PersonRole(**payload.model_dump())
        db.add(link)
        db.commit()
        db.refresh(link)
        return cast(PersonRoleModel, link)

    @staticmethod
    def get(db: Session, link_id: str) -> PersonRoleModel:
        _, PersonRole, _, _ = _rbac_models()
        link = db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Person role not found")
        return cast(PersonRoleModel, link)

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        role_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[PersonRoleModel]:
        _, PersonRole, _, _ = _rbac_models()
        query = select(PersonRole)
        if person_id:
            query = query.where(PersonRole.person_id == coerce_uuid(person_id))
        if role_id:
            query = query.where(PersonRole.role_id == coerce_uuid(role_id))
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"assigned_at": PersonRole.assigned_at},
        )
        return list(db.scalars(_apply_pagination(query, limit, offset)).all())

    @staticmethod
    def update(
        db: Session, link_id: str, payload: PersonRoleUpdate
    ) -> PersonRoleModel:
        _, PersonRole, Role, _ = _rbac_models()
        link = db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Person role not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data:
            person = db.get(Person, data["person_id"])
            if not person:
                raise HTTPException(status_code=404, detail="Person not found")
        if "role_id" in data:
            role = db.get(Role, data["role_id"])
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")
        for key, value in data.items():
            setattr(link, key, value)
        db.commit()
        db.refresh(link)
        return cast(PersonRoleModel, link)

    @staticmethod
    def delete(db: Session, link_id: str) -> None:
        _, PersonRole, _, _ = _rbac_models()
        link = db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise HTTPException(status_code=404, detail="Person role not found")
        db.delete(link)
        db.commit()


roles = Roles()
permissions = Permissions()
role_permissions = RolePermissions()
person_roles = PersonRoles()
