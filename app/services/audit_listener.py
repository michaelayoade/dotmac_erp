"""
Automatic ORM Audit Listener — captures all data changes via SQLAlchemy events.

Registers ``before_flush`` / ``after_flush`` hooks on the Session class so
that every INSERT, UPDATE, and DELETE on auditable models is automatically
logged to ``audit.audit_log``.  This provides **baseline coverage** that
cannot be forgotten when new services or methods are added.

Existing manual ``fire_audit_event()`` calls continue to work and take
precedence for business context (``reason`` field).  The auto-listener
skips records that were already audited in the same flush by checking
a session-local set.

Design decisions:
- Uses ``connection.execute()`` (raw SQL) to insert audit rows, avoiding
  recursive ORM flush.
- Captures only columns that changed (for UPDATE) using attribute history.
- Serialises values to JSON-safe strings (UUIDs, datetimes, Decimals, enums).
- Skips models without ``organization_id`` (non-tenant tables).
- Skips infrastructure tables (audit log, notifications, outbox, sessions).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Tables to SKIP (infrastructure / high-volume / self-referencing) ───────
_SKIP_TABLES: frozenset[tuple[str | None, str]] = frozenset(
    {
        ("audit", "audit_log"),
        ("public", "audit_event"),
        ("public", "notification"),
        ("public", "session_token"),
        ("platform", "event_outbox"),
        ("platform", "idempotency_key"),
        # Snapshots and computed aggregates — not business transactions
        ("ar", "ar_aging_snapshot"),
        ("ap", "ap_aging_snapshot"),
        ("gl", "account_balance"),
        ("gl", "posted_ledger_line"),
        # Field-level change tracking (separate from forensic audit)
        ("audit", "field_change_log"),
    }
)

# Session info key used to store pending audit records between hooks
_PENDING_KEY = "_audit_pending_records"
# Session info key to track records already audited by manual fire_audit_event
_MANUAL_KEY = "_audit_manual_ids"


def _serialise_value(val: Any) -> Any:
    """Convert a Python value to a JSON-safe representation."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, bytes):
        return "<binary>"
    if isinstance(val, (int, float, bool, str)):
        return val
    # Fallback — best-effort string
    return str(val)


def _get_model_schema(mapper: Any) -> str | None:
    """Extract the PostgreSQL schema from a model's __table_args__."""
    table = mapper.persist_selectable
    schema: str | None = table.schema
    return schema


def _get_table_name(mapper: Any) -> str:
    """Get the table name from a mapper."""
    name: str = mapper.persist_selectable.name
    return name


def _get_pk_value(obj: Any, mapper: Any) -> str | None:
    """Get the stringified primary key value of an ORM instance."""
    pk_cols = mapper.primary_key
    if len(pk_cols) == 1:
        val = getattr(obj, pk_cols[0].name, None)
        return str(val) if val is not None else None
    # Composite PK — join with ':'
    parts: list[str] = []
    for col in pk_cols:
        v = getattr(obj, col.name, None)
        if v is None:
            return None
        parts.append(str(v))
    return ":".join(parts)


def _get_org_id(obj: Any) -> uuid.UUID | None:
    """Extract organization_id from a model instance."""
    oid = getattr(obj, "organization_id", None)
    if isinstance(oid, uuid.UUID):
        return oid
    if isinstance(oid, str):
        try:
            return uuid.UUID(oid)
        except ValueError:
            return None
    return None


def _snapshot_values(obj: Any, mapper: Any) -> dict[str, Any]:
    """Snapshot all column values of an ORM instance."""
    result: dict[str, Any] = {}
    for col in mapper.columns:
        name = col.key
        val = getattr(obj, name, None)
        result[name] = _serialise_value(val)
    return result


def _changed_values(obj: Any, mapper: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """For a dirty instance, return (old_values, new_values) of changed columns only."""
    state = inspect(obj)
    old_vals: dict[str, Any] = {}
    new_vals: dict[str, Any] = {}
    for attr in state.attrs:
        hist = attr.history
        if hist.has_changes():
            col_name = attr.key
            # hist.deleted = old values, hist.added = new values
            if hist.deleted:
                old_vals[col_name] = _serialise_value(hist.deleted[0])
            if hist.added:
                new_vals[col_name] = _serialise_value(hist.added[0])
    return old_vals, new_vals


def _should_skip(schema: str | None, table_name: str) -> bool:
    """Check if this table should be skipped."""
    return (schema, table_name) in _SKIP_TABLES


def _collect_changes(session: Session) -> list[dict[str, Any]]:
    """Collect all pending changes from the session before flush.

    Called in ``before_flush`` so attribute history is still available.
    """
    records: list[dict[str, Any]] = []

    # INSERTs
    for obj in session.new:
        mapper = inspect(type(obj))
        schema = _get_model_schema(mapper)
        table_name = _get_table_name(mapper)
        if _should_skip(schema, table_name):
            continue
        org_id = _get_org_id(obj)
        if org_id is None:
            continue
        pk = _get_pk_value(obj, mapper)
        # PK may be None before flush (server-side default) — we'll fill it after
        records.append(
            {
                "obj": obj,
                "mapper": mapper,
                "action": "INSERT",
                "schema": schema or "public",
                "table_name": table_name,
                "organization_id": org_id,
                "record_id": pk,
                "old_values": None,
                "new_values": None,  # Will snapshot after flush when PK is available
            }
        )

    # UPDATEs
    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        mapper = inspect(type(obj))
        schema = _get_model_schema(mapper)
        table_name = _get_table_name(mapper)
        if _should_skip(schema, table_name):
            continue
        org_id = _get_org_id(obj)
        if org_id is None:
            continue
        pk = _get_pk_value(obj, mapper)
        if pk is None:
            continue
        old_vals, new_vals = _changed_values(obj, mapper)
        if not old_vals and not new_vals:
            continue
        records.append(
            {
                "obj": obj,
                "mapper": mapper,
                "action": "UPDATE",
                "schema": schema or "public",
                "table_name": table_name,
                "organization_id": org_id,
                "record_id": pk,
                "old_values": old_vals,
                "new_values": new_vals,
            }
        )

    # DELETEs
    for obj in session.deleted:
        mapper = inspect(type(obj))
        schema = _get_model_schema(mapper)
        table_name = _get_table_name(mapper)
        if _should_skip(schema, table_name):
            continue
        org_id = _get_org_id(obj)
        if org_id is None:
            continue
        pk = _get_pk_value(obj, mapper)
        if pk is None:
            continue
        old_vals = _snapshot_values(obj, mapper)
        records.append(
            {
                "obj": obj,
                "mapper": mapper,
                "action": "DELETE",
                "schema": schema or "public",
                "table_name": table_name,
                "organization_id": org_id,
                "record_id": pk,
                "old_values": old_vals,
                "new_values": None,
            }
        )

    return records


def _resolve_context() -> tuple[uuid.UUID | None, str | None, str | None, str | None]:
    """Read actor/request context from ContextVars (same as fire_audit_event)."""
    try:
        from app.observability import (
            actor_id_var,
            ip_address_var,
            request_id_var,
            user_agent_var,
        )

        user_id: uuid.UUID | None = None
        actor_str = actor_id_var.get()
        if actor_str:
            try:
                user_id = uuid.UUID(actor_str)
            except ValueError:
                pass
        correlation_id = request_id_var.get() or None
        ip_address = ip_address_var.get() or None
        user_agent = user_agent_var.get() or None
        return user_id, correlation_id, ip_address, user_agent
    except Exception:
        return None, None, None, None


def _write_audit_records(session: Session) -> None:
    """Write collected audit records via raw SQL (no recursive flush)."""
    pending: list[dict[str, Any]] | None = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return

    user_id, correlation_id, ip_address, user_agent = _resolve_context()
    connection = session.connection()

    for rec in pending:
        try:
            # For INSERTs, snapshot values now that PK is generated
            if rec["action"] == "INSERT":
                obj = rec["obj"]
                mapper = rec["mapper"]
                rec["record_id"] = _get_pk_value(obj, mapper)
                rec["new_values"] = _snapshot_values(obj, mapper)

            if rec["record_id"] is None:
                continue

            import json

            old_json = json.dumps(rec["old_values"]) if rec["old_values"] else None
            new_json = json.dumps(rec["new_values"]) if rec["new_values"] else None

            # Compute changed_fields for UPDATEs
            changed_fields: list[str] | None = None
            if rec["action"] == "UPDATE" and rec["old_values"] and rec["new_values"]:
                all_keys = set(rec["old_values"].keys()) | set(rec["new_values"].keys())
                changed_fields = sorted(all_keys)

            # Use SAVEPOINT so audit failures don't abort the parent transaction.
            # Use CAST() instead of ::jsonb — the :: shorthand conflicts with
            # SQLAlchemy text() parameter binding (colons are ambiguous).
            nested = connection.begin_nested()
            try:
                connection.execute(
                    text("""
                        INSERT INTO audit.audit_log (
                            audit_id, organization_id, table_schema, table_name,
                            record_id, action, old_values, new_values,
                            changed_fields, user_id, ip_address, user_agent,
                            correlation_id, occurred_at
                        ) VALUES (
                            gen_random_uuid(), :org_id, :schema, :table_name,
                            :record_id, :action,
                            CAST(:old_values AS jsonb), CAST(:new_values AS jsonb),
                            :changed_fields, :user_id, :ip_address, :user_agent,
                            :correlation_id, now()
                        )
                    """),
                    {
                        "org_id": rec["organization_id"],
                        "schema": rec["schema"],
                        "table_name": rec["table_name"],
                        "record_id": rec["record_id"],
                        "action": rec["action"],
                        "old_values": old_json,
                        "new_values": new_json,
                        "changed_fields": changed_fields,
                        "user_id": user_id,
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                        "correlation_id": correlation_id,
                    },
                )
                nested.commit()
            except Exception:
                nested.rollback()
                raise
        except Exception:
            logger.warning(
                "Auto-audit failed: %s.%s %s %s",
                rec["schema"],
                rec["table_name"],
                rec["action"],
                rec.get("record_id"),
                exc_info=True,
            )


# ── Event handlers ─────────────────────────────────────────────────────────


def _on_before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    """Capture changes before the SQL executes (attribute history available)."""
    try:
        records = _collect_changes(session)
        if records:
            session.info[_PENDING_KEY] = records
    except Exception:
        logger.warning("Auto-audit before_flush failed", exc_info=True)


def _on_after_flush(session: Session, flush_context: Any) -> None:
    """Write audit records after the SQL executes."""
    try:
        _write_audit_records(session)
    except Exception:
        logger.warning("Auto-audit after_flush failed", exc_info=True)


def register_audit_listeners() -> None:
    """Register the automatic audit listeners on all Sessions.

    Call this once at application startup (e.g. in ``app/main.py``).
    """
    event.listen(Session, "before_flush", _on_before_flush)
    event.listen(Session, "after_flush", _on_after_flush)
    logger.info("Automatic ORM audit listeners registered")
