"""
Audit Information Service.

Shared service for fetching and formatting audit trail information.
Used by both API and web routes across all modules.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.person import Person

logger = logging.getLogger(__name__)


class HasAuditFields(Protocol):
    """Protocol for models with audit fields."""

    created_by_user_id: Optional[UUID]
    created_at: Optional[datetime]
    updated_by_user_id: Optional[UUID]
    updated_at: Optional[datetime]


T = TypeVar("T", bound=HasAuditFields)


class AuditInfoService:
    """
    Service for fetching and formatting audit information.

    Usage:
        audit_service = AuditInfoService(db)

        # Fetch names for a list of entities
        names = audit_service.get_creator_names(customers)

        # Or enrich entities with audit info
        enriched = audit_service.enrich_with_audit_info(customers)
    """

    def __init__(self, db: Session):
        self.db = db
        self._user_cache: Dict[UUID, str] = {}

    def get_user_name(self, user_id: Optional[UUID]) -> Optional[str]:
        """Get display name for a single user ID."""
        if not user_id:
            return None

        if user_id in self._user_cache:
            return self._user_cache[user_id]

        person = self.db.query(Person).filter(Person.id == user_id).first()
        if person:
            name = self._format_person_name(person)
            self._user_cache[user_id] = name
            return name
        return None

    def get_user_names_batch(self, user_ids: List[UUID]) -> Dict[UUID, str]:
        """
        Fetch display names for multiple user IDs in a single query.

        Returns a dict mapping user_id -> display_name.
        """
        if not user_ids:
            return {}

        # Filter out already cached IDs
        uncached_ids = [uid for uid in user_ids if uid not in self._user_cache]

        if uncached_ids:
            persons = self.db.query(Person).filter(Person.id.in_(uncached_ids)).all()
            for person in persons:
                self._user_cache[person.id] = self._format_person_name(person)

        return {
            uid: name
            for uid in user_ids
            if (name := self._user_cache.get(uid)) is not None
        }

    def get_creator_names(self, entities: List[T]) -> Dict[UUID, str]:
        """
        Get creator names for a list of entities with created_by_user_id.

        Returns a dict mapping user_id -> display_name.
        """
        creator_ids = [
            e.created_by_user_id
            for e in entities
            if hasattr(e, "created_by_user_id") and e.created_by_user_id
        ]
        return self.get_user_names_batch(creator_ids)

    def get_audit_info(self, entity: T) -> Dict[str, Any]:
        """
        Get formatted audit info for a single entity.

        Returns dict with created_at, created_by_name, updated_at, updated_by_name.
        """
        created_by_name = None
        updated_by_name = None

        if hasattr(entity, "created_by_user_id") and entity.created_by_user_id:
            created_by_name = self.get_user_name(entity.created_by_user_id)

        if hasattr(entity, "updated_by_user_id") and entity.updated_by_user_id:
            updated_by_name = self.get_user_name(entity.updated_by_user_id)

        return {
            "created_at": getattr(entity, "created_at", None),
            "created_by_user_id": getattr(entity, "created_by_user_id", None),
            "created_by_name": created_by_name,
            "updated_at": getattr(entity, "updated_at", None),
            "updated_by_user_id": getattr(entity, "updated_by_user_id", None),
            "updated_by_name": updated_by_name,
        }

    def enrich_with_audit_info(self, entities: List[T]) -> List[Dict[str, Any]]:
        """
        Enrich a list of entities with audit information.

        Pre-fetches all user names in a single query for efficiency.
        Returns list of dicts with audit info added.
        """
        # Pre-fetch all creator and updater names
        all_user_ids = []
        for e in entities:
            if hasattr(e, "created_by_user_id") and e.created_by_user_id:
                all_user_ids.append(e.created_by_user_id)
            if hasattr(e, "updated_by_user_id") and e.updated_by_user_id:
                all_user_ids.append(e.updated_by_user_id)

        # Batch fetch all names
        self.get_user_names_batch(list(set(all_user_ids)))

        # Now get individual audit info (will use cache)
        return [self.get_audit_info(e) for e in entities]

    @staticmethod
    def _format_person_name(person: Person) -> str:
        """Format person's display name."""
        # Prefer display_name if set
        if person.display_name:
            return person.display_name

        # Otherwise use first + last name
        name_parts = []
        if person.first_name:
            name_parts.append(person.first_name)
        if person.last_name:
            name_parts.append(person.last_name)

        if name_parts:
            return " ".join(name_parts)
        return person.email or "Unknown"

    @staticmethod
    def format_audit_date(dt: Optional[datetime], include_time: bool = False) -> str:
        """Format datetime for display."""
        if not dt:
            return "-"
        if include_time:
            return dt.strftime("%b %d, %Y %H:%M")
        return dt.strftime("%b %d, %Y")


def get_audit_service(db: Session) -> AuditInfoService:
    """Factory function to create audit service instance."""
    return AuditInfoService(db)
