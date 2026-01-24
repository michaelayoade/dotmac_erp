"""
Training web routes.

Lists training programs and events with full CRUD operations.
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.training import TrainingEventStatus, TrainingProgramStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, OrganizationService
from app.services.people.training import TrainingService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/training", tags=["people-training-web"])


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/programs", response_class=HTMLResponse)
def list_programs(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training programs list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = TrainingService(db)

    status_enum = None
    if status:
        try:
            status_enum = TrainingProgramStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_programs(
        org_id,
        search=search,
        status=status_enum,
        category=category,
        pagination=pagination,
    )

    context = base_context(request, auth, "Training Programs", "training", db=db)
    context["request"] = request
    context.update(
        {
            "programs": result.items,
            "search": search,
            "status": status,
            "category": category,
            "statuses": [s.value for s in TrainingProgramStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/training/programs.html", context)


@router.get("/programs/new", response_class=HTMLResponse)
def new_program_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New training program form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db)
    departments = org_svc.list_departments(org_id, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, "New Training Program", "training", db=db)
    context["request"] = request
    context.update({
        "program": None,
        "form_data": {},
        "departments": departments,
        "error": None,
    })
    return templates.TemplateResponse(request, "people/training/program_form.html", context)


@router.post("/programs/new", response_class=HTMLResponse)
def create_program(
    request: Request,
    program_code: str = Form(...),
    program_name: str = Form(...),
    training_type: str = Form("INTERNAL"),
    category: Optional[str] = Form(None),
    department_id: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    duration_hours: Optional[int] = Form(None),
    duration_days: Optional[int] = Form(None),
    cost_per_attendee: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    provider_name: Optional[str] = Form(None),
    provider_contact: Optional[str] = Form(None),
    objectives: Optional[str] = Form(None),
    prerequisites: Optional[str] = Form(None),
    syllabus: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new training program."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    form_data = {
        "program_code": program_code,
        "program_name": program_name,
        "training_type": training_type,
        "category": category,
        "department_id": department_id,
        "description": description,
        "duration_hours": duration_hours,
        "duration_days": duration_days,
        "cost_per_attendee": cost_per_attendee,
        "currency_code": currency_code,
        "provider_name": provider_name,
        "provider_contact": provider_contact,
        "objectives": objectives,
        "prerequisites": prerequisites,
        "syllabus": syllabus,
    }

    try:
        program = svc.create_program(
            org_id,
            program_code=program_code,
            program_name=program_name,
            training_type=training_type,
            category=category or None,
            department_id=coerce_uuid(department_id) if department_id else None,
            description=description or None,
            duration_hours=duration_hours,
            duration_days=duration_days,
            cost_per_attendee=Decimal(cost_per_attendee) if cost_per_attendee else None,
            currency_code=currency_code,
            provider_name=provider_name or None,
            provider_contact=provider_contact or None,
            objectives=objectives or None,
            prerequisites=prerequisites or None,
            syllabus=syllabus or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/training/programs/{program.program_id}?success=Program+created+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        departments = org_svc.list_departments(org_id, pagination=PaginationParams(limit=200)).items
        context = base_context(request, auth, "New Training Program", "training", db=db)
        context["request"] = request
        context.update({
            "program": None,
            "form_data": form_data,
            "departments": departments,
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/training/program_form.html", context)


@router.get("/programs/{program_id}", response_class=HTMLResponse)
def view_program(
    request: Request,
    program_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View training program detail."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        program = svc.get_program(org_id, coerce_uuid(program_id))
    except Exception:
        return RedirectResponse(url="/people/training/programs", status_code=303)

    events = svc.list_events(
        org_id,
        program_id=coerce_uuid(program_id),
        pagination=PaginationParams(limit=10),
    ).items

    context = base_context(request, auth, program.program_name, "training", db=db)
    context["request"] = request
    context.update({
        "program": program,
        "events": events,
        "error": None,
        "success": success,
    })
    return templates.TemplateResponse(request, "people/training/program_detail.html", context)


@router.get("/programs/{program_id}/edit", response_class=HTMLResponse)
def edit_program_form(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit training program form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    try:
        program = svc.get_program(org_id, coerce_uuid(program_id))
    except Exception:
        return RedirectResponse(url="/people/training/programs", status_code=303)

    departments = org_svc.list_departments(org_id, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, f"Edit {program.program_code}", "training", db=db)
    context["request"] = request
    context.update({
        "program": program,
        "form_data": {},
        "departments": departments,
        "error": None,
    })
    return templates.TemplateResponse(request, "people/training/program_form.html", context)


@router.post("/programs/{program_id}/edit", response_class=HTMLResponse)
def update_program(
    request: Request,
    program_id: str,
    program_code: str = Form(...),
    program_name: str = Form(...),
    training_type: str = Form("INTERNAL"),
    category: Optional[str] = Form(None),
    department_id: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    duration_hours: Optional[int] = Form(None),
    duration_days: Optional[int] = Form(None),
    cost_per_attendee: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    provider_name: Optional[str] = Form(None),
    provider_contact: Optional[str] = Form(None),
    objectives: Optional[str] = Form(None),
    prerequisites: Optional[str] = Form(None),
    syllabus: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a training program."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    try:
        svc.update_program(
            org_id,
            coerce_uuid(program_id),
            program_code=program_code,
            program_name=program_name,
            training_type=training_type,
            category=category or None,
            department_id=coerce_uuid(department_id) if department_id else None,
            description=description or None,
            duration_hours=duration_hours,
            duration_days=duration_days,
            cost_per_attendee=Decimal(cost_per_attendee) if cost_per_attendee else None,
            currency_code=currency_code,
            provider_name=provider_name or None,
            provider_contact=provider_contact or None,
            objectives=objectives or None,
            prerequisites=prerequisites or None,
            syllabus=syllabus or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/training/programs/{program_id}?success=Program+updated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        program = svc.get_program(org_id, coerce_uuid(program_id))
        departments = org_svc.list_departments(org_id, pagination=PaginationParams(limit=200)).items
        context = base_context(request, auth, f"Edit {program.program_code}", "training", db=db)
        context["request"] = request
        context.update({
            "program": program,
            "form_data": {},
            "departments": departments,
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/training/program_form.html", context)


@router.post("/programs/{program_id}/activate", response_class=HTMLResponse)
def activate_program(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a training program."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.activate_program(org_id, coerce_uuid(program_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/training/programs/{program_id}?success=Program+activated",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/programs/{program_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/programs/{program_id}/retire", response_class=HTMLResponse)
def retire_program(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Retire a training program."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.retire_program(org_id, coerce_uuid(program_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/training/programs/{program_id}?success=Program+retired",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/programs/{program_id}?error={str(e)}",
            status_code=303,
        )


@router.get("/events", response_class=HTMLResponse)
def list_events(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    program_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training events list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = TrainingService(db)

    status_enum = None
    if status:
        try:
            status_enum = TrainingEventStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_events(
        org_id,
        search=search,
        status=status_enum,
        program_id=coerce_uuid(program_id) if program_id else None,
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date),
        pagination=pagination,
    )

    programs = svc.list_programs(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Training Events", "training", db=db)
    context["request"] = request
    context.update(
        {
            "events": result.items,
            "programs": programs,
            "search": search,
            "status": status,
            "program_id": program_id,
            "start_date": start_date,
            "end_date": end_date,
            "statuses": [s.value for s in TrainingEventStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/training/events.html", context)


def _parse_time(value: Optional[str]) -> Optional[time]:
    """Parse time from string."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


@router.get("/events/new", response_class=HTMLResponse)
def new_event_form(
    request: Request,
    program_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New training event form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    programs = svc.list_programs(
        org_id,
        status=TrainingProgramStatus.ACTIVE,
        pagination=PaginationParams(limit=200),
    ).items

    employees = org_svc.list_employees(
        org_id,
        filters=EmployeeFilters(is_active=True),
        pagination=PaginationParams(limit=500),
    ).items

    context = base_context(request, auth, "New Training Event", "training", db=db)
    context["request"] = request
    context.update({
        "event": None,
        "form_data": {},
        "programs": programs,
        "employees": employees,
        "preselected_program_id": program_id,
        "error": None,
    })
    return templates.TemplateResponse(request, "people/training/event_form.html", context)


@router.post("/events/new", response_class=HTMLResponse)
def create_event(
    request: Request,
    program_id: str = Form(...),
    event_name: str = Form(...),
    event_type: str = Form("IN_PERSON"),
    start_date: str = Form(...),
    end_date: str = Form(...),
    start_time: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    meeting_link: Optional[str] = Form(None),
    trainer_employee_id: Optional[str] = Form(None),
    trainer_name: Optional[str] = Form(None),
    trainer_email: Optional[str] = Form(None),
    max_attendees: Optional[int] = Form(None),
    total_cost: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new training event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    form_data = {
        "program_id": program_id,
        "event_name": event_name,
        "event_type": event_type,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "meeting_link": meeting_link,
        "trainer_employee_id": trainer_employee_id,
        "trainer_name": trainer_name,
        "trainer_email": trainer_email,
        "max_attendees": max_attendees,
        "total_cost": total_cost,
        "currency_code": currency_code,
        "description": description,
    }

    try:
        event = svc.create_event(
            org_id,
            program_id=coerce_uuid(program_id),
            event_name=event_name,
            event_type=event_type,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            start_time=_parse_time(start_time),
            end_time=_parse_time(end_time),
            location=location or None,
            meeting_link=meeting_link or None,
            trainer_employee_id=coerce_uuid(trainer_employee_id) if trainer_employee_id else None,
            trainer_name=trainer_name or None,
            trainer_email=trainer_email or None,
            max_attendees=max_attendees,
            total_cost=Decimal(total_cost) if total_cost else None,
            currency_code=currency_code,
            description=description or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event.event_id}?success=Event+created+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        programs = svc.list_programs(
            org_id,
            status=TrainingProgramStatus.ACTIVE,
            pagination=PaginationParams(limit=200),
        ).items
        employees = org_svc.list_employees(
            org_id,
            filters=EmployeeFilters(is_active=True),
            pagination=PaginationParams(limit=500),
        ).items

        context = base_context(request, auth, "New Training Event", "training", db=db)
        context["request"] = request
        context.update({
            "event": None,
            "form_data": form_data,
            "programs": programs,
            "employees": employees,
            "preselected_program_id": None,
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/training/event_form.html", context)


@router.get("/events/{event_id}", response_class=HTMLResponse)
def view_event(
    request: Request,
    event_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View training event detail."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        event = svc.get_event(org_id, coerce_uuid(event_id))
    except Exception:
        return RedirectResponse(url="/people/training/events", status_code=303)

    context = base_context(request, auth, event.event_name, "training", db=db)
    context["request"] = request
    context.update({
        "event": event,
        "error": None,
        "success": success,
    })
    return templates.TemplateResponse(request, "people/training/event_detail.html", context)


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_event_form(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit training event form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    try:
        event = svc.get_event(org_id, coerce_uuid(event_id))
    except Exception:
        return RedirectResponse(url="/people/training/events", status_code=303)

    programs = svc.list_programs(
        org_id,
        status=TrainingProgramStatus.ACTIVE,
        pagination=PaginationParams(limit=200),
    ).items

    employees = org_svc.list_employees(
        org_id,
        filters=EmployeeFilters(is_active=True),
        pagination=PaginationParams(limit=500),
    ).items

    context = base_context(request, auth, f"Edit {event.event_name}", "training", db=db)
    context["request"] = request
    context.update({
        "event": event,
        "form_data": {},
        "programs": programs,
        "employees": employees,
        "preselected_program_id": None,
        "error": None,
    })
    return templates.TemplateResponse(request, "people/training/event_form.html", context)


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
def update_event(
    request: Request,
    event_id: str,
    program_id: str = Form(...),
    event_name: str = Form(...),
    event_type: str = Form("IN_PERSON"),
    start_date: str = Form(...),
    end_date: str = Form(...),
    start_time: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    meeting_link: Optional[str] = Form(None),
    trainer_employee_id: Optional[str] = Form(None),
    trainer_name: Optional[str] = Form(None),
    trainer_email: Optional[str] = Form(None),
    max_attendees: Optional[int] = Form(None),
    total_cost: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a training event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)
    org_svc = OrganizationService(db)

    try:
        svc.update_event(
            org_id,
            coerce_uuid(event_id),
            program_id=coerce_uuid(program_id),
            event_name=event_name,
            event_type=event_type,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            start_time=_parse_time(start_time),
            end_time=_parse_time(end_time),
            location=location or None,
            meeting_link=meeting_link or None,
            trainer_employee_id=coerce_uuid(trainer_employee_id) if trainer_employee_id else None,
            trainer_name=trainer_name or None,
            trainer_email=trainer_email or None,
            max_attendees=max_attendees,
            total_cost=Decimal(total_cost) if total_cost else None,
            currency_code=currency_code,
            description=description or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?success=Event+updated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        event = svc.get_event(org_id, coerce_uuid(event_id))
        programs = svc.list_programs(
            org_id,
            status=TrainingProgramStatus.ACTIVE,
            pagination=PaginationParams(limit=200),
        ).items
        employees = org_svc.list_employees(
            org_id,
            filters=EmployeeFilters(is_active=True),
            pagination=PaginationParams(limit=500),
        ).items

        context = base_context(request, auth, f"Edit {event.event_name}", "training", db=db)
        context["request"] = request
        context.update({
            "event": event,
            "form_data": {},
            "programs": programs,
            "employees": employees,
            "preselected_program_id": None,
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/training/event_form.html", context)


@router.post("/events/{event_id}/schedule", response_class=HTMLResponse)
def schedule_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedule a draft event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.update_event(org_id, coerce_uuid(event_id), status=TrainingEventStatus.SCHEDULED)
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?success=Event+scheduled",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/events/{event_id}/start", response_class=HTMLResponse)
def start_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start a training event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.start_event(org_id, coerce_uuid(event_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?success=Event+started",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/events/{event_id}/complete", response_class=HTMLResponse)
def complete_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Complete a training event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.complete_event(org_id, coerce_uuid(event_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?success=Event+completed",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/events/{event_id}/cancel", response_class=HTMLResponse)
def cancel_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel a training event."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    try:
        svc.cancel_event(org_id, coerce_uuid(event_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?success=Event+cancelled",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/training/events/{event_id}?error={str(e)}",
            status_code=303,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Training Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/completion", response_class=HTMLResponse)
def report_completion(
    request: Request,
    start_date: str = None,
    end_date: str = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training completion rates report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    report = svc.get_training_completion_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    return templates.TemplateResponse(
        "people/training/reports/completion.html",
        {
            "request": request,
            "auth": auth,
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    start_date: str = None,
    end_date: str = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training participation by department report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    report = svc.get_training_by_department_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    return templates.TemplateResponse(
        "people/training/reports/by_department.html",
        {
            "request": request,
            "auth": auth,
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/reports/cost-analysis", response_class=HTMLResponse)
def report_cost_analysis(
    request: Request,
    start_date: str = None,
    end_date: str = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training cost analysis report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    report = svc.get_training_cost_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    return templates.TemplateResponse(
        "people/training/reports/cost_analysis.html",
        {
            "request": request,
            "auth": auth,
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/reports/effectiveness", response_class=HTMLResponse)
def report_effectiveness(
    request: Request,
    start_date: str = None,
    end_date: str = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training effectiveness/feedback report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = TrainingService(db)

    report = svc.get_training_effectiveness_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    return templates.TemplateResponse(
        "people/training/reports/effectiveness.html",
        {
            "request": request,
            "auth": auth,
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )
