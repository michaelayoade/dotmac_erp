"""
Auth flow API service.

Provides API-focused handlers for auth flow routes.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.auth import AuthProvider, Session as AuthSession, SessionStatus, UserCredential
from app.models.person import Person
from app.schemas.auth_flow import (
    AvatarUploadResponse,
    ForgotPasswordResponse,
    MeResponse,
    PasswordChangeResponse,
    PasswordResetRequiredRequest,
    PasswordResetRequiredResponse,
    SessionInfoResponse,
    SessionListResponse,
    SessionRevokeResponse,
)
from app.services import avatar as avatar_service
from app.services.auth_flow import (
    hash_password,
    request_password_reset,
    reset_password,
    revoke_sessions_for_person,
    verify_password,
)
from app.services.common import coerce_uuid
from app.services.email import send_password_reset_email


class AuthFlowApiService:
    def get_me(self, auth: dict, db: Session) -> MeResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        return MeResponse(
            id=person.id,
            first_name=person.first_name,
            last_name=person.last_name,
            display_name=person.display_name,
            avatar_url=person.avatar_url,
            email=person.email,
            email_verified=person.email_verified,
            phone=person.phone,
            date_of_birth=person.date_of_birth,
            gender=person.gender.value if person.gender else "unknown",
            preferred_contact_method=person.preferred_contact_method.value
            if person.preferred_contact_method
            else None,
            locale=person.locale,
            timezone=person.timezone,
            roles=auth.get("roles", []),
            scopes=auth.get("scopes", []),
        )

    def update_me(self, auth: dict, payload, db: Session) -> MeResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(person, field, value)

        db.commit()
        db.refresh(person)

        return self.get_me(auth, db)

    async def upload_avatar(self, file: UploadFile, auth: dict, db: Session) -> AvatarUploadResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        avatar_service.delete_avatar(person.avatar_url)

        avatar_url = await avatar_service.save_avatar(file, str(person.id))
        person.avatar_url = avatar_url
        db.commit()

        return AvatarUploadResponse(avatar_url=avatar_url)

    def delete_avatar(self, auth: dict, db: Session) -> None:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")

        avatar_service.delete_avatar(person.avatar_url)
        person.avatar_url = None
        db.commit()

    def list_sessions(self, auth: dict, db: Session) -> SessionListResponse:
        person_id = coerce_uuid(auth["person_id"])
        now = datetime.now(timezone.utc)
        sessions = (
            db.query(AuthSession)
            .filter(AuthSession.person_id == person_id)
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .order_by(AuthSession.created_at.desc())
            .all()
        )

        current_session_id = auth.get("session_id")

        return SessionListResponse(
            sessions=[
                SessionInfoResponse(
                    id=session.id,
                    status=session.status.value,
                    ip_address=session.ip_address,
                    user_agent=session.user_agent,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    expires_at=session.expires_at,
                    is_current=(str(session.id) == current_session_id),
                )
                for session in sessions
            ],
            total=len(sessions),
        )

    def revoke_session(self, session_id: str, auth: dict, db: Session) -> SessionRevokeResponse:
        now = datetime.now(timezone.utc)
        session = (
            db.query(AuthSession)
            .filter(AuthSession.id == coerce_uuid(session_id))
            .filter(AuthSession.person_id == coerce_uuid(auth["person_id"]))
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .first()
        )

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        session.status = SessionStatus.revoked
        session.revoked_at = now
        db.commit()

        return SessionRevokeResponse(revoked_at=now)

    def revoke_all_other_sessions(self, auth: dict, db: Session) -> SessionRevokeResponse:
        current_session_id = auth.get("session_id")
        if current_session_id:
            current_session_id = coerce_uuid(current_session_id)

        now = datetime.now(timezone.utc)
        sessions = (
            db.query(AuthSession)
            .filter(AuthSession.person_id == coerce_uuid(auth["person_id"]))
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .filter(AuthSession.id != current_session_id)
            .all()
        )

        for session in sessions:
            session.status = SessionStatus.revoked
            session.revoked_at = now

        db.commit()

        return SessionRevokeResponse(revoked_at=now, revoked_count=len(sessions))

    def change_password(self, payload, auth: dict, db: Session) -> PasswordChangeResponse:
        credential = (
            db.query(UserCredential)
            .filter(UserCredential.person_id == coerce_uuid(auth["person_id"]))
            .filter(UserCredential.is_active.is_(True))
            .first()
        )

        if not credential:
            raise HTTPException(status_code=404, detail="No credentials found")

        if not verify_password(payload.current_password, credential.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        if payload.current_password == payload.new_password:
            raise HTTPException(status_code=400, detail="New password must be different")

        now = datetime.now(timezone.utc)
        credential.password_hash = hash_password(payload.new_password)
        credential.password_updated_at = now
        credential.must_change_password = False
        revoke_sessions_for_person(db, auth["person_id"])
        db.commit()

        return PasswordChangeResponse(changed_at=now)

    def reset_password_required(
        self,
        payload: PasswordResetRequiredRequest,
        db: Session,
    ) -> PasswordResetRequiredResponse:
        username = payload.username.strip()
        credential = (
            db.query(UserCredential)
            .filter(UserCredential.username == username)
            .filter(UserCredential.provider == AuthProvider.local)
            .filter(UserCredential.is_active.is_(True))
            .first()
        )
        if not credential:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        now = datetime.now(timezone.utc)
        if credential.locked_until and credential.locked_until > now:
            raise HTTPException(status_code=403, detail="Account locked")

        if not verify_password(payload.current_password, credential.password_hash):
            credential.failed_login_attempts += 1
            if credential.failed_login_attempts >= 5:
                credential.locked_until = now + timedelta(minutes=15)
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if payload.current_password == payload.new_password:
            raise HTTPException(status_code=400, detail="New password must be different")

        credential.password_hash = hash_password(payload.new_password)
        credential.password_updated_at = now
        credential.must_change_password = False
        credential.failed_login_attempts = 0
        credential.locked_until = None
        db.commit()

        return PasswordResetRequiredResponse(changed_at=now)

    def forgot_password(
        self,
        payload,
        db: Session,
        app_url: str | None = None,
    ) -> ForgotPasswordResponse:
        result = request_password_reset(db, payload.email)

        if result:
            send_password_reset_email(
                db=db,
                to_email=result["email"],
                reset_token=result["token"],
                person_name=result["person_name"],
                app_url=app_url,
            )

        return ForgotPasswordResponse()

    def reset_password(self, payload, db: Session):
        reset_at = reset_password(db, payload.token, payload.new_password)
        return reset_at


auth_flow_api_service = AuthFlowApiService()
