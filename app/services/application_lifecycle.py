"""One ERP owner for local account state and its managed-service port."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from uuid import UUID
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from app.models.auth import AuthProvider, UserCredential
from app.models.application_lifecycle import ApplicationLifecycleOperation
from app.models.person import Person, PersonStatus
from app.schemas.application_lifecycle import (
    ApplicationLifecycleApplyRequest,
    ApplicationLifecycleFailure,
    ApplicationLifecycleOperationState,
    ApplicationLifecycleOutcome,
    ApplicationLifecyclePlanRequest,
    ApplicationLifecycleResult,
    ApplicationLifecycleState,
    ApplicationLifecycleTarget,
    DesiredApplicationState,
)
from app.services.auth_flow import revoke_sessions_for_person
from app.services.external_identity import (
    ERPExternalIdentityAuthority,
    ExternalIdentityConflict,
)
from app.services.oidc_runtime import configuration_matches


@dataclass(frozen=True, slots=True)
class ApplicationAccessChange:
    person: Person
    changed: bool
    credentials_changed: int
    sessions_revoked: int


class ApplicationAccessLifecycle:
    """Canonical writer for Person login eligibility and local credentials."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _person(
        self, organization_id: UUID, person_id: UUID, *, lock: bool = False
    ) -> Person | None:
        query = select(Person).where(
            Person.organization_id == organization_id,
            Person.id == person_id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.db.scalar(query)

    def activate(
        self, organization_id: UUID, person_id: UUID
    ) -> ApplicationAccessChange:
        person = self._person(organization_id, person_id, lock=True)
        if person is None:
            raise LookupError("person_not_found")

        changed = person.status != PersonStatus.active or not person.is_active
        person.status = PersonStatus.active
        person.is_active = True

        credentials = self.db.scalars(
            select(UserCredential).where(
                UserCredential.person_id == person.id,
                UserCredential.provider == AuthProvider.local,
            )
        ).all()
        credentials_changed = 0
        for credential in credentials:
            credential_changed = (
                not credential.is_active
                or credential.failed_login_attempts != 0
                or credential.locked_until is not None
            )
            if credential_changed:
                credentials_changed += 1
            credential.is_active = True
            credential.failed_login_attempts = 0
            credential.locked_until = None

        self.db.flush()
        return ApplicationAccessChange(
            person=person,
            changed=changed or credentials_changed > 0,
            credentials_changed=credentials_changed,
            sessions_revoked=0,
        )

    def deactivate(
        self, organization_id: UUID, person_id: UUID
    ) -> ApplicationAccessChange:
        person = self._person(organization_id, person_id, lock=True)
        if person is None:
            raise LookupError("person_not_found")

        changed = person.status != PersonStatus.inactive or person.is_active
        person.status = PersonStatus.inactive
        person.is_active = False
        person.updated_at = datetime.now(UTC)

        credentials = self.db.scalars(
            select(UserCredential).where(
                UserCredential.person_id == person.id,
                UserCredential.is_active.is_(True),
            )
        ).all()
        now = datetime.now(UTC)
        for credential in credentials:
            credential.is_active = False
            credential.locked_until = now
            credential.must_change_password = True
            credential.failed_login_attempts = 0

        sessions_revoked = revoke_sessions_for_person(self.db, str(person.id))

        self.db.flush()
        return ApplicationAccessChange(
            person=person,
            changed=changed or bool(credentials) or sessions_revoked > 0,
            credentials_changed=len(credentials),
            sessions_revoked=sessions_revoked,
        )


class ManagedApplicationLifecycle:
    """Provider-neutral lifecycle port over the ERP-owned account decision."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def _digest(cls, value: object) -> str:
        return f"sha256:{hashlib.sha256(cls._canonical(value)).hexdigest()}"

    @staticmethod
    def _target_document(target: ApplicationLifecycleTarget) -> dict[str, object]:
        return cast(dict[str, object], target.model_dump(mode="json"))

    def _person(
        self, target: ApplicationLifecycleTarget, *, lock: bool = False
    ) -> Person | None:
        query = select(Person).where(
            Person.organization_id == target.organization_id,
            Person.id == target.person_id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return cast(Person | None, self.db.scalar(query))

    @staticmethod
    def _person_state(person: Person) -> DesiredApplicationState:
        if person.status == PersonStatus.active and person.is_active:
            return DesiredApplicationState.active
        return DesiredApplicationState.inactive

    @staticmethod
    def _actions(
        current: DesiredApplicationState,
        desired: DesiredApplicationState,
    ) -> tuple[str, ...]:
        if current == desired:
            return ()
        if desired == DesiredApplicationState.active:
            return ("activate_local_account", "bind_external_subject")
        return (
            "deactivate_local_account",
            "disable_external_subject",
            "revoke_local_sessions",
        )

    def _result(
        self, operation: ApplicationLifecycleOperation
    ) -> ApplicationLifecycleResult:
        return ApplicationLifecycleResult(
            outcome=ApplicationLifecycleOutcome(operation.outcome),
            failure_code=(
                ApplicationLifecycleFailure(operation.failure_code)
                if operation.failure_code is not None
                else None
            ),
            operation_ref=operation.operation_id,
            idempotency_key=operation.idempotency_key,
            operation_state=ApplicationLifecycleOperationState(
                operation.operation_state
            ),
            target=ApplicationLifecycleTarget.model_validate(operation.target),
            target_digest=operation.target_digest,
            expected_state=ApplicationLifecycleState.model_validate(
                operation.expected_state
            ),
            expected_state_digest=operation.expected_state_digest,
            plan_digest=operation.plan_digest,
            current_state=DesiredApplicationState(operation.current_state),
            desired_state=DesiredApplicationState(operation.desired_state),
            actions=tuple(operation.actions),
            changed=operation.changed,
            external_identity_ready=(
                bool(operation.result_state.get("external_identity_ready"))
                if operation.result_state is not None
                else bool(operation.expected_state.get("external_identity_ready"))
            ),
            result_state_digest=operation.result_state_digest,
            result_state=(
                ApplicationLifecycleState.model_validate(operation.result_state)
                if operation.result_state is not None
                else None
            ),
        )

    def _by_reference(
        self, organization_id: UUID, operation_ref: UUID, *, lock: bool = False
    ) -> ApplicationLifecycleOperation | None:
        query = select(ApplicationLifecycleOperation).where(
            ApplicationLifecycleOperation.organization_id == organization_id,
            ApplicationLifecycleOperation.operation_id == operation_ref,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return cast(ApplicationLifecycleOperation | None, self.db.scalar(query))

    def _external_identity_ready(
        self, target: ApplicationLifecycleTarget, *, lock: bool = False
    ) -> bool:
        subject = target.external_subject
        binding = ERPExternalIdentityAuthority(self.db).find_exact(
            organization_id=target.organization_id,
            person_id=target.person_id,
            provider_binding=subject.provider_binding,
            issuer=subject.issuer,
            subject=subject.subject,
            lock=lock,
        )
        return bool(
            binding is not None
            and binding.is_active
            and binding.disabled_at is None
            and configuration_matches(
                provider_binding=subject.provider_binding,
                issuer=subject.issuer,
            )
        )

    def plan(
        self,
        request: ApplicationLifecyclePlanRequest,
        *,
        organization_id: UUID | None = None,
    ) -> ApplicationLifecycleResult:
        target = request.target
        if organization_id is not None and target.organization_id != organization_id:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.organization_scope_mismatch,
                current_state=None,
                desired_state=target.desired_state,
            )
        target_document = self._target_document(target)
        target_digest = self._digest(target_document)
        existing = self.db.scalar(
            select(ApplicationLifecycleOperation).where(
                ApplicationLifecycleOperation.organization_id == target.organization_id,
                ApplicationLifecycleOperation.idempotency_key
                == request.idempotency_key,
            )
        )
        if existing is not None:
            if existing.target_digest != target_digest:
                return self._mismatch(
                    existing, ApplicationLifecycleFailure.idempotency_key_conflict
                )
            return self._result(existing)

        person = self._person(target)
        if person is None:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.person_not_found,
                current_state=None,
                desired_state=target.desired_state,
            )
        current = self._person_state(person)
        actions = self._actions(current, target.desired_state)
        expected_state = {
            "application_state": current.value,
            "external_identity_ready": self._external_identity_ready(target),
        }
        expected_state_digest = self._digest(expected_state)
        operation_id = uuid4()
        plan_document = {
            "schema": "dotmac-erp.application-lifecycle-plan/v1",
            "operation_ref": str(operation_id),
            "idempotency_key": request.idempotency_key,
            "target_digest": target_digest,
            "expected_state_digest": expected_state_digest,
            "actions": list(actions),
        }
        operation = ApplicationLifecycleOperation(
            operation_id=operation_id,
            organization_id=target.organization_id,
            idempotency_key=request.idempotency_key,
            person_id=target.person_id,
            desired_state=target.desired_state.value,
            provider_binding=target.external_subject.provider_binding,
            issuer=target.external_subject.issuer,
            subject=target.external_subject.subject,
            target=target_document,
            target_digest=target_digest,
            expected_state=expected_state,
            expected_state_digest=expected_state_digest,
            plan_digest=self._digest(plan_document),
            current_state=current.value,
            actions=list(actions),
            operation_state="planned",
            outcome="succeeded",
            failure_code=None,
        )
        try:
            with self.db.begin_nested():
                self.db.add(operation)
                self.db.flush()
        except IntegrityError:
            raced = self.db.scalar(
                select(ApplicationLifecycleOperation).where(
                    ApplicationLifecycleOperation.organization_id
                    == target.organization_id,
                    ApplicationLifecycleOperation.idempotency_key
                    == request.idempotency_key,
                )
            )
            if raced is None:
                raise
            if raced.target_digest != target_digest:
                return self._mismatch(
                    raced, ApplicationLifecycleFailure.idempotency_key_conflict
                )
            return self._result(raced)
        return self._result(operation)

    def apply(
        self,
        request: ApplicationLifecycleApplyRequest,
        *,
        organization_id: UUID | None = None,
    ) -> ApplicationLifecycleResult:
        target = request.target
        if organization_id is not None and target.organization_id != organization_id:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.organization_scope_mismatch,
                current_state=None,
                desired_state=target.desired_state,
            )
        operation = self._by_reference(
            target.organization_id, request.operation_ref, lock=True
        )
        if operation is None:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.operation_not_found,
                current_state=None,
                desired_state=target.desired_state,
            )
        if operation.idempotency_key != request.idempotency_key:
            return self._mismatch(
                operation, ApplicationLifecycleFailure.idempotency_key_conflict
            )
        if (
            operation.target_digest != request.target_digest
            or operation.target_digest != self._digest(self._target_document(target))
        ):
            return self._mismatch(
                operation, ApplicationLifecycleFailure.target_mismatch
            )
        if (
            operation.plan_digest != request.plan_digest
            or operation.expected_state_digest != request.expected_state_digest
        ):
            return self._mismatch(operation, ApplicationLifecycleFailure.plan_mismatch)
        if operation.operation_state == "cancelled":
            return self._mismatch(
                operation, ApplicationLifecycleFailure.operation_cancelled
            )
        if operation.operation_state == "applied":
            return self._result(operation)

        identity = ERPExternalIdentityAuthority(self.db)
        subject = target.external_subject
        try:
            locked_identity = identity.lock_for_binding_change(
                organization_id=target.organization_id,
                person_id=target.person_id,
                provider_binding=subject.provider_binding,
                issuer=subject.issuer,
                subject=subject.subject,
            )
            locked_binding = identity.binding_after_lock(locked_identity)
        except ExternalIdentityConflict:
            return self._mismatch(
                operation, ApplicationLifecycleFailure.external_subject_conflict
            )
        identity_ready = self._external_identity_ready(target)
        person = self._person(target, lock=True)
        if person is None:
            return self._mismatch(
                operation, ApplicationLifecycleFailure.person_not_found
            )
        observed_state = {
            "application_state": self._person_state(person).value,
            "external_identity_ready": identity_ready,
        }
        if self._digest(observed_state) != operation.expected_state_digest:
            return self._mismatch(
                operation, ApplicationLifecycleFailure.expected_state_mismatch
            )

        access = ApplicationAccessLifecycle(self.db)
        changed = False
        try:
            if target.desired_state == DesiredApplicationState.active:
                if not configuration_matches(
                    provider_binding=subject.provider_binding,
                    issuer=subject.issuer,
                ):
                    return self._mismatch(
                        operation,
                        ApplicationLifecycleFailure.external_identity_not_configured,
                    )
                binding_change = identity.bind_after_lock(locked_identity)
                access_change = access.activate(
                    target.organization_id, target.person_id
                )
                changed = access_change.changed or binding_change.changed
            else:
                deactivation_binding_change = (
                    identity.disable_after_lock(locked_binding)
                    if locked_binding is not None
                    else None
                )
                access_change = access.deactivate(
                    target.organization_id, target.person_id
                )
                changed = access_change.changed or bool(
                    deactivation_binding_change and deactivation_binding_change.changed
                )
        except ExternalIdentityConflict:
            return self._mismatch(
                operation, ApplicationLifecycleFailure.external_subject_conflict
            )

        result_state: dict[str, object] = {
            "application_state": target.desired_state.value,
            "external_identity_ready": (
                target.desired_state == DesiredApplicationState.active
                and self._external_identity_ready(target)
            ),
        }
        operation.operation_state = ApplicationLifecycleOperationState.applied.value
        operation.outcome = ApplicationLifecycleOutcome.succeeded.value
        operation.failure_code = None
        operation.changed = changed
        operation.result_state = result_state
        operation.result_state_digest = self._digest(result_state)
        operation.applied_at = datetime.now(UTC)
        self.db.flush()
        return self._result(operation)

    def _mismatch(
        self,
        operation: ApplicationLifecycleOperation,
        failure_code: ApplicationLifecycleFailure,
    ) -> ApplicationLifecycleResult:
        return cast(
            ApplicationLifecycleResult,
            self._result(operation).model_copy(
                update={"outcome": "refused", "failure_code": failure_code}
            ),
        )

    def observe(
        self, organization_id: UUID, operation_ref: UUID
    ) -> ApplicationLifecycleResult:
        operation = self._by_reference(organization_id, operation_ref)
        if operation is None:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.operation_not_found,
                current_state=None,
                desired_state=None,
            )
        return self._result(operation)

    def cancel(
        self, organization_id: UUID, operation_ref: UUID
    ) -> ApplicationLifecycleResult:
        operation = self._by_reference(organization_id, operation_ref, lock=True)
        if operation is None:
            return ApplicationLifecycleResult(
                outcome=ApplicationLifecycleOutcome.refused,
                failure_code=ApplicationLifecycleFailure.operation_not_found,
                current_state=None,
                desired_state=None,
            )
        if operation.operation_state == "applied":
            return self._mismatch(
                operation, ApplicationLifecycleFailure.compensation_not_supported
            )
        if operation.operation_state == "cancelled":
            return self._result(operation)

        result_state = dict(operation.expected_state)
        operation.operation_state = "cancelled"
        operation.outcome = ApplicationLifecycleOutcome.succeeded.value
        operation.failure_code = None
        operation.changed = False
        operation.result_state = result_state
        operation.result_state_digest = self._digest(result_state)
        operation.cancelled_at = datetime.now(UTC)
        self.db.flush()
        return self._result(operation)


__all__ = [
    "ApplicationAccessChange",
    "ApplicationAccessLifecycle",
    "ManagedApplicationLifecycle",
]
