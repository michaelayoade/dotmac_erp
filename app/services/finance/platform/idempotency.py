"""
IdempotencyService - API idempotency management.

Ensures that duplicate API requests with the same idempotency key
return cached responses rather than re-executing operations.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from fastapi import HTTPException
from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.finance.platform.idempotency_record import IdempotencyRecord
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class IdempotencyService(ListResponseMixin):
    """
    Service for managing API idempotency.

    Ensures that duplicate requests with the same idempotency key
    return cached responses rather than re-executing operations.
    """

    DEFAULT_TTL_HOURS: int = 24

    #: HTTP status stored by `reserve` to mark a key as claimed-but-unfinished.
    RESERVATION_STATUS: int = 202

    #: How long an unfinished reservation may block retries before another
    #: attempt may take it over. `reserve` writes its placeholder BEFORE the
    #: side effect runs, so a request that dies in between leaves a `202
    #: "Request in progress"` row that every retry replays. Without a lease that
    #: row blocks the operation for the full `DEFAULT_TTL_HOURS` (24h) and the
    #: work can never be re-driven — there is no other recovery path. The lease
    #: is deliberately far shorter than the TTL: a *completed* response should
    #: be replayable all day, an *unfinished* claim should not.
    RESERVATION_LEASE_MINUTES: int = 15

    @staticmethod
    def is_stale_reservation(
        record: IdempotencyRecord,
        *,
        lease_minutes: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Whether `record` is an unfinished reservation whose lease has lapsed.

        Only ever true for a row still carrying the placeholder written by
        `reserve` — once `update_response` records a real outcome the row is a
        completed response and is replayed for its full TTL.
        """
        if record.response_status != IdempotencyService.RESERVATION_STATUS:
            return False
        lease = timedelta(
            minutes=lease_minutes
            if lease_minutes is not None
            else IdempotencyService.RESERVATION_LEASE_MINUTES
        )
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at < (now or datetime.now(UTC)) - lease

    @staticmethod
    def take_over_reservation(
        db: Session,
        record: IdempotencyRecord,
        *,
        lease_minutes: int | None = None,
    ) -> bool:
        """Try to take over a lapsed reservation. True means THIS caller owns it.

        A single conditional UPDATE, so concurrent retries resolve in the
        database rather than in Python: exactly one gets `rowcount == 1` and
        re-executes; the others keep replaying the in-progress placeholder until
        the winner records a real outcome.
        """
        lease = timedelta(
            minutes=lease_minutes
            if lease_minutes is not None
            else IdempotencyService.RESERVATION_LEASE_MINUTES
        )
        now = datetime.now(UTC)
        result = db.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.record_id == record.record_id,
                IdempotencyRecord.response_status
                == IdempotencyService.RESERVATION_STATUS,
                IdempotencyRecord.created_at < now - lease,
            )
            .values(created_at=now)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.expire(record)
        won = cast("CursorResult[Any]", result).rowcount == 1
        if won:
            logger.warning(
                "Taking over a lapsed idempotency reservation: key=%s endpoint=%s",
                record.idempotency_key,
                record.endpoint,
            )
        return won

    @staticmethod
    def check(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> IdempotencyRecord | None:
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
        now = datetime.now(UTC)

        record = db.scalars(
            select(IdempotencyRecord).where(
                and_(
                    IdempotencyRecord.organization_id == org_id,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                    IdempotencyRecord.endpoint == endpoint,
                )
            )
        ).first()

        if record is None:
            return None

        # Check if expired (handle naive datetimes in SQLite)
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < now:
            # Expired record - delete it and return None
            db.delete(record)
            db.commit()
            return None

        # Check if request hash matches. An EMPTY stored hash means "unknown",
        # not "different": `update_response` writes `request_hash=""` when it
        # has to create a record it expected to already exist, and comparing a
        # real hash against that sentinel made every later retry of a legitimate
        # request 409 forever — the key was permanently poisoned. An unknown
        # hash cannot contradict anything, so it replays.
        if record.request_hash and record.request_hash != request_hash:
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
        response_body: dict[str, Any] | None = None,
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
        now = datetime.now(UTC)
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
    def reserve(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
        *,
        ttl_hours: int = 24,
    ) -> IdempotencyRecord:
        """
        Reserve an idempotency key before any side effects.

        Stores a placeholder response so retries can be served deterministically.
        """
        try:
            return IdempotencyService.store_response(
                db=db,
                organization_id=organization_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_hash=request_hash,
                response_status=202,
                response_body={"detail": "Request in progress"},
                ttl_hours=ttl_hours,
            )
        except IntegrityError:
            db.rollback()
            # Another request reserved the key; validate and return existing
            record = IdempotencyService.check(
                db=db,
                organization_id=organization_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_hash=request_hash,
            )
            if record is None:
                raise
            return record

    @staticmethod
    def update_response(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        response_status: int,
        response_body: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        """
        Update the cached response for an existing idempotency key.
        """
        org_id = coerce_uuid(organization_id)
        record = db.scalars(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.organization_id == org_id)
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
            .where(IdempotencyRecord.endpoint == endpoint)
        ).first()

        if record is None:
            # Fallback: create a record if it doesn't exist
            record = IdempotencyService.store_response(
                db=db,
                organization_id=organization_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_hash="",
                response_status=response_status,
                response_body=response_body,
                ttl_hours=IdempotencyService.DEFAULT_TTL_HOURS,
            )
            return record

        record.response_status = response_status
        record.response_body = response_body
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_cached_response(
        db: Session,
        organization_id: UUID,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> tuple[int, dict[str, Any] | None] | None:
        """
        Retrieve cached response for replay.

        Args:
            db: Database session
            organization_id: Organization scope
            idempotency_key: Client-provided idempotency key
            endpoint: API endpoint path
            request_hash: SHA256 hash of the request body

        Returns:
            Tuple of (status_code, response_body) if found, None otherwise

        Raises:
            HTTPException(409): If key exists but request_hash differs

        Note:
            `request_hash` became REQUIRED here. This read path skipped the
            comparison `check` performs, so it would happily hand one request's
            recorded response to a DIFFERENT request presenting the same key —
            the conflict rule existed on one read path and not the other. It has
            no production caller today; the parameter is required so it cannot
            be adopted in the unsafe shape.
        """
        org_id = coerce_uuid(organization_id)
        now = datetime.now(UTC)

        record = db.scalars(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.organization_id == org_id)
            .where(IdempotencyRecord.idempotency_key == idempotency_key)
            .where(IdempotencyRecord.endpoint == endpoint)
            .where(IdempotencyRecord.expires_at > now)
        ).first()

        if record is None:
            return None

        # Same "empty means unknown" rule as `check` — see the comment there.
        if record.request_hash and record.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used with different request body",
            )

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
        now = datetime.now(UTC)

        # Get IDs of expired records (limited by batch_size)
        expired_ids = db.execute(
            select(IdempotencyRecord.record_id)
            .where(IdempotencyRecord.expires_at < now)
            .limit(batch_size)
        ).all()

        if not expired_ids:
            return 0

        ids_to_delete = [r[0] if isinstance(r, tuple) else r for r in expired_ids]

        # Delete the records
        result = db.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.record_id.in_(ids_to_delete)
            )
        )
        db.commit()

        return cast(CursorResult[Any], result).rowcount or 0

    @staticmethod
    def get(
        db: Session,
        record_id: str,
        organization_id: UUID | None = None,
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
        if organization_id is not None and record.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Idempotency record not found")
        return record

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        endpoint: str | None = None,
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
        stmt = select(IdempotencyRecord)

        if organization_id:
            stmt = stmt.where(
                IdempotencyRecord.organization_id == coerce_uuid(organization_id)
            )

        if endpoint:
            stmt = stmt.where(IdempotencyRecord.endpoint == endpoint)

        if not include_expired:
            now = datetime.now(UTC)
            stmt = stmt.where(IdempotencyRecord.expires_at > now)

        return list(
            db.scalars(
                stmt.order_by(IdempotencyRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )


# Module-level singleton instance
idempotency_service = IdempotencyService()
