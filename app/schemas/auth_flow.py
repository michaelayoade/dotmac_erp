from __future__ import annotations

import re
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.auth import AuthProvider


def validate_password_strength(password: str) -> str:
    """Validate password meets security requirements.

    Requirements:
    - At least 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\;'/`~]", password):
        raise ValueError("Password must contain at least one special character")
    return password


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=255)
    provider: AuthProvider | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None


class MfaSetupRequest(BaseModel):
    person_id: UUID
    label: str | None = Field(default=None, max_length=120)


class MfaSetupResponse(BaseModel):
    method_id: UUID
    secret: str
    otpauth_uri: str


class MfaConfirmRequest(BaseModel):
    method_id: UUID
    code: str = Field(min_length=6, max_length=10)


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=10)


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1)


class LogoutResponse(BaseModel):
    revoked_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class MeResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    display_name: str | None = None
    avatar_url: str | None = None
    email: EmailStr
    email_verified: bool = False
    phone: str | None = None
    date_of_birth: date | None = None
    gender: str = "unknown"
    preferred_contact_method: str | None = None
    locale: str | None = None
    timezone: str | None = None
    roles: list[str] = []
    scopes: list[str] = []


class MeUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    gender: str | None = None
    preferred_contact_method: str | None = None
    locale: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)


class AvatarUploadResponse(BaseModel):
    avatar_url: str


class SessionInfoResponse(BaseModel):
    id: UUID
    status: str
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime
    is_current: bool = False


class SessionListResponse(BaseModel):
    sessions: list[SessionInfoResponse]
    total: int


class SessionRevokeResponse(BaseModel):
    revoked_at: datetime
    revoked_count: int = 1


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password_strength(v)


class PasswordChangeResponse(BaseModel):
    changed_at: datetime


class PasswordResetRequiredRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password_strength(v)


class PasswordResetRequiredResponse(BaseModel):
    changed_at: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str = "If the email exists, a reset link has been sent"


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=255)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password_strength(v)


class ResetPasswordResponse(BaseModel):
    reset_at: datetime
