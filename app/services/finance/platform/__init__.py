"""
Platform Services for IFRS Accounting System.

Foundational infrastructure services that all accounting modules depend upon.
"""

from app.services.finance.platform.approval_workflow import (
    ApprovalWorkflowService,
    approval_workflow_service,
)
from app.services.finance.platform.audit_log import AuditLogService, audit_log_service
from app.services.finance.platform.authorization import (
    AuthorizationService,
    authorization_service,
)
from app.services.finance.platform.fx import FXService, fx_service
from app.services.finance.platform.idempotency import (
    IdempotencyService,
    idempotency_service,
)
from app.services.finance.platform.org_context import (
    OrgContextService,
    org_context_service,
)
from app.services.finance.platform.outbox_publisher import (
    OutboxPublisher,
    outbox_publisher,
)
from app.services.finance.platform.sequence import SequenceService, sequence_service

__all__ = [
    "IdempotencyService",
    "idempotency_service",
    "SequenceService",
    "sequence_service",
    "FXService",
    "fx_service",
    "OutboxPublisher",
    "outbox_publisher",
    "AuditLogService",
    "audit_log_service",
    "AuthorizationService",
    "authorization_service",
    "ApprovalWorkflowService",
    "approval_workflow_service",
    "OrgContextService",
    "org_context_service",
]
