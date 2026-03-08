"""HR Onboarding Admin Web Service.

Handles web routes for HR administrators to manage:
- Checklist templates
- Active onboardings across all employees
- Onboarding dashboard/metrics
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.people.hr.checklist_template import (
    AssigneeRole,
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateType,
    OnboardingCategory,
)
from app.models.people.hr.lifecycle import (
    BoardingStatus,
    EmployeeOnboarding,
    EmployeeOnboardingActivity,
)
from app.services.common import ValidationError, coerce_uuid
from app.services.people.hr.onboarding import OnboardingService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class OnboardingAdminWebService:
    """Web service for HR onboarding admin routes."""

    # ─────────────────────────────────────────────────────────────────────────
    # Dashboard
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def dashboard_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render onboarding dashboard with metrics."""
        org_id = coerce_uuid(auth.organization_id)

        # Get active onboardings count
        active_count = (
            db.scalar(
                select(func.count(EmployeeOnboarding.onboarding_id)).where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboarding.status.in_(
                        [
                            BoardingStatus.PENDING,
                            BoardingStatus.IN_PROGRESS,
                        ]
                    ),
                )
            )
            or 0
        )

        # Get overdue activities count
        today = date.today()
        overdue_count = (
            db.scalar(
                select(func.count(EmployeeOnboardingActivity.activity_id))
                .join(EmployeeOnboarding)
                .where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboarding.status.in_(
                        [
                            BoardingStatus.PENDING,
                            BoardingStatus.IN_PROGRESS,
                        ]
                    ),
                    EmployeeOnboardingActivity.activity_status == "PENDING",
                    EmployeeOnboardingActivity.due_date < today,
                )
            )
            or 0
        )

        # Get completed this month
        first_of_month = today.replace(day=1)
        completed_this_month = (
            db.scalar(
                select(func.count(EmployeeOnboarding.onboarding_id)).where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboarding.status == BoardingStatus.COMPLETED,
                    EmployeeOnboarding.actual_completion_date >= first_of_month,
                )
            )
            or 0
        )

        # Get recent completions (last 5)
        recent_completions = list(
            db.scalars(
                select(EmployeeOnboarding)
                .options(joinedload(EmployeeOnboarding.employee))
                .where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboarding.status == BoardingStatus.COMPLETED,
                )
                .order_by(EmployeeOnboarding.actual_completion_date.desc())
                .limit(5)
            ).all()
        )

        # Get upcoming start dates (next 7 days)
        next_week = today + timedelta(days=7)
        upcoming_starts = list(
            db.scalars(
                select(EmployeeOnboarding)
                .options(joinedload(EmployeeOnboarding.employee))
                .where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboarding.status.in_(
                        [
                            BoardingStatus.PENDING,
                            BoardingStatus.IN_PROGRESS,
                        ]
                    ),
                    EmployeeOnboarding.date_of_joining >= today,
                    EmployeeOnboarding.date_of_joining <= next_week,
                )
                .order_by(EmployeeOnboarding.date_of_joining)
                .limit(10)
            ).all()
        )

        # Template count
        template_count = (
            db.scalar(
                select(func.count(ChecklistTemplate.template_id)).where(
                    ChecklistTemplate.organization_id == org_id,
                    ChecklistTemplate.is_active == True,
                )
            )
            or 0
        )

        context = base_context(
            request, auth, "Onboarding Dashboard", "onboarding", db=db
        )
        context.update(
            {
                "active_count": active_count,
                "overdue_count": overdue_count,
                "completed_this_month": completed_this_month,
                "recent_completions": recent_completions,
                "upcoming_starts": upcoming_starts,
                "template_count": template_count,
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/dashboard.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Templates List
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def templates_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page: int = 1,
    ) -> HTMLResponse:
        """Render checklist templates list."""
        from sqlalchemy import func as sa_func

        org_id = coerce_uuid(auth.organization_id)
        per_page = 50

        base_where = ChecklistTemplate.organization_id == org_id
        total_count = (
            db.scalar(
                select(sa_func.count()).select_from(ChecklistTemplate).where(base_where)
            )
            or 0
        )
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        stmt = (
            select(ChecklistTemplate)
            .options(selectinload(ChecklistTemplate.items))
            .where(base_where)
            .order_by(ChecklistTemplate.template_type, ChecklistTemplate.template_name)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        template_list = list(db.scalars(stmt).all())

        # Count items per template
        item_counts = {}
        for tpl in template_list:
            item_counts[tpl.template_id] = len(tpl.items) if tpl.items else 0

        context = base_context(
            request, auth, "Onboarding Templates", "onboarding", db=db
        )
        context.update(
            {
                "templates": template_list,
                "item_counts": item_counts,
                "template_types": ChecklistTemplateType,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/templates.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Template Form (Create/Edit)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def template_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: UUID | None = None,
    ) -> HTMLResponse:
        """Render template create/edit form."""
        org_id = coerce_uuid(auth.organization_id)

        template = None
        title = "New Checklist Template"
        if template_id:
            template = db.get(ChecklistTemplate, template_id)
            if template and template.organization_id != org_id:
                template = None
            if template:
                title = f"Edit: {template.template_name}"

        context = base_context(request, auth, title, "onboarding", db=db)
        context.update(
            {
                "template": template,
                "template_types": list(ChecklistTemplateType),
                "categories": list(OnboardingCategory),
                "assignee_roles": list(AssigneeRole),
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/template_form.html", context
        )

    @staticmethod
    async def save_template_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: UUID | None = None,
    ) -> RedirectResponse:
        """Save checklist template."""
        org_id = coerce_uuid(auth.organization_id)
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        # Parse form data
        template_code = str(form.get("template_code", "")).strip()
        template_name = str(form.get("template_name", "")).strip()
        description = str(form.get("description", "")).strip() or None
        template_type = str(form.get("template_type", "ONBOARDING"))
        is_active = str(form.get("is_active", "")).lower() in ("1", "true", "on")

        if not template_code or not template_name:
            raise ValidationError("Template code and name are required")

        if template_id:
            # Update existing
            template = db.get(ChecklistTemplate, template_id)
            if not template or template.organization_id != org_id:
                raise ValidationError("Template not found")
            template.template_code = template_code
            template.template_name = template_name
            template.description = description
            template.template_type = ChecklistTemplateType(template_type)
            template.is_active = is_active
            logger.info("Updated checklist template %s", template_id)
        else:
            # Create new
            template = ChecklistTemplate(
                organization_id=org_id,
                template_code=template_code,
                template_name=template_name,
                description=description,
                template_type=ChecklistTemplateType(template_type),
                is_active=is_active,
            )
            db.add(template)
            db.flush()
            logger.info("Created checklist template %s", template.template_id)

        db.commit()
        return RedirectResponse(
            url=f"/people/hr/onboarding/templates/{template.template_id}?saved=1",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Template Detail (with items)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def template_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: UUID,
    ) -> HTMLResponse:
        """Render template detail with items list."""
        org_id = coerce_uuid(auth.organization_id)

        template = db.get(ChecklistTemplate, template_id)
        if not template or template.organization_id != org_id:
            raise ValidationError("Template not found")

        # Sort items by sequence
        items = sorted(template.items, key=lambda x: x.sequence)

        context = base_context(
            request, auth, template.template_name, "onboarding", db=db
        )
        context.update(
            {
                "template": template,
                "items": items,
                "categories": list(OnboardingCategory),
                "assignee_roles": list(AssigneeRole),
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/template_detail.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Template Item Management
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def add_template_item_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: UUID,
    ) -> RedirectResponse:
        """Add item to checklist template."""
        org_id = coerce_uuid(auth.organization_id)
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        template = db.get(ChecklistTemplate, template_id)
        if not template or template.organization_id != org_id:
            raise ValidationError("Template not found")

        item_name = str(form.get("item_name", "")).strip()
        if not item_name:
            raise ValidationError("Item name is required")

        # Parse optional fields
        category = str(form.get("category", "")).strip() or None
        assignee_role = str(form.get("default_assignee_role", "")).strip() or None
        days_from_start_val = str(form.get("days_from_start", "0")).strip() or "0"
        days_from_start = int(days_from_start_val)
        requires_document = str(form.get("requires_document", "")).lower() in (
            "1",
            "true",
            "on",
        )
        document_type = str(form.get("document_type", "")).strip() or None
        instructions = str(form.get("instructions", "")).strip() or None
        is_required = str(form.get("is_required", "1")).lower() in ("1", "true", "on")

        # Get next sequence
        max_seq = (
            db.scalar(
                select(func.max(ChecklistTemplateItem.sequence)).where(
                    ChecklistTemplateItem.template_id == template_id
                )
            )
            or 0
        )

        item = ChecklistTemplateItem(
            template_id=template_id,
            item_name=item_name,
            sequence=max_seq + 1,
            is_required=is_required,
            category=category,
            default_assignee_role=assignee_role,
            days_from_start=days_from_start,
            requires_document=requires_document,
            document_type=document_type if requires_document else None,
            instructions=instructions,
        )
        db.add(item)
        db.commit()

        logger.info("Added item to template %s: %s", template_id, item_name)

        return RedirectResponse(
            url=f"/people/hr/onboarding/templates/{template_id}?saved=1",
            status_code=303,
        )

    @staticmethod
    async def delete_template_item_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: UUID,
        item_id: UUID,
    ) -> RedirectResponse:
        """Delete item from checklist template."""
        org_id = coerce_uuid(auth.organization_id)

        template = db.get(ChecklistTemplate, template_id)
        if not template or template.organization_id != org_id:
            raise ValidationError("Template not found")

        item = db.get(ChecklistTemplateItem, item_id)
        if not item or item.template_id != template_id:
            raise ValidationError("Item not found")

        db.delete(item)
        db.commit()

        logger.info("Deleted item %s from template %s", item_id, template_id)

        return RedirectResponse(
            url=f"/people/hr/onboarding/templates/{template_id}?saved=1",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Active Onboardings List
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def employees_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status_filter: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render list of all onboardings."""
        from sqlalchemy import func as sa_func

        org_id = coerce_uuid(auth.organization_id)
        per_page = 50

        base_stmt = select(EmployeeOnboarding).where(
            EmployeeOnboarding.organization_id == org_id
        )

        if status_filter:
            try:
                status_enum = BoardingStatus(status_filter)
                base_stmt = base_stmt.where(EmployeeOnboarding.status == status_enum)
            except ValueError:
                pass  # Ignore invalid status filter

        total_count = (
            db.scalar(select(sa_func.count()).select_from(base_stmt.subquery())) or 0
        )
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        stmt = (
            base_stmt.options(
                joinedload(EmployeeOnboarding.employee),
                selectinload(EmployeeOnboarding.activities),
            )
            .order_by(
                EmployeeOnboarding.status,
                EmployeeOnboarding.date_of_joining.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )

        onboardings = list(db.scalars(stmt).all())

        # Calculate progress for each — template expects dict with percentage/completed/total
        progress_map: dict[Any, dict[str, int]] = {}
        for ob in onboardings:
            total = len(ob.activities) if ob.activities else 0
            completed = (
                sum(
                    1
                    for a in ob.activities
                    if getattr(a, "activity_status", "") in ("COMPLETED", "SKIPPED")
                    or getattr(a, "status", "") in ("completed", "skipped")
                )
                if ob.activities
                else 0
            )
            percentage = int((completed / total) * 100) if total else 0
            progress_map[ob.onboarding_id] = {
                "percentage": percentage,
                "completed": completed,
                "total": total,
            }

        context = base_context(
            request, auth, "Employee Onboardings", "onboarding", db=db
        )
        context.update(
            {
                "onboardings": onboardings,
                "progress_map": progress_map,
                "status_filter": status_filter,
                "statuses": list(BoardingStatus),
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/employees.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Employee Onboarding Detail
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def employee_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        onboarding_id: UUID,
    ) -> HTMLResponse:
        """Render onboarding detail for an employee."""
        org_id = coerce_uuid(auth.organization_id)

        onboarding = db.scalar(
            select(EmployeeOnboarding)
            .options(
                joinedload(EmployeeOnboarding.employee),
                joinedload(EmployeeOnboarding.activities),
            )
            .where(
                EmployeeOnboarding.onboarding_id == onboarding_id,
                EmployeeOnboarding.organization_id == org_id,
            )
        )

        if not onboarding:
            raise ValidationError("Onboarding not found")

        # Sort activities by category and sequence
        activities = sorted(
            onboarding.activities,
            key=lambda a: (a.category or "ZZZZZ", a.sequence or 0),
        )

        # Group by category
        categories: dict[str, list[Any]] = {}
        for activity in activities:
            cat = activity.category or "GENERAL"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(activity)

        # Calculate progress
        svc = OnboardingService(db)
        progress = svc.calculate_progress(onboarding)

        context = base_context(
            request, auth, "Onboarding Progress", "onboarding", db=db
        )
        context.update(
            {
                "onboarding": onboarding,
                "employee": onboarding.employee,
                "activities": activities,
                "categories": categories,
                "progress": progress,
                "category_labels": {
                    "PRE_BOARDING": "Pre-Boarding",
                    "DAY_ONE": "Day One",
                    "FIRST_WEEK": "First Week",
                    "FIRST_MONTH": "First Month",
                    "ONGOING": "Ongoing",
                    "GENERAL": "General",
                },
            }
        )

        return templates.TemplateResponse(
            request, "people/onboarding/admin/employee_detail.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Admin Task Actions
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def toggle_activity_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        onboarding_id: UUID,
        activity_id: UUID,
    ) -> RedirectResponse:
        """Toggle activity completion status (admin)."""
        org_id = coerce_uuid(auth.organization_id)

        onboarding = db.get(EmployeeOnboarding, onboarding_id)
        if not onboarding or onboarding.organization_id != org_id:
            raise ValidationError("Onboarding not found")

        svc = OnboardingService(db)

        activity = db.get(EmployeeOnboardingActivity, activity_id)
        if not activity or activity.onboarding_id != onboarding_id:
            raise ValidationError("Activity not found")

        if activity.activity_status in ("COMPLETED", "SKIPPED"):
            # Reopen the activity
            activity.activity_status = "PENDING"
            activity.completed_on = None
            activity.completed_by = None
            logger.info("Reopened activity %s", activity_id)
        else:
            # Complete the activity
            svc.complete_activity(
                org_id=org_id,
                activity_id=activity_id,
                completed_by=coerce_uuid(auth.person_id),
                completion_notes="Completed by HR",
            )

        db.commit()

        return RedirectResponse(
            url=f"/people/hr/onboarding/employees/{onboarding_id}?saved=1",
            status_code=303,
        )


# Module-level singleton
onboarding_admin_web_service = OnboardingAdminWebService()
