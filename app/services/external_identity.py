"""ERP's sole writer for external-subject bindings and their sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.auth import FederatedIdentity, SessionStatus
from app.models.auth import Session as AuthSession
from app.models.person import Person, PersonStatus
from app.services.auth_dependencies import invalidate_session_cache


@dataclass(frozen=True, slots=True)
class ExternalIdentityBindingChange:
    binding: FederatedIdentity
    changed: bool
    sessions_revoked: int = 0


@dataclass(frozen=True, slots=True)
class FinalizedExternalIdentity:
    binding: FederatedIdentity
    person: Person


class ExternalIdentityConflict(RuntimeError):
    """A local identity tuple was concurrently or previously bound elsewhere."""


@dataclass(frozen=True, slots=True, repr=False)
class _LockedExternalIdentityCandidates:
    organization_id: UUID
    person_id: UUID
    provider_binding: str
    issuer: str
    subject: str
    exact: FederatedIdentity | None
    existing: FederatedIdentity | None


class ERPExternalIdentityAuthority:
    """Canonical ERP Person binding/finalization authority.

    Login only finalizes an already provisioned exact tuple. It never creates a
    Person or binding, and it never considers an email or provider role.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_candidates(
        self,
        *,
        organization_id: UUID,
        person_id: UUID,
        provider_binding: str,
        issuer: str,
        subject: str,
    ) -> tuple[FederatedIdentity | None, FederatedIdentity | None]:
        # One UUID-ordered statement gives every binding mutation the same
        # candidate order, including a pre-existing cross-person conflict.
        candidates = self.db.scalars(
            select(FederatedIdentity)
            .where(
                FederatedIdentity.organization_id == organization_id,
                FederatedIdentity.provider_binding == provider_binding,
                FederatedIdentity.issuer == issuer,
                or_(
                    FederatedIdentity.subject == subject,
                    FederatedIdentity.person_id == person_id,
                ),
            )
            .order_by(FederatedIdentity.id)
            .with_for_update()
        ).all()
        exact = next((row for row in candidates if row.subject == subject), None)
        existing = next((row for row in candidates if row.person_id == person_id), None)
        return exact, existing

    def lock_for_binding_change(
        self,
        *,
        organization_id: UUID,
        person_id: UUID,
        provider_binding: str,
        issuer: str,
        subject: str,
    ) -> _LockedExternalIdentityCandidates:
        """Acquire possible bindings before a lifecycle caller locks Person."""
        exact, existing = self._lock_candidates(
            organization_id=organization_id,
            person_id=person_id,
            provider_binding=provider_binding,
            issuer=issuer,
            subject=subject,
        )
        return _LockedExternalIdentityCandidates(
            organization_id=organization_id,
            person_id=person_id,
            provider_binding=provider_binding,
            issuer=issuer,
            subject=subject,
            exact=exact,
            existing=existing,
        )

    def bind(
        self,
        *,
        organization_id: UUID,
        person_id: UUID,
        provider_binding: str,
        issuer: str,
        subject: str,
    ) -> ExternalIdentityBindingChange:
        # Every path that can see a binding locks it before Person. Finalize
        # uses the same order, preventing reactivation/login lock inversion.
        locked = self.lock_for_binding_change(
            organization_id=organization_id,
            person_id=person_id,
            provider_binding=provider_binding,
            issuer=issuer,
            subject=subject,
        )
        return self.bind_after_lock(locked)

    def bind_after_lock(
        self,
        locked: _LockedExternalIdentityCandidates,
    ) -> ExternalIdentityBindingChange:
        """Bind using candidate rows already locked in this transaction.

        Managed lifecycle uses this after checking expected Person state. It
        must not re-read a binding while Person is held, because finalization
        always takes the binding lock before the Person lock.
        """
        organization_id = locked.organization_id
        person_id = locked.person_id
        provider_binding = locked.provider_binding
        issuer = locked.issuer
        subject = locked.subject
        binding = self.binding_after_lock(locked)

        person = self.db.scalar(
            select(Person)
            .where(
                Person.organization_id == organization_id,
                Person.id == person_id,
            )
            .with_for_update()
        )
        if person is None:
            raise LookupError("person_not_found")

        changed = binding is None or not binding.is_active
        if binding is None:
            binding = FederatedIdentity(
                organization_id=organization_id,
                person_id=person_id,
                provider_binding=provider_binding,
                issuer=issuer,
                subject=subject,
                is_active=True,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(binding)
                    self.db.flush()
            except IntegrityError as exc:
                # A uniqueness loser must not abort the tenant transaction.
                # Do not inspect the violated constraint or re-lock a binding
                # while Person is held: either would leak collision detail or
                # restore the Person→binding inversion. A caller may retry and
                # re-read in a fresh transaction.
                raise ExternalIdentityConflict("external_subject_conflict") from exc
        else:
            binding.is_active = True
            binding.disabled_at = None
        self.db.flush()
        return ExternalIdentityBindingChange(binding=binding, changed=changed)

    @staticmethod
    def binding_after_lock(
        locked: _LockedExternalIdentityCandidates,
    ) -> FederatedIdentity | None:
        """Return the exact locked binding without issuing another query."""
        exact = locked.exact
        existing = locked.existing
        if exact is not None and exact.person_id != locked.person_id:
            raise ExternalIdentityConflict("external_subject_conflict")
        if existing is not None and existing.subject != locked.subject:
            raise ExternalIdentityConflict("external_subject_conflict")
        return exact or existing

    def finalize_login(
        self,
        *,
        organization_id: UUID,
        provider_binding: str,
        issuer: str,
        subject: str,
    ) -> FinalizedExternalIdentity | None:
        binding = self.db.scalar(
            select(FederatedIdentity)
            .where(
                FederatedIdentity.organization_id == organization_id,
                FederatedIdentity.provider_binding == provider_binding,
                FederatedIdentity.issuer == issuer,
                FederatedIdentity.subject == subject,
                FederatedIdentity.is_active.is_(True),
                FederatedIdentity.disabled_at.is_(None),
            )
            .with_for_update()
        )
        if binding is None:
            return None
        person = self.db.scalar(
            select(Person)
            .where(
                Person.organization_id == organization_id,
                Person.id == binding.person_id,
                Person.is_active.is_(True),
                Person.status == PersonStatus.active,
            )
            .with_for_update()
        )
        if person is None:
            return None
        binding.last_authenticated_at = datetime.now(UTC)
        self.db.flush()
        return FinalizedExternalIdentity(binding=binding, person=person)

    def disable(
        self, *, organization_id: UUID, binding_id: UUID
    ) -> ExternalIdentityBindingChange:
        binding = self.db.scalar(
            select(FederatedIdentity)
            .where(
                FederatedIdentity.organization_id == organization_id,
                FederatedIdentity.id == binding_id,
            )
            .with_for_update()
        )
        if binding is None:
            raise LookupError("external_identity_not_found")
        return self.disable_after_lock(binding)

    def disable_after_lock(
        self, binding: FederatedIdentity
    ) -> ExternalIdentityBindingChange:
        """Disable a binding already locked by this transaction."""
        changed = binding.is_active or binding.disabled_at is None
        now = datetime.now(UTC)
        binding.is_active = False
        binding.disabled_at = now
        sessions = self.db.scalars(
            select(AuthSession).where(
                AuthSession.person_id == binding.person_id,
                AuthSession.external_identity_binding_id == binding.id,
                AuthSession.status == SessionStatus.active,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        for auth_session in sessions:
            auth_session.status = SessionStatus.revoked
            auth_session.revoked_at = now
            invalidate_session_cache(auth_session.id)
        self.db.flush()
        return ExternalIdentityBindingChange(
            binding=binding,
            changed=changed or bool(sessions),
            sessions_revoked=len(sessions),
        )

    def find_exact(
        self,
        *,
        organization_id: UUID,
        person_id: UUID,
        provider_binding: str,
        issuer: str,
        subject: str,
        lock: bool = False,
    ) -> FederatedIdentity | None:
        query = select(FederatedIdentity).where(
            FederatedIdentity.organization_id == organization_id,
            FederatedIdentity.person_id == person_id,
            FederatedIdentity.provider_binding == provider_binding,
            FederatedIdentity.issuer == issuer,
            FederatedIdentity.subject == subject,
        )
        if lock:
            query = query.with_for_update()
        return cast(FederatedIdentity | None, self.db.scalar(query))


__all__ = [
    "ERPExternalIdentityAuthority",
    "ExternalIdentityConflict",
    "ExternalIdentityBindingChange",
    "FinalizedExternalIdentity",
]
