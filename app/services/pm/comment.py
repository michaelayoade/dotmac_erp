"""
PM Comment Service.

Handles comments for projects and tasks.
"""
import logging
import uuid
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pm.comment import PMComment, PMCommentAttachment, PMCommentType

logger = logging.getLogger(__name__)


class PMCommentService:
    """Service for managing project/task comments."""

    def list_comments(
        self,
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        include_internal: bool = True,
        include_deleted: bool = False,
    ) -> List[PMComment]:
        """List comments for a project or task."""
        query = select(PMComment).where(
            PMComment.organization_id == organization_id,
            PMComment.entity_type == entity_type,
            PMComment.entity_id == entity_id,
        )

        if not include_internal:
            query = query.where(PMComment.is_internal == False)  # noqa: E712

        if not include_deleted:
            query = query.where(PMComment.is_deleted == False)  # noqa: E712

        query = query.order_by(PMComment.created_at.asc())
        return list(db.execute(query).scalars().all())

    def add_comment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        author_id: uuid.UUID,
        content: str,
        is_internal: bool = False,
    ) -> PMComment:
        """Add a comment to a project or task."""
        comment = PMComment(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            comment_type=PMCommentType.INTERNAL_NOTE if is_internal else PMCommentType.COMMENT,
            content=content,
            author_id=author_id,
            is_internal=is_internal,
        )
        db.add(comment)
        db.flush()
        logger.info("Added comment to %s %s", entity_type, entity_id)
        return comment

    def delete_comment(
        self,
        db: Session,
        comment_id: uuid.UUID,
        hard_delete: bool = False,
    ) -> bool:
        """Delete a comment."""
        comment = db.get(PMComment, comment_id)
        if not comment:
            return False

        if comment.comment_type == PMCommentType.SYSTEM:
            logger.warning("Cannot delete system comment %s", comment_id)
            return False

        if hard_delete:
            db.delete(comment)
        else:
            comment.is_deleted = True
            db.flush()
        return True

    def list_comment_attachments(
        self,
        db: Session,
        comment_ids: Iterable[uuid.UUID],
    ) -> List[PMCommentAttachment]:
        """Return attachment links for the provided comment IDs."""
        ids = list(comment_ids)
        if not ids:
            return []
        query = select(PMCommentAttachment).where(
            PMCommentAttachment.comment_id.in_(ids),
        )
        return list(db.execute(query).scalars().all())


comment_service = PMCommentService()

