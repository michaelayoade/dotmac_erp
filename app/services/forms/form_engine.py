"""Service layer for the generic configurable form engine."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.forms import (
    DynamicForm,
    DynamicFormAnswer,
    DynamicFormField,
    DynamicFormFieldOption,
    DynamicFormSection,
    DynamicFormSubmission,
    DynamicFormVersion,
    FormFieldType,
    FormStatus,
)


class FormValidationError(ValueError):
    """Raised when a submitted configurable form is invalid."""


SYSTEM_MAPPINGS = {
    "",
    "display_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "resume",
}
CHOICE_TYPES = {
    FormFieldType.SINGLE_CHOICE,
    FormFieldType.MULTI_CHOICE,
    FormFieldType.DROPDOWN,
}
FILE_TYPES = {FormFieldType.FILE, FormFieldType.IMAGE, FormFieldType.PDF}


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", value.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:70] or fallback


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class FormEngineService:
    """Service for form definitions and submissions."""

    def __init__(self, db: Session):
        self.db = db

    def get_version(
        self, organization_id: uuid.UUID, form_version_id: uuid.UUID
    ) -> DynamicFormVersion | None:
        stmt = (
            select(DynamicFormVersion)
            .options(
                selectinload(DynamicFormVersion.sections)
                .selectinload(DynamicFormSection.fields)
                .selectinload(DynamicFormField.options)
            )
            .where(
                DynamicFormVersion.organization_id == organization_id,
                DynamicFormVersion.form_version_id == form_version_id,
            )
        )
        return self.db.scalar(stmt)

    def get_job_form_version(
        self,
        organization_id: uuid.UUID,
        job_opening_id: uuid.UUID,
        form_version_id: uuid.UUID | None,
    ) -> DynamicFormVersion | None:
        if not form_version_id:
            return None
        version = self.get_version(organization_id, form_version_id)
        if not version:
            return None
        if version.form.owner_entity_type != "RECRUIT_JOB":
            return None
        if version.form.owner_entity_id != job_opening_id:
            return None
        return version

    def get_submission_for_subject(
        self,
        organization_id: uuid.UUID,
        subject_type: str,
        subject_id: uuid.UUID,
    ) -> DynamicFormSubmission | None:
        stmt = (
            select(DynamicFormSubmission)
            .options(selectinload(DynamicFormSubmission.answers))
            .where(
                DynamicFormSubmission.organization_id == organization_id,
                DynamicFormSubmission.subject_type == subject_type,
                DynamicFormSubmission.subject_id == subject_id,
            )
        )
        return self.db.scalar(stmt)

    def serialize_version(self, version: DynamicFormVersion | None) -> dict:
        if not version:
            return {
                "sections": [
                    {
                        "title": "Application",
                        "description": "",
                        "fields": [],
                    }
                ]
            }
        return {
            "form_version_id": str(version.form_version_id),
            "status": version.status.value,
            "sections": [
                {
                    "section_id": str(section.section_id),
                    "title": section.title,
                    "description": section.description or "",
                    "sort_order": section.sort_order,
                    "fields": [
                        {
                            "field_id": str(field.field_id),
                            "field_key": field.field_key,
                            "label": field.label,
                            "field_type": field.field_type.value,
                            "help_text": field.help_text or "",
                            "placeholder": field.placeholder or "",
                            "is_required": field.is_required,
                            "show_in_list": field.show_in_list,
                            "is_filterable": field.is_filterable,
                            "system_mapping": field.system_mapping or "",
                            "settings": field.settings_json or {},
                            "validation": field.validation_json or {},
                            "options": [
                                {
                                    "option_id": str(option.option_id),
                                    "label": option.label,
                                    "value": option.value,
                                    "sort_order": option.sort_order,
                                }
                                for option in field.options
                                if option.is_active
                            ],
                            "sort_order": field.sort_order,
                        }
                        for field in section.fields
                    ],
                }
                for section in version.sections
            ],
        }

    def upsert_job_form_version(
        self,
        organization_id: uuid.UUID,
        job_opening_id: uuid.UUID,
        job_title: str,
        payload: Mapping[str, Any] | None,
    ) -> DynamicFormVersion | None:
        if not payload:
            return None
        sections_payload = list(payload.get("sections") or [])
        if not sections_payload:
            return None

        fields_count = sum(
            len(section.get("fields") or []) for section in sections_payload
        )
        if fields_count == 0:
            return None

        self.validate_publishable_definition(sections_payload)
        form = self._get_or_create_job_form(organization_id, job_opening_id, job_title)
        latest_number = self.db.scalar(
            select(DynamicFormVersion.version_number)
            .where(DynamicFormVersion.form_id == form.form_id)
            .order_by(DynamicFormVersion.version_number.desc())
            .limit(1)
        )
        version = DynamicFormVersion(
            form_id=form.form_id,
            organization_id=organization_id,
            version_number=(latest_number or 0) + 1,
            status=FormStatus.PUBLISHED,
        )
        self.db.add(version)
        self.db.flush()

        for section_index, section_payload in enumerate(sections_payload):
            section = DynamicFormSection(
                form_version_id=version.form_version_id,
                title=_string_or_none(section_payload.get("title")) or "Application",
                description=_string_or_none(section_payload.get("description")),
                sort_order=section_index,
            )
            self.db.add(section)
            self.db.flush()
            used_keys: set[str] = set()
            for field_index, field_payload in enumerate(
                section_payload.get("fields") or []
            ):
                label = _string_or_none(field_payload.get("label"))
                if not label:
                    continue
                field_key = _slugify(
                    _string_or_none(field_payload.get("field_key")) or label,
                    f"field_{field_index + 1}",
                )
                while field_key in used_keys:
                    field_key = f"{field_key}_{field_index + 1}"
                used_keys.add(field_key)
                field_type = FormFieldType(field_payload.get("field_type") or "TEXT")
                mapping = _string_or_none(field_payload.get("system_mapping"))
                field = DynamicFormField(
                    form_version_id=version.form_version_id,
                    section_id=section.section_id,
                    field_key=field_key,
                    label=label,
                    field_type=field_type,
                    help_text=_string_or_none(field_payload.get("help_text")),
                    placeholder=_string_or_none(field_payload.get("placeholder")),
                    is_required=bool(field_payload.get("is_required")),
                    show_in_list=bool(field_payload.get("show_in_list")),
                    is_filterable=bool(field_payload.get("is_filterable")),
                    system_mapping=mapping,
                    settings_json=dict(field_payload.get("settings") or {}),
                    validation_json=dict(field_payload.get("validation") or {}),
                    visibility_json=dict(field_payload.get("visibility") or {}),
                    sort_order=field_index,
                )
                self.db.add(field)
                self.db.flush()
                for option_index, option_payload in enumerate(
                    field_payload.get("options") or []
                ):
                    option_label = _string_or_none(option_payload.get("label"))
                    if not option_label:
                        continue
                    self.db.add(
                        DynamicFormFieldOption(
                            field_id=field.field_id,
                            label=option_label,
                            value=_slugify(
                                _string_or_none(option_payload.get("value"))
                                or option_label,
                                f"option_{option_index + 1}",
                            ),
                            sort_order=option_index,
                            is_active=True,
                        )
                    )
        self.db.flush()
        return version

    def validate_publishable_definition(
        self, sections: list[Mapping[str, Any]]
    ) -> None:
        mappings: set[str] = set()
        for section in sections:
            for field in section.get("fields") or []:
                label = _string_or_none(field.get("label"))
                if not label:
                    continue
                field_type = FormFieldType(field.get("field_type") or "TEXT")
                mapping = _string_or_none(field.get("system_mapping")) or ""
                if mapping not in SYSTEM_MAPPINGS:
                    raise FormValidationError(f"Unsupported system mapping: {mapping}")
                if mapping:
                    mappings.add(mapping)
                if field_type in CHOICE_TYPES and not field.get("options"):
                    raise FormValidationError(f"{label} needs at least one option.")
        has_name = "display_name" in mappings or {
            "first_name",
            "last_name",
        }.issubset(mappings)
        if not has_name:
            raise FormValidationError(
                "Map one field to Display Name, or map First Name and Last Name."
            )
        if "email" not in mappings:
            raise FormValidationError("Map one email field to Applicant Email.")

    def submit(
        self,
        organization_id: uuid.UUID,
        form_version: DynamicFormVersion,
        answers: Mapping[str, Any],
        *,
        subject_type: str | None = None,
        subject_id: uuid.UUID | None = None,
    ) -> tuple[DynamicFormSubmission, dict[str, Any]]:
        normalized, mapped = self.validate_answers(form_version, answers)
        submission = DynamicFormSubmission(
            organization_id=organization_id,
            form_version_id=form_version.form_version_id,
            subject_type=subject_type,
            subject_id=subject_id,
            submitted_by_email=mapped.get("email"),
        )
        self.db.add(submission)
        self.db.flush()
        for field, value, display, file_data in normalized:
            self.db.add(
                DynamicFormAnswer(
                    submission_id=submission.submission_id,
                    field_id=field.field_id,
                    field_key_snapshot=field.field_key,
                    field_label_snapshot=field.label,
                    field_type_snapshot=field.field_type.value,
                    value_json=value,
                    display_value=display,
                    file_url=file_data.get("file_url") if file_data else None,
                    file_name=file_data.get("file_name") if file_data else None,
                )
            )
        self.db.flush()
        return submission, mapped

    def attach_submission_to_subject(
        self,
        submission: DynamicFormSubmission,
        subject_type: str,
        subject_id: uuid.UUID,
    ) -> None:
        submission.subject_type = subject_type
        submission.subject_id = subject_id
        self.db.flush()

    def validate_answers(
        self,
        form_version: DynamicFormVersion,
        answers: Mapping[str, Any],
    ) -> tuple[list[tuple[DynamicFormField, Any, str, dict | None]], dict[str, Any]]:
        normalized = []
        mapped: dict[str, Any] = {}
        fields = [
            field for section in form_version.sections for field in section.fields
        ]
        for field in fields:
            raw = answers.get(field.field_key)
            value, display, file_data = self._coerce_field_value(field, raw)
            if field.is_required and self._is_empty(value):
                raise FormValidationError(f"{field.label} is required.")
            if not self._is_empty(value):
                normalized.append((field, value, display, file_data))
                if field.system_mapping:
                    mapped[field.system_mapping] = (
                        display if field.field_type not in FILE_TYPES else value
                    )
        if "display_name" not in mapped:
            first_name = _string_or_none(mapped.get("first_name")) or ""
            last_name = _string_or_none(mapped.get("last_name")) or ""
            mapped["display_name"] = f"{first_name} {last_name}".strip()
        if not _string_or_none(mapped.get("display_name")):
            raise FormValidationError("Applicant name is required.")
        if not _string_or_none(mapped.get("email")):
            raise FormValidationError("Applicant email is required.")
        return normalized, mapped

    def list_column_answers(
        self, organization_id: uuid.UUID, applicant_ids: list[uuid.UUID]
    ) -> tuple[list[DynamicFormField], dict[uuid.UUID, dict[str, str]]]:
        if not applicant_ids:
            return [], {}
        submissions = self.db.scalars(
            select(DynamicFormSubmission).where(
                DynamicFormSubmission.organization_id == organization_id,
                DynamicFormSubmission.subject_type == "JOB_APPLICANT",
                DynamicFormSubmission.subject_id.in_(applicant_ids),
            )
        ).all()
        submission_by_id = {item.submission_id: item for item in submissions}
        if not submission_by_id:
            return [], {}
        answers = self.db.scalars(
            select(DynamicFormAnswer)
            .options(selectinload(DynamicFormAnswer.field))
            .where(DynamicFormAnswer.submission_id.in_(list(submission_by_id)))
        ).all()
        fields: dict[uuid.UUID, DynamicFormField] = {}
        values: dict[uuid.UUID, dict[str, str]] = {}
        for answer in answers:
            if not answer.field.show_in_list:
                continue
            submission = submission_by_id[answer.submission_id]
            if not submission.subject_id:
                continue
            fields[answer.field_id] = answer.field
            values.setdefault(submission.subject_id, {})[answer.field.field_key] = (
                answer.display_value or ""
            )
        sorted_fields = sorted(fields.values(), key=lambda field: field.sort_order)
        return sorted_fields[:8], values

    def detail_answers(
        self, organization_id: uuid.UUID, applicant_id: uuid.UUID
    ) -> list[DynamicFormAnswer]:
        submission = self.get_submission_for_subject(
            organization_id, "JOB_APPLICANT", applicant_id
        )
        if not submission:
            return []
        return sorted(submission.answers, key=lambda answer: answer.field_key_snapshot)

    def _get_or_create_job_form(
        self, organization_id: uuid.UUID, job_opening_id: uuid.UUID, job_title: str
    ) -> DynamicForm:
        stmt = select(DynamicForm).where(
            DynamicForm.organization_id == organization_id,
            DynamicForm.owner_entity_type == "RECRUIT_JOB",
            DynamicForm.owner_entity_id == job_opening_id,
        )
        form = self.db.scalar(stmt)
        if form:
            form.name = f"{job_title} Application"
            self.db.flush()
            return form
        form = DynamicForm(
            organization_id=organization_id,
            name=f"{job_title} Application",
            form_type="RECRUITMENT_APPLICATION",
            owner_entity_type="RECRUIT_JOB",
            owner_entity_id=job_opening_id,
        )
        self.db.add(form)
        self.db.flush()
        return form

    def _coerce_field_value(
        self, field: DynamicFormField, raw: Any
    ) -> tuple[Any, str, dict | None]:
        if field.field_type in FILE_TYPES:
            if not raw:
                return None, "", None
            if isinstance(raw, Mapping):
                file_url = _string_or_none(raw.get("file_url"))
                file_name = _string_or_none(raw.get("file_name")) or file_url
                value = {"file_url": file_url, "file_name": file_name}
                return value if file_url else None, file_name or "", value
            text = _string_or_none(raw)
            value = {"file_url": text, "file_name": text}
            return value if text else None, text or "", value
        if field.field_type == FormFieldType.MULTI_CHOICE:
            values = raw if isinstance(raw, list) else [raw] if raw else []
            option_values = {option.value: option.label for option in field.options}
            invalid = [value for value in values if value not in option_values]
            if invalid:
                raise FormValidationError(f"Invalid option for {field.label}.")
            display = ", ".join(option_values[value] for value in values)
            return values, display, None
        if field.field_type in CHOICE_TYPES:
            text = _string_or_none(raw)
            if not text:
                return None, "", None
            option_values = {option.value: option.label for option in field.options}
            if text not in option_values:
                raise FormValidationError(f"Invalid option for {field.label}.")
            return text, option_values[text], None
        if field.field_type in {
            FormFieldType.CHECKBOX,
            FormFieldType.YES_NO,
            FormFieldType.CONSENT,
        }:
            bool_value = raw in {True, "true", "on", "1", "yes", "YES"}
            return bool_value, "Yes" if bool_value else "No", None
        if field.field_type in {FormFieldType.NUMBER, FormFieldType.RATING}:
            text = _string_or_none(raw)
            if not text:
                return None, "", None
            try:
                decimal_value = Decimal(text)
            except (InvalidOperation, ValueError) as exc:
                raise FormValidationError(f"{field.label} must be a number.") from exc
            return str(decimal_value), str(decimal_value), None
        if field.field_type == FormFieldType.DATE:
            text = _string_or_none(raw)
            if not text:
                return None, "", None
            try:
                date.fromisoformat(text)
            except ValueError as exc:
                raise FormValidationError(
                    f"{field.label} must be a valid date."
                ) from exc
            return text, text, None
        text = _string_or_none(raw)
        return text, text or "", None

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or value == "" or value == []
