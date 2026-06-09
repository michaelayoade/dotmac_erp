"""Learning and assessment models for the People training module."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.department import Department
    from app.models.people.hr.employee import Employee
    from app.models.person import Person


class TrainingLessonType(str, enum.Enum):
    """Supported lesson content types."""

    VIDEO = "video"
    PDF = "pdf"
    TEXT = "text"
    LINK = "link"


class TrainingCourseStatus(str, enum.Enum):
    """Course authoring lifecycle status."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TrainingAssessmentStatus(str, enum.Enum):
    """Assessment authoring lifecycle status."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TrainingQuestionType(str, enum.Enum):
    """Supported assessment question types."""

    MULTIPLE_CHOICE = "multiple_choice"
    MULTIPLE_SELECT = "multiple_select"
    TRUE_FALSE = "true_false"
    FILL_GAP = "fill_gap"
    SHORT_ANSWER = "short_answer"
    ESSAY = "essay"


class TrainingQuestionDifficulty(str, enum.Enum):
    """Question difficulty levels for filtering and future randomization."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TrainingProgressStatus(str, enum.Enum):
    """Course progress status."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_cls]


class TrainingCourse(Base):
    """Learning course containing modules, lessons, and assessments."""

    __tablename__ = "training_course"
    __table_args__ = (
        Index("idx_training_course_org_active", "organization_id", "is_active"),
        Index("idx_training_course_department", "organization_id", "department_id"),
        Index("idx_training_course_status", "organization_id", "status"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    pass_mark: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("70.00"),
        server_default="70.00",
    )
    retake_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    status: Mapped[TrainingCourseStatus] = mapped_column(
        Enum(
            TrainingCourseStatus,
            name="training_course_status",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TrainingCourseStatus.DRAFT,
        server_default=TrainingCourseStatus.DRAFT.value,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    department: Mapped[Department | None] = relationship("Department")
    creator: Mapped[Person | None] = relationship("Person")
    modules: Mapped[list[TrainingCourseModule]] = relationship(
        "TrainingCourseModule",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="TrainingCourseModule.sequence",
    )
    assignments: Mapped[list[TrainingCourseAssignment]] = relationship(
        "TrainingCourseAssignment",
        back_populates="course",
        cascade="all, delete-orphan",
    )
    progress_records: Mapped[list[TrainingCourseProgress]] = relationship(
        "TrainingCourseProgress",
        back_populates="course",
        cascade="all, delete-orphan",
    )
    prerequisites: Mapped[list[TrainingCoursePrerequisite]] = relationship(
        "TrainingCoursePrerequisite",
        back_populates="course",
        cascade="all, delete-orphan",
        foreign_keys="TrainingCoursePrerequisite.course_id",
    )


class TrainingCourseModule(Base):
    """Ordered module within a learning course."""

    __tablename__ = "training_course_module"
    __table_args__ = (
        UniqueConstraint("course_id", "sequence", name="uq_training_module_sequence"),
        Index("idx_training_module_course", "organization_id", "course_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    course: Mapped[TrainingCourse] = relationship(
        "TrainingCourse",
        back_populates="modules",
    )
    lessons: Mapped[list[TrainingLesson]] = relationship(
        "TrainingLesson",
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="TrainingLesson.sequence",
    )
    assessments: Mapped[list[TrainingAssessment]] = relationship(
        "TrainingAssessment",
        back_populates="module",
        cascade="all, delete-orphan",
    )


class TrainingLesson(Base):
    """Learning content item within a module."""

    __tablename__ = "training_lesson"
    __table_args__ = (
        UniqueConstraint("module_id", "sequence", name="uq_training_lesson_sequence"),
        Index("idx_training_lesson_module", "organization_id", "module_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course_module.id", ondelete="CASCADE"),
        nullable=False,
    )
    lesson_type: Mapped[TrainingLessonType] = mapped_column(
        Enum(
            TrainingLessonType,
            name="training_lesson_type",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    module: Mapped[TrainingCourseModule] = relationship(
        "TrainingCourseModule",
        back_populates="lessons",
    )
    progress_records: Mapped[list[TrainingLessonProgress]] = relationship(
        "TrainingLessonProgress",
        back_populates="lesson",
        cascade="all, delete-orphan",
    )


class TrainingAssessment(Base):
    """Assessment attached to a course module."""

    __tablename__ = "training_assessment"
    __table_args__ = (
        Index("idx_training_assessment_module", "organization_id", "module_id"),
        Index("idx_training_assessment_status", "organization_id", "status"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course_module.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pass_mark: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("70.00"),
        server_default="70.00",
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    randomize_questions: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    randomize_options: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    status: Mapped[TrainingAssessmentStatus] = mapped_column(
        Enum(
            TrainingAssessmentStatus,
            name="training_assessment_status",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TrainingAssessmentStatus.DRAFT,
        server_default=TrainingAssessmentStatus.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    module: Mapped[TrainingCourseModule] = relationship(
        "TrainingCourseModule",
        back_populates="assessments",
    )
    assessment_questions: Mapped[list[TrainingAssessmentQuestion]] = relationship(
        "TrainingAssessmentQuestion",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="TrainingAssessmentQuestion.sequence",
    )
    attempts: Mapped[list[TrainingExamAttempt]] = relationship(
        "TrainingExamAttempt",
        back_populates="assessment",
        cascade="all, delete-orphan",
    )


class TrainingQuestionBank(Base):
    """Reusable question collection for assessment authoring."""

    __tablename__ = "training_question_bank"
    __table_args__ = (
        Index("idx_training_question_bank_org_active", "organization_id", "is_active"),
        Index("idx_training_question_bank_department", "organization_id", "department_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    department: Mapped[Department | None] = relationship("Department")
    creator: Mapped[Person | None] = relationship("Person")
    questions: Mapped[list[TrainingQuestion]] = relationship(
        "TrainingQuestion",
        back_populates="question_bank",
        cascade="all, delete-orphan",
    )


class TrainingQuestion(Base):
    """Reusable assessment question stored in a question bank."""

    __tablename__ = "training_question"
    __table_args__ = (
        Index("idx_training_question_bank", "organization_id", "question_bank_id"),
        Index("idx_training_question_type", "organization_id", "question_type"),
        Index("idx_training_question_difficulty", "organization_id", "difficulty_level"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    question_bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question_bank.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_type: Mapped[TrainingQuestionType] = mapped_column(
        Enum(
            TrainingQuestionType,
            name="training_question_type",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty_level: Mapped[TrainingQuestionDifficulty] = mapped_column(
        Enum(
            TrainingQuestionDifficulty,
            name="training_question_difficulty",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TrainingQuestionDifficulty.MEDIUM,
        server_default=TrainingQuestionDifficulty.MEDIUM.value,
    )
    points: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False,
        default=Decimal("1.00"),
        server_default="1.00",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    question_bank: Mapped[TrainingQuestionBank] = relationship(
        "TrainingQuestionBank",
        back_populates="questions",
    )
    assessment_links: Mapped[list[TrainingAssessmentQuestion]] = relationship(
        "TrainingAssessmentQuestion",
        back_populates="question",
        cascade="all, delete-orphan",
    )
    options: Mapped[list[TrainingQuestionOption]] = relationship(
        "TrainingQuestionOption",
        back_populates="question",
        cascade="all, delete-orphan",
    )
    tag_links: Mapped[list[TrainingQuestionTagMap]] = relationship(
        "TrainingQuestionTagMap",
        back_populates="question",
        cascade="all, delete-orphan",
    )
    answers: Mapped[list[TrainingExamAnswer]] = relationship(
        "TrainingExamAnswer",
        back_populates="question",
    )


class TrainingQuestionOption(Base):
    """Selectable option for objective questions."""

    __tablename__ = "training_question_option"
    __table_args__ = (
        Index("idx_training_option_question", "organization_id", "question_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    question: Mapped[TrainingQuestion] = relationship(
        "TrainingQuestion",
        back_populates="options",
    )


class TrainingAssessmentQuestion(Base):
    """Mapping table that attaches reusable questions to assessments."""

    __tablename__ = "training_assessment_question"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "question_id",
            name="uq_training_assessment_question",
        ),
        UniqueConstraint(
            "assessment_id",
            "sequence",
            name="uq_training_assessment_question_sequence",
        ),
        Index(
            "idx_training_assessment_question_assessment",
            "organization_id",
            "assessment_id",
        ),
        Index(
            "idx_training_assessment_question_question",
            "organization_id",
            "question_id",
        ),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_assessment.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question.id"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    points_override: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    assessment: Mapped[TrainingAssessment] = relationship(
        "TrainingAssessment",
        back_populates="assessment_questions",
    )
    question: Mapped[TrainingQuestion] = relationship(
        "TrainingQuestion",
        back_populates="assessment_links",
    )


class TrainingQuestionTag(Base):
    """Reusable tag used to categorize questions."""

    __tablename__ = "training_question_tag"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_training_question_tag_name"),
        Index("idx_training_question_tag_org", "organization_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    question_links: Mapped[list[TrainingQuestionTagMap]] = relationship(
        "TrainingQuestionTagMap",
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class TrainingQuestionTagMap(Base):
    """Many-to-many mapping between questions and tags."""

    __tablename__ = "training_question_tag_map"
    __table_args__ = (
        UniqueConstraint("question_id", "tag_id", name="uq_training_question_tag_map"),
        Index("idx_training_question_tag_map_question", "organization_id", "question_id"),
        Index("idx_training_question_tag_map_tag", "organization_id", "tag_id"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question.id", ondelete="CASCADE"),
        nullable=False,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question_tag.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    question: Mapped[TrainingQuestion] = relationship(
        "TrainingQuestion",
        back_populates="tag_links",
    )
    tag: Mapped[TrainingQuestionTag] = relationship(
        "TrainingQuestionTag",
        back_populates="question_links",
    )


class TrainingCoursePrerequisite(Base):
    """Prerequisite course requirement."""

    __tablename__ = "training_course_prerequisite"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "prerequisite_course_id",
            name="uq_training_course_prerequisite",
        ),
        Index("idx_training_prerequisite_course", "organization_id", "course_id"),
        Index(
            "idx_training_prerequisite_required",
            "organization_id",
            "prerequisite_course_id",
        ),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    prerequisite_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    course: Mapped[TrainingCourse] = relationship(
        "TrainingCourse",
        back_populates="prerequisites",
        foreign_keys=[course_id],
    )
    prerequisite_course: Mapped[TrainingCourse] = relationship(
        "TrainingCourse",
        foreign_keys=[prerequisite_course_id],
    )


class TrainingCourseAssignment(Base):
    """Employee assignment to a course."""

    __tablename__ = "training_course_assignment"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "employee_id",
            name="uq_training_course_assignment_employee",
        ),
        Index("idx_training_assignment_course", "organization_id", "course_id"),
        Index("idx_training_assignment_employee", "organization_id", "employee_id"),
        Index("idx_training_assignment_due", "organization_id", "due_date"),
        Index("idx_training_assignment_source", "organization_id", "assignment_source"),
        Index("idx_training_assignment_date", "organization_id", "assigned_date"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    assigned_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
        server_default=func.current_date(),
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assignment_source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="employee",
        server_default="employee",
    )
    assignment_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    course_version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    course: Mapped[TrainingCourse] = relationship(
        "TrainingCourse",
        back_populates="assignments",
    )
    employee: Mapped[Employee] = relationship("Employee")
    assigner: Mapped[Person | None] = relationship("Person")


class TrainingCourseProgress(Base):
    """Employee progress through a course."""

    __tablename__ = "training_course_progress"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "employee_id",
            name="uq_training_course_progress_employee",
        ),
        Index("idx_training_progress_course", "organization_id", "course_id"),
        Index("idx_training_progress_employee", "organization_id", "employee_id"),
        Index("idx_training_progress_status", "organization_id", "status"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_course.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    completion_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    course_version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    status: Mapped[TrainingProgressStatus] = mapped_column(
        Enum(
            TrainingProgressStatus,
            name="training_progress_status",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TrainingProgressStatus.NOT_STARTED,
        server_default=TrainingProgressStatus.NOT_STARTED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    course: Mapped[TrainingCourse] = relationship(
        "TrainingCourse",
        back_populates="progress_records",
    )
    employee: Mapped[Employee] = relationship("Employee")


class TrainingLessonProgress(Base):
    """Employee progress on an individual lesson."""

    __tablename__ = "training_lesson_progress"
    __table_args__ = (
        UniqueConstraint(
            "lesson_id",
            "employee_id",
            name="uq_training_lesson_progress_employee",
        ),
        Index("idx_training_lesson_progress_lesson", "organization_id", "lesson_id"),
        Index(
            "idx_training_lesson_progress_employee",
            "organization_id",
            "employee_id",
        ),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_lesson.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    lesson: Mapped[TrainingLesson] = relationship(
        "TrainingLesson",
        back_populates="progress_records",
    )
    employee: Mapped[Employee] = relationship("Employee")


class TrainingExamAttempt(Base):
    """Employee assessment attempt."""

    __tablename__ = "training_exam_attempt"
    __table_args__ = (
        Index("idx_training_attempt_assessment", "organization_id", "assessment_id"),
        Index("idx_training_attempt_employee", "organization_id", "employee_id"),
        Index("idx_training_attempt_submitted", "organization_id", "submitted_at"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_assessment.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    course_version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    assessment_snapshot_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    assessment: Mapped[TrainingAssessment] = relationship(
        "TrainingAssessment",
        back_populates="attempts",
    )
    employee: Mapped[Employee] = relationship("Employee")
    answers: Mapped[list[TrainingExamAnswer]] = relationship(
        "TrainingExamAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class TrainingExamAnswer(Base):
    """Answer submitted for a question within an attempt."""

    __tablename__ = "training_exam_answer"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_training_exam_answer_question",
        ),
        Index("idx_training_answer_attempt", "organization_id", "attempt_id"),
        Index("idx_training_answer_question", "organization_id", "question_id"),
        Index(
            "idx_training_answer_manual_review",
            "organization_id",
            "question_type_snapshot",
            "score_awarded",
        ),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_exam_attempt.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_question.id"),
        nullable=False,
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    question_type_snapshot: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    options_snapshot: Mapped[list | dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    correct_answer_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_awarded: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    attempt: Mapped[TrainingExamAttempt] = relationship(
        "TrainingExamAttempt",
        back_populates="answers",
    )
    question: Mapped[TrainingQuestion] = relationship(
        "TrainingQuestion",
        back_populates="answers",
    )
