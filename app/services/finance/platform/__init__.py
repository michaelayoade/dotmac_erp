"""
Platform services for the IFRS accounting system.

This package sits on the import path for many finance submodules. Keep the
package `__init__` import-light and lazy-load exports to avoid importing the
entire platform stack during test collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.services.finance.platform.approval_workflow import (  # noqa: F401
        ApprovalWorkflowService,
        approval_workflow_service,
    )
    from app.services.finance.platform.audit_log import (  # noqa: F401
        AuditLogService,
        audit_log_service,
    )
    from app.services.finance.platform.authorization import (  # noqa: F401
        AuthorizationService,
        authorization_service,
    )
    from app.services.finance.platform.fx import FXService, fx_service  # noqa: F401
    from app.services.finance.platform.idempotency import (  # noqa: F401
        IdempotencyService,
        idempotency_service,
    )
    from app.services.finance.platform.org_context import (  # noqa: F401
        OrgContextService,
        org_context_service,
    )
    from app.services.finance.platform.outbox_publisher import (  # noqa: F401
        OutboxPublisher,
        StaleClaimError,
        outbox_publisher,
    )
    from app.services.finance.platform.outbox_reconciler import (  # noqa: F401
        OutboxBalanceReconciler,
    )
    from app.services.finance.platform.sequence import (  # noqa: F401
        SequenceService,
        sequence_service,
    )


__all__ = [
    "IdempotencyService",
    "idempotency_service",
    "SequenceService",
    "sequence_service",
    "FXService",
    "fx_service",
    "OutboxPublisher",
    "outbox_publisher",
    "StaleClaimError",
    "OutboxBalanceReconciler",
    "AuditLogService",
    "audit_log_service",
    "AuthorizationService",
    "authorization_service",
    "ApprovalWorkflowService",
    "approval_workflow_service",
    "OrgContextService",
    "org_context_service",
]


_NAME_TO_MODULE = {
    "ApprovalWorkflowService": "approval_workflow",
    "approval_workflow_service": "approval_workflow",
    "AuditLogService": "audit_log",
    "audit_log_service": "audit_log",
    "AuthorizationService": "authorization",
    "authorization_service": "authorization",
    "FXService": "fx",
    "fx_service": "fx",
    "IdempotencyService": "idempotency",
    "idempotency_service": "idempotency",
    "OrgContextService": "org_context",
    "org_context_service": "org_context",
    "OutboxPublisher": "outbox_publisher",
    "outbox_publisher": "outbox_publisher",
    "StaleClaimError": "outbox_publisher",
    "OutboxBalanceReconciler": "outbox_reconciler",
    "SequenceService": "sequence",
    "sequence_service": "sequence",
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    module_name = _NAME_TO_MODULE.get(name)
    if not module_name:
        raise AttributeError(name)
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)
