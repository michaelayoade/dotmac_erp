"""Learning and assessment service layer.

This module contains Phase 1 Learning & Assessment business logic only.
Routes, APIs, templates, notifications, and UI are intentionally out of scope.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.finance.audit.audit_log import AuditAction
from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.hr.employee import Employee
from app.models.people.training import (
    TrainingAssessment,
    TrainingAssessmentQuestion,
    TrainingAssessmentStatus,
    TrainingCourse,
    TrainingCourseAssignment,
    TrainingCourseModule,
    TrainingCoursePrerequisite,
    TrainingCourseProgress,
    TrainingCourseStatus,
    TrainingExamAnswer,
    TrainingExamAttempt,
    TrainingLesson,
    TrainingLessonProgress,
    TrainingLessonType,
    TrainingProgressStatus,
    TrainingQuestion,
    TrainingQuestionBank,
    TrainingQuestionDifficulty,
    TrainingQuestionOption,
    TrainingQuestionTag,
    TrainingQuestionTagMap,
    TrainingQuestionType,
)
from app.models.rbac import PersonRole, Role
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
)
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)

UTC = timezone.utc

__all__ = [
    "CourseService",
    "ModuleService",
    "LessonService",
    "QuestionBankService",
    "AssessmentService",
    "AssignmentService",
    "ProgressService",
    "ExamService",
    "LearningReportService",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_decimal(value: Decimal | int | float | str | None, default: str) -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _as_answer_set(value: str | Sequence[Any] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in text.split(",")]
    else:
        parsed = list(value)
    return {_normalize_text(item) for item in parsed if str(item).strip()}


def _paginated_result(
    db: Session,
    stmt,
    pagination: PaginationParams | None,
) -> PaginatedResult:
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.scalar(count_stmt) or 0
    offset = pagination.offset if pagination else 0
    limit = pagination.limit if pagination else total
    if pagination:
        stmt = stmt.offset(pagination.offset).limit(pagination.limit)
    items = list(db.scalars(stmt).all())
    return PaginatedResult(items=items, total=total, offset=offset, limit=limit)


class LearningAssessmentBaseService:
    """Shared helpers for tenant-scoped learning services."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _audit(
        self,
        org_id: UUID,
        table_name: str,
        record_id: UUID | str,
        action: AuditAction,
        *,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        user_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        fire_audit_event(
            db=self.db,
            organization_id=org_id,
            table_schema="training",
            table_name=table_name,
            record_id=record_id,
            action=action,
            old_values=old_values,
            new_values=new_values,
            user_id=user_id,
            reason=reason,
        )

    def _get_course(self, org_id: UUID, course_id: UUID) -> TrainingCourse:
        course = self.db.scalar(
            select(TrainingCourse).where(
                TrainingCourse.organization_id == org_id,
                TrainingCourse.id == course_id,
            )
        )
        if not course:
            raise NotFoundError("Training course not found")
        return course

    def _get_module(self, org_id: UUID, module_id: UUID) -> TrainingCourseModule:
        module = self.db.scalar(
            select(TrainingCourseModule).where(
                TrainingCourseModule.organization_id == org_id,
                TrainingCourseModule.id == module_id,
            )
        )
        if not module:
            raise NotFoundError("Training course module not found")
        return module

    def _get_lesson(self, org_id: UUID, lesson_id: UUID) -> TrainingLesson:
        lesson = self.db.scalar(
            select(TrainingLesson).where(
                TrainingLesson.organization_id == org_id,
                TrainingLesson.id == lesson_id,
            )
        )
        if not lesson:
            raise NotFoundError("Training lesson not found")
        return lesson

    def _get_assessment(self, org_id: UUID, assessment_id: UUID) -> TrainingAssessment:
        assessment = self.db.scalar(
            select(TrainingAssessment).where(
                TrainingAssessment.organization_id == org_id,
                TrainingAssessment.id == assessment_id,
            )
        )
        if not assessment:
            raise NotFoundError("Training assessment not found")
        return assessment

    def _get_question(self, org_id: UUID, question_id: UUID) -> TrainingQuestion:
        question = self.db.scalar(
            select(TrainingQuestion)
            .options(selectinload(TrainingQuestion.options))
            .where(
                TrainingQuestion.organization_id == org_id,
                TrainingQuestion.id == question_id,
            )
        )
        if not question:
            raise NotFoundError("Training question not found")
        return question

    def _get_employee(self, org_id: UUID, employee_id: UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
            )
        )
        if not employee:
            raise NotFoundError("Employee not found")
        return employee

    def _ensure_same_org(self, parent_org_id: UUID, child_org_id: UUID) -> None:
        if parent_org_id != child_org_id:
            raise ValidationError("Related records must belong to the same organization")


class CourseService(LearningAssessmentBaseService):
    """Course catalog and prerequisite operations."""

    def list_courses(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        department_id: UUID | None = None,
        status: TrainingCourseStatus | None = None,
        is_active: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingCourse]:
        stmt = select(TrainingCourse).where(TrainingCourse.organization_id == org_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    TrainingCourse.title.ilike(pattern),
                    TrainingCourse.description.ilike(pattern),
                )
            )
        if department_id:
            stmt = stmt.where(TrainingCourse.department_id == department_id)
        if status:
            stmt = stmt.where(TrainingCourse.status == status)
        if is_active is not None:
            stmt = stmt.where(TrainingCourse.is_active == is_active)
        stmt = stmt.order_by(TrainingCourse.title)
        return _paginated_result(self.db, stmt, pagination)

    def get_course(self, org_id: UUID, course_id: UUID) -> TrainingCourse:
        course = self.db.scalar(
            select(TrainingCourse)
            .options(
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.lessons
                ),
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.assessments
                ),
                selectinload(TrainingCourse.prerequisites),
            )
            .where(
                TrainingCourse.organization_id == org_id,
                TrainingCourse.id == course_id,
            )
        )
        if not course:
            raise NotFoundError("Training course not found")
        return course

    def create_course(
        self,
        org_id: UUID,
        *,
        title: str,
        description: str | None = None,
        thumbnail_file_id: str | None = None,
        department_id: UUID | None = None,
        pass_mark: Decimal | int | float | str | None = None,
        retake_limit: int = 3,
        is_mandatory: bool = False,
        created_by: UUID | None = None,
    ) -> TrainingCourse:
        if not title.strip():
            raise ValidationError("Course title is required")
        if retake_limit < 0:
            raise ValidationError("Retake limit cannot be negative")
        course = TrainingCourse(
            organization_id=org_id,
            title=title.strip(),
            description=description,
            thumbnail_file_id=thumbnail_file_id,
            department_id=department_id,
            pass_mark=_coerce_decimal(pass_mark, "70.00"),
            retake_limit=retake_limit,
            is_mandatory=is_mandatory,
            status=TrainingCourseStatus.DRAFT,
            created_by=created_by,
        )
        self.db.add(course)
        self.db.flush()
        self._audit(
            org_id,
            "training_course",
            course.id,
            AuditAction.INSERT,
            new_values={"title": course.title, "status": course.status.value},
            user_id=created_by,
        )
        return course

    def update_course(
        self,
        org_id: UUID,
        course_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        thumbnail_file_id: str | None = None,
        department_id: UUID | None = None,
        pass_mark: Decimal | int | float | str | None = None,
        retake_limit: int | None = None,
        is_mandatory: bool | None = None,
        bump_version: bool = True,
        actor_id: UUID | None = None,
    ) -> TrainingCourse:
        course = self._get_course(org_id, course_id)
        old_values = {
            "title": course.title,
            "version_number": course.version_number,
            "status": course.status.value,
        }
        if title is not None:
            if not title.strip():
                raise ValidationError("Course title is required")
            course.title = title.strip()
        if description is not None:
            course.description = description
        if thumbnail_file_id is not None:
            course.thumbnail_file_id = thumbnail_file_id
        if department_id is not None:
            course.department_id = department_id
        if pass_mark is not None:
            course.pass_mark = _coerce_decimal(pass_mark, "70.00")
        if retake_limit is not None:
            if retake_limit < 0:
                raise ValidationError("Retake limit cannot be negative")
            course.retake_limit = retake_limit
        if is_mandatory is not None:
            course.is_mandatory = is_mandatory
        if bump_version and course.status == TrainingCourseStatus.PUBLISHED:
            course.version_number += 1
            course.status = TrainingCourseStatus.DRAFT
        self.db.flush()
        self._audit(
            org_id,
            "training_course",
            course.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={
                "title": course.title,
                "version_number": course.version_number,
                "status": course.status.value,
            },
            user_id=actor_id,
        )
        return course

    def publish_course(
        self,
        org_id: UUID,
        course_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingCourse:
        course = self.get_course(org_id, course_id)
        if not course.modules:
            raise ValidationError("Course must have at least one module before publishing")
        course.status = TrainingCourseStatus.PUBLISHED
        course.is_active = True
        self.db.flush()
        self._audit(
            org_id,
            "training_course",
            course.id,
            AuditAction.UPDATE,
            new_values={"status": course.status.value},
            user_id=actor_id,
            reason="Course published",
        )
        return course

    def archive_course(
        self,
        org_id: UUID,
        course_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingCourse:
        course = self._get_course(org_id, course_id)
        course.status = TrainingCourseStatus.ARCHIVED
        course.is_active = False
        self.db.flush()
        self._audit(
            org_id,
            "training_course",
            course.id,
            AuditAction.UPDATE,
            new_values={"status": course.status.value, "is_active": course.is_active},
            user_id=actor_id,
            reason="Course archived",
        )
        return course

    def add_prerequisite(
        self,
        org_id: UUID,
        course_id: UUID,
        prerequisite_course_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingCoursePrerequisite:
        if course_id == prerequisite_course_id:
            raise ValidationError("A course cannot require itself as a prerequisite")
        self._get_course(org_id, course_id)
        self._get_course(org_id, prerequisite_course_id)
        existing = self.db.scalar(
            select(TrainingCoursePrerequisite).where(
                TrainingCoursePrerequisite.organization_id == org_id,
                TrainingCoursePrerequisite.course_id == course_id,
                TrainingCoursePrerequisite.prerequisite_course_id
                == prerequisite_course_id,
            )
        )
        if existing:
            return existing
        prerequisite = TrainingCoursePrerequisite(
            organization_id=org_id,
            course_id=course_id,
            prerequisite_course_id=prerequisite_course_id,
        )
        self.db.add(prerequisite)
        self.db.flush()
        self._audit(
            org_id,
            "training_course_prerequisite",
            prerequisite.id,
            AuditAction.INSERT,
            new_values={
                "course_id": str(course_id),
                "prerequisite_course_id": str(prerequisite_course_id),
            },
            user_id=actor_id,
        )
        return prerequisite

    def remove_prerequisite(
        self,
        org_id: UUID,
        course_id: UUID,
        prerequisite_course_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> None:
        prerequisite = self.db.scalar(
            select(TrainingCoursePrerequisite).where(
                TrainingCoursePrerequisite.organization_id == org_id,
                TrainingCoursePrerequisite.course_id == course_id,
                TrainingCoursePrerequisite.prerequisite_course_id
                == prerequisite_course_id,
            )
        )
        if not prerequisite:
            return
        record_id = prerequisite.id
        self.db.delete(prerequisite)
        self.db.flush()
        self._audit(
            org_id,
            "training_course_prerequisite",
            record_id,
            AuditAction.DELETE,
            old_values={
                "course_id": str(course_id),
                "prerequisite_course_id": str(prerequisite_course_id),
            },
            user_id=actor_id,
        )


class ModuleService(LearningAssessmentBaseService):
    """Course module operations."""

    def get_module(self, org_id: UUID, module_id: UUID) -> TrainingCourseModule:
        return self._get_module(org_id, module_id)

    def list_modules(self, org_id: UUID, course_id: UUID) -> list[TrainingCourseModule]:
        self._get_course(org_id, course_id)
        return list(
            self.db.scalars(
                select(TrainingCourseModule)
                .where(
                    TrainingCourseModule.organization_id == org_id,
                    TrainingCourseModule.course_id == course_id,
                )
                .order_by(TrainingCourseModule.sequence)
            ).all()
        )

    def create_module(
        self,
        org_id: UUID,
        course_id: UUID,
        *,
        title: str,
        description: str | None = None,
        sequence: int | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingCourseModule:
        course = self._get_course(org_id, course_id)
        if not title.strip():
            raise ValidationError("Module title is required")
        if sequence is None:
            sequence = (
                self.db.scalar(
                    select(func.coalesce(func.max(TrainingCourseModule.sequence), 0))
                    .where(
                        TrainingCourseModule.organization_id == org_id,
                        TrainingCourseModule.course_id == course_id,
                    )
                )
                or 0
            ) + 1
        module = TrainingCourseModule(
            organization_id=org_id,
            course_id=course.id,
            title=title.strip(),
            description=description,
            sequence=sequence,
        )
        self.db.add(module)
        self.db.flush()
        self._audit(
            org_id,
            "training_course_module",
            module.id,
            AuditAction.INSERT,
            new_values={"course_id": str(course_id), "title": module.title},
            user_id=actor_id,
        )
        return module

    def update_module(
        self,
        org_id: UUID,
        module_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        sequence: int | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingCourseModule:
        module = self._get_module(org_id, module_id)
        old_values = {"title": module.title, "sequence": module.sequence}
        if title is not None:
            if not title.strip():
                raise ValidationError("Module title is required")
            module.title = title.strip()
        if description is not None:
            module.description = description
        if sequence is not None:
            module.sequence = sequence
        self.db.flush()
        self._audit(
            org_id,
            "training_course_module",
            module.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={"title": module.title, "sequence": module.sequence},
            user_id=actor_id,
        )
        return module

    def reorder_modules(
        self,
        org_id: UUID,
        course_id: UUID,
        ordered_module_ids: Sequence[UUID],
        *,
        actor_id: UUID | None = None,
    ) -> list[TrainingCourseModule]:
        modules = self.list_modules(org_id, course_id)
        module_by_id = {module.id: module for module in modules}
        if set(module_by_id) != set(ordered_module_ids):
            raise ValidationError("Reorder payload must include every course module")
        for index, module_id in enumerate(ordered_module_ids, start=1):
            module_by_id[module_id].sequence = index
        self.db.flush()
        self._audit(
            org_id,
            "training_course_module",
            course_id,
            AuditAction.UPDATE,
            new_values={"ordered_module_ids": [str(value) for value in ordered_module_ids]},
            user_id=actor_id,
            reason="Modules reordered",
        )
        return self.list_modules(org_id, course_id)

    def delete_module(
        self,
        org_id: UUID,
        module_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> None:
        module = self._get_module(org_id, module_id)
        old_values = {"course_id": str(module.course_id), "title": module.title}
        self.db.delete(module)
        self.db.flush()
        self._audit(
            org_id,
            "training_course_module",
            module_id,
            AuditAction.DELETE,
            old_values=old_values,
            user_id=actor_id,
        )


class LessonService(LearningAssessmentBaseService):
    """Lesson content operations."""

    def get_lesson(self, org_id: UUID, lesson_id: UUID) -> TrainingLesson:
        return self._get_lesson(org_id, lesson_id)

    def list_lessons(self, org_id: UUID, module_id: UUID) -> list[TrainingLesson]:
        self._get_module(org_id, module_id)
        return list(
            self.db.scalars(
                select(TrainingLesson)
                .where(
                    TrainingLesson.organization_id == org_id,
                    TrainingLesson.module_id == module_id,
                )
                .order_by(TrainingLesson.sequence)
            ).all()
        )

    def create_lesson(
        self,
        org_id: UUID,
        module_id: UUID,
        *,
        lesson_type: TrainingLessonType,
        title: str,
        description: str | None = None,
        sequence: int | None = None,
        content: str | None = None,
        file_id: str | None = None,
        youtube_url: str | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingLesson:
        self._get_module(org_id, module_id)
        if not title.strip():
            raise ValidationError("Lesson title is required")
        self._validate_lesson_payload(lesson_type, content, file_id, youtube_url)
        if sequence is None:
            sequence = (
                self.db.scalar(
                    select(func.coalesce(func.max(TrainingLesson.sequence), 0)).where(
                        TrainingLesson.organization_id == org_id,
                        TrainingLesson.module_id == module_id,
                    )
                )
                or 0
            ) + 1
        lesson = TrainingLesson(
            organization_id=org_id,
            module_id=module_id,
            lesson_type=lesson_type,
            title=title.strip(),
            description=description,
            sequence=sequence,
            content=content,
            file_id=file_id,
            youtube_url=youtube_url,
        )
        self.db.add(lesson)
        self.db.flush()
        self._audit(
            org_id,
            "training_lesson",
            lesson.id,
            AuditAction.INSERT,
            new_values={"module_id": str(module_id), "title": lesson.title},
            user_id=actor_id,
        )
        return lesson

    def update_lesson(
        self,
        org_id: UUID,
        lesson_id: UUID,
        *,
        lesson_type: TrainingLessonType | None = None,
        title: str | None = None,
        description: str | None = None,
        sequence: int | None = None,
        content: str | None = None,
        file_id: str | None = None,
        youtube_url: str | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingLesson:
        lesson = self._get_lesson(org_id, lesson_id)
        resolved_type = lesson_type or lesson.lesson_type
        resolved_content = content if content is not None else lesson.content
        resolved_file_id = file_id if file_id is not None else lesson.file_id
        resolved_youtube = youtube_url if youtube_url is not None else lesson.youtube_url
        self._validate_lesson_payload(
            resolved_type,
            resolved_content,
            resolved_file_id,
            resolved_youtube,
        )
        old_values = {"title": lesson.title, "lesson_type": lesson.lesson_type.value}
        if lesson_type is not None:
            lesson.lesson_type = lesson_type
        if title is not None:
            if not title.strip():
                raise ValidationError("Lesson title is required")
            lesson.title = title.strip()
        if description is not None:
            lesson.description = description
        if sequence is not None:
            lesson.sequence = sequence
        if content is not None:
            lesson.content = content
        if file_id is not None:
            lesson.file_id = file_id
        if youtube_url is not None:
            lesson.youtube_url = youtube_url
        self.db.flush()
        self._audit(
            org_id,
            "training_lesson",
            lesson.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={"title": lesson.title, "lesson_type": lesson.lesson_type.value},
            user_id=actor_id,
        )
        return lesson

    def delete_lesson(
        self,
        org_id: UUID,
        lesson_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> None:
        lesson = self._get_lesson(org_id, lesson_id)
        old_values = {"module_id": str(lesson.module_id), "title": lesson.title}
        self.db.delete(lesson)
        self.db.flush()
        self._audit(
            org_id,
            "training_lesson",
            lesson_id,
            AuditAction.DELETE,
            old_values=old_values,
            user_id=actor_id,
        )

    def _validate_lesson_payload(
        self,
        lesson_type: TrainingLessonType,
        content: str | None,
        file_id: str | None,
        youtube_url: str | None,
    ) -> None:
        if lesson_type == TrainingLessonType.PDF and not file_id:
            raise ValidationError("PDF lessons require a file_id")
        if lesson_type == TrainingLessonType.TEXT and not (content or "").strip():
            raise ValidationError("Text lessons require content")
        if lesson_type == TrainingLessonType.LINK and not (content or "").strip():
            raise ValidationError("Link lessons require content containing the URL")
        if lesson_type == TrainingLessonType.VIDEO and not (youtube_url or file_id):
            raise ValidationError("Video lessons require youtube_url or file_id")


class QuestionBankService(LearningAssessmentBaseService):
    """Reusable question bank, question, option, and tag operations."""

    def list_banks(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        department_id: UUID | None = None,
        is_active: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingQuestionBank]:
        stmt = select(TrainingQuestionBank).where(
            TrainingQuestionBank.organization_id == org_id
        )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    TrainingQuestionBank.name.ilike(pattern),
                    TrainingQuestionBank.description.ilike(pattern),
                )
            )
        if department_id:
            stmt = stmt.where(TrainingQuestionBank.department_id == department_id)
        if is_active is not None:
            stmt = stmt.where(TrainingQuestionBank.is_active == is_active)
        stmt = stmt.order_by(TrainingQuestionBank.name)
        return _paginated_result(self.db, stmt, pagination)

    def get_bank(self, org_id: UUID, bank_id: UUID) -> TrainingQuestionBank:
        bank = self.db.scalar(
            select(TrainingQuestionBank)
            .options(
                selectinload(TrainingQuestionBank.questions).selectinload(
                    TrainingQuestion.options
                ),
                selectinload(TrainingQuestionBank.questions)
                .selectinload(TrainingQuestion.tag_links)
                .selectinload(TrainingQuestionTagMap.tag),
            )
            .where(
                TrainingQuestionBank.organization_id == org_id,
                TrainingQuestionBank.id == bank_id,
            )
        )
        if not bank:
            raise NotFoundError("Training question bank not found")
        return bank

    def get_question(self, org_id: UUID, question_id: UUID) -> TrainingQuestion:
        return self._get_question(org_id, question_id)

    def list_questions(
        self,
        org_id: UUID,
        *,
        bank_id: UUID | None = None,
        search: str | None = None,
        question_type: TrainingQuestionType | None = None,
        difficulty_level: TrainingQuestionDifficulty | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingQuestion]:
        stmt = (
            select(TrainingQuestion)
            .options(
                selectinload(TrainingQuestion.question_bank),
                selectinload(TrainingQuestion.options),
                selectinload(TrainingQuestion.tag_links).selectinload(
                    TrainingQuestionTagMap.tag
                ),
            )
            .where(TrainingQuestion.organization_id == org_id)
        )
        if bank_id:
            stmt = stmt.where(TrainingQuestion.question_bank_id == bank_id)
        if search:
            stmt = stmt.where(TrainingQuestion.question_text.ilike(f"%{search}%"))
        if question_type:
            stmt = stmt.where(TrainingQuestion.question_type == question_type)
        if difficulty_level:
            stmt = stmt.where(TrainingQuestion.difficulty_level == difficulty_level)
        stmt = stmt.order_by(TrainingQuestion.created_at.desc())
        return _paginated_result(self.db, stmt, pagination)

    def create_bank(
        self,
        org_id: UUID,
        *,
        name: str,
        description: str | None = None,
        category: str | None = None,
        department_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> TrainingQuestionBank:
        if not name.strip():
            raise ValidationError("Question bank name is required")
        bank = TrainingQuestionBank(
            organization_id=org_id,
            name=name.strip(),
            description=description,
            category=category,
            department_id=department_id,
            created_by=created_by,
        )
        self.db.add(bank)
        self.db.flush()
        self._audit(
            org_id,
            "training_question_bank",
            bank.id,
            AuditAction.INSERT,
            new_values={"name": bank.name},
            user_id=created_by,
        )
        return bank

    def update_bank(
        self,
        org_id: UUID,
        bank_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        category: str | None = None,
        department_id: UUID | None = None,
        is_active: bool | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingQuestionBank:
        bank = self.get_bank(org_id, bank_id)
        old_values = {"name": bank.name, "is_active": bank.is_active}
        if name is not None:
            if not name.strip():
                raise ValidationError("Question bank name is required")
            bank.name = name.strip()
        if description is not None:
            bank.description = description
        if category is not None:
            bank.category = category
        if department_id is not None:
            bank.department_id = department_id
        if is_active is not None:
            bank.is_active = is_active
        self.db.flush()
        self._audit(
            org_id,
            "training_question_bank",
            bank.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={"name": bank.name, "is_active": bank.is_active},
            user_id=actor_id,
        )
        return bank

    def create_question(
        self,
        org_id: UUID,
        bank_id: UUID,
        *,
        question_type: TrainingQuestionType,
        question_text: str,
        difficulty_level: TrainingQuestionDifficulty = TrainingQuestionDifficulty.MEDIUM,
        points: Decimal | int | float | str | None = None,
        options: Sequence[dict[str, Any]] | None = None,
        tag_names: Sequence[str] | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingQuestion:
        bank = self.get_bank(org_id, bank_id)
        if not bank.is_active:
            raise ValidationError("Cannot add questions to an inactive question bank")
        if not question_text.strip():
            raise ValidationError("Question text is required")
        question = TrainingQuestion(
            organization_id=org_id,
            question_bank_id=bank_id,
            question_type=question_type,
            question_text=question_text.strip(),
            difficulty_level=difficulty_level,
            points=_coerce_decimal(points, "1.00"),
        )
        self.db.add(question)
        self.db.flush()
        if options or question_type in {
            TrainingQuestionType.MULTIPLE_CHOICE,
            TrainingQuestionType.MULTIPLE_SELECT,
            TrainingQuestionType.TRUE_FALSE,
            TrainingQuestionType.FILL_GAP,
        }:
            self.replace_options(org_id, question.id, options or [], actor_id=actor_id)
        if tag_names:
            self.set_question_tags(org_id, question.id, tag_names, actor_id=actor_id)
        self._audit(
            org_id,
            "training_question",
            question.id,
            AuditAction.INSERT,
            new_values={
                "question_bank_id": str(bank_id),
                "question_type": question.question_type.value,
            },
            user_id=actor_id,
        )
        return self._get_question(org_id, question.id)

    def update_question(
        self,
        org_id: UUID,
        question_id: UUID,
        *,
        question_type: TrainingQuestionType | None = None,
        question_text: str | None = None,
        difficulty_level: TrainingQuestionDifficulty | None = None,
        points: Decimal | int | float | str | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingQuestion:
        question = self._get_question(org_id, question_id)
        old_values = {
            "question_type": question.question_type.value,
            "difficulty_level": question.difficulty_level.value,
            "points": str(question.points),
        }
        if question_type is not None:
            question.question_type = question_type
        if question_text is not None:
            if not question_text.strip():
                raise ValidationError("Question text is required")
            question.question_text = question_text.strip()
        if difficulty_level is not None:
            question.difficulty_level = difficulty_level
        if points is not None:
            question.points = _coerce_decimal(points, "1.00")
        self.db.flush()
        self._audit(
            org_id,
            "training_question",
            question.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={
                "question_type": question.question_type.value,
                "difficulty_level": question.difficulty_level.value,
                "points": str(question.points),
            },
            user_id=actor_id,
        )
        return question

    def replace_options(
        self,
        org_id: UUID,
        question_id: UUID,
        options: Sequence[dict[str, Any]],
        *,
        actor_id: UUID | None = None,
    ) -> list[TrainingQuestionOption]:
        question = self._get_question(org_id, question_id)
        if question.question_type in {
            TrainingQuestionType.MULTIPLE_CHOICE,
            TrainingQuestionType.MULTIPLE_SELECT,
            TrainingQuestionType.TRUE_FALSE,
            TrainingQuestionType.FILL_GAP,
        } and not options:
            raise ValidationError("Objective questions require options")
        for option in list(question.options):
            self.db.delete(option)
        self.db.flush()
        created: list[TrainingQuestionOption] = []
        for item in options:
            text = str(item.get("option_text") or "").strip()
            if not text:
                raise ValidationError("Option text is required")
            option = TrainingQuestionOption(
                organization_id=org_id,
                question_id=question_id,
                option_text=text,
                is_correct=bool(item.get("is_correct")),
            )
            self.db.add(option)
            created.append(option)
        self.db.flush()
        self._validate_correct_option_count(question, created)
        self._audit(
            org_id,
            "training_question_option",
            question_id,
            AuditAction.UPDATE,
            new_values={"option_count": len(created)},
            user_id=actor_id,
            reason="Question options replaced",
        )
        return created

    def create_tag(self, org_id: UUID, name: str) -> TrainingQuestionTag:
        normalized = name.strip()
        if not normalized:
            raise ValidationError("Tag name is required")
        existing = self.db.scalar(
            select(TrainingQuestionTag).where(
                TrainingQuestionTag.organization_id == org_id,
                func.lower(TrainingQuestionTag.name) == normalized.lower(),
            )
        )
        if existing:
            return existing
        tag = TrainingQuestionTag(organization_id=org_id, name=normalized)
        self.db.add(tag)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Question tag already exists") from exc
        return tag

    def set_question_tags(
        self,
        org_id: UUID,
        question_id: UUID,
        tag_names: Sequence[str],
        *,
        actor_id: UUID | None = None,
    ) -> list[TrainingQuestionTagMap]:
        question = self._get_question(org_id, question_id)
        for link in list(question.tag_links):
            self.db.delete(link)
        self.db.flush()
        links: list[TrainingQuestionTagMap] = []
        for tag_name in tag_names:
            tag = self.create_tag(org_id, tag_name)
            link = TrainingQuestionTagMap(
                organization_id=org_id,
                question_id=question_id,
                tag_id=tag.id,
            )
            self.db.add(link)
            links.append(link)
        self.db.flush()
        self._audit(
            org_id,
            "training_question_tag_map",
            question_id,
            AuditAction.UPDATE,
            new_values={"tags": list(tag_names)},
            user_id=actor_id,
            reason="Question tags updated",
        )
        return links

    def _validate_correct_option_count(
        self,
        question: TrainingQuestion,
        options: Sequence[TrainingQuestionOption] | None = None,
    ) -> None:
        source_options = options if options is not None else question.options
        correct_count = sum(1 for option in source_options if option.is_correct)
        if question.question_type == TrainingQuestionType.MULTIPLE_CHOICE and correct_count != 1:
            raise ValidationError("Multiple choice questions require exactly one correct option")
        if question.question_type == TrainingQuestionType.TRUE_FALSE and correct_count != 1:
            raise ValidationError("True/false questions require exactly one correct option")
        if question.question_type == TrainingQuestionType.MULTIPLE_SELECT and correct_count < 1:
            raise ValidationError("Multiple select questions require at least one correct option")
        if question.question_type == TrainingQuestionType.FILL_GAP and correct_count < 1:
            raise ValidationError("Fill-gap questions require at least one accepted answer")


class AssessmentService(LearningAssessmentBaseService):
    """Assessment and assessment-question operations."""

    def get_assessment(self, org_id: UUID, assessment_id: UUID) -> TrainingAssessment:
        assessment = self.db.scalar(
            select(TrainingAssessment)
            .options(
                selectinload(TrainingAssessment.module).selectinload(
                    TrainingCourseModule.course
                ),
                selectinload(TrainingAssessment.assessment_questions)
                .selectinload(TrainingAssessmentQuestion.question)
                .selectinload(TrainingQuestion.options),
            )
            .where(
                TrainingAssessment.organization_id == org_id,
                TrainingAssessment.id == assessment_id,
            )
        )
        if not assessment:
            raise NotFoundError("Training assessment not found")
        return assessment

    def create_assessment(
        self,
        org_id: UUID,
        module_id: UUID,
        *,
        title: str,
        description: str | None = None,
        pass_mark: Decimal | int | float | str | None = None,
        duration_minutes: int | None = None,
        max_attempts: int = 1,
        randomize_questions: bool = False,
        randomize_options: bool = False,
        actor_id: UUID | None = None,
    ) -> TrainingAssessment:
        self._get_module(org_id, module_id)
        if not title.strip():
            raise ValidationError("Assessment title is required")
        if max_attempts < 1:
            raise ValidationError("Assessment must allow at least one attempt")
        assessment = TrainingAssessment(
            organization_id=org_id,
            module_id=module_id,
            title=title.strip(),
            description=description,
            pass_mark=_coerce_decimal(pass_mark, "70.00"),
            duration_minutes=duration_minutes,
            max_attempts=max_attempts,
            randomize_questions=randomize_questions,
            randomize_options=randomize_options,
            status=TrainingAssessmentStatus.DRAFT,
        )
        self.db.add(assessment)
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment",
            assessment.id,
            AuditAction.INSERT,
            new_values={"title": assessment.title, "module_id": str(module_id)},
            user_id=actor_id,
        )
        return assessment

    def update_assessment(
        self,
        org_id: UUID,
        assessment_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        pass_mark: Decimal | int | float | str | None = None,
        duration_minutes: int | None = None,
        max_attempts: int | None = None,
        randomize_questions: bool | None = None,
        randomize_options: bool | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingAssessment:
        assessment = self._get_assessment(org_id, assessment_id)
        old_values = {"title": assessment.title, "status": assessment.status.value}
        if title is not None:
            if not title.strip():
                raise ValidationError("Assessment title is required")
            assessment.title = title.strip()
        if description is not None:
            assessment.description = description
        if pass_mark is not None:
            assessment.pass_mark = _coerce_decimal(pass_mark, "70.00")
        if duration_minutes is not None:
            assessment.duration_minutes = duration_minutes
        if max_attempts is not None:
            if max_attempts < 1:
                raise ValidationError("Assessment must allow at least one attempt")
            assessment.max_attempts = max_attempts
        if randomize_questions is not None:
            assessment.randomize_questions = randomize_questions
        if randomize_options is not None:
            assessment.randomize_options = randomize_options
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment",
            assessment.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={"title": assessment.title, "status": assessment.status.value},
            user_id=actor_id,
        )
        return assessment

    def publish_assessment(
        self,
        org_id: UUID,
        assessment_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingAssessment:
        assessment = self.db.scalar(
            select(TrainingAssessment)
            .options(selectinload(TrainingAssessment.assessment_questions))
            .where(
                TrainingAssessment.organization_id == org_id,
                TrainingAssessment.id == assessment_id,
            )
        )
        if not assessment:
            raise NotFoundError("Training assessment not found")
        if not assessment.assessment_questions:
            raise ValidationError("Assessment must have questions before publishing")
        assessment.status = TrainingAssessmentStatus.PUBLISHED
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment",
            assessment.id,
            AuditAction.UPDATE,
            new_values={"status": assessment.status.value},
            user_id=actor_id,
            reason="Assessment published",
        )
        return assessment

    def archive_assessment(
        self,
        org_id: UUID,
        assessment_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingAssessment:
        assessment = self._get_assessment(org_id, assessment_id)
        assessment.status = TrainingAssessmentStatus.ARCHIVED
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment",
            assessment.id,
            AuditAction.UPDATE,
            new_values={"status": assessment.status.value},
            user_id=actor_id,
            reason="Assessment archived",
        )
        return assessment

    def attach_question(
        self,
        org_id: UUID,
        assessment_id: UUID,
        question_id: UUID,
        *,
        sequence: int | None = None,
        points_override: Decimal | int | float | str | None = None,
        is_required: bool = True,
        actor_id: UUID | None = None,
    ) -> TrainingAssessmentQuestion:
        assessment = self._get_assessment(org_id, assessment_id)
        question = self._get_question(org_id, question_id)
        self._ensure_same_org(assessment.organization_id, question.organization_id)
        if sequence is None:
            sequence = (
                self.db.scalar(
                    select(
                        func.coalesce(func.max(TrainingAssessmentQuestion.sequence), 0)
                    ).where(
                        TrainingAssessmentQuestion.organization_id == org_id,
                        TrainingAssessmentQuestion.assessment_id == assessment_id,
                    )
                )
                or 0
            ) + 1
        link = TrainingAssessmentQuestion(
            organization_id=org_id,
            assessment_id=assessment_id,
            question_id=question_id,
            sequence=sequence,
            points_override=(
                _coerce_decimal(points_override, "0.00")
                if points_override is not None
                else None
            ),
            is_required=is_required,
        )
        self.db.add(link)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Question is already attached or sequence is in use") from exc
        self._audit(
            org_id,
            "training_assessment_question",
            link.id,
            AuditAction.INSERT,
            new_values={
                "assessment_id": str(assessment_id),
                "question_id": str(question_id),
            },
            user_id=actor_id,
        )
        return link

    def detach_question(
        self,
        org_id: UUID,
        assessment_id: UUID,
        question_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> None:
        link = self.db.scalar(
            select(TrainingAssessmentQuestion).where(
                TrainingAssessmentQuestion.organization_id == org_id,
                TrainingAssessmentQuestion.assessment_id == assessment_id,
                TrainingAssessmentQuestion.question_id == question_id,
            )
        )
        if not link:
            return
        link_id = link.id
        self.db.delete(link)
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment_question",
            link_id,
            AuditAction.DELETE,
            old_values={
                "assessment_id": str(assessment_id),
                "question_id": str(question_id),
            },
            user_id=actor_id,
        )

    def update_assessment_question(
        self,
        org_id: UUID,
        assessment_id: UUID,
        question_id: UUID,
        *,
        sequence: int | None = None,
        points_override: Decimal | int | float | str | None = None,
        is_required: bool | None = None,
        actor_id: UUID | None = None,
    ) -> TrainingAssessmentQuestion:
        link = self.db.scalar(
            select(TrainingAssessmentQuestion).where(
                TrainingAssessmentQuestion.organization_id == org_id,
                TrainingAssessmentQuestion.assessment_id == assessment_id,
                TrainingAssessmentQuestion.question_id == question_id,
            )
        )
        if not link:
            raise NotFoundError("Assessment question link not found")
        old_values = {
            "sequence": link.sequence,
            "points_override": str(link.points_override)
            if link.points_override is not None
            else None,
            "is_required": link.is_required,
        }
        if sequence is not None:
            link.sequence = sequence
        if points_override is not None:
            link.points_override = _coerce_decimal(points_override, "0.00")
        if is_required is not None:
            link.is_required = is_required
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment_question",
            link.id,
            AuditAction.UPDATE,
            old_values=old_values,
            new_values={
                "sequence": link.sequence,
                "points_override": str(link.points_override)
                if link.points_override is not None
                else None,
                "is_required": link.is_required,
            },
            user_id=actor_id,
        )
        return link

    def reorder_questions(
        self,
        org_id: UUID,
        assessment_id: UUID,
        ordered_question_ids: Sequence[UUID],
        *,
        actor_id: UUID | None = None,
    ) -> list[TrainingAssessmentQuestion]:
        assessment = self.get_assessment(org_id, assessment_id)
        links = list(assessment.assessment_questions)
        link_by_question_id = {link.question_id: link for link in links}
        if set(link_by_question_id) != set(ordered_question_ids):
            raise ValidationError(
                "Reorder payload must include every assessment question"
            )
        for index, question_id in enumerate(ordered_question_ids, start=1):
            link_by_question_id[question_id].sequence = index
        self.db.flush()
        self._audit(
            org_id,
            "training_assessment_question",
            assessment_id,
            AuditAction.UPDATE,
            new_values={
                "ordered_question_ids": [str(value) for value in ordered_question_ids]
            },
            user_id=actor_id,
            reason="Assessment questions reordered",
        )
        return list(self.get_assessment(org_id, assessment_id).assessment_questions)


class AssignmentService(LearningAssessmentBaseService):
    """Course assignment operations for employees, departments, and roles."""

    VALID_ASSIGNMENT_SOURCES = {"employee", "department", "role"}

    def assign_employee(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_id: UUID,
        *,
        assigned_by: UUID | None = None,
        due_date: date | None = None,
        is_mandatory: bool | None = None,
        assignment_source: str = "employee",
        assignment_source_id: UUID | None = None,
        notify: bool = True,
    ) -> TrainingCourseAssignment:
        course = self._get_course(org_id, course_id)
        if course.status != TrainingCourseStatus.PUBLISHED:
            raise ValidationError("Only published courses can be assigned")
        employee = self._get_employee(org_id, employee_id)
        assignment_source = assignment_source.strip().lower()
        if assignment_source not in self.VALID_ASSIGNMENT_SOURCES:
            raise ValidationError("Invalid assignment source")
        resolved_mandatory = course.is_mandatory if is_mandatory is None else is_mandatory
        existing = self.db.scalar(
            select(TrainingCourseAssignment).where(
                TrainingCourseAssignment.organization_id == org_id,
                TrainingCourseAssignment.course_id == course_id,
                TrainingCourseAssignment.employee_id == employee_id,
            )
        )
        if existing:
            if due_date is not None:
                existing.due_date = due_date
            existing.course_version_number = course.version_number
            existing.assignment_source = assignment_source
            existing.assignment_source_id = assignment_source_id or employee_id
            existing.is_mandatory = resolved_mandatory
            self._ensure_progress(org_id, course, employee_id)
            self.db.flush()
            if notify:
                self._notify_assignment(org_id, existing, course, employee, assigned_by)
            return existing
        assignment = TrainingCourseAssignment(
            organization_id=org_id,
            course_id=course_id,
            employee_id=employee_id,
            assigned_by=assigned_by,
            due_date=due_date,
            assignment_source=assignment_source,
            assignment_source_id=assignment_source_id or employee_id,
            is_mandatory=resolved_mandatory,
            course_version_number=course.version_number,
        )
        self.db.add(assignment)
        self._ensure_progress(org_id, course, employee_id)
        self.db.flush()
        self._audit(
            org_id,
            "training_course_assignment",
            assignment.id,
            AuditAction.INSERT,
            new_values={
                "course_id": str(course_id),
                "employee_id": str(employee_id),
                "course_version_number": course.version_number,
                "assignment_source": assignment.assignment_source,
                "assignment_source_id": str(assignment.assignment_source_id)
                if assignment.assignment_source_id
                else None,
                "is_mandatory": assignment.is_mandatory,
            },
            user_id=assigned_by,
        )
        if notify:
            self._notify_assignment(org_id, assignment, course, employee, assigned_by)
        return assignment

    def assign_employees(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_ids: Iterable[UUID],
        *,
        assigned_by: UUID | None = None,
        due_date: date | None = None,
        is_mandatory: bool | None = None,
        assignment_source: str = "employee",
        assignment_source_id: UUID | None = None,
    ) -> list[TrainingCourseAssignment]:
        assignments: list[TrainingCourseAssignment] = []
        for employee_id in employee_ids:
            assignments.append(
                self.assign_employee(
                    org_id,
                    course_id,
                    employee_id,
                    assigned_by=assigned_by,
                    due_date=due_date,
                    is_mandatory=is_mandatory,
                    assignment_source=assignment_source,
                    assignment_source_id=assignment_source_id,
                )
            )
        return assignments

    def assign_department(
        self,
        org_id: UUID,
        course_id: UUID,
        department_id: UUID,
        *,
        assigned_by: UUID | None = None,
        due_date: date | None = None,
        is_mandatory: bool | None = None,
    ) -> list[TrainingCourseAssignment]:
        employees = list(
            self.db.scalars(
                select(Employee.employee_id).where(
                    Employee.organization_id == org_id,
                    Employee.department_id == department_id,
                )
            ).all()
        )
        return self.assign_employees(
            org_id,
            course_id,
            employees,
            assigned_by=assigned_by,
            due_date=due_date,
            is_mandatory=is_mandatory,
            assignment_source="department",
            assignment_source_id=department_id,
        )

    def assign_role(
        self,
        org_id: UUID,
        course_id: UUID,
        role_id: UUID,
        *,
        assigned_by: UUID | None = None,
        due_date: date | None = None,
        is_mandatory: bool | None = None,
    ) -> list[TrainingCourseAssignment]:
        role = self.db.scalar(select(Role).where(Role.id == role_id, Role.is_active.is_(True)))
        if not role:
            raise NotFoundError("Role not found")
        employee_ids = list(
            self.db.scalars(
                select(Employee.employee_id)
                .join(PersonRole, PersonRole.person_id == Employee.person_id)
                .where(
                    Employee.organization_id == org_id,
                    PersonRole.role_id == role_id,
                )
            ).all()
        )
        return self.assign_employees(
            org_id,
            course_id,
            employee_ids,
            assigned_by=assigned_by,
            due_date=due_date,
            is_mandatory=is_mandatory,
            assignment_source="role",
            assignment_source_id=role_id,
        )

    def list_assignments(
        self,
        org_id: UUID,
        *,
        course_id: UUID | None = None,
        employee_id: UUID | None = None,
        assignment_source: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingCourseAssignment]:
        stmt = (
            select(TrainingCourseAssignment)
            .options(
                joinedload(TrainingCourseAssignment.course),
                joinedload(TrainingCourseAssignment.employee).joinedload(
                    Employee.person
                ),
            )
            .where(TrainingCourseAssignment.organization_id == org_id)
        )
        if course_id:
            stmt = stmt.where(TrainingCourseAssignment.course_id == course_id)
        if employee_id:
            stmt = stmt.where(TrainingCourseAssignment.employee_id == employee_id)
        if assignment_source:
            stmt = stmt.where(
                TrainingCourseAssignment.assignment_source == assignment_source
            )
        stmt = stmt.order_by(TrainingCourseAssignment.assigned_at.desc())
        return _paginated_result(self.db, stmt, pagination)

    def _notify_assignment(
        self,
        org_id: UUID,
        assignment: TrainingCourseAssignment,
        course: TrainingCourse,
        employee: Employee,
        actor_id: UUID | None,
    ) -> None:
        if not employee.person_id:
            return
        service = NotificationService()
        service.create(
            self.db,
            organization_id=org_id,
            recipient_id=employee.person_id,
            entity_type=EntityType.SYSTEM,
            entity_id=assignment.id,
            notification_type=NotificationType.ASSIGNED,
            title="Training course assigned",
            message=f"You have been assigned '{course.title}'.",
            channel=NotificationChannel.IN_APP,
            action_url="/people/training",
            actor_id=actor_id,
        )
        if assignment.due_date:
            service.create(
                self.db,
                organization_id=org_id,
                recipient_id=employee.person_id,
                entity_type=EntityType.SYSTEM,
                entity_id=assignment.id,
                notification_type=NotificationType.DUE_SOON,
                title="Training course due",
                message=(
                    f"'{course.title}' is due on "
                    f"{assignment.due_date.isoformat()}."
                ),
                channel=NotificationChannel.IN_APP,
                action_url="/people/training",
                actor_id=actor_id,
            )

    def _ensure_progress(
        self,
        org_id: UUID,
        course: TrainingCourse,
        employee_id: UUID,
    ) -> TrainingCourseProgress:
        progress = self.db.scalar(
            select(TrainingCourseProgress).where(
                TrainingCourseProgress.organization_id == org_id,
                TrainingCourseProgress.course_id == course.id,
                TrainingCourseProgress.employee_id == employee_id,
            )
        )
        if progress:
            return progress
        progress = TrainingCourseProgress(
            organization_id=org_id,
            course_id=course.id,
            employee_id=employee_id,
            course_version_number=course.version_number,
            completion_percentage=Decimal("0.00"),
            status=TrainingProgressStatus.NOT_STARTED,
        )
        self.db.add(progress)
        return progress


class ProgressService(LearningAssessmentBaseService):
    """Learner progress operations."""

    def get_assigned_course(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_id: UUID,
    ) -> TrainingCourseAssignment:
        assignment = self.db.scalar(
            select(TrainingCourseAssignment)
            .options(joinedload(TrainingCourseAssignment.course))
            .where(
                TrainingCourseAssignment.organization_id == org_id,
                TrainingCourseAssignment.course_id == course_id,
                TrainingCourseAssignment.employee_id == employee_id,
            )
        )
        if not assignment:
            raise NotFoundError("Course assignment not found")
        return assignment

    def get_course_progress(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_id: UUID,
    ) -> TrainingCourseProgress:
        progress = self.db.scalar(
            select(TrainingCourseProgress).where(
                TrainingCourseProgress.organization_id == org_id,
                TrainingCourseProgress.course_id == course_id,
                TrainingCourseProgress.employee_id == employee_id,
            )
        )
        if not progress:
            raise NotFoundError("Course progress not found")
        return progress

    def list_employee_courses(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        status: TrainingProgressStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingCourseProgress]:
        stmt = (
            select(TrainingCourseProgress)
            .options(
                joinedload(TrainingCourseProgress.course).joinedload(
                    TrainingCourse.department
                )
            )
            .where(
                TrainingCourseProgress.organization_id == org_id,
                TrainingCourseProgress.employee_id == employee_id,
            )
        )
        if status:
            stmt = stmt.where(TrainingCourseProgress.status == status)
        stmt = stmt.order_by(TrainingCourseProgress.updated_at.desc().nullslast())
        return _paginated_result(self.db, stmt, pagination)

    def is_module_unlocked(
        self,
        org_id: UUID,
        module_id: UUID,
        employee_id: UUID,
    ) -> bool:
        module = self._get_module(org_id, module_id)
        self.get_assigned_course(org_id, module.course_id, employee_id)
        previous_modules = list(
            self.db.scalars(
                select(TrainingCourseModule)
                .where(
                    TrainingCourseModule.organization_id == org_id,
                    TrainingCourseModule.course_id == module.course_id,
                    TrainingCourseModule.sequence < module.sequence,
                )
                .order_by(TrainingCourseModule.sequence)
            ).all()
        )
        return all(
            self.is_module_completed(org_id, item.id, employee_id)
            for item in previous_modules
        )

    def assert_module_unlocked(
        self,
        org_id: UUID,
        module_id: UUID,
        employee_id: UUID,
    ) -> None:
        if not self.is_module_unlocked(org_id, module_id, employee_id):
            raise ValidationError("Previous modules must be completed first")

    def is_module_completed(
        self,
        org_id: UUID,
        module_id: UUID,
        employee_id: UUID,
    ) -> bool:
        module = self.db.scalar(
            select(TrainingCourseModule)
            .options(
                selectinload(TrainingCourseModule.lessons),
                selectinload(TrainingCourseModule.assessments),
            )
            .where(
                TrainingCourseModule.organization_id == org_id,
                TrainingCourseModule.id == module_id,
            )
        )
        if not module:
            raise NotFoundError("Training course module not found")
        lesson_ids = [lesson.id for lesson in module.lessons]
        assessment_ids = [
            assessment.id
            for assessment in module.assessments
            if assessment.status == TrainingAssessmentStatus.PUBLISHED
        ]
        if not lesson_ids and not assessment_ids:
            return True
        if lesson_ids:
            completed_lessons = (
                self.db.scalar(
                    select(func.count()).select_from(TrainingLessonProgress).where(
                        TrainingLessonProgress.organization_id == org_id,
                        TrainingLessonProgress.employee_id == employee_id,
                        TrainingLessonProgress.lesson_id.in_(lesson_ids),
                        TrainingLessonProgress.completed.is_(True),
                    )
                )
                or 0
            )
            if completed_lessons != len(lesson_ids):
                return False
        for assessment_id in assessment_ids:
            passed_attempt = self.db.scalar(
                select(TrainingExamAttempt.id).where(
                    TrainingExamAttempt.organization_id == org_id,
                    TrainingExamAttempt.employee_id == employee_id,
                    TrainingExamAttempt.assessment_id == assessment_id,
                    TrainingExamAttempt.submitted_at.is_not(None),
                    TrainingExamAttempt.passed.is_(True),
                )
            )
            if not passed_attempt:
                return False
        return True

    def get_course_learning_state(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_id: UUID,
    ) -> dict[str, Any]:
        assignment = self.get_assigned_course(org_id, course_id, employee_id)
        course = self.db.scalar(
            select(TrainingCourse)
            .options(
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.lessons
                ),
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.assessments
                ),
            )
            .where(
                TrainingCourse.organization_id == org_id,
                TrainingCourse.id == course_id,
            )
        )
        if not course:
            raise NotFoundError("Training course not found")
        lesson_progress = {
            row.lesson_id: row
            for row in self.db.scalars(
                select(TrainingLessonProgress).where(
                    TrainingLessonProgress.organization_id == org_id,
                    TrainingLessonProgress.employee_id == employee_id,
                    TrainingLessonProgress.lesson_id.in_(
                        [
                            lesson.id
                            for module in course.modules
                            for lesson in module.lessons
                        ]
                        or [UUID(int=0)]
                    ),
                )
            ).all()
        }
        latest_attempts: dict[UUID, TrainingExamAttempt] = {}
        assessment_ids = [
            assessment.id
            for module in course.modules
            for assessment in module.assessments
        ]
        if assessment_ids:
            attempts = self.db.scalars(
                select(TrainingExamAttempt)
                .where(
                    TrainingExamAttempt.organization_id == org_id,
                    TrainingExamAttempt.employee_id == employee_id,
                    TrainingExamAttempt.assessment_id.in_(assessment_ids),
                )
                .order_by(TrainingExamAttempt.started_at.desc())
            ).all()
            for attempt in attempts:
                latest_attempts.setdefault(attempt.assessment_id, attempt)
        module_unlocked = {
            module.id: self.is_module_unlocked(org_id, module.id, employee_id)
            for module in course.modules
        }
        module_completed = {
            module.id: self.is_module_completed(org_id, module.id, employee_id)
            for module in course.modules
        }
        return {
            "assignment": assignment,
            "course": course,
            "progress": self.get_course_progress(org_id, course_id, employee_id),
            "lesson_progress": lesson_progress,
            "latest_attempts": latest_attempts,
            "module_unlocked": module_unlocked,
            "module_completed": module_completed,
        }

    def mark_lesson_completed(
        self,
        org_id: UUID,
        lesson_id: UUID,
        employee_id: UUID,
        *,
        completed: bool = True,
        actor_id: UUID | None = None,
    ) -> TrainingLessonProgress:
        lesson = self.db.scalar(
            select(TrainingLesson)
            .join(TrainingCourseModule, TrainingCourseModule.id == TrainingLesson.module_id)
            .join(TrainingCourse, TrainingCourse.id == TrainingCourseModule.course_id)
            .options(joinedload(TrainingLesson.module).joinedload(TrainingCourseModule.course))
            .where(
                TrainingLesson.organization_id == org_id,
                TrainingLesson.id == lesson_id,
            )
        )
        if not lesson:
            raise NotFoundError("Training lesson not found")
        self.get_assigned_course(org_id, lesson.module.course_id, employee_id)
        self.assert_module_unlocked(org_id, lesson.module_id, employee_id)
        progress = self.db.scalar(
            select(TrainingLessonProgress).where(
                TrainingLessonProgress.organization_id == org_id,
                TrainingLessonProgress.lesson_id == lesson_id,
                TrainingLessonProgress.employee_id == employee_id,
            )
        )
        if not progress:
            progress = TrainingLessonProgress(
                organization_id=org_id,
                lesson_id=lesson_id,
                employee_id=employee_id,
            )
            self.db.add(progress)
        progress.completed = completed
        progress.completed_at = _now() if completed else None
        self.db.flush()
        self.recalculate_course_progress(
            org_id,
            lesson.module.course_id,
            employee_id,
            actor_id=actor_id,
        )
        self._audit(
            org_id,
            "training_lesson_progress",
            progress.id,
            AuditAction.UPDATE,
            new_values={"completed": progress.completed},
            user_id=actor_id,
        )
        return progress

    def recalculate_course_progress(
        self,
        org_id: UUID,
        course_id: UUID,
        employee_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingCourseProgress:
        course = self.db.scalar(
            select(TrainingCourse)
            .options(
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.lessons
                ),
                selectinload(TrainingCourse.modules).selectinload(
                    TrainingCourseModule.assessments
                ),
            )
            .where(
                TrainingCourse.organization_id == org_id,
                TrainingCourse.id == course_id,
            )
        )
        if not course:
            raise NotFoundError("Training course not found")
        progress = self.db.scalar(
            select(TrainingCourseProgress).where(
                TrainingCourseProgress.organization_id == org_id,
                TrainingCourseProgress.course_id == course_id,
                TrainingCourseProgress.employee_id == employee_id,
            )
        )
        if not progress:
            progress = TrainingCourseProgress(
                organization_id=org_id,
                course_id=course_id,
                employee_id=employee_id,
                course_version_number=course.version_number,
            )
            self.db.add(progress)
        lesson_ids = [lesson.id for module in course.modules for lesson in module.lessons]
        assessment_ids = [
            assessment.id
            for module in course.modules
            for assessment in module.assessments
            if assessment.status == TrainingAssessmentStatus.PUBLISHED
        ]
        required_count = len(lesson_ids) + len(assessment_ids)
        if not required_count:
            progress.completion_percentage = Decimal("0.00")
            progress.status = TrainingProgressStatus.NOT_STARTED
            self.db.flush()
            return progress
        completed_count = (
            self.db.scalar(
                select(func.count()).select_from(TrainingLessonProgress).where(
                    TrainingLessonProgress.organization_id == org_id,
                    TrainingLessonProgress.employee_id == employee_id,
                    TrainingLessonProgress.lesson_id.in_(lesson_ids),
                    TrainingLessonProgress.completed.is_(True),
                )
            )
            or 0
        )
        passed_assessments = 0
        for assessment_id in assessment_ids:
            passed_attempt = self.db.scalar(
                select(TrainingExamAttempt.id).where(
                    TrainingExamAttempt.organization_id == org_id,
                    TrainingExamAttempt.employee_id == employee_id,
                    TrainingExamAttempt.assessment_id == assessment_id,
                    TrainingExamAttempt.submitted_at.is_not(None),
                    TrainingExamAttempt.passed.is_(True),
                )
            )
            if passed_attempt:
                passed_assessments += 1
        completed_total = completed_count + passed_assessments
        percentage = (Decimal(completed_total) / Decimal(required_count)) * Decimal("100")
        progress.completion_percentage = percentage.quantize(Decimal("0.01"))
        if completed_total == 0:
            progress.status = TrainingProgressStatus.NOT_STARTED
        elif completed_total == required_count:
            progress.status = TrainingProgressStatus.COMPLETED
        else:
            progress.status = TrainingProgressStatus.IN_PROGRESS
        self.db.flush()
        self._audit(
            org_id,
            "training_course_progress",
            progress.id,
            AuditAction.UPDATE,
            new_values={
                "completion_percentage": str(progress.completion_percentage),
                "status": progress.status.value,
            },
            user_id=actor_id,
            reason="Course progress recalculated",
        )
        return progress


class ExamService(LearningAssessmentBaseService):
    """Assessment attempt and objective grading operations."""

    def start_attempt(
        self,
        org_id: UUID,
        assessment_id: UUID,
        employee_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingExamAttempt:
        assessment = self._load_assessment_for_exam(org_id, assessment_id)
        if assessment.status != TrainingAssessmentStatus.PUBLISHED:
            raise ValidationError("Only published assessments can be attempted")
        ProgressService(self.db).get_assigned_course(
            org_id,
            assessment.module.course_id,
            employee_id,
        )
        ProgressService(self.db).assert_module_unlocked(
            org_id,
            assessment.module_id,
            employee_id,
        )
        attempt_count = (
            self.db.scalar(
                select(func.count()).select_from(TrainingExamAttempt).where(
                    TrainingExamAttempt.organization_id == org_id,
                    TrainingExamAttempt.assessment_id == assessment_id,
                    TrainingExamAttempt.employee_id == employee_id,
                )
            )
            or 0
        )
        if attempt_count >= assessment.max_attempts:
            raise ConflictError("Maximum assessment attempts reached")
        course = assessment.module.course
        attempt = TrainingExamAttempt(
            organization_id=org_id,
            assessment_id=assessment_id,
            employee_id=employee_id,
            course_version_number=course.version_number,
            assessment_snapshot_json=self._assessment_snapshot(assessment),
        )
        self.db.add(attempt)
        self.db.flush()
        self._audit(
            org_id,
            "training_exam_attempt",
            attempt.id,
            AuditAction.INSERT,
            new_values={
                "assessment_id": str(assessment_id),
                "employee_id": str(employee_id),
                "course_version_number": attempt.course_version_number,
            },
            user_id=actor_id,
        )
        return attempt

    def get_attempt(
        self,
        org_id: UUID,
        attempt_id: UUID,
        employee_id: UUID | None = None,
    ) -> TrainingExamAttempt:
        stmt = (
            select(TrainingExamAttempt)
            .options(
                joinedload(TrainingExamAttempt.assessment).joinedload(
                    TrainingAssessment.module
                ).joinedload(TrainingCourseModule.course),
                selectinload(TrainingExamAttempt.answers),
            )
            .where(
                TrainingExamAttempt.organization_id == org_id,
                TrainingExamAttempt.id == attempt_id,
            )
        )
        if employee_id:
            stmt = stmt.where(TrainingExamAttempt.employee_id == employee_id)
        attempt = self.db.scalar(stmt)
        if not attempt:
            raise NotFoundError("Training exam attempt not found")
        return attempt

    def list_attempts(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        assessment_id: UUID | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingExamAttempt]:
        stmt = (
            select(TrainingExamAttempt)
            .options(
                joinedload(TrainingExamAttempt.assessment)
                .joinedload(TrainingAssessment.module)
                .joinedload(TrainingCourseModule.course)
            )
            .where(
                TrainingExamAttempt.organization_id == org_id,
                TrainingExamAttempt.employee_id == employee_id,
            )
        )
        if assessment_id:
            stmt = stmt.where(TrainingExamAttempt.assessment_id == assessment_id)
        stmt = stmt.order_by(TrainingExamAttempt.started_at.desc())
        return _paginated_result(self.db, stmt, pagination)

    def list_pending_manual_answers(
        self,
        org_id: UUID,
        *,
        course_id: UUID | None = None,
        assessment_id: UUID | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingExamAnswer]:
        stmt = (
            select(TrainingExamAnswer)
            .join(TrainingExamAttempt, TrainingExamAttempt.id == TrainingExamAnswer.attempt_id)
            .join(TrainingAssessment, TrainingAssessment.id == TrainingExamAttempt.assessment_id)
            .join(TrainingCourseModule, TrainingCourseModule.id == TrainingAssessment.module_id)
            .options(
                joinedload(TrainingExamAnswer.attempt)
                .joinedload(TrainingExamAttempt.assessment)
                .joinedload(TrainingAssessment.module)
                .joinedload(TrainingCourseModule.course),
                joinedload(TrainingExamAnswer.attempt).joinedload(
                    TrainingExamAttempt.employee
                ).joinedload(Employee.person),
            )
            .where(
                TrainingExamAnswer.organization_id == org_id,
                TrainingExamAttempt.submitted_at.is_not(None),
                TrainingExamAnswer.question_type_snapshot.in_(
                    [
                        TrainingQuestionType.SHORT_ANSWER.value,
                        TrainingQuestionType.ESSAY.value,
                    ]
                ),
                TrainingExamAnswer.score_awarded.is_(None),
            )
        )
        if course_id:
            stmt = stmt.where(TrainingCourseModule.course_id == course_id)
        if assessment_id:
            stmt = stmt.where(TrainingAssessment.id == assessment_id)
        stmt = stmt.order_by(TrainingExamAttempt.submitted_at.asc())
        return _paginated_result(self.db, stmt, pagination)

    def grade_manual_answer(
        self,
        org_id: UUID,
        answer_id: UUID,
        *,
        score_awarded: Decimal | int | float | str,
        feedback: str | None = None,
        graded_by: UUID | None = None,
    ) -> TrainingExamAnswer:
        answer = self.db.scalar(
            select(TrainingExamAnswer)
            .options(
                joinedload(TrainingExamAnswer.attempt)
                .joinedload(TrainingExamAttempt.assessment)
                .selectinload(TrainingAssessment.assessment_questions)
            )
            .where(
                TrainingExamAnswer.organization_id == org_id,
                TrainingExamAnswer.id == answer_id,
            )
        )
        if not answer:
            raise NotFoundError("Training exam answer not found")
        if answer.question_type_snapshot not in {
            TrainingQuestionType.SHORT_ANSWER.value,
            TrainingQuestionType.ESSAY.value,
        }:
            raise ValidationError("Only manual-review answers can be graded here")
        max_points = self._answer_max_points(answer)
        score = _coerce_decimal(score_awarded, "0.00")
        if score < 0 or score > max_points:
            raise ValidationError("Score awarded must be within available points")
        answer.score_awarded = score
        answer.is_correct = score > Decimal("0.00")
        answer.feedback = feedback
        answer.graded_by = graded_by
        answer.graded_at = _now()
        self.db.flush()
        self._audit(
            org_id,
            "training_exam_answer",
            answer.id,
            AuditAction.UPDATE,
            new_values={
                "score_awarded": str(answer.score_awarded),
                "is_correct": answer.is_correct,
                "graded_at": answer.graded_at.isoformat(),
            },
            user_id=graded_by,
            reason="Manual answer graded",
        )
        return answer

    def finalize_manual_grading(
        self,
        org_id: UUID,
        attempt_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> TrainingExamAttempt:
        attempt = self.get_attempt(org_id, attempt_id)
        if not attempt.submitted_at:
            raise ValidationError("Attempt must be submitted before final grading")
        pending = [
            answer
            for answer in attempt.answers
            if answer.question_type_snapshot
            in {
                TrainingQuestionType.SHORT_ANSWER.value,
                TrainingQuestionType.ESSAY.value,
            }
            and answer.score_awarded is None
        ]
        if pending:
            raise ValidationError("All manual answers must be scored before finalizing")
        total_score = Decimal("0.00")
        total_points = Decimal("0.00")
        links = {
            link.question_id: link
            for link in attempt.assessment.assessment_questions
        }
        for answer in attempt.answers:
            link = links.get(answer.question_id)
            if not link:
                continue
            points = (
                link.points_override
                if link.points_override is not None
                else link.question.points
            )
            total_points += points
            total_score += answer.score_awarded or Decimal("0.00")
        attempt.score = (
            ((total_score / total_points) * Decimal("100")).quantize(Decimal("0.01"))
            if total_points
            else Decimal("0.00")
        )
        attempt.passed = attempt.score >= attempt.assessment.pass_mark
        self.db.flush()
        ProgressService(self.db).recalculate_course_progress(
            org_id,
            attempt.assessment.module.course_id,
            attempt.employee_id,
            actor_id=actor_id,
        )
        self._audit(
            org_id,
            "training_exam_attempt",
            attempt.id,
            AuditAction.UPDATE,
            new_values={"score": str(attempt.score), "passed": attempt.passed},
            user_id=actor_id,
            reason="Manual grading finalized",
        )
        return attempt

    def submit_attempt(
        self,
        org_id: UUID,
        attempt_id: UUID,
        answers: Sequence[dict[str, Any]],
        *,
        actor_id: UUID | None = None,
    ) -> TrainingExamAttempt:
        attempt = self.db.scalar(
            select(TrainingExamAttempt)
            .options(
                joinedload(TrainingExamAttempt.assessment).joinedload(
                    TrainingAssessment.module
                ),
                joinedload(TrainingExamAttempt.assessment)
                .selectinload(TrainingAssessment.assessment_questions)
                .joinedload(TrainingAssessmentQuestion.question)
                .selectinload(TrainingQuestion.options),
                selectinload(TrainingExamAttempt.answers),
            )
            .where(
                TrainingExamAttempt.organization_id == org_id,
                TrainingExamAttempt.id == attempt_id,
            )
        )
        if not attempt:
            raise NotFoundError("Training exam attempt not found")
        if attempt.submitted_at is not None:
            raise ConflictError("Assessment attempt has already been submitted")
        answer_by_question = {
            UUID(str(item["question_id"])): item.get("answer") for item in answers
        }
        total_score = Decimal("0.00")
        total_points = Decimal("0.00")
        has_manual_questions = False
        for link in sorted(
            attempt.assessment.assessment_questions,
            key=lambda item: item.sequence,
        ):
            question = link.question
            raw_answer = answer_by_question.get(question.id)
            answer_text = self._serialize_answer(raw_answer)
            points = link.points_override if link.points_override is not None else question.points
            total_points += points
            is_correct, score_awarded = self._grade_answer(question, raw_answer, points)
            if is_correct is None:
                has_manual_questions = True
            else:
                total_score += score_awarded or Decimal("0.00")
            answer = TrainingExamAnswer(
                organization_id=org_id,
                attempt_id=attempt.id,
                question_id=question.id,
                answer=answer_text,
                question_text_snapshot=question.question_text,
                question_type_snapshot=question.question_type.value,
                options_snapshot=self._options_snapshot(question),
                correct_answer_snapshot=self._correct_answer_snapshot(question),
                score_awarded=score_awarded,
                is_correct=is_correct,
            )
            self.db.add(answer)
        attempt.submitted_at = _now()
        attempt.score = (
            ((total_score / total_points) * Decimal("100")).quantize(Decimal("0.01"))
            if total_points
            else Decimal("0.00")
        )
        attempt.passed = (
            None
            if has_manual_questions
            else attempt.score >= attempt.assessment.pass_mark
        )
        self.db.flush()
        ProgressService(self.db).recalculate_course_progress(
            org_id,
            attempt.assessment.module.course_id,
            attempt.employee_id,
            actor_id=actor_id,
        )
        self._audit(
            org_id,
            "training_exam_attempt",
            attempt.id,
            AuditAction.UPDATE,
            new_values={
                "score": str(attempt.score),
                "passed": attempt.passed,
                "submitted_at": attempt.submitted_at.isoformat(),
            },
            user_id=actor_id,
            reason="Assessment submitted",
        )
        return attempt

    def _answer_max_points(self, answer: TrainingExamAnswer) -> Decimal:
        link = self.db.scalar(
            select(TrainingAssessmentQuestion)
            .options(joinedload(TrainingAssessmentQuestion.question))
            .where(
                TrainingAssessmentQuestion.organization_id == answer.organization_id,
                TrainingAssessmentQuestion.assessment_id
                == answer.attempt.assessment_id,
                TrainingAssessmentQuestion.question_id == answer.question_id,
            )
        )
        if not link:
            return Decimal("0.00")
        return link.points_override if link.points_override is not None else link.question.points

    def _load_assessment_for_exam(
        self,
        org_id: UUID,
        assessment_id: UUID,
    ) -> TrainingAssessment:
        assessment = self.db.scalar(
            select(TrainingAssessment)
            .options(
                joinedload(TrainingAssessment.module).joinedload(
                    TrainingCourseModule.course
                ),
                selectinload(TrainingAssessment.assessment_questions)
                .joinedload(TrainingAssessmentQuestion.question)
                .selectinload(TrainingQuestion.options),
            )
            .where(
                TrainingAssessment.organization_id == org_id,
                TrainingAssessment.id == assessment_id,
            )
        )
        if not assessment:
            raise NotFoundError("Training assessment not found")
        if not assessment.assessment_questions:
            raise ValidationError("Assessment has no questions")
        return assessment

    def _assessment_snapshot(self, assessment: TrainingAssessment) -> dict[str, Any]:
        return {
            "assessment_id": str(assessment.id),
            "title": assessment.title,
            "description": assessment.description,
            "pass_mark": str(assessment.pass_mark),
            "duration_minutes": assessment.duration_minutes,
            "max_attempts": assessment.max_attempts,
            "module_id": str(assessment.module_id),
            "course_id": str(assessment.module.course_id),
            "course_version_number": assessment.module.course.version_number,
            "question_ids": [
                str(link.question_id)
                for link in sorted(
                    assessment.assessment_questions,
                    key=lambda item: item.sequence,
                )
            ],
        }

    def _options_snapshot(self, question: TrainingQuestion) -> list[dict[str, Any]]:
        return [
            {
                "option_id": str(option.id),
                "option_text": option.option_text,
                "is_correct": option.is_correct,
            }
            for option in question.options
        ]

    def _correct_answer_snapshot(self, question: TrainingQuestion) -> str | None:
        correct_values = [
            option.option_text for option in question.options if option.is_correct
        ]
        if not correct_values:
            return None
        return json.dumps(correct_values)

    def _serialize_answer(self, answer: Any) -> str | None:
        if answer is None:
            return None
        if isinstance(answer, str):
            return answer
        return json.dumps(answer)

    def _grade_answer(
        self,
        question: TrainingQuestion,
        answer: Any,
        points: Decimal,
    ) -> tuple[bool | None, Decimal | None]:
        if question.question_type in {
            TrainingQuestionType.SHORT_ANSWER,
            TrainingQuestionType.ESSAY,
        }:
            return None, None
        correct_options = [option for option in question.options if option.is_correct]
        correct_texts = {_normalize_text(option.option_text) for option in correct_options}
        correct_ids = {str(option.id) for option in correct_options}
        if question.question_type in {
            TrainingQuestionType.MULTIPLE_CHOICE,
            TrainingQuestionType.TRUE_FALSE,
            TrainingQuestionType.FILL_GAP,
        }:
            submitted_text = _normalize_text(answer)
            is_correct = submitted_text in correct_texts or str(answer or "") in correct_ids
            return is_correct, points if is_correct else Decimal("0.00")
        if question.question_type == TrainingQuestionType.MULTIPLE_SELECT:
            submitted_set = _as_answer_set(answer)
            is_correct = submitted_set == correct_texts or submitted_set == {
                _normalize_text(value) for value in correct_ids
            }
            return is_correct, points if is_correct else Decimal("0.00")
        return None, None


class LearningReportService(LearningAssessmentBaseService):
    """Operational dashboards and reports for Learning & Assessment."""

    def dashboard(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        active_courses = self.db.scalar(
            select(func.count()).select_from(TrainingCourse).where(
                TrainingCourse.organization_id == org_id,
                TrainingCourse.status == TrainingCourseStatus.PUBLISHED,
                TrainingCourse.is_active.is_(True),
            )
        ) or 0
        assignment_stmt = select(TrainingCourseAssignment).where(
            TrainingCourseAssignment.organization_id == org_id
        )
        assignment_stmt = self._apply_assignment_dates(
            assignment_stmt, start_date, end_date
        )
        assigned_courses = self.db.scalar(
            select(func.count()).select_from(assignment_stmt.subquery())
        ) or 0
        completed_courses = self.db.scalar(
            select(func.count()).select_from(TrainingCourseProgress).where(
                TrainingCourseProgress.organization_id == org_id,
                TrainingCourseProgress.status == TrainingProgressStatus.COMPLETED,
            )
        ) or 0
        overdue_courses = self.db.scalar(
            select(func.count()).select_from(TrainingCourseAssignment).join(
                TrainingCourseProgress,
                (
                    TrainingCourseProgress.organization_id
                    == TrainingCourseAssignment.organization_id
                )
                & (TrainingCourseProgress.course_id == TrainingCourseAssignment.course_id)
                & (
                    TrainingCourseProgress.employee_id
                    == TrainingCourseAssignment.employee_id
                ),
            ).where(
                TrainingCourseAssignment.organization_id == org_id,
                TrainingCourseAssignment.due_date < date.today(),
                TrainingCourseProgress.status != TrainingProgressStatus.COMPLETED,
            )
        ) or 0
        pending_reviews = self.db.scalar(
            select(func.count()).select_from(TrainingExamAnswer).join(
                TrainingExamAttempt,
                TrainingExamAttempt.id == TrainingExamAnswer.attempt_id,
            ).where(
                TrainingExamAnswer.organization_id == org_id,
                TrainingExamAttempt.submitted_at.is_not(None),
                TrainingExamAnswer.question_type_snapshot.in_(
                    [
                        TrainingQuestionType.SHORT_ANSWER.value,
                        TrainingQuestionType.ESSAY.value,
                    ]
                ),
                TrainingExamAnswer.score_awarded.is_(None),
            )
        ) or 0
        return {
            "active_courses": active_courses,
            "assigned_courses": assigned_courses,
            "completed_courses": completed_courses,
            "overdue_courses": overdue_courses,
            "pending_reviews": pending_reviews,
            "department_completion": self.department_completion(
                org_id, start_date=start_date, end_date=end_date
            )["rows"],
        }

    def employee_progress(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        department_id: UUID | None = None,
        course_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        stmt = (
            select(TrainingCourseAssignment, TrainingCourseProgress, Employee, TrainingCourse)
            .join(
                TrainingCourseProgress,
                (
                    TrainingCourseProgress.organization_id
                    == TrainingCourseAssignment.organization_id
                )
                & (TrainingCourseProgress.course_id == TrainingCourseAssignment.course_id)
                & (
                    TrainingCourseProgress.employee_id
                    == TrainingCourseAssignment.employee_id
                ),
            )
            .join(Employee, Employee.employee_id == TrainingCourseAssignment.employee_id)
            .join(TrainingCourse, TrainingCourse.id == TrainingCourseAssignment.course_id)
            .options(joinedload(Employee.person), joinedload(Employee.department))
            .where(TrainingCourseAssignment.organization_id == org_id)
        )
        stmt = self._apply_assignment_dates(stmt, start_date, end_date)
        if employee_id:
            stmt = stmt.where(Employee.employee_id == employee_id)
        if department_id:
            stmt = stmt.where(Employee.department_id == department_id)
        if course_id:
            stmt = stmt.where(TrainingCourse.id == course_id)
        rows = []
        for assignment, progress, employee, course in self.db.execute(stmt).all():
            rows.append(
                {
                    "employee": employee.person.name,
                    "employee_code": employee.employee_code,
                    "department": employee.department.department_name
                    if employee.department
                    else "",
                    "course": course.title,
                    "status": progress.status.value,
                    "completion": progress.completion_percentage,
                    "due_date": assignment.due_date,
                    "mandatory": assignment.is_mandatory,
                }
            )
        return {"rows": rows}

    def department_completion(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        course_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        data = self.employee_progress(
            org_id,
            department_id=department_id,
            course_id=course_id,
            start_date=start_date,
            end_date=end_date,
        )["rows"]
        grouped: dict[str, dict[str, Any]] = {}
        for row in data:
            key = row["department"] or "Unassigned"
            item = grouped.setdefault(
                key, {"department": key, "assigned": 0, "completed": 0}
            )
            item["assigned"] += 1
            if row["status"] == TrainingProgressStatus.COMPLETED.value:
                item["completed"] += 1
        for item in grouped.values():
            item["completion_rate"] = (
                round((item["completed"] / item["assigned"]) * 100, 2)
                if item["assigned"]
                else 0
            )
        return {"rows": list(grouped.values())}

    def assessment_results(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        course_id: UUID | None = None,
        assessment_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        stmt = (
            select(TrainingExamAttempt)
            .options(
                joinedload(TrainingExamAttempt.employee).joinedload(Employee.person),
                joinedload(TrainingExamAttempt.assessment)
                .joinedload(TrainingAssessment.module)
                .joinedload(TrainingCourseModule.course),
            )
            .where(
                TrainingExamAttempt.organization_id == org_id,
                TrainingExamAttempt.submitted_at.is_not(None),
            )
        )
        if employee_id:
            stmt = stmt.where(TrainingExamAttempt.employee_id == employee_id)
        if assessment_id:
            stmt = stmt.where(TrainingExamAttempt.assessment_id == assessment_id)
        if course_id:
            stmt = stmt.join(
                TrainingAssessment,
                TrainingAssessment.id == TrainingExamAttempt.assessment_id,
            ).join(TrainingCourseModule, TrainingCourseModule.id == TrainingAssessment.module_id).where(
                TrainingCourseModule.course_id == course_id
            )
        if start_date:
            stmt = stmt.where(func.date(TrainingExamAttempt.submitted_at) >= start_date)
        if end_date:
            stmt = stmt.where(func.date(TrainingExamAttempt.submitted_at) <= end_date)
        rows = []
        for attempt in self.db.scalars(stmt.order_by(TrainingExamAttempt.submitted_at.desc())).all():
            rows.append(
                {
                    "employee": attempt.employee.person.name,
                    "assessment": attempt.assessment.title,
                    "course": attempt.assessment.module.course.title,
                    "score": attempt.score,
                    "passed": attempt.passed,
                    "submitted_at": attempt.submitted_at,
                }
            )
        return {"rows": rows}

    def outstanding_mandatory(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        department_id: UUID | None = None,
        course_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        rows = [
            row
            for row in self.employee_progress(
                org_id,
                employee_id=employee_id,
                department_id=department_id,
                course_id=course_id,
                start_date=start_date,
                end_date=end_date,
            )["rows"]
            if row["mandatory"] and row["status"] != TrainingProgressStatus.COMPLETED.value
        ]
        return {"rows": rows}

    def question_analysis(
        self,
        org_id: UUID,
        *,
        course_id: UUID | None = None,
        assessment_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        stmt = (
            select(TrainingExamAnswer, TrainingExamAttempt, TrainingAssessment)
            .join(TrainingExamAttempt, TrainingExamAttempt.id == TrainingExamAnswer.attempt_id)
            .join(TrainingAssessment, TrainingAssessment.id == TrainingExamAttempt.assessment_id)
            .join(TrainingCourseModule, TrainingCourseModule.id == TrainingAssessment.module_id)
            .where(
                TrainingExamAnswer.organization_id == org_id,
                TrainingExamAttempt.submitted_at.is_not(None),
            )
        )
        if assessment_id:
            stmt = stmt.where(TrainingAssessment.id == assessment_id)
        if course_id:
            stmt = stmt.where(TrainingCourseModule.course_id == course_id)
        if start_date:
            stmt = stmt.where(func.date(TrainingExamAttempt.submitted_at) >= start_date)
        if end_date:
            stmt = stmt.where(func.date(TrainingExamAttempt.submitted_at) <= end_date)
        grouped: dict[UUID, dict[str, Any]] = {}
        for answer, _attempt, assessment in self.db.execute(stmt).all():
            item = grouped.setdefault(
                answer.question_id,
                {
                    "question": answer.question_text_snapshot,
                    "assessment": assessment.title,
                    "attempts": 0,
                    "correct": 0,
                    "pending": 0,
                    "total_score": Decimal("0.00"),
                },
            )
            item["attempts"] += 1
            if answer.is_correct is True:
                item["correct"] += 1
            if answer.is_correct is None:
                item["pending"] += 1
            item["total_score"] += answer.score_awarded or Decimal("0.00")
        for item in grouped.values():
            item["correct_rate"] = (
                round((item["correct"] / item["attempts"]) * 100, 2)
                if item["attempts"]
                else 0
            )
            item["average_score"] = (
                (item["total_score"] / Decimal(item["attempts"])).quantize(
                    Decimal("0.01")
                )
                if item["attempts"]
                else Decimal("0.00")
            )
        return {"rows": list(grouped.values())}

    def _apply_assignment_dates(self, stmt, start_date: date | None, end_date: date | None):
        if start_date:
            stmt = stmt.where(TrainingCourseAssignment.assigned_date >= start_date)
        if end_date:
            stmt = stmt.where(TrainingCourseAssignment.assigned_date <= end_date)
        return stmt
