"""Learning and Assessment Module web view service."""

from __future__ import annotations

import csv
import re
from io import StringIO
from datetime import date
from decimal import Decimal
from typing import Any, cast
from urllib.parse import parse_qs, quote_plus, urlparse
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.people.training import (
    TrainingAssessment,
    TrainingAssessmentQuestion,
    TrainingAssessmentStatus,
    TrainingCourseAssignment,
    TrainingCourseModule,
    TrainingCourseStatus,
    TrainingLessonType,
    TrainingProgressStatus,
    TrainingQuestion,
    TrainingQuestionDifficulty,
    TrainingQuestionType,
)
from app.models.rbac import Role
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import OrganizationService
from app.services.people.training import (
    AssessmentService,
    AssignmentService,
    CourseService,
    ExamService,
    LearningReportService,
    LessonService,
    ModuleService,
    ProgressService,
    QuestionBankService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import logger, parse_date, parse_decimal, parse_int, parse_uuid

COURSE_READ_PERMISSIONS = ["training:courses:read", "training:programs:read"]
COURSE_CREATE_PERMISSIONS = ["training:courses:create", "training:programs:create"]
COURSE_UPDATE_PERMISSIONS = ["training:courses:update", "training:programs:update"]
ASSESSMENT_MANAGE_PERMISSIONS = [
    "training:assessments:manage",
    "training:programs:update",
]
QUESTION_MANAGE_PERMISSIONS = ["training:questions:manage", "training:programs:update"]
ASSIGNMENT_MANAGE_PERMISSIONS = [
    "training:enrollments:create",
    "training:courses:update",
]
LEARNER_PERMISSIONS = ["training:enrollments:self_enroll", "training:events:read"]


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _error_url(url: str, error: Exception | str) -> str:
    return f"{url}?error={quote_plus(str(error))}"


def _youtube_embed_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    video_id: str | None = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "m.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/embed/", 1)[1].split("/", 1)[0]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", video_id):
        return None
    return f"https://www.youtube.com/embed/{video_id}"


def _actor_id(auth: WebAuthContext) -> UUID | None:
    return coerce_uuid(auth.person_id) if auth.person_id else None


def _employee_id(auth: WebAuthContext) -> UUID:
    if not auth.employee_id:
        raise HTTPException(status_code=403, detail="Employee record required")
    return cast(UUID, coerce_uuid(auth.employee_id))


def _enum(enum_cls, value: str | None, default=None):
    if not value:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _bool(form_data: dict, name: str) -> bool:
    return str(form_data.get(name) or "").lower() in {"1", "true", "on", "yes"}


def _form_str(form_data: dict, name: str) -> str | None:
    value = str(form_data.get(name) or "").strip()
    return value or None


def _form_uuid(form_data: dict, name: str) -> UUID | None:
    return parse_uuid(str(form_data.get(name) or ""))


def _form_decimal(form_data: dict, name: str) -> Decimal | None:
    value = str(form_data.get(name) or "").strip()
    return parse_decimal(value) if value else None


def _form_int(form_data: dict, name: str) -> int | None:
    value = str(form_data.get(name) or "").strip()
    return parse_int(value) if value else None


def _csv_response(filename: str, rows: list[dict]) -> Response:
    buffer = StringIO()
    fieldnames = list(rows[0].keys()) if rows else ["message"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    if rows:
        writer.writerows(rows)
    else:
        writer.writerow({"message": "No data"})
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_any(auth: WebAuthContext, permissions: list[str]) -> None:
    if not auth.has_any_permission(permissions):
        raise HTTPException(
            status_code=403,
            detail=f"Permission required: {' or '.join(permissions)}",
        )


class LearningAssessmentWebService:
    """Web response helpers for LAM authoring screens."""

    @staticmethod
    def _permissions(auth: WebAuthContext) -> dict[str, bool]:
        return {
            "can_read_courses": auth.has_any_permission(COURSE_READ_PERMISSIONS),
            "can_create_courses": auth.has_any_permission(COURSE_CREATE_PERMISSIONS),
            "can_update_courses": auth.has_any_permission(COURSE_UPDATE_PERMISSIONS),
            "can_manage_assessments": auth.has_any_permission(
                ASSESSMENT_MANAGE_PERMISSIONS
            ),
            "can_manage_questions": auth.has_any_permission(
                QUESTION_MANAGE_PERMISSIONS
            ),
            "can_manage_assignments": auth.has_any_permission(
                ASSIGNMENT_MANAGE_PERMISSIONS
            ),
        }

    @staticmethod
    def _departments(db: Session, org_id: UUID) -> list:
        return (
            OrganizationService(db, org_id)
            .list_departments(pagination=PaginationParams(limit=200))
            .items
        )

    @staticmethod
    def _employees(db: Session, org_id: UUID) -> list:
        return (
            OrganizationService(db, org_id)
            .list_employees(
                pagination=PaginationParams(limit=500),
                eager_load=True,
            )
            .items
        )

    @staticmethod
    def _roles(db: Session) -> list[Role]:
        return list(
            db.scalars(
                select(Role).where(Role.is_active.is_(True)).order_by(Role.name)
            ).all()
        )

    @staticmethod
    def _designations(db: Session, org_id: UUID) -> list:
        return (
            OrganizationService(db, org_id)
            .list_designations(pagination=PaginationParams(limit=200))
            .items
        )

    @staticmethod
    def _teams(db: Session, org_id: UUID) -> list:
        from app.models.support.team import SupportTeam

        return list(
            db.scalars(
                select(SupportTeam)
                .where(
                    SupportTeam.organization_id == org_id,
                    SupportTeam.is_active.is_(True),
                )
                .order_by(SupportTeam.team_name)
            ).all()
        )

    @staticmethod
    def _course_counts(
        db: Session, org_id: UUID
    ) -> tuple[dict[UUID, int], dict[UUID, int]]:
        module_rows = db.execute(
            select(TrainingCourseModule.course_id, func.count(TrainingCourseModule.id))
            .where(TrainingCourseModule.organization_id == org_id)
            .group_by(TrainingCourseModule.course_id)
        ).all()
        assignment_rows = db.execute(
            select(
                TrainingCourseAssignment.course_id,
                func.count(TrainingCourseAssignment.id),
            )
            .where(TrainingCourseAssignment.organization_id == org_id)
            .group_by(TrainingCourseAssignment.course_id)
        ).all()
        return (
            {course_id: count for course_id, count in module_rows},
            {course_id: count for course_id, count in assignment_rows},
        )

    def courses_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        department_id: str | None = None,
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        _require_any(auth, COURSE_READ_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        result = CourseService(db).list_courses(
            org_id,
            search=search,
            department_id=parse_uuid(department_id),
            status=_enum(TrainingCourseStatus, status),
            pagination=pagination,
        )
        module_counts, assignment_counts = self._course_counts(db, org_id)
        active_filters = [
            name
            for name, value in [
                ("department", department_id),
                ("status", status),
            ]
            if value
        ]
        context = base_context(request, auth, "Learning Courses", "training", db=db)
        context.update(
            {
                "courses": result.items,
                "departments": self._departments(db, org_id),
                "module_counts": module_counts,
                "assignment_counts": assignment_counts,
                "search": search,
                "department_id": department_id,
                "status": status,
                "statuses": [item.value for item in TrainingCourseStatus],
                "active_filters": active_filters,
                "page": result.page,
                "total_pages": result.total_pages,
                "total_count": result.total,
                "limit": pagination.limit,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/courses.html", context
        )

    def my_courses_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        employee_id = _employee_id(auth)
        progress_status = _enum(TrainingProgressStatus, status)
        result = ProgressService(db).list_employee_courses(
            org_id,
            employee_id,
            status=progress_status if status != "overdue" else None,
            pagination=PaginationParams.from_page(page, per_page=20),
        )
        assignments = {
            item.course_id: item
            for item in AssignmentService(db)
            .list_assignments(
                org_id,
                employee_id=employee_id,
                pagination=PaginationParams(limit=1000),
            )
            .items
        }
        items = result.items
        if status == "overdue":
            today = date.today()
            items = [
                item
                for item in items
                if (
                    item.status != TrainingProgressStatus.COMPLETED
                    and (assignment := assignments.get(item.course_id)) is not None
                    and assignment.due_date is not None
                    and assignment.due_date < today
                )
            ]
        context = base_context(request, auth, "My Courses", "training", db=db)
        context.update(
            {
                "progress_records": items,
                "assignments": assignments,
                "status": status,
                "statuses": ["in_progress", "completed", "overdue"],
                "page": result.page,
                "total_pages": result.total_pages,
                "total_count": len(items) if status == "overdue" else result.total,
                "limit": result.limit,
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/my_courses.html", context
        )

    def learner_course_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        try:
            state = ProgressService(db).get_course_learning_state(
                org_id, coerce_uuid(course_id), _employee_id(auth)
            )
        except Exception as exc:
            return _redirect(_error_url("/people/training/my-courses", exc))
        context = base_context(request, auth, state["course"].title, "training", db=db)
        context.update({**state, "success": success, "error": error})
        return templates.TemplateResponse(
            request, "people/training/learning/learner_course_detail.html", context
        )

    def lesson_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        lesson_id: str,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        employee_id = _employee_id(auth)
        lesson = LessonService(db).get_lesson(org_id, coerce_uuid(lesson_id))
        try:
            ProgressService(db).assert_module_unlocked(
                org_id, lesson.module_id, employee_id
            )
        except Exception as exc:
            module = ModuleService(db).get_module(org_id, lesson.module_id)
            return _redirect(
                _error_url(f"/people/training/my-courses/{module.course_id}", exc)
            )
        module = ModuleService(db).get_module(org_id, lesson.module_id)
        course = CourseService(db).get_course(org_id, module.course_id)
        state = ProgressService(db).get_course_learning_state(
            org_id, course.id, employee_id
        )
        context = base_context(request, auth, lesson.title, "training", db=db)
        context.update(
            {
                "lesson": lesson,
                "module": module,
                "course": course,
                "lesson_progress": state["lesson_progress"].get(lesson.id),
                "youtube_embed_url": _youtube_embed_url(lesson.youtube_url),
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/lesson_view.html", context
        )

    def complete_lesson_response(
        self,
        auth: WebAuthContext,
        db: Session,
        lesson_id: str,
    ) -> RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        employee_id = _employee_id(auth)
        try:
            lesson = ProgressService(db).mark_lesson_completed(
                org_id,
                coerce_uuid(lesson_id),
                employee_id,
                actor_id=_actor_id(auth),
            )
            module = ModuleService(db).get_module(org_id, lesson.lesson.module_id)
            db.commit()
            return _redirect(
                f"/people/training/my-courses/{module.course_id}?success=Lesson+completed"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/lessons/{lesson_id}", exc))

    def start_assessment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        try:
            attempt = ExamService(db).start_attempt(
                coerce_uuid(auth.organization_id),
                coerce_uuid(assessment_id),
                _employee_id(auth),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(f"/people/training/attempts/{attempt.id}")
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url("/people/training/my-courses", exc))

    def attempt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        attempt_id: str,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        employee_id = _employee_id(auth)
        try:
            attempt = ExamService(db).get_attempt(
                org_id, coerce_uuid(attempt_id), employee_id
            )
            if attempt.submitted_at:
                return _redirect(f"/people/training/attempts/{attempt_id}/result")
            assessment = AssessmentService(db).get_assessment(
                org_id, attempt.assessment_id
            )
            ProgressService(db).assert_module_unlocked(
                org_id, assessment.module_id, employee_id
            )
        except Exception as exc:
            return _redirect(_error_url("/people/training/my-courses", exc))
        context = base_context(request, auth, assessment.title, "training", db=db)
        context.update({"attempt": attempt, "assessment": assessment, "error": error})
        return templates.TemplateResponse(
            request, "people/training/learning/assessment_take.html", context
        )

    async def submit_attempt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        attempt_id: str,
    ) -> RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        form_data = await request.form()
        try:
            attempt = ExamService(db).get_attempt(
                coerce_uuid(auth.organization_id),
                coerce_uuid(attempt_id),
                _employee_id(auth),
            )
            assessment = AssessmentService(db).get_assessment(
                coerce_uuid(auth.organization_id), attempt.assessment_id
            )
            answers = []
            for link in assessment.assessment_questions:
                key = f"answer_{link.question_id}"
                answer: str | list[UploadFile | str]
                if link.question.question_type == TrainingQuestionType.MULTIPLE_SELECT:
                    answer = form_data.getlist(key)
                else:
                    answer = str(form_data.get(key) or "")
                answers.append({"question_id": str(link.question_id), "answer": answer})
            ExamService(db).submit_attempt(
                coerce_uuid(auth.organization_id),
                attempt.id,
                answers,
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(f"/people/training/attempts/{attempt.id}/result")
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/attempts/{attempt_id}", exc))

    def result_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        attempt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, LEARNER_PERMISSIONS)
        try:
            attempt = ExamService(db).get_attempt(
                coerce_uuid(auth.organization_id),
                coerce_uuid(attempt_id),
                _employee_id(auth),
            )
            attempts = (
                ExamService(db)
                .list_attempts(
                    coerce_uuid(auth.organization_id),
                    _employee_id(auth),
                    assessment_id=attempt.assessment_id,
                    pagination=PaginationParams(limit=100),
                )
                .items
            )
        except Exception as exc:
            return _redirect(_error_url("/people/training/my-courses", exc))
        attempt_number = len(
            [item for item in attempts if item.started_at <= attempt.started_at]
        )
        context = base_context(request, auth, "Assessment Result", "training", db=db)
        context.update({"attempt": attempt, "attempt_number": attempt_number})
        return templates.TemplateResponse(
            request, "people/training/learning/assessment_result.html", context
        )

    def grading_queue_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str | None = None,
        assessment_id: str | None = None,
        page: int = 1,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        result = ExamService(db).list_pending_manual_answers(
            org_id,
            course_id=parse_uuid(course_id),
            assessment_id=parse_uuid(assessment_id),
            pagination=PaginationParams.from_page(page, per_page=25),
        )
        context = base_context(
            request, auth, "Pending Essay Reviews", "training", db=db
        )
        context.update(
            {
                "answers": result.items,
                "courses": CourseService(db)
                .list_courses(org_id, pagination=PaginationParams(limit=500))
                .items,
                "assessments": self._assessments(db, org_id),
                "course_id": course_id,
                "assessment_id": assessment_id,
                "page": result.page,
                "total_pages": result.total_pages,
                "total_count": result.total,
                "limit": result.limit,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/grading_queue.html", context
        )

    async def grade_answer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        answer_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            answer = ExamService(db).grade_manual_answer(
                coerce_uuid(auth.organization_id),
                coerce_uuid(answer_id),
                score_awarded=str(form_data.get("score_awarded") or "0"),
                feedback=_form_str(form_data, "feedback"),
                graded_by=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/grading?success=Answer+graded#attempt-{answer.attempt_id}"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url("/people/training/grading", exc))

    def finalize_grading_response(
        self,
        auth: WebAuthContext,
        db: Session,
        attempt_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        try:
            ExamService(db).finalize_manual_grading(
                coerce_uuid(auth.organization_id),
                coerce_uuid(attempt_id),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect("/people/training/grading?success=Attempt+finalized")
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url("/people/training/grading", exc))

    @staticmethod
    def _assessments(db: Session, org_id: UUID) -> list[TrainingAssessment]:
        return list(
            db.scalars(
                select(TrainingAssessment)
                .where(TrainingAssessment.organization_id == org_id)
                .order_by(TrainingAssessment.title)
            ).all()
        )

    def learning_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> HTMLResponse:
        _require_any(auth, COURSE_READ_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        report = LearningReportService(db).dashboard(
            org_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
        )
        context = base_context(request, auth, "Learning Dashboard", "training", db=db)
        context.update(
            {
                "report": report,
                "start_date": start_date or "",
                "end_date": end_date or "",
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/reports/dashboard.html", context
        )

    def learning_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        report_name: str,
        employee_id: str | None = None,
        department_id: str | None = None,
        course_id: str | None = None,
        assessment_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        export: str | None = None,
    ) -> HTMLResponse | Response:
        _require_any(auth, COURSE_READ_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        data = self._learning_report_data(
            db,
            org_id,
            report_name,
            employee_id,
            department_id,
            course_id,
            assessment_id,
            start_date,
            end_date,
        )
        if export == "csv":
            return _csv_response(f"learning_{report_name}.csv", data["report"]["rows"])
        context = base_context(request, auth, data["title"], "training", db=db)
        context.update(data)
        context.update(
            {
                "employees": self._employees(db, org_id),
                "departments": self._departments(db, org_id),
                "courses": CourseService(db)
                .list_courses(org_id, pagination=PaginationParams(limit=500))
                .items,
                "assessments": self._assessments(db, org_id),
                "employee_id": employee_id,
                "department_id": department_id,
                "course_id": course_id,
                "assessment_id": assessment_id,
                "start_date": start_date or "",
                "end_date": end_date or "",
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/reports/report.html", context
        )

    def _learning_report_data(
        self,
        db: Session,
        org_id: UUID,
        report_name: str,
        employee_id: str | None,
        department_id: str | None,
        course_id: str | None,
        assessment_id: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> dict[str, Any]:
        svc = LearningReportService(db)
        employee_uuid = parse_uuid(employee_id)
        department_uuid = parse_uuid(department_id)
        course_uuid = parse_uuid(course_id)
        assessment_uuid = parse_uuid(assessment_id)
        start = parse_date(start_date)
        end = parse_date(end_date)
        if report_name == "employee-progress":
            report = svc.employee_progress(
                org_id,
                employee_id=employee_uuid,
                department_id=department_uuid,
                course_id=course_uuid,
                start_date=start,
                end_date=end,
            )
            title = "Employee Progress Report"
        elif report_name == "department-completion":
            report = svc.department_completion(
                org_id,
                department_id=department_uuid,
                course_id=course_uuid,
                start_date=start,
                end_date=end,
            )
            title = "Department Completion Report"
        elif report_name == "assessment-results":
            report = svc.assessment_results(
                org_id,
                employee_id=employee_uuid,
                course_id=course_uuid,
                assessment_id=assessment_uuid,
                start_date=start,
                end_date=end,
            )
            title = "Assessment Results Report"
        elif report_name == "outstanding-mandatory":
            report = svc.outstanding_mandatory(
                org_id,
                employee_id=employee_uuid,
                department_id=department_uuid,
                course_id=course_uuid,
                start_date=start,
                end_date=end,
            )
            title = "Outstanding Mandatory Training"
        elif report_name == "question-analysis":
            report = svc.question_analysis(
                org_id,
                course_id=course_uuid,
                assessment_id=assessment_uuid,
                start_date=start,
                end_date=end,
            )
            title = "Question Analysis Report"
        else:
            raise HTTPException(status_code=404, detail="Report not found")
        return {"report": report, "report_name": report_name, "title": title}

    def assignments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str | None = None,
        assignment_source: str | None = None,
        page: int = 1,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=25)
        result = AssignmentService(db).list_assignments(
            org_id,
            course_id=parse_uuid(course_id),
            assignment_source=assignment_source,
            pagination=pagination,
        )
        courses = (
            CourseService(db)
            .list_courses(
                org_id,
                status=TrainingCourseStatus.PUBLISHED,
                pagination=PaginationParams(limit=500),
            )
            .items
        )
        context = base_context(request, auth, "Course Assignments", "training", db=db)
        context.update(
            {
                "assignments": result.items,
                "courses": courses,
                "employees": self._employees(db, org_id),
                "departments": self._departments(db, org_id),
                "roles": self._roles(db),
                "designations": self._designations(db, org_id),
                "teams": self._teams(db, org_id),
                "course_id": course_id,
                "assignment_source": assignment_source,
                "assignment_sources": [
                    "employee",
                    "department",
                    "role",
                    "designation",
                    "team",
                ],
                "active_filters": [
                    name
                    for name, value in [
                        ("course", course_id),
                        ("source", assignment_source),
                    ]
                    if value
                ],
                "page": result.page,
                "total_pages": result.total_pages,
                "total_count": result.total,
                "limit": pagination.limit,
                "success": success,
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/assignments.html", context
        )

    async def assign_employee_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        form_data = await request.form()
        try:
            course_id = coerce_uuid(form_data["course_id"])
            employee_ids = [
                coerce_uuid(value) for value in form_data.getlist("employee_ids")
            ]
            AssignmentService(db).assign_employees(
                coerce_uuid(auth.organization_id),
                course_id,
                employee_ids,
                assigned_by=_actor_id(auth),
                due_date=parse_date(str(form_data.get("due_date") or "")),
                is_mandatory=_bool(dict(form_data), "is_mandatory"),
                assignment_source="employee",
            )
            db.commit()
            return _redirect("/people/training/assignments?success=Course+assigned")
        except Exception as exc:
            db.rollback()
            logger.exception("assign_employee_response: failed")
            return _redirect(_error_url("/people/training/assignments", exc))

    async def assign_department_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssignmentService(db).assign_department(
                coerce_uuid(auth.organization_id),
                coerce_uuid(form_data["course_id"]),
                coerce_uuid(form_data["department_id"]),
                assigned_by=_actor_id(auth),
                due_date=parse_date(str(form_data.get("due_date") or "")),
                is_mandatory=_bool(form_data, "is_mandatory"),
            )
            db.commit()
            return _redirect("/people/training/assignments?success=Department+assigned")
        except Exception as exc:
            db.rollback()
            logger.exception("assign_department_response: failed")
            return _redirect(_error_url("/people/training/assignments", exc))

    async def assign_role_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssignmentService(db).assign_role(
                coerce_uuid(auth.organization_id),
                coerce_uuid(form_data["course_id"]),
                coerce_uuid(form_data["role_id"]),
                assigned_by=_actor_id(auth),
                due_date=parse_date(str(form_data.get("due_date") or "")),
                is_mandatory=_bool(form_data, "is_mandatory"),
            )
            db.commit()
            return _redirect("/people/training/assignments?success=Role+assigned")
        except Exception as exc:
            db.rollback()
            logger.exception("assign_role_response: failed")
            return _redirect(_error_url("/people/training/assignments", exc))

    async def assign_designation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssignmentService(db).assign_designation(
                coerce_uuid(auth.organization_id),
                coerce_uuid(form_data["course_id"]),
                coerce_uuid(form_data["designation_id"]),
                assigned_by=_actor_id(auth),
                due_date=parse_date(str(form_data.get("due_date") or "")),
                is_mandatory=_bool(form_data, "is_mandatory"),
            )
            db.commit()
            return _redirect(
                "/people/training/assignments?success=Designation+assigned"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("assign_designation_response: failed")
            return _redirect(_error_url("/people/training/assignments", exc))

    async def assign_team_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        _require_any(auth, ASSIGNMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssignmentService(db).assign_team(
                coerce_uuid(auth.organization_id),
                coerce_uuid(form_data["course_id"]),
                coerce_uuid(form_data["team_id"]),
                assigned_by=_actor_id(auth),
                due_date=parse_date(str(form_data.get("due_date") or "")),
                is_mandatory=_bool(form_data, "is_mandatory"),
            )
            db.commit()
            return _redirect("/people/training/assignments?success=Team+assigned")
        except Exception as exc:
            db.rollback()
            logger.exception("assign_team_response: failed")
            return _redirect(_error_url("/people/training/assignments", exc))

    def course_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(
            auth, COURSE_UPDATE_PERMISSIONS if course_id else COURSE_CREATE_PERMISSIONS
        )
        org_id = coerce_uuid(auth.organization_id)
        course = None
        if course_id:
            try:
                course = CourseService(db).get_course(org_id, coerce_uuid(course_id))
            except Exception:
                return _redirect("/people/training/courses?error=Course+not+found")
        context = base_context(
            request,
            auth,
            "Edit Learning Course" if course else "New Learning Course",
            "training",
            db=db,
        )
        context.update(
            {
                "course": course,
                "departments": self._departments(db, org_id),
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/course_form.html", context
        )

    async def create_course_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_CREATE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            course = CourseService(db).create_course(
                org_id,
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                thumbnail_file_id=_form_str(form_data, "thumbnail_file_id"),
                department_id=_form_uuid(form_data, "department_id"),
                pass_mark=_form_decimal(form_data, "pass_mark"),
                retake_limit=_form_int(form_data, "retake_limit") or 3,
                is_mandatory=_bool(form_data, "is_mandatory"),
                created_by=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course.id}?success=Course+created"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_course_response: failed")
            return self.course_form_response(
                request, auth, db, form_data=form_data, error=str(exc)
            )

    async def update_course_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            CourseService(db).update_course(
                org_id,
                coerce_uuid(course_id),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                thumbnail_file_id=_form_str(form_data, "thumbnail_file_id"),
                department_id=_form_uuid(form_data, "department_id"),
                pass_mark=_form_decimal(form_data, "pass_mark"),
                retake_limit=_form_int(form_data, "retake_limit"),
                is_mandatory=_bool(form_data, "is_mandatory"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Course+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_course_response: failed")
            return self.course_form_response(
                request, auth, db, course_id, form_data=form_data, error=str(exc)
            )

    def course_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_READ_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        try:
            course = CourseService(db).get_course(org_id, coerce_uuid(course_id))
        except Exception:
            return _redirect("/people/training/courses?error=Course+not+found")
        available_prerequisites = (
            CourseService(db)
            .list_courses(
                org_id,
                pagination=PaginationParams(limit=200),
            )
            .items
        )
        assessment_counts: dict[UUID, int] = {
            assessment_id: count
            for assessment_id, count in db.execute(
                select(
                    TrainingAssessmentQuestion.assessment_id,
                    func.count(TrainingAssessmentQuestion.id),
                )
                .where(TrainingAssessmentQuestion.organization_id == org_id)
                .group_by(TrainingAssessmentQuestion.assessment_id)
            ).all()
            if assessment_id is not None
        }
        context = base_context(request, auth, course.title, "training", db=db)
        context.update(
            {
                "course": course,
                "available_prerequisites": [
                    item for item in available_prerequisites if item.id != course.id
                ],
                "assessment_counts": assessment_counts,
                "success": success,
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/course_detail.html", context
        )

    def publish_course_response(
        self,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        try:
            CourseService(db).publish_course(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Course+published"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/courses/{course_id}", exc))

    def archive_course_response(
        self,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        try:
            CourseService(db).archive_course(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Course+archived"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/courses/{course_id}", exc))

    async def add_prerequisite_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            CourseService(db).add_prerequisite(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                coerce_uuid(form_data["prerequisite_course_id"]),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Prerequisite+added"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/courses/{course_id}", exc))

    def remove_prerequisite_response(
        self,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
        prerequisite_course_id: str,
    ) -> RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        try:
            CourseService(db).remove_prerequisite(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                coerce_uuid(prerequisite_course_id),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Prerequisite+removed"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/courses/{course_id}", exc))

    def module_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        course_id: str | None = None,
        module_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        module = None
        course = None
        if module_id:
            try:
                module = ModuleService(db).get_module(org_id, coerce_uuid(module_id))
                course = CourseService(db).get_course(org_id, module.course_id)
            except Exception:
                return _redirect("/people/training/courses?error=Module+not+found")
        elif course_id:
            course = CourseService(db).get_course(org_id, coerce_uuid(course_id))
        context = base_context(request, auth, "Course Module", "training", db=db)
        context.update(
            {
                "course": course,
                "module": module,
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/module_form.html", context
        )

    async def create_module_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            ModuleService(db).create_module(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                sequence=_form_int(form_data, "sequence"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Module+added"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_module_response: failed")
            return self.module_form_response(
                request,
                auth,
                db,
                course_id=course_id,
                form_data=form_data,
                error=str(exc),
            )

    async def update_module_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        module_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            module = ModuleService(db).update_module(
                org_id,
                coerce_uuid(module_id),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                sequence=_form_int(form_data, "sequence"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Module+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_module_response: failed")
            return self.module_form_response(
                request,
                auth,
                db,
                module_id=module_id,
                form_data=form_data,
                error=str(exc),
            )

    async def reorder_modules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        course_id: str,
    ) -> RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = await request.form()
        try:
            ordered_ids = [
                coerce_uuid(value) for value in form_data.getlist("module_ids")
            ]
            ModuleService(db).reorder_modules(
                coerce_uuid(auth.organization_id),
                coerce_uuid(course_id),
                ordered_ids,
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{course_id}?success=Modules+reordered"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(_error_url(f"/people/training/courses/{course_id}", exc))

    def lesson_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        module_id: str | None = None,
        lesson_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        lesson = None
        module = None
        course = None
        try:
            if lesson_id:
                lesson = LessonService(db).get_lesson(org_id, coerce_uuid(lesson_id))
                module = ModuleService(db).get_module(org_id, lesson.module_id)
            elif module_id:
                module = ModuleService(db).get_module(org_id, coerce_uuid(module_id))
            if module:
                course = CourseService(db).get_course(org_id, module.course_id)
        except Exception:
            return _redirect("/people/training/courses?error=Lesson+not+found")
        context = base_context(request, auth, "Lesson", "training", db=db)
        context.update(
            {
                "course": course,
                "module": module,
                "lesson": lesson,
                "lesson_types": [item.value for item in TrainingLessonType],
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/lesson_form.html", context
        )

    async def create_lesson_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        module_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            module = ModuleService(db).get_module(org_id, coerce_uuid(module_id))
            LessonService(db).create_lesson(
                org_id,
                coerce_uuid(module_id),
                lesson_type=_enum(
                    TrainingLessonType,
                    str(form_data.get("lesson_type") or ""),
                    TrainingLessonType.TEXT,
                ),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                sequence=_form_int(form_data, "sequence"),
                content=_form_str(form_data, "content"),
                file_id=_form_str(form_data, "file_id"),
                youtube_url=_form_str(form_data, "youtube_url"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Lesson+added"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_lesson_response: failed")
            return self.lesson_form_response(
                request,
                auth,
                db,
                module_id=module_id,
                form_data=form_data,
                error=str(exc),
            )

    async def update_lesson_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        lesson_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, COURSE_UPDATE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            lesson = LessonService(db).update_lesson(
                org_id,
                coerce_uuid(lesson_id),
                lesson_type=_enum(
                    TrainingLessonType, str(form_data.get("lesson_type") or "")
                ),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                sequence=_form_int(form_data, "sequence"),
                content=_form_str(form_data, "content"),
                file_id=_form_str(form_data, "file_id"),
                youtube_url=_form_str(form_data, "youtube_url"),
                actor_id=_actor_id(auth),
            )
            module = ModuleService(db).get_module(org_id, lesson.module_id)
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Lesson+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_lesson_response: failed")
            return self.lesson_form_response(
                request,
                auth,
                db,
                lesson_id=lesson_id,
                form_data=form_data,
                error=str(exc),
            )

    def question_banks_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        department_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        result = QuestionBankService(db).list_banks(
            org_id,
            search=search,
            department_id=parse_uuid(department_id),
            pagination=pagination,
        )
        question_counts: dict[UUID, int] = {
            question_bank_id: count
            for question_bank_id, count in db.execute(
                select(
                    TrainingQuestion.question_bank_id, func.count(TrainingQuestion.id)
                )
                .where(TrainingQuestion.organization_id == org_id)
                .group_by(TrainingQuestion.question_bank_id)
            ).all()
            if question_bank_id is not None
        }
        context = base_context(request, auth, "Question Banks", "training", db=db)
        context.update(
            {
                "banks": result.items,
                "departments": self._departments(db, org_id),
                "question_counts": question_counts,
                "search": search,
                "department_id": department_id,
                "active_filters": ["department"] if department_id else [],
                "page": result.page,
                "total_pages": result.total_pages,
                "total_count": result.total,
                "limit": pagination.limit,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/question_banks.html", context
        )

    def question_bank_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        bank_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        bank = None
        if bank_id:
            try:
                bank = QuestionBankService(db).get_bank(org_id, coerce_uuid(bank_id))
            except Exception:
                return _redirect(
                    "/people/training/question-banks?error=Question+bank+not+found"
                )
        context = base_context(request, auth, "Question Bank", "training", db=db)
        context.update(
            {
                "bank": bank,
                "departments": self._departments(db, org_id),
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/question_bank_form.html", context
        )

    async def create_question_bank_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            bank = QuestionBankService(db).create_bank(
                coerce_uuid(auth.organization_id),
                name=str(form_data.get("name") or ""),
                description=_form_str(form_data, "description"),
                category=_form_str(form_data, "category"),
                department_id=_form_uuid(form_data, "department_id"),
                created_by=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/question-banks/{bank.id}?success=Question+bank+created"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_question_bank_response: failed")
            return self.question_bank_form_response(
                request, auth, db, form_data=form_data, error=str(exc)
            )

    async def update_question_bank_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        bank_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            QuestionBankService(db).update_bank(
                coerce_uuid(auth.organization_id),
                coerce_uuid(bank_id),
                name=str(form_data.get("name") or ""),
                description=_form_str(form_data, "description"),
                category=_form_str(form_data, "category"),
                department_id=_form_uuid(form_data, "department_id"),
                is_active=_bool(form_data, "is_active"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/question-banks/{bank_id}?success=Question+bank+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_question_bank_response: failed")
            return self.question_bank_form_response(
                request,
                auth,
                db,
                bank_id,
                form_data=form_data,
                error=str(exc),
            )

    def question_bank_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        bank_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        try:
            bank = QuestionBankService(db).get_bank(
                coerce_uuid(auth.organization_id), coerce_uuid(bank_id)
            )
        except Exception:
            return _redirect(
                "/people/training/question-banks?error=Question+bank+not+found"
            )
        context = base_context(request, auth, bank.name, "training", db=db)
        context.update(
            {
                "bank": bank,
                "success": success,
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/question_bank_detail.html", context
        )

    @staticmethod
    def _question_options(form_data) -> list[dict]:
        correct_values = set(form_data.getlist("correct_options"))
        options: list[dict] = []
        for index, text in enumerate(form_data.getlist("option_text"), start=1):
            option_text = str(text or "").strip()
            if option_text:
                options.append(
                    {
                        "option_text": option_text,
                        "is_correct": str(index) in correct_values,
                    }
                )
        return options

    def question_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        bank_id: str | None = None,
        question_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        question = None
        bank = None
        try:
            if question_id:
                question = QuestionBankService(db).get_question(
                    org_id, coerce_uuid(question_id)
                )
                bank = QuestionBankService(db).get_bank(
                    org_id, question.question_bank_id
                )
            elif bank_id:
                bank = QuestionBankService(db).get_bank(org_id, coerce_uuid(bank_id))
        except Exception:
            return _redirect("/people/training/question-banks?error=Question+not+found")
        context = base_context(request, auth, "Question", "training", db=db)
        context.update(
            {
                "bank": bank,
                "question": question,
                "question_types": [item.value for item in TrainingQuestionType],
                "difficulty_levels": [
                    item.value for item in TrainingQuestionDifficulty
                ],
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/question_form.html", context
        )

    async def create_question_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        bank_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        raw_form = await request.form()
        form_data = dict(raw_form)
        org_id = coerce_uuid(auth.organization_id)
        try:
            QuestionBankService(db).create_question(
                org_id,
                coerce_uuid(bank_id),
                question_type=_enum(
                    TrainingQuestionType,
                    str(form_data.get("question_type") or ""),
                    TrainingQuestionType.MULTIPLE_CHOICE,
                ),
                question_text=str(form_data.get("question_text") or ""),
                difficulty_level=_enum(
                    TrainingQuestionDifficulty,
                    str(form_data.get("difficulty_level") or ""),
                    TrainingQuestionDifficulty.MEDIUM,
                ),
                points=_form_decimal(form_data, "points"),
                options=self._question_options(raw_form),
                tag_names=[
                    item.strip()
                    for item in str(form_data.get("tags") or "").split(",")
                    if item.strip()
                ],
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/question-banks/{bank_id}?success=Question+created"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_question_response: failed")
            return self.question_form_response(
                request,
                auth,
                db,
                bank_id=bank_id,
                form_data=form_data,
                error=str(exc),
            )

    async def update_question_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        question_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, QUESTION_MANAGE_PERMISSIONS)
        raw_form = await request.form()
        form_data = dict(raw_form)
        org_id = coerce_uuid(auth.organization_id)
        try:
            question = QuestionBankService(db).update_question(
                org_id,
                coerce_uuid(question_id),
                question_type=_enum(
                    TrainingQuestionType, str(form_data.get("question_type") or "")
                ),
                question_text=str(form_data.get("question_text") or ""),
                difficulty_level=_enum(
                    TrainingQuestionDifficulty,
                    str(form_data.get("difficulty_level") or ""),
                ),
                points=_form_decimal(form_data, "points"),
                actor_id=_actor_id(auth),
            )
            QuestionBankService(db).replace_options(
                org_id,
                question.id,
                self._question_options(raw_form),
                actor_id=_actor_id(auth),
            )
            QuestionBankService(db).set_question_tags(
                org_id,
                question.id,
                [
                    item.strip()
                    for item in str(form_data.get("tags") or "").split(",")
                    if item.strip()
                ],
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/question-banks/{question.question_bank_id}?success=Question+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_question_response: failed")
            return self.question_form_response(
                request,
                auth,
                db,
                question_id=question_id,
                form_data=form_data,
                error=str(exc),
            )

    def assessment_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        module_id: str | None = None,
        assessment_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        assessment = None
        module = None
        course = None
        try:
            if assessment_id:
                assessment = AssessmentService(db).get_assessment(
                    org_id, coerce_uuid(assessment_id)
                )
                module = assessment.module
            elif module_id:
                module = ModuleService(db).get_module(org_id, coerce_uuid(module_id))
            if module:
                course = CourseService(db).get_course(org_id, module.course_id)
        except Exception:
            return _redirect("/people/training/courses?error=Assessment+not+found")
        context = base_context(request, auth, "Assessment", "training", db=db)
        context.update(
            {
                "course": course,
                "module": module,
                "assessment": assessment,
                "statuses": [item.value for item in TrainingAssessmentStatus],
                "form_data": form_data or {},
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/assessment_form.html", context
        )

    async def create_assessment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        module_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            module = ModuleService(db).get_module(org_id, coerce_uuid(module_id))
            AssessmentService(db).create_assessment(
                org_id,
                coerce_uuid(module_id),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                pass_mark=_form_decimal(form_data, "pass_mark"),
                duration_minutes=_form_int(form_data, "duration_minutes"),
                max_attempts=_form_int(form_data, "max_attempts") or 1,
                randomize_questions=_bool(form_data, "randomize_questions"),
                randomize_options=_bool(form_data, "randomize_options"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Assessment+created"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("create_assessment_response: failed")
            return self.assessment_form_response(
                request,
                auth,
                db,
                module_id=module_id,
                form_data=form_data,
                error=str(exc),
            )

    async def update_assessment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        org_id = coerce_uuid(auth.organization_id)
        try:
            assessment = AssessmentService(db).update_assessment(
                org_id,
                coerce_uuid(assessment_id),
                title=str(form_data.get("title") or ""),
                description=_form_str(form_data, "description"),
                pass_mark=_form_decimal(form_data, "pass_mark"),
                duration_minutes=_form_int(form_data, "duration_minutes"),
                max_attempts=_form_int(form_data, "max_attempts"),
                randomize_questions=_bool(form_data, "randomize_questions"),
                randomize_options=_bool(form_data, "randomize_options"),
                actor_id=_actor_id(auth),
            )
            module = ModuleService(db).get_module(org_id, assessment.module_id)
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Assessment+updated"
            )
        except Exception as exc:
            db.rollback()
            logger.exception("update_assessment_response: failed")
            return self.assessment_form_response(
                request,
                auth,
                db,
                assessment_id=assessment_id,
                form_data=form_data,
                error=str(exc),
            )

    def publish_assessment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        try:
            assessment = AssessmentService(db).publish_assessment(
                org_id, coerce_uuid(assessment_id), actor_id=_actor_id(auth)
            )
            module = ModuleService(db).get_module(org_id, assessment.module_id)
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Assessment+published"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )

    def archive_assessment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        try:
            assessment = AssessmentService(db).archive_assessment(
                org_id, coerce_uuid(assessment_id), actor_id=_actor_id(auth)
            )
            module = ModuleService(db).get_module(org_id, assessment.module_id)
            db.commit()
            return _redirect(
                f"/people/training/courses/{module.course_id}?success=Assessment+archived"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )

    def assessment_questions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
        search: str | None = None,
        bank_id: str | None = None,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        org_id = coerce_uuid(auth.organization_id)
        try:
            assessment = AssessmentService(db).get_assessment(
                org_id, coerce_uuid(assessment_id)
            )
        except Exception:
            return _redirect("/people/training/courses?error=Assessment+not+found")
        banks = (
            QuestionBankService(db)
            .list_banks(org_id, pagination=PaginationParams(limit=200))
            .items
        )
        questions = (
            QuestionBankService(db)
            .list_questions(
                org_id,
                bank_id=parse_uuid(bank_id),
                search=search,
                pagination=PaginationParams(limit=200),
            )
            .items
        )
        attached_ids = {link.question_id for link in assessment.assessment_questions}
        context = base_context(request, auth, assessment.title, "training", db=db)
        context.update(
            {
                "assessment": assessment,
                "course": assessment.module.course,
                "banks": banks,
                "questions": questions,
                "attached_ids": attached_ids,
                "search": search,
                "bank_id": bank_id,
                "success": success,
                "error": error,
                **self._permissions(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/training/learning/assessment_questions.html", context
        )

    async def attach_question_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssessmentService(db).attach_question(
                coerce_uuid(auth.organization_id),
                coerce_uuid(assessment_id),
                coerce_uuid(form_data["question_id"]),
                sequence=_form_int(form_data, "sequence"),
                points_override=_form_decimal(form_data, "points_override"),
                is_required=_bool(form_data, "is_required") or True,
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/assessments/{assessment_id}/questions?success=Question+attached"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )

    def detach_question_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
        question_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        try:
            AssessmentService(db).detach_question(
                coerce_uuid(auth.organization_id),
                coerce_uuid(assessment_id),
                coerce_uuid(question_id),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/assessments/{assessment_id}/questions?success=Question+detached"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )

    async def update_assessment_question_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
        question_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = dict(await request.form())
        try:
            AssessmentService(db).update_assessment_question(
                coerce_uuid(auth.organization_id),
                coerce_uuid(assessment_id),
                coerce_uuid(question_id),
                sequence=_form_int(form_data, "sequence"),
                points_override=_form_decimal(form_data, "points_override"),
                is_required=_bool(form_data, "is_required"),
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/assessments/{assessment_id}/questions?success=Question+updated"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )

    async def reorder_assessment_questions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assessment_id: str,
    ) -> RedirectResponse:
        _require_any(auth, ASSESSMENT_MANAGE_PERMISSIONS)
        form_data = await request.form()
        try:
            AssessmentService(db).reorder_questions(
                coerce_uuid(auth.organization_id),
                coerce_uuid(assessment_id),
                [coerce_uuid(value) for value in form_data.getlist("question_ids")],
                actor_id=_actor_id(auth),
            )
            db.commit()
            return _redirect(
                f"/people/training/assessments/{assessment_id}/questions?success=Questions+reordered"
            )
        except Exception as exc:
            db.rollback()
            return _redirect(
                _error_url(
                    f"/people/training/assessments/{assessment_id}/questions", exc
                )
            )
