"""Read-only service for published SLA policy content."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.help.models import ArticleStatus, HelpArticleOverride
from app.services.storage import get_storage


class SLAPolicyDocumentNotFoundError(LookupError):
    """Raised when a published SLA document is missing or outside its scope."""


@dataclass(frozen=True)
class SLAPolicyDocumentStream:
    """Storage stream and trusted metadata for one published SLA document."""

    chunks: Iterator[bytes]
    content_type: str
    content_length: int | None
    file_name: str


class SLAPolicyReadService:
    """Load the published SLA policies visible to an organization."""

    MODULE_KEY = "sla_policies"
    CONTENT_TYPE = "sla_policy"
    INLINE_DOCUMENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})

    def __init__(self, db: Session):
        self.db = db

    def list_published_for_org(
        self, organization_id: UUID
    ) -> list[HelpArticleOverride]:
        """Return only published SLA policy rows for the organization."""
        stmt = (
            select(HelpArticleOverride)
            .where(
                HelpArticleOverride.organization_id == organization_id,
                HelpArticleOverride.module_key == self.MODULE_KEY,
                HelpArticleOverride.content_type == self.CONTENT_TYPE,
                HelpArticleOverride.status == ArticleStatus.PUBLISHED,
            )
            .order_by(
                HelpArticleOverride.published_at.desc().nullslast(),
                HelpArticleOverride.title.asc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def get_published_document_for_org(
        self,
        organization_id: UUID,
        article_id: UUID,
    ) -> SLAPolicyDocumentStream:
        """Stream one published document without crossing tenant/content scope."""
        stmt = select(HelpArticleOverride).where(
            HelpArticleOverride.organization_id == organization_id,
            HelpArticleOverride.module_key == self.MODULE_KEY,
            HelpArticleOverride.content_type == self.CONTENT_TYPE,
            HelpArticleOverride.status == ArticleStatus.PUBLISHED,
            HelpArticleOverride.article_id == article_id,
        )
        policy = self.db.scalar(stmt)
        if (
            policy is None
            or not policy.file_path
            or not policy.file_name
            or policy.file_content_type not in self.INLINE_DOCUMENT_TYPES
        ):
            raise SLAPolicyDocumentNotFoundError("SLA policy document not found")

        if policy.file_path.startswith("tenants/"):
            # Post-cutover staged document: resolve through the module
            # under this organization's tenant scope (the row query above
            # already pinned organization, module and published status).
            from dotmac_files import FileError, download_target, open_object

            from app.services.storage import get_dotmac_files_provider
            from app.tenancy import OrganizationTenantContext

            try:
                file_id = UUID(policy.file_path.rsplit("/", 1)[-1])
                scope = OrganizationTenantContext.for_organization(
                    organization_id
                ).tenant_scope
                target = download_target(self.db, scope=scope, file_id=file_id)
                readable = open_object(get_dotmac_files_provider(), target=target)
            except (FileError, ValueError):
                raise SLAPolicyDocumentNotFoundError(
                    "SLA policy document not found"
                ) from None

            def _chunks():
                with readable:
                    while chunk := readable.read(64 * 1024):
                        yield chunk

            return SLAPolicyDocumentStream(
                chunks=_chunks(),
                content_type=policy.file_content_type,
                content_length=target.size_bytes,
                file_name=policy.file_name,
            )

        expected_prefix = f"sla_policies/{organization_id}/{article_id}/"
        if not policy.file_path.startswith(expected_prefix):
            raise SLAPolicyDocumentNotFoundError("SLA policy document not found")

        storage = get_storage()
        if not storage.exists(policy.file_path):
            raise SLAPolicyDocumentNotFoundError("SLA policy document not found")
        chunks, _stored_content_type, content_length = storage.stream(policy.file_path)
        return SLAPolicyDocumentStream(
            chunks=chunks,
            content_type=policy.file_content_type,
            content_length=content_length,
            file_name=policy.file_name,
        )
