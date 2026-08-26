"""Provider-neutral contract for ERP application-account lifecycle."""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DesiredApplicationState(str, Enum):
    active = "active"
    inactive = "inactive"


class ApplicationLifecycleOutcome(str, Enum):
    blocked = "blocked"
    refused = "refused"
    succeeded = "succeeded"


class ApplicationLifecycleFailure(str, Enum):
    compensation_not_supported = "compensation_not_supported"
    expected_state_mismatch = "expected_state_mismatch"
    external_identity_not_configured = "external_identity_not_configured"
    external_subject_conflict = "external_subject_conflict"
    idempotency_key_conflict = "idempotency_key_conflict"
    operation_cancelled = "operation_cancelled"
    operation_not_found = "operation_not_found"
    organization_scope_mismatch = "organization_scope_mismatch"
    person_not_found = "person_not_found"
    plan_mismatch = "plan_mismatch"
    target_mismatch = "target_mismatch"


class ApplicationLifecycleOperationState(str, Enum):
    applied = "applied"
    cancelled = "cancelled"
    planned = "planned"


class ApplicationLifecycleState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_state: DesiredApplicationState
    external_identity_ready: bool


class ExternalSubject(BaseModel):
    """An exact identity tuple, never a source of ERP authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_binding: str = Field(min_length=1, max_length=80)
    issuer: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^https://[^\s]+$",
    )
    subject: str = Field(min_length=1, max_length=255)

    @field_validator("provider_binding", "issuer", "subject")
    @classmethod
    def reject_outer_whitespace(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("identity tuple values must be non-blank and trimmed")
        return value


class ApplicationLifecycleTarget(BaseModel):
    """The whole writable target. No profile or authorization fields exist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    person_id: UUID
    desired_state: DesiredApplicationState
    external_subject: ExternalSubject


class ApplicationLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: ApplicationLifecycleTarget


class ApplicationLifecyclePlanRequest(ApplicationLifecycleRequest):
    idempotency_key: str = Field(min_length=1, max_length=120)

    @field_validator("idempotency_key")
    @classmethod
    def require_trimmed_idempotency_key(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("idempotency_key must be trimmed")
        return value


class ApplicationLifecycleApplyRequest(ApplicationLifecyclePlanRequest):
    operation_ref: UUID
    target_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ApplicationLifecycleReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_ref: UUID


class ApplicationLifecycleResult(BaseModel):
    """Stable product outcome; HTTP details do not leak into the owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ApplicationLifecycleOutcome
    failure_code: ApplicationLifecycleFailure | None = None
    operation_ref: UUID | None = None
    idempotency_key: str | None = None
    operation_state: ApplicationLifecycleOperationState | None = None
    target: ApplicationLifecycleTarget | None = None
    target_digest: str | None = None
    expected_state: ApplicationLifecycleState | None = None
    expected_state_digest: str | None = None
    plan_digest: str | None = None
    current_state: DesiredApplicationState | None
    desired_state: DesiredApplicationState | None
    actions: tuple[str, ...] = ()
    changed: bool = False
    external_identity_ready: bool = False
    result_state_digest: str | None = None
    result_state: ApplicationLifecycleState | None = None


__all__ = [
    "ApplicationLifecycleFailure",
    "ApplicationLifecycleOutcome",
    "ApplicationLifecycleOperationState",
    "ApplicationLifecycleRequest",
    "ApplicationLifecycleApplyRequest",
    "ApplicationLifecyclePlanRequest",
    "ApplicationLifecycleReferenceRequest",
    "ApplicationLifecycleResult",
    "ApplicationLifecycleState",
    "ApplicationLifecycleTarget",
    "DesiredApplicationState",
    "ExternalSubject",
]
