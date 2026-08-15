"""
Training Management Models.

This module contains models for training programs, events, attendance,
learning courses, lessons, assessments, and exam attempts.
"""

from app.models.people.training.learning_assessment import (
    TrainingAssessmentStatus,
    TrainingAssessmentQuestion,
    TrainingAssessment,
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
    TrainingQuestionBank,
    TrainingQuestionDifficulty,
    TrainingQuestion,
    TrainingQuestionOption,
    TrainingQuestionTag,
    TrainingQuestionTagMap,
    TrainingQuestionType,
)
from app.models.people.training.academy import (
    AcademyLearningProgress,
    AcademyLearningRequirement,
    AcademyProgressStatus,
)
from app.models.people.training.training_event import (
    AttendeeStatus,
    TrainingAttendee,
    TrainingEvent,
    TrainingEventStatus,
)
from app.models.people.training.training_program import (
    TrainingProgram,
    TrainingProgramStatus,
)

__all__ = [
    "TrainingProgram",
    "TrainingProgramStatus",
    "TrainingEvent",
    "TrainingEventStatus",
    "TrainingAttendee",
    "AttendeeStatus",
    "TrainingCourse",
    "TrainingCourseStatus",
    "TrainingCourseModule",
    "TrainingCoursePrerequisite",
    "TrainingLesson",
    "TrainingLessonType",
    "TrainingAssessment",
    "TrainingAssessmentStatus",
    "TrainingAssessmentQuestion",
    "TrainingQuestionBank",
    "TrainingQuestion",
    "TrainingQuestionDifficulty",
    "TrainingQuestionType",
    "TrainingQuestionOption",
    "TrainingQuestionTag",
    "TrainingQuestionTagMap",
    "TrainingCourseAssignment",
    "TrainingCourseProgress",
    "TrainingProgressStatus",
    "TrainingLessonProgress",
    "TrainingExamAttempt",
    "TrainingExamAnswer",
    "AcademyLearningRequirement",
    "AcademyLearningProgress",
    "AcademyProgressStatus",
]
