"""Admin service for authoring SLA policy content."""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.help.models import ArticleStatus, HelpArticleOverride
from app.services.file_upload import (
    FileUploadError,
    get_sla_policy_document_upload,
)

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

logger = logging.getLogger(__name__)


class SLAPolicySection(TypedDict):
    """Validated section stored in ``body_json``."""

    title: str
    body: str
    items: list[str]


class SLAPolicyBody(TypedDict):
    """Validated SLA policy body structure."""

    sections: list[SLAPolicySection]


@dataclass(frozen=True)
class SLAPolicyInput:
    """Validated input for an SLA policy create or update."""

    title: str
    summary: str
    body_json: SLAPolicyBody


@dataclass(frozen=True)
class SLAPolicyDocumentInput:
    """Validated metadata and bytes for an uploaded SLA policy document."""

    title: str
    summary: str
    file_name: str
    file_content_type: str
    file_data: bytes


class SLAPolicyValidationError(ValueError):
    """Raised when an SLA policy form is invalid."""


class SLAPolicyNotFoundError(LookupError):
    """Raised when an SLA policy is outside the required scope or missing."""


class SLAPolicyAdminService:
    """Create and manage only SLA policy overrides for one organization."""

    MODULE_KEY = "sla_policies"
    CONTENT_TYPE = "sla_policy"
    MAX_SECTIONS = 25
    MAX_ITEMS_PER_SECTION = 50
    MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024
    DOCUMENT_CONTENT_TYPES = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }

    def __init__(self, db: Session):
        self.db = db

    @classmethod
    def _scope(cls, organization_id: UUID) -> tuple:
        return (
            HelpArticleOverride.organization_id == organization_id,
            HelpArticleOverride.module_key == cls.MODULE_KEY,
            HelpArticleOverride.content_type == cls.CONTENT_TYPE,
        )

    def list_for_org(self, organization_id: UUID) -> list[HelpArticleOverride]:
        """List SLA policies in every lifecycle status for an organization."""
        stmt = (
            select(HelpArticleOverride)
            .where(*self._scope(organization_id))
            .order_by(
                HelpArticleOverride.updated_at.desc(),
                HelpArticleOverride.title.asc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def get_for_org(
        self, organization_id: UUID, article_id: UUID
    ) -> HelpArticleOverride:
        """Get one SLA policy without crossing tenant or content boundaries."""
        stmt = select(HelpArticleOverride).where(
            *self._scope(organization_id),
            HelpArticleOverride.article_id == article_id,
        )
        policy = self.db.scalar(stmt)
        if policy is None:
            raise SLAPolicyNotFoundError("SLA policy not found")
        return policy

    def create(
        self, organization_id: UUID, policy_input: SLAPolicyInput
    ) -> HelpArticleOverride:
        """Create an SLA policy as a draft with fixed classification."""
        policy = HelpArticleOverride(
            organization_id=organization_id,
            slug=f"sla-policy-{uuid4()}",
            title=policy_input.title,
            summary=policy_input.summary,
            body_json=policy_input.body_json,
            module_key=self.MODULE_KEY,
            content_type=self.CONTENT_TYPE,
            status=ArticleStatus.DRAFT,
        )
        self.db.add(policy)
        self.db.flush()
        logger.info("Created SLA policy: %s", policy.article_id)
        return policy

    def create_document(
        self,
        organization_id: UUID,
        document_input: SLAPolicyDocumentInput,
    ) -> HelpArticleOverride:
        """Create a draft SLA policy backed by an S3 document object."""
        article_id = uuid4()
        upload_service = get_sla_policy_document_upload()
        try:
            upload = upload_service.save(
                file_data=document_input.file_data,
                content_type=document_input.file_content_type,
                subdirs=(str(organization_id), str(article_id)),
                original_filename=document_input.file_name,
            )
        except FileUploadError as exc:
            raise SLAPolicyValidationError(str(exc)) from exc

        policy = HelpArticleOverride(
            article_id=article_id,
            organization_id=organization_id,
            slug=f"sla-policy-{article_id}",
            title=document_input.title,
            summary=document_input.summary,
            body_json=None,
            file_path=upload.s3_key,
            file_name=document_input.file_name,
            file_content_type=document_input.file_content_type,
            file_size_bytes=upload.file_size,
            content_hash=upload.checksum,
            module_key=self.MODULE_KEY,
            content_type=self.CONTENT_TYPE,
            status=ArticleStatus.DRAFT,
        )
        try:
            self.db.add(policy)
            self.db.flush()
        except Exception:
            try:
                upload_service.delete(upload.s3_key)
            except Exception:
                logger.exception(
                    "Failed to clean up SLA policy document after database error"
                )
            raise
        logger.info("Created SLA policy document: %s", policy.article_id)
        return policy

    def update(
        self,
        organization_id: UUID,
        article_id: UUID,
        policy_input: SLAPolicyInput,
    ) -> HelpArticleOverride:
        """Update SLA policy content while preserving its lifecycle status."""
        policy = self.get_for_org(organization_id, article_id)
        policy.title = policy_input.title
        policy.summary = policy_input.summary
        policy.body_json = cast(dict[Any, Any], policy_input.body_json)
        self.db.flush()
        logger.info("Updated SLA policy: %s", policy.article_id)
        return policy

    def publish(self, organization_id: UUID, article_id: UUID) -> HelpArticleOverride:
        """Publish an SLA policy for the authenticated public page."""
        policy = self.get_for_org(organization_id, article_id)
        policy.status = ArticleStatus.PUBLISHED
        policy.published_at = datetime.now(UTC)
        self.db.flush()
        logger.info("Published SLA policy: %s", policy.article_id)
        return policy

    def archive(self, organization_id: UUID, article_id: UUID) -> HelpArticleOverride:
        """Archive an SLA policy so it is no longer publicly visible."""
        policy = self.get_for_org(organization_id, article_id)
        policy.status = ArticleStatus.ARCHIVED
        self.db.flush()
        logger.info("Archived SLA policy: %s", policy.article_id)
        return policy

    @classmethod
    def build_policy_input(cls, form: Any) -> SLAPolicyInput:
        """Parse and validate the structured SLA policy form."""
        title = cls._normalize_text(
            cls._form_value(form, "title"),
            field="Policy title",
            max_length=300,
            required=True,
        )
        summary = cls._normalize_text(
            cls._form_value(form, "summary"),
            field="Summary",
            max_length=2000,
            multiline=True,
        )
        section_titles = cls._form_list(form, "section_title")
        section_bodies = cls._form_list(form, "section_body")
        section_items = cls._form_list(form, "section_items")

        lengths = {len(section_titles), len(section_bodies), len(section_items)}
        if len(lengths) != 1:
            raise SLAPolicyValidationError("The policy section data is incomplete.")
        if not section_titles:
            raise SLAPolicyValidationError("Add at least one policy section.")
        if len(section_titles) > cls.MAX_SECTIONS:
            raise SLAPolicyValidationError(
                f"A policy can contain at most {cls.MAX_SECTIONS} sections."
            )

        sections: list[SLAPolicySection] = []
        for index, (raw_title, raw_body, raw_items) in enumerate(
            zip(section_titles, section_bodies, section_items, strict=True), start=1
        ):
            section_title = cls._normalize_text(
                raw_title,
                field=f"Section {index} title",
                max_length=300,
                required=True,
            )
            section_body = cls._normalize_text(
                raw_body,
                field=f"Section {index} body",
                max_length=20_000,
                multiline=True,
            )
            items = [
                cls._normalize_text(
                    item,
                    field=f"Section {index} item",
                    max_length=1000,
                    required=True,
                )
                for item in str(raw_items).splitlines()
                if item.strip()
            ]
            if len(items) > cls.MAX_ITEMS_PER_SECTION:
                raise SLAPolicyValidationError(
                    f"Section {index} can contain at most "
                    f"{cls.MAX_ITEMS_PER_SECTION} items."
                )
            if not section_body and not items:
                raise SLAPolicyValidationError(
                    f"Section {index} needs body text or at least one item."
                )
            sections.append(
                {"title": section_title, "body": section_body, "items": items}
            )

        return SLAPolicyInput(
            title=title,
            summary=summary,
            body_json={"sections": sections},
        )

    @classmethod
    def build_document_input(
        cls,
        form: Any,
        *,
        file_name: str,
        file_content_type: str | None,
        file_data: bytes,
    ) -> SLAPolicyDocumentInput:
        """Validate an uploaded policy document and its display metadata."""
        title = cls._normalize_text(
            cls._form_value(form, "title"),
            field="Policy title",
            max_length=300,
            required=True,
        )
        summary = cls._normalize_text(
            cls._form_value(form, "summary"),
            field="Summary",
            max_length=2000,
            multiline=True,
        )
        normalized_name = cls._normalize_text(
            file_name.replace("\\", "/").rsplit("/", maxsplit=1)[-1],
            field="File name",
            max_length=255,
            required=True,
        )
        extension = Path(normalized_name).suffix.lower()
        expected_content_type = cls.DOCUMENT_CONTENT_TYPES.get(extension)
        normalized_content_type = (
            (file_content_type or "").split(";", maxsplit=1)[0].strip().lower()
        )
        if (
            expected_content_type is None
            or normalized_content_type != expected_content_type
        ):
            raise SLAPolicyValidationError(
                "Upload a PDF, JPEG, or PNG file whose content matches its file type."
            )
        if not file_data:
            raise SLAPolicyValidationError("The selected document is empty.")

        return SLAPolicyDocumentInput(
            title=title,
            summary=summary,
            file_name=normalized_name,
            file_content_type=normalized_content_type,
            file_data=file_data,
        )

    @classmethod
    def document_form_data(cls, form: Any) -> dict[str, str]:
        """Return safe scalar values when the upload form must be re-rendered."""
        return {
            "title": cls._form_value(form, "title"),
            "summary": cls._form_value(form, "summary"),
        }

    @classmethod
    def form_data(cls, form: Any) -> dict[str, Any]:
        """Return safe scalar form values for error re-rendering."""
        titles = cls._form_list(form, "section_title")
        bodies = cls._form_list(form, "section_body")
        items = cls._form_list(form, "section_items")
        count = max(len(titles), len(bodies), len(items), 1)
        return {
            "title": cls._form_value(form, "title"),
            "summary": cls._form_value(form, "summary"),
            "sections": [
                {
                    "title": titles[index] if index < len(titles) else "",
                    "body": bodies[index] if index < len(bodies) else "",
                    "items_text": items[index] if index < len(items) else "",
                }
                for index in range(count)
            ],
        }

    @staticmethod
    def policy_form_data(policy: HelpArticleOverride) -> dict[str, Any]:
        """Map an existing policy to the shared create/edit form."""
        body_json = policy.body_json if isinstance(policy.body_json, dict) else {}
        raw_sections = body_json.get("sections", [])
        sections = []
        if isinstance(raw_sections, list):
            for section in raw_sections:
                if not isinstance(section, dict):
                    continue
                raw_items = section.get("items", [])
                items = raw_items if isinstance(raw_items, list) else []
                sections.append(
                    {
                        "title": str(section.get("title") or ""),
                        "body": str(section.get("body") or ""),
                        "items_text": "\n".join(str(item) for item in items),
                    }
                )
        if not sections:
            sections.append({"title": "", "body": "", "items_text": ""})
        return {
            "title": policy.title,
            "summary": policy.summary,
            "sections": sections,
        }

    @staticmethod
    def empty_form_data() -> dict[str, Any]:
        """Return defaults for a new policy form."""
        return {
            "title": "",
            "summary": "",
            "sections": [{"title": "", "body": "", "items_text": ""}],
        }

    @staticmethod
    def _form_value(form: Any, key: str) -> str:
        value = form.get(key, "") if form is not None else ""
        return value if isinstance(value, str) else ""

    @staticmethod
    def _form_list(form: Any, key: str) -> list[str]:
        if form is None:
            return []
        values = form.getlist(key) if hasattr(form, "getlist") else [form.get(key, "")]
        return [value if isinstance(value, str) else "" for value in values]

    @staticmethod
    def _normalize_text(
        value: str,
        *,
        field: str,
        max_length: int,
        required: bool = False,
        multiline: bool = False,
    ) -> str:
        text = unicodedata.normalize("NFKC", value).replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        allowed_controls = {"\n", "\t"} if multiline else set()
        if any(ord(char) < 32 and char not in allowed_controls for char in text):
            raise SLAPolicyValidationError(f"{field} contains invalid characters.")
        text = text.strip()
        if not multiline:
            text = re.sub(r"\s+", " ", text)
        if required and not text:
            raise SLAPolicyValidationError(f"{field} is required.")
        if len(text) > max_length:
            raise SLAPolicyValidationError(
                f"{field} must be {max_length:,} characters or fewer."
            )
        return text
