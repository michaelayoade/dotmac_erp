from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.person import PersonStatus
from app.schemas.application_lifecycle import (
    ApplicationLifecycleRequest,
    ApplicationLifecycleApplyRequest,
    ApplicationLifecyclePlanRequest,
    ApplicationLifecycleTarget,
    DesiredApplicationState,
    ExternalSubject,
)
from app.services.application_lifecycle import (
    ApplicationAccessLifecycle,
    ManagedApplicationLifecycle,
)
from app.services.oidc_runtime import OIDCProviderConfig, clear, install
from tests.conftest import DEFAULT_TEST_ORG_ID


def _target(person_id, *, desired_state: DesiredApplicationState):
    return ApplicationLifecycleTarget(
        organization_id=DEFAULT_TEST_ORG_ID,
        person_id=person_id,
        desired_state=desired_state,
        external_subject=ExternalSubject(
            provider_binding="customer-workforce",
            issuer="https://idp.example.test/realms/customer",
            subject="stable-subject-123",
        ),
    )


@pytest.fixture(autouse=True)
def _isolated_oidc_runtime():
    clear()
    yield
    clear()


def _install_matching_provider() -> None:
    install(
        OIDCProviderConfig(
            provider_binding="customer-workforce",
            issuer="https://idp.example.test/realms/customer",
            client_id="erp-client",
            client_secret="held-test-material",
            redirect_uri="https://erp.example.test/auth/oidc/callback",
            discovery_url=None,
            timeout_seconds=10.0,
            ceremony_ttl_seconds=600,
            clock_skew_seconds=60,
        )
    )


def test_request_rejects_authorization_and_profile_fields() -> None:
    base = {
        "target": {
            "organization_id": str(DEFAULT_TEST_ORG_ID),
            "person_id": str(uuid4()),
            "desired_state": "active",
            "external_subject": {
                "provider_binding": "customer-workforce",
                "issuer": "https://idp.example.test/realms/customer",
                "subject": "stable-subject-123",
            },
        }
    }

    for forbidden in (
        "email",
        "name",
        "roles",
        "groups",
        "scopes",
        "claims",
        "password",
        "employment",
        "enrolment",
    ):
        payload = {"target": {**base["target"], forbidden: "not-authorized"}}
        with pytest.raises(ValidationError):
            ApplicationLifecycleRequest.model_validate(payload)


def test_plan_is_read_only_and_reports_current_identity_readiness(
    db_session, person, user_credential
) -> None:
    person.status = PersonStatus.inactive
    person.is_active = False
    user_credential.is_active = False
    db_session.commit()

    result = ManagedApplicationLifecycle(db_session).plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="activate-person-1",
            target=_target(person.id, desired_state=DesiredApplicationState.active),
        )
    )

    assert result.outcome == "succeeded"
    assert result.failure_code is None
    assert result.current_state == DesiredApplicationState.inactive
    assert result.desired_state == DesiredApplicationState.active
    assert result.actions == ("activate_local_account", "bind_external_subject")
    assert result.operation_ref is not None
    assert result.plan_digest is not None
    db_session.refresh(person)
    db_session.refresh(user_credential)
    assert person.status == PersonStatus.inactive
    assert person.is_active is False
    assert user_credential.is_active is False


def test_apply_refuses_before_mutation_when_provider_is_not_installed(
    db_session, person, user_credential
) -> None:
    person.status = PersonStatus.inactive
    person.is_active = False
    user_credential.is_active = False
    db_session.commit()

    owner = ManagedApplicationLifecycle(db_session)
    target = _target(person.id, desired_state=DesiredApplicationState.active)
    plan = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="activate-person-2",
            target=target,
        )
    )
    result = owner.apply(
        ApplicationLifecycleApplyRequest(
            operation_ref=plan.operation_ref,
            idempotency_key="activate-person-2",
            target=target,
            target_digest=plan.target_digest,
            expected_state_digest=plan.expected_state_digest,
            plan_digest=plan.plan_digest,
        )
    )

    assert result.outcome == "refused"
    assert result.failure_code == "external_identity_not_configured"
    assert result.changed is False
    db_session.refresh(person)
    db_session.refresh(user_credential)
    assert person.status == PersonStatus.inactive
    assert person.is_active is False
    assert user_credential.is_active is False


def test_apply_atomically_activates_access_and_exact_external_binding(
    db_session, person, user_credential
) -> None:
    person.status = PersonStatus.inactive
    person.is_active = False
    user_credential.is_active = False
    db_session.commit()
    _install_matching_provider()
    owner = ManagedApplicationLifecycle(db_session)
    target = _target(person.id, desired_state=DesiredApplicationState.active)
    plan = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="activate-with-external-binding",
            target=target,
        )
    )

    result = owner.apply(
        ApplicationLifecycleApplyRequest(
            operation_ref=plan.operation_ref,
            idempotency_key=plan.idempotency_key,
            target=target,
            target_digest=plan.target_digest,
            expected_state_digest=plan.expected_state_digest,
            plan_digest=plan.plan_digest,
        )
    )

    assert result.outcome == "succeeded"
    assert result.operation_state == "applied"
    assert result.external_identity_ready is True
    assert person.is_active is True
    assert user_credential.is_active is True


def test_observe_hides_a_person_from_another_organization(db_session, person) -> None:
    result = ManagedApplicationLifecycle(db_session).observe(uuid4(), uuid4())

    assert result.outcome == "refused"
    assert result.failure_code == "operation_not_found"
    assert result.current_state is None


def test_cancel_is_a_durable_idempotent_transition_for_a_planned_operation(
    db_session, person
) -> None:
    owner = ManagedApplicationLifecycle(db_session)
    plan = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="deactivate-person-1",
            target=_target(person.id, desired_state=DesiredApplicationState.inactive),
        )
    )
    result = owner.cancel(DEFAULT_TEST_ORG_ID, plan.operation_ref)

    replay = owner.cancel(DEFAULT_TEST_ORG_ID, plan.operation_ref)

    assert result.outcome == "succeeded"
    assert result.failure_code is None
    assert result.changed is False
    assert result.result_state_digest is not None
    assert replay == result


def test_plan_replay_returns_same_operation_and_collision_is_refused(
    db_session, person
) -> None:
    owner = ManagedApplicationLifecycle(db_session)
    first_target = _target(person.id, desired_state=DesiredApplicationState.active)
    first = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="stable-operation-key",
            target=first_target,
        )
    )
    replay = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="stable-operation-key",
            target=first_target,
        )
    )
    collision = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="stable-operation-key",
            target=_target(person.id, desired_state=DesiredApplicationState.inactive),
        )
    )

    assert replay.operation_ref == first.operation_ref
    assert replay.plan_digest == first.plan_digest
    assert collision.outcome == "refused"
    assert collision.failure_code == "idempotency_key_conflict"


def test_apply_rejects_changed_static_pins_before_mutation(db_session, person) -> None:
    owner = ManagedApplicationLifecycle(db_session)
    target = _target(person.id, desired_state=DesiredApplicationState.active)
    plan = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="static-pin-check",
            target=target,
        )
    )

    mismatch = owner.apply(
        ApplicationLifecycleApplyRequest(
            operation_ref=plan.operation_ref,
            idempotency_key="static-pin-check",
            target=target,
            target_digest=plan.target_digest,
            expected_state_digest=plan.expected_state_digest,
            plan_digest="sha256:" + "0" * 64,
        )
    )

    assert mismatch.outcome == "refused"
    assert mismatch.failure_code == "plan_mismatch"
    assert mismatch.changed is False


def test_apply_rejects_when_the_observed_person_state_changed_since_plan(
    db_session, person
) -> None:
    owner = ManagedApplicationLifecycle(db_session)
    target = _target(person.id, desired_state=DesiredApplicationState.inactive)
    plan = owner.plan(
        ApplicationLifecyclePlanRequest(
            idempotency_key="expected-state-pin",
            target=target,
        )
    )
    person.status = PersonStatus.inactive
    person.is_active = False
    db_session.flush()

    mismatch = owner.apply(
        ApplicationLifecycleApplyRequest(
            operation_ref=plan.operation_ref,
            idempotency_key="expected-state-pin",
            target=target,
            target_digest=plan.target_digest,
            expected_state_digest=plan.expected_state_digest,
            plan_digest=plan.plan_digest,
        )
    )

    assert mismatch.outcome == "refused"
    assert mismatch.failure_code == "expected_state_mismatch"
    assert mismatch.changed is False


def test_plan_refuses_an_authenticated_organization_mismatch(
    db_session, person
) -> None:
    request = ApplicationLifecyclePlanRequest(
        idempotency_key="wrong-org",
        target=_target(person.id, desired_state=DesiredApplicationState.active),
    )

    result = ManagedApplicationLifecycle(db_session).plan(
        request, organization_id=uuid4()
    )

    assert result.outcome == "refused"
    assert result.failure_code == "organization_scope_mismatch"
    assert result.operation_ref is None


def test_local_access_owner_deactivates_credentials_and_sessions(
    db_session, person, user_credential
) -> None:
    session = AuthSession(
        person_id=person.id,
        token_hash=f"managed-lifecycle-{uuid4().hex}",
        status=SessionStatus.active,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()

    result = ApplicationAccessLifecycle(db_session).deactivate(
        DEFAULT_TEST_ORG_ID, person.id
    )

    assert result.changed is True
    assert result.credentials_changed == 1
    assert result.sessions_revoked == 1
    assert person.status == PersonStatus.inactive
    assert person.is_active is False
    assert user_credential.is_active is False
    assert session.status == SessionStatus.revoked
    assert session.revoked_at is not None


def test_local_access_owner_flushes_but_does_not_commit(
    db_session, person, user_credential
) -> None:
    ApplicationAccessLifecycle(db_session).deactivate(DEFAULT_TEST_ORG_ID, person.id)
    db_session.rollback()
    db_session.refresh(person)
    db_session.refresh(user_credential)

    assert person.status == PersonStatus.active
    assert person.is_active is True
    assert user_credential.is_active is True


def test_local_access_owner_activation_is_idempotent(
    db_session, person, user_credential
) -> None:
    person.status = PersonStatus.inactive
    person.is_active = False
    user_credential.is_active = False
    db_session.commit()
    owner = ApplicationAccessLifecycle(db_session)

    first = owner.activate(DEFAULT_TEST_ORG_ID, person.id)
    second = owner.activate(DEFAULT_TEST_ORG_ID, person.id)

    assert first.changed is True
    assert second.changed is False
    assert person.status == PersonStatus.active
    assert person.is_active is True
    assert user_credential.is_active is True
