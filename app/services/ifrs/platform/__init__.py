"""
Platform Services for IFRS Accounting System.

Foundational infrastructure services that all accounting modules depend upon.
"""

from app.services.ifrs.platform.idempotency import IdempotencyService, idempotency_service
from app.services.ifrs.platform.sequence import SequenceService, sequence_service
from app.services.ifrs.platform.fx import FXService, fx_service
from app.services.ifrs.platform.feature_flag import FeatureFlagService, feature_flag_service
from app.services.ifrs.platform.outbox_publisher import OutboxPublisher, outbox_publisher
from app.services.ifrs.platform.audit_log import AuditLogService, audit_log_service
from app.services.ifrs.platform.authorization import AuthorizationService, authorization_service
from app.services.ifrs.platform.approval_workflow import (
    ApprovalWorkflowService,
    approval_workflow_service,
)
from app.services.ifrs.platform.org_context import (
    OrgContextService,
    org_context_service,
)

__all__ = [
    "IdempotencyService",
    "idempotency_service",
    "SequenceService",
    "sequence_service",
    "FXService",
    "fx_service",
    "FeatureFlagService",
    "feature_flag_service",
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
