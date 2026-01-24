"""
AuditLogService - Immutable audit trail with tamper detection.

Provides append-only audit logging with hash chain for integrity verification.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.finance.audit.audit_log import AuditAction, AuditLog
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class AuditLogService(ListResponseMixin):
    """
    Service for immutable audit logging.

    Provides append-only audit trail with hash chain for tamper detection.
    """

    @staticmethod
    def log_change(
        db: Session,
        organization_id: UUID,
        table_schema: str,
        table_name: str,
        record_id: str,
        action: AuditAction,
        old_values: Optional[dict[str, Any]] = None,
        new_values: Optional[dict[str, Any]] = None,
        user_id: Optional[UUID] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[UUID] = None,
        correlation_id: Optional[str] = None,
        reason: Optional[str] = None,
        compute_hash: bool = True,
    ) -> UUID:
        """
        Log a data change to the audit trail.

        Automatically computes changed_fields and hash_chain.

        Args:
            db: Database session
            organization_id: Organization scope
            table_schema: Schema name (e.g., "gl", "ar")
            table_name: Table name
            record_id: Primary key of affected record
            action: Type of change (INSERT, UPDATE, DELETE)
            old_values: Previous values (for UPDATE/DELETE)
            new_values: New values (for INSERT/UPDATE)
            user_id: User making the change
            ip_address: Client IP address
            user_agent: Client user agent
            session_id: Session ID
            correlation_id: Correlation ID for tracing
            reason: Business reason for change
            compute_hash: Whether to compute hash chain

        Returns:
            Created audit log ID
        """
        org_id = coerce_uuid(organization_id)
        occurred_at = datetime.now(timezone.utc)

        # Compute changed fields
        changed_fields = AuditLogService._compute_changed_fields(old_values, new_values)

        # Get previous hash for chain
        hash_chain = None
        if compute_hash:
            prev_hash = AuditLogService._get_previous_hash(db, org_id)
            record_payload = {
                "organization_id": str(org_id),
                "table_schema": table_schema,
                "table_name": table_name,
                "record_id": record_id,
                "action": action.value,
                "old_values": old_values,
                "new_values": new_values,
                "user_id": str(user_id) if user_id else None,
                "occurred_at": occurred_at.isoformat(),
            }
            hash_chain = AuditLogService._compute_hash(prev_hash, record_payload)

        try:
            audit_log = AuditLog(
                organization_id=org_id,
                table_schema=table_schema,
                table_name=table_name,
                record_id=record_id,
                action=action,
                old_values=old_values,
                new_values=new_values,
                changed_fields=changed_fields,
                user_id=coerce_uuid(user_id) if user_id else None,
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=coerce_uuid(session_id) if session_id else None,
                correlation_id=correlation_id,
                reason=reason,
                hash_chain=hash_chain,
                occurred_at=occurred_at,
            )

            db.add(audit_log)
            db.commit()
            db.refresh(audit_log)

            return audit_log.audit_id
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Failed to write audit log: {e}")
            raise

    @staticmethod
    def get_audit_trail(
        db: Session,
        organization_id: UUID,
        table_schema: Optional[str] = None,
        table_name: Optional[str] = None,
        record_id: Optional[str] = None,
        user_id: Optional[UUID] = None,
        correlation_id: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """
        Query the audit trail.

        Args:
            db: Database session
            organization_id: Organization scope
            table_schema: Filter by schema
            table_name: Filter by table
            record_id: Filter by record ID
            user_id: Filter by user
            correlation_id: Filter by correlation ID
            from_date: Start of date range
            to_date: End of date range
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of audit log entries
        """
        org_id = coerce_uuid(organization_id)

        query = db.query(AuditLog).filter(AuditLog.organization_id == org_id)

        if table_schema:
            query = query.filter(AuditLog.table_schema == table_schema)

        if table_name:
            query = query.filter(AuditLog.table_name == table_name)

        if record_id:
            query = query.filter(AuditLog.record_id == record_id)

        if user_id:
            query = query.filter(AuditLog.user_id == coerce_uuid(user_id))

        if correlation_id:
            query = query.filter(AuditLog.correlation_id == correlation_id)

        if from_date:
            query = query.filter(AuditLog.occurred_at >= from_date)

        if to_date:
            query = query.filter(AuditLog.occurred_at <= to_date)

        query = query.order_by(AuditLog.occurred_at.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_record_history(
        db: Session,
        organization_id: UUID,
        table_schema: str,
        table_name: str,
        record_id: str,
    ) -> list[AuditLog]:
        """
        Get complete history of a specific record.

        Args:
            db: Database session
            organization_id: Organization scope
            table_schema: Schema name
            table_name: Table name
            record_id: Record ID

        Returns:
            Chronological list of changes
        """
        org_id = coerce_uuid(organization_id)

        return (
            db.query(AuditLog)
            .filter(
                and_(
                    AuditLog.organization_id == org_id,
                    AuditLog.table_schema == table_schema,
                    AuditLog.table_name == table_name,
                    AuditLog.record_id == record_id,
                )
            )
            .order_by(AuditLog.occurred_at.asc())
            .all()
        )

    @staticmethod
    def build_hash_chain(
        db: Session,
        organization_id: UUID,
        from_date: datetime,
        to_date: datetime,
    ) -> str:
        """
        Build/verify hash chain for a date range.

        Computes SHA256 chain: hash(prev_hash + record_payload).

        Args:
            db: Database session
            organization_id: Organization scope
            from_date: Start of range
            to_date: End of range

        Returns:
            Final hash in chain
        """
        org_id = coerce_uuid(organization_id)

        records = (
            db.query(AuditLog)
            .filter(
                and_(
                    AuditLog.organization_id == org_id,
                    AuditLog.occurred_at >= from_date,
                    AuditLog.occurred_at <= to_date,
                )
            )
            .order_by(AuditLog.occurred_at.asc())
            .all()
        )

        if not records:
            return ""

        prev_hash = None
        for record in records:
            record_payload = {
                "audit_id": str(record.audit_id),
                "organization_id": str(record.organization_id),
                "table_schema": record.table_schema,
                "table_name": record.table_name,
                "record_id": record.record_id,
                "action": record.action.value,
                "old_values": record.old_values,
                "new_values": record.new_values,
                "user_id": str(record.user_id) if record.user_id else None,
                "occurred_at": record.occurred_at.isoformat(),
            }
            prev_hash = AuditLogService._compute_hash(prev_hash, record_payload)

        return prev_hash

    @staticmethod
    def verify_hash_chain(
        db: Session,
        organization_id: UUID,
        from_date: datetime,
        to_date: datetime,
    ) -> tuple[bool, Optional[str]]:
        """
        Verify integrity of hash chain.

        Args:
            db: Database session
            organization_id: Organization scope
            from_date: Start of range
            to_date: End of range

        Returns:
            Tuple of (is_valid, first_invalid_audit_id)
        """
        org_id = coerce_uuid(organization_id)

        records = (
            db.query(AuditLog)
            .filter(
                and_(
                    AuditLog.organization_id == org_id,
                    AuditLog.occurred_at >= from_date,
                    AuditLog.occurred_at <= to_date,
                )
            )
            .order_by(AuditLog.occurred_at.asc())
            .all()
        )

        if not records:
            return (True, None)

        prev_hash = None
        for record in records:
            if record.hash_chain is None:
                # Skip records without hash chain
                continue

            record_payload = {
                "audit_id": str(record.audit_id),
                "organization_id": str(record.organization_id),
                "table_schema": record.table_schema,
                "table_name": record.table_name,
                "record_id": record.record_id,
                "action": record.action.value,
                "old_values": record.old_values,
                "new_values": record.new_values,
                "user_id": str(record.user_id) if record.user_id else None,
                "occurred_at": record.occurred_at.isoformat(),
            }
            expected_hash = AuditLogService._compute_hash(prev_hash, record_payload)

            if record.hash_chain != expected_hash:
                return (False, str(record.audit_id))

            prev_hash = record.hash_chain

        return (True, None)

    @staticmethod
    def _compute_changed_fields(
        old_values: Optional[dict],
        new_values: Optional[dict],
    ) -> list[str]:
        """Compute list of changed field names."""
        if old_values is None and new_values is None:
            return []

        if old_values is None:
            return list(new_values.keys()) if new_values else []

        if new_values is None:
            return list(old_values.keys())

        changed = []
        all_keys = set(old_values.keys()) | set(new_values.keys())

        for key in all_keys:
            old_val = old_values.get(key)
            new_val = new_values.get(key)
            if old_val != new_val:
                changed.append(key)

        return sorted(changed)

    @staticmethod
    def _compute_hash(
        prev_hash: Optional[str],
        record_payload: dict,
    ) -> str:
        """Compute SHA256 hash for chain."""
        try:
            data = {
                "prev_hash": prev_hash or "",
                "payload": record_payload,
            }
            json_str = json.dumps(data, sort_keys=True, default=str)
            return hashlib.sha256(json_str.encode()).hexdigest()
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to compute audit hash: {e}")
            # Return a fallback hash to not break the audit trail
            fallback = f"{prev_hash or ''}:{str(record_payload)}"
            return hashlib.sha256(fallback.encode()).hexdigest()

    @staticmethod
    def _get_previous_hash(
        db: Session,
        organization_id: UUID,
    ) -> Optional[str]:
        """Get the hash of the most recent audit log entry."""
        latest = (
            db.query(AuditLog)
            .filter(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.occurred_at.desc())
            .first()
        )

        return latest.hash_chain if latest else None

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        table_schema: Optional[str] = None,
        table_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        """
        List audit logs (for ListResponseMixin compatibility).

        Args:
            db: Database session
            organization_id: Filter by organization
            table_schema: Filter by schema
            table_name: Filter by table
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of AuditLog objects
        """
        query = db.query(AuditLog)

        if organization_id:
            query = query.filter(
                AuditLog.organization_id == coerce_uuid(organization_id)
            )

        if table_schema:
            query = query.filter(AuditLog.table_schema == table_schema)

        if table_name:
            query = query.filter(AuditLog.table_name == table_name)

        query = query.order_by(AuditLog.occurred_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
audit_log_service = AuditLogService()
