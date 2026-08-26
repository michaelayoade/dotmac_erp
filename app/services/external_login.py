"""ERP login composition: shared protocol, local binding, local session."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from dotmac_auth_oidc import OIDCError
from fastapi import Request
from sqlalchemy.orm import Session

from app.services.auth_flow import AuthFlow
from app.services.external_identity import ERPExternalIdentityAuthority
from app.services.oidc_runtime import installation
from app.services.oidc_state_store import PostgresOIDCStateStore


class ExternalLoginRefused(RuntimeError):
    """One non-enumerating refusal for every external login failure."""


@dataclass(frozen=True, slots=True, repr=False)
class StartedExternalLogin:
    authorization_url: str
    stored_state: str
    ceremony_ttl_seconds: int


@dataclass(frozen=True, slots=True, repr=False)
class CompletedExternalLogin:
    token_payload: dict[str, str]
    return_to: str


def start_external_login(
    db: Session,
    *,
    organization_id: UUID,
    return_to: str,
) -> StartedExternalLogin:
    try:
        config, installed_client = installation()
    except RuntimeError:
        raise ExternalLoginRefused("external_login_unavailable")
    store = PostgresOIDCStateStore(
        db,
        organization_id=organization_id,
        provider_binding=config.provider_binding,
    )
    try:
        # StateStore.put happens before discovery. Keep both under a savepoint
        # so a provider/discovery refusal cannot be rendered and then commit an
        # orphan ceremony through the request-owned outer transaction.
        with db.begin_nested():
            started = installed_client.start_login(
                return_to=return_to,
                ttl_seconds=config.ceremony_ttl_seconds,
                state_store=store,
            )
    except OIDCError as exc:
        raise ExternalLoginRefused("external_login_refused") from exc
    return StartedExternalLogin(
        started.url,
        started.state,
        config.ceremony_ttl_seconds,
    )


def complete_external_login(
    db: Session,
    *,
    organization_id: UUID,
    code: str,
    state_parameter: str,
    stored_state: str,
    request: Request,
) -> CompletedExternalLogin:
    try:
        config, installed_client = installation()
    except RuntimeError:
        raise ExternalLoginRefused("external_login_unavailable")
    store = PostgresOIDCStateStore(
        db,
        organization_id=organization_id,
        provider_binding=config.provider_binding,
    )
    try:
        verified = installed_client.complete_login(
            code=code,
            state_parameter=state_parameter,
            stored_state=stored_state,
            ttl_seconds=config.ceremony_ttl_seconds,
            state_store=store,
        )
    except OIDCError as exc:
        raise ExternalLoginRefused("external_login_refused") from exc
    finalized = ERPExternalIdentityAuthority(db).finalize_login(
        organization_id=organization_id,
        provider_binding=config.provider_binding,
        issuer=verified.issuer,
        subject=verified.subject,
    )
    if finalized is None:
        raise ExternalLoginRefused("external_login_refused")
    payload = AuthFlow.issue_external_identity_session(
        db,
        person_id=finalized.person.id,
        external_identity_binding_id=finalized.binding.id,
        request=request,
    )
    return CompletedExternalLogin(payload, verified.return_to)


__all__ = [
    "CompletedExternalLogin",
    "ExternalLoginRefused",
    "StartedExternalLogin",
    "complete_external_login",
    "start_external_login",
]
