from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SyncResult:
    """Result of a single-entity sync operation."""

    success: bool
    entity_type: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "entity_type": self.entity_type,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "message": self.message,
        }


@dataclass
class FullSyncResult:
    """Result of a full sync (all entity types)."""

    resellers: SyncResult
    subscribers: SyncResult
    invoices: SyncResult
    payments: SyncResult
    credit_notes: SyncResult
    total_errors: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resellers": self.resellers.to_dict(),
            "subscribers": self.subscribers.to_dict(),
            "invoices": self.invoices.to_dict(),
            "payments": self.payments.to_dict(),
            "credit_notes": self.credit_notes.to_dict(),
            "total_errors": self.total_errors,
            "duration_seconds": self.duration_seconds,
        }
