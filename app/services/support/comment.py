"""
Ticket Comment Service.

Handles comments, internal notes, and activity tracking for support tickets.
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models.support.comment import TicketComment, CommentType
from app.models.support.ticket import Ticket

logger = logging.getLogger(__name__)


class CommentService:
    """Service for managing ticket comments and activity log."""

    def list_comments(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        include_internal: bool = True,
        include_deleted: bool = False,
    ) -> List[TicketComment]:
        """
        List comments for a ticket.

        Args:
            db: Database session
            ticket_id: Ticket UUID
            include_internal: Include internal notes (staff only)
            include_deleted: Include soft-deleted comments

        Returns:
            List of comments ordered by creation time
        """
        query = select(TicketComment).where(
            TicketComment.ticket_id == ticket_id
        )

        if not include_internal:
            query = query.where(TicketComment.is_internal == False)  # noqa: E712

        if not include_deleted:
            query = query.where(TicketComment.is_deleted == False)  # noqa: E712

        query = query.order_by(TicketComment.created_at)

        return list(db.execute(query).scalars().all())

    def get_comment(
        self,
        db: Session,
        comment_id: uuid.UUID,
    ) -> Optional[TicketComment]:
        """Get a comment by ID."""
        return db.get(TicketComment, comment_id)

    def add_comment(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        author_id: uuid.UUID,
        content: str,
        is_internal: bool = False,
    ) -> TicketComment:
        """
        Add a comment to a ticket.

        Args:
            db: Database session
            ticket_id: Ticket UUID
            author_id: Author's person UUID
            content: Comment content (supports markdown)
            is_internal: If true, only visible to staff

        Returns:
            Created comment
        """
        comment = TicketComment(
            ticket_id=ticket_id,
            comment_type=CommentType.INTERNAL_NOTE if is_internal else CommentType.COMMENT,
            content=content,
            author_id=author_id,
            is_internal=is_internal,
        )
        db.add(comment)
        db.flush()

        logger.info(
            "Added %s to ticket %s by %s",
            "internal note" if is_internal else "comment",
            ticket_id,
            author_id,
        )

        return comment

    def update_comment(
        self,
        db: Session,
        comment_id: uuid.UUID,
        content: str,
    ) -> Optional[TicketComment]:
        """
        Update a comment's content.

        Args:
            db: Database session
            comment_id: Comment UUID
            content: New content

        Returns:
            Updated comment or None if not found
        """
        comment = self.get_comment(db, comment_id)
        if not comment:
            return None

        if comment.comment_type == CommentType.SYSTEM:
            logger.warning("Cannot edit system comment %s", comment_id)
            return None

        comment.content = content
        db.flush()

        return comment

    def delete_comment(
        self,
        db: Session,
        comment_id: uuid.UUID,
        hard_delete: bool = False,
    ) -> bool:
        """
        Delete a comment.

        Args:
            db: Database session
            comment_id: Comment UUID
            hard_delete: If true, permanently delete; otherwise soft delete

        Returns:
            True if deleted, False if not found
        """
        comment = self.get_comment(db, comment_id)
        if not comment:
            return False

        if comment.comment_type == CommentType.SYSTEM:
            logger.warning("Cannot delete system comment %s", comment_id)
            return False

        if hard_delete:
            db.delete(comment)
        else:
            comment.is_deleted = True
            db.flush()

        return True

    def log_activity(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        action: str,
        description: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        author_id: Optional[uuid.UUID] = None,
    ) -> TicketComment:
        """
        Log a system activity on a ticket.

        Args:
            db: Database session
            ticket_id: Ticket UUID
            action: Action type (status_change, assigned, priority_change, etc.)
            description: Human-readable description
            old_value: Previous value (for changes)
            new_value: New value (for changes)
            author_id: User who performed the action

        Returns:
            Created system comment
        """
        comment = TicketComment.create_system_comment(
            ticket_id=ticket_id,
            action=action,
            content=description,
            old_value=old_value,
            new_value=new_value,
            author_id=author_id,
        )
        db.add(comment)
        db.flush()

        logger.debug("Logged activity %s on ticket %s", action, ticket_id)

        return comment

    def log_status_change(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        old_status: str,
        new_status: str,
        author_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
    ) -> TicketComment:
        """Log a status change."""
        content = f"Status changed from {old_status} to {new_status}"
        if notes:
            content += f": {notes}"

        return self.log_activity(
            db,
            ticket_id,
            action="status_change",
            description=content,
            old_value=old_status,
            new_value=new_status,
            author_id=author_id,
        )

    def log_assignment(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        assignee_name: str,
        previous_assignee: Optional[str] = None,
        author_id: Optional[uuid.UUID] = None,
    ) -> TicketComment:
        """Log an assignment change."""
        if previous_assignee:
            content = f"Reassigned from {previous_assignee} to {assignee_name}"
        else:
            content = f"Assigned to {assignee_name}"

        return self.log_activity(
            db,
            ticket_id,
            action="assigned",
            description=content,
            old_value=previous_assignee,
            new_value=assignee_name,
            author_id=author_id,
        )

    def log_priority_change(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        old_priority: str,
        new_priority: str,
        author_id: Optional[uuid.UUID] = None,
    ) -> TicketComment:
        """Log a priority change."""
        return self.log_activity(
            db,
            ticket_id,
            action="priority_change",
            description=f"Priority changed from {old_priority} to {new_priority}",
            old_value=old_priority,
            new_value=new_priority,
            author_id=author_id,
        )

    def log_category_change(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        old_category: Optional[str],
        new_category: str,
        author_id: Optional[uuid.UUID] = None,
    ) -> TicketComment:
        """Log a category change."""
        if old_category:
            content = f"Category changed from {old_category} to {new_category}"
        else:
            content = f"Category set to {new_category}"

        return self.log_activity(
            db,
            ticket_id,
            action="category_change",
            description=content,
            old_value=old_category,
            new_value=new_category,
            author_id=author_id,
        )

    def log_team_change(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        old_team: Optional[str],
        new_team: str,
        author_id: Optional[uuid.UUID] = None,
    ) -> TicketComment:
        """Log a team assignment change."""
        if old_team:
            content = f"Team changed from {old_team} to {new_team}"
        else:
            content = f"Assigned to team {new_team}"

        return self.log_activity(
            db,
            ticket_id,
            action="team_change",
            description=content,
            old_value=old_team,
            new_value=new_team,
            author_id=author_id,
        )

    def get_activity_timeline(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        limit: int = 50,
    ) -> List[TicketComment]:
        """
        Get activity timeline for a ticket (all comment types).

        Args:
            db: Database session
            ticket_id: Ticket UUID
            limit: Maximum entries to return

        Returns:
            List of comments/activities ordered by time (newest first)
        """
        query = (
            select(TicketComment)
            .where(
                TicketComment.ticket_id == ticket_id,
                TicketComment.is_deleted == False,  # noqa: E712
            )
            .order_by(TicketComment.created_at.desc())
            .limit(limit)
        )

        return list(db.execute(query).scalars().all())


# Singleton instance
comment_service = CommentService()
