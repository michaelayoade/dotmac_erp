from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.auth import FederatedIdentity, SessionStatus
from app.models.auth import Session as AuthSession
from app.models.person import Person
from app.services.external_identity import (
    ERPExternalIdentityAuthority,
    ExternalIdentityConflict,
)


PROVIDER_BINDING = "primary"
ISSUER = "https://idp.example.test/realms/erp"


def _bind(db_session, person: Person, *, subject: str) -> FederatedIdentity:
    return (
        ERPExternalIdentityAuthority(db_session)
        .bind(
            organization_id=person.organization_id,
            person_id=person.id,
            provider_binding=PROVIDER_BINDING,
            issuer=ISSUER,
            subject=subject,
        )
        .binding
    )


def _session(
    db_session,
    person: Person,
    binding: FederatedIdentity,
) -> AuthSession:
    session = AuthSession(
        person_id=person.id,
        external_identity_binding_id=binding.id,
        token_hash=f"external-session-{uuid4().hex}",
        status=SessionStatus.active,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(session)
    db_session.flush()
    return session


def test_unbound_subject_is_refused_without_jit_or_email_linking(
    db_session, person
) -> None:
    before = db_session.scalar(select(func.count()).select_from(Person))

    finalized = ERPExternalIdentityAuthority(db_session).finalize_login(
        organization_id=person.organization_id,
        provider_binding=PROVIDER_BINDING,
        issuer=ISSUER,
        subject="unbound-subject",
    )

    assert finalized is None
    assert db_session.scalar(select(func.count()).select_from(Person)) == before


def test_finalizer_returns_only_the_exact_active_issuer_subject_binding(
    db_session, person
) -> None:
    binding = _bind(db_session, person, subject="subject-1")

    assert (
        ERPExternalIdentityAuthority(db_session).finalize_login(
            organization_id=person.organization_id,
            provider_binding=PROVIDER_BINDING,
            issuer=ISSUER,
            subject="another-subject",
        )
        is None
    )
    finalized = ERPExternalIdentityAuthority(db_session).finalize_login(
        organization_id=person.organization_id,
        provider_binding=PROVIDER_BINDING,
        issuer=ISSUER,
        subject="subject-1",
    )

    assert finalized is not None
    assert finalized.binding.id == binding.id
    assert finalized.person.id == person.id
    assert binding.last_authenticated_at is not None


def test_disable_revokes_only_sessions_issued_by_that_binding(
    db_session, person
) -> None:
    other = Person(
        organization_id=person.organization_id,
        first_name="Other",
        last_name="Member",
        email=f"other-{uuid4()}@example.test",
        is_active=True,
    )
    db_session.add(other)
    db_session.flush()
    first_binding = _bind(db_session, person, subject="subject-first")
    second_binding = _bind(db_session, other, subject="subject-second")
    first_session = _session(db_session, person, first_binding)
    second_session = _session(db_session, other, second_binding)

    change = ERPExternalIdentityAuthority(db_session).disable(
        organization_id=person.organization_id,
        binding_id=first_binding.id,
    )

    assert change.sessions_revoked == 1
    assert first_binding.is_active is False
    assert first_session.status == SessionStatus.revoked
    assert first_session.revoked_at is not None
    assert second_binding.is_active is True
    assert second_session.status == SessionStatus.active
    assert second_session.revoked_at is None


def test_disable_and_selective_revocation_rollback_together(db_session, person) -> None:
    binding = _bind(db_session, person, subject="subject-rollback")
    session = _session(db_session, person, binding)
    db_session.commit()

    ERPExternalIdentityAuthority(db_session).disable(
        organization_id=person.organization_id,
        binding_id=binding.id,
    )
    db_session.rollback()
    db_session.refresh(binding)
    db_session.refresh(session)

    assert binding.is_active is True
    assert session.status == SessionStatus.active
    assert session.revoked_at is None


def test_finalizer_and_disable_take_the_same_binding_row_lock() -> None:
    source = Path(inspect.getfile(ERPExternalIdentityAuthority)).read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"finalize_login", "disable"}
    }

    for method in methods.values():
        calls = [
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "with_for_update" in calls
        assert "commit" not in calls


def test_bind_uses_binding_then_person_lock_order_and_a_conflict_savepoint() -> None:
    source = Path(inspect.getfile(ERPExternalIdentityAuthority)).read_text(
        encoding="utf-8"
    )
    bind_start = source.index("    def bind(")
    bind_end = source.index("    def finalize_login(", bind_start)
    bind_source = source[bind_start:bind_end]

    assert bind_source.index("locked = self.lock_for_binding_change(") < (
        bind_source.index("return self.bind_after_lock(locked)")
    )
    bind_after_lock = bind_source.split("    def bind_after_lock(", 1)[1]
    assert "self._lock_candidates(" not in bind_after_lock
    assert "person = self.db.scalar" in bind_after_lock
    assert "with self.db.begin_nested()" in bind_source
    assert "except IntegrityError as exc" in bind_source
    assert "raise ExternalIdentityConflict" in bind_source
    conflict_handler = bind_source.split("except IntegrityError as exc:", 1)[1]
    assert ".scalar(" not in conflict_handler
    assert "with_for_update" not in conflict_handler

    lifecycle = (
        Path(inspect.getfile(ERPExternalIdentityAuthority)).with_name(
            "application_lifecycle.py"
        )
    ).read_text(encoding="utf-8")
    apply_start = lifecycle.index("    def apply(")
    apply_end = lifecycle.index("    def _mismatch(", apply_start)
    apply_source = lifecycle[apply_start:apply_end]
    assert apply_source.index("locked_identity = identity.lock_for_binding_change(") < (
        apply_source.index("person = self._person(target, lock=True)")
    )
    assert apply_source.index("binding_change = identity.bind_after_lock(") < (
        apply_source.index("access_change = access.activate(")
    )
    assert "identity.bind(" not in apply_source
    person_lock = apply_source.index("person = self._person(target, lock=True)")
    assert "find_exact(" not in apply_source[person_lock:]
    assert "identity.disable(" not in apply_source[person_lock:]


def test_existing_subject_cannot_be_rebound_to_another_person(
    db_session, person
) -> None:
    binding = _bind(db_session, person, subject="immutable-subject")
    other = Person(
        organization_id=person.organization_id,
        first_name="Other",
        last_name="Identity",
        email=f"identity-{uuid4()}@example.test",
        is_active=True,
    )
    db_session.add(other)
    db_session.flush()

    with pytest.raises(ExternalIdentityConflict):
        ERPExternalIdentityAuthority(db_session).bind(
            organization_id=person.organization_id,
            person_id=other.id,
            provider_binding=PROVIDER_BINDING,
            issuer=ISSUER,
            subject=binding.subject,
        )
