"""Checklist template service."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.checklist_template import (
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateType,
)
from app.services.common import PaginatedResult, PaginationParams

__all__ = ["ChecklistTemplateService"]


class ChecklistTemplateService:
    """Service for managing checklist templates."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_templates(
        self,
        org_id: UUID,
        *,
        template_type: Optional[ChecklistTemplateType] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[ChecklistTemplate]:
        query = select(ChecklistTemplate).where(ChecklistTemplate.organization_id == org_id)

        if template_type:
            query = query.where(ChecklistTemplate.template_type == template_type)

        if is_active is not None:
            query = query.where(ChecklistTemplate.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                ChecklistTemplate.template_name.ilike(search_term)
                | ChecklistTemplate.template_code.ilike(search_term)
            )

        query = query.options(joinedload(ChecklistTemplate.items))
        query = query.order_by(ChecklistTemplate.template_name)

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_template(self, org_id: UUID, template_id: UUID) -> ChecklistTemplate:
        template = self.db.scalar(
            select(ChecklistTemplate)
            .options(joinedload(ChecklistTemplate.items))
            .where(
                ChecklistTemplate.organization_id == org_id,
                ChecklistTemplate.template_id == template_id,
            )
        )
        if not template:
            raise ValueError("Checklist template not found")
        return template

    def create_template(
        self,
        org_id: UUID,
        *,
        template_code: str,
        template_name: str,
        description: Optional[str],
        template_type: ChecklistTemplateType,
        is_active: bool,
        items: Optional[list[dict]],
    ) -> ChecklistTemplate:
        template = ChecklistTemplate(
            organization_id=org_id,
            template_code=template_code,
            template_name=template_name,
            description=description,
            template_type=template_type,
            is_active=is_active,
        )
        self.db.add(template)
        self.db.flush()

        if items:
            for idx, item in enumerate(items):
                self.db.add(
                    ChecklistTemplateItem(
                        template_id=template.template_id,
                        item_name=item["item_name"],
                        is_required=item.get("is_required", True),
                        sequence=item.get("sequence", idx),
                    )
                )
        self.db.flush()
        return template

    def update_template(
        self,
        org_id: UUID,
        template_id: UUID,
        **kwargs,
    ) -> ChecklistTemplate:
        items = kwargs.pop("items", None)
        template = self.get_template(org_id, template_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(template, key):
                setattr(template, key, value)

        if items is not None:
            self.db.query(ChecklistTemplateItem).filter(
                ChecklistTemplateItem.template_id == template_id
            ).delete()
            for idx, item in enumerate(items):
                self.db.add(
                    ChecklistTemplateItem(
                        template_id=template_id,
                        item_name=item["item_name"],
                        is_required=item.get("is_required", True),
                        sequence=item.get("sequence", idx),
                    )
                )
        self.db.flush()
        return template

    def delete_template(self, org_id: UUID, template_id: UUID) -> None:
        template = self.get_template(org_id, template_id)
        self.db.query(ChecklistTemplateItem).filter(
            ChecklistTemplateItem.template_id == template_id
        ).delete()
        self.db.delete(template)
        self.db.flush()
