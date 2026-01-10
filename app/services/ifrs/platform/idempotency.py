"""
IdempotencyService - API idempotency management.

Ensures that duplicate API requests with the same idempotency key
return cached responses rather than re-executing operations.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete
from sqlalchemy.orm import Session

from app.models.ifrs.platform.idempotency_record import IdempotencyRecord
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


class IdempotencyService(ListResponseMixin):
    """
    Service for managing API idempotency.

    Ensures that duplicate requests with the same idempotency key
    return cached responses rather than re-executing operations.
    """

    DEFAULT_TTL_HOURS: int = 24

    @staticmethod
    def check(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> Optional[IdempotencyRecord]:
        """
        Check if an idempotency key exists and is valid.

        Args:
            db: Database session
            organization_id: Organization scope
            idempotency_key: Client-provided idempotency key
            endpoint: API endpoint path
            request_hash: SHA256 hash of request body

        Returns:
            IdempotencyRecord if found and valid, None otherwise

        Raises:
            HTTPException(409): If key exists but request_hash differs (conflict)
        """
        org_id = coerce_uuid(organization_id)
        now = datetime.now(timezone.utc)

        record = (
            db.query(IdempotencyRecord)
            .filter(
                and_(
                    IdempotencyRecord.organization_id == org_id,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                    IdempotencyRecord.endpoint == endpoint,
                )
            )
            .first()
        )

        if record is None:
            return None

        # Check if expired
        if record.expires_at < now:
            # Expired record - delete it and return None
            db.delete(record)
            db.commit()
            return None

        # Check if request hash matches
        if record.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used with different request body",
            )

        return record

    @staticmethod
    def store_response(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
        response_status: int,
        response_body: Optional[dict[str, Any]] = None,
        ttl_hours: int = 24,
    ) -> IdempotencyRecord:
        """
        Store a response for future replay.

        Args:
            db: Database session
            organization_id: Organization scope
            idempotency_key: Client-provided idempotency key
            endpoint: API endpoint path
            request_hash: SHA256 hash of request body
            response_status: HTTP status code
            response_body: JSON-serializable response body
            ttl_hours: Time-to-live in hours (default: 24)

        Returns:
            Created IdempotencyRecord
        """
        org_id = coerce_uuid(organization_id)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=ttl_hours)

        record = IdempotencyRecord(
            organization_id=org_id,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            expires_at=expires_at,
        )

        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_cached_response(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
    ) -> Optional[tuple[int, Optional[dict[str, Any]]]]:
        """
        Retrieve cached response for replay.

        Args:
            db: Database session
            organization_id: Organization scope
            idempotency_key: Client-provided idempotency key
            endpoint: API endpoint path

        Returns:
            Tuple of (status_code, response_body) if found, None otherwise
        """
        org_id = coerce_uuid(organization_id)
        now = datetime.now(timezone.utc)

        record = (
            db.query(IdempotencyRecord)
            .filter(
                and_(
                    IdempotencyRecord.organization_id == org_id,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                    IdempotencyRecord.endpoint == endpoint,
                    IdempotencyRecord.expires_at > now,
                )
            )
            .first()
        )

        if record is None:
            return None

        return (record.response_status, record.response_body)

    @staticmethod
    def cleanup_expired(
        db: Session,
        batch_size: int = 1000,
    ) -> int:
        """
        Remove expired idempotency records.

        Args:
            db: Database session
            batch_size: Maximum records to delete per call

        Returns:
            Number of records deleted
        """
        now = datetime.now(timezone.utc)

        # Get IDs of expired records (limited by batch_size)
        expired_ids = (
            db.query(IdempotencyRecord.record_id)
            .filter(IdempotencyRecord.expires_at < now)
            .limit(batch_size)
            .all()
        )

        if not expired_ids:
            return 0

        ids_to_delete = [r[0] for r in expired_ids]

        # Delete the records
        result = db.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.record_id.in_(ids_to_delete)
            )
        )
        db.commit()

        return result.rowcount

    @staticmethod
    def get(
        db: Session,
        record_id: str,
    ) -> IdempotencyRecord:
        """
        Get an idempotency record by ID.

        Args:
            db: Database session
            record_id: Record ID

        Returns:
            IdempotencyRecord

        Raises:
            HTTPException(404): If record not found
        """
        record = db.get(IdempotencyRecord, coerce_uuid(record_id))
        if not record:
            raise HTTPException(status_code=404, detail="Idempotency record not found")
        return record

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IdempotencyRecord]:
        """
        List idempotency records.

        Args:
            db: Database session
            organization_id: Filter by organization
            endpoint: Filter by endpoint
            include_expired: Include expired records
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of IdempotencyRecord objects
        """
        query = db.query(IdempotencyRecord)

        if organization_id:
            query = query.filter(
                IdempotencyRecord.organization_id == coerce_uuid(organization_id)
            )

        if endpoint:
            query = query.filter(IdempotencyRecord.endpoint == endpoint)

        if not include_expired:
            now = datetime.now(timezone.utc)
            query = query.filter(IdempotencyRecord.expires_at > now)

        query = query.order_by(IdempotencyRecord.created_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
idempotency_service = IdempotencyService()
