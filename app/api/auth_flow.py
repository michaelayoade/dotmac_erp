from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.auth import MFAMethodRead
from app.schemas.auth_flow import (
    AvatarUploadResponse,
    ErrorResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    MeUpdateRequest,
    MfaConfirmRequest,
    MfaSetupRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    PasswordChangeRequest,
    PasswordChangeResponse,
    RefreshRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SessionInfoResponse,
    SessionListResponse,
    SessionRevokeResponse,
    TokenResponse,
)
from app.services import auth_flow as auth_flow_service
from app.services.auth_dependencies import require_user_auth
from app.services.auth_flow_api import auth_flow_api_service

router = APIRouter(prefix="/auth", tags=["auth"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    responses={
        428: {
            "model": ErrorResponse,
            "description": "Password reset required",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "code": "PASSWORD_RESET_REQUIRED",
                            "message": "Password reset required",
                        }
                    }
                }
            },
        }
    },
)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    provider = payload.provider.value if payload.provider else None
    return auth_flow_service.auth_flow.login_response(
        db, payload.username, payload.password, request, provider
    )


@router.post(
    "/mfa/setup",
    response_model=MfaSetupResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
def mfa_setup(
    payload: MfaSetupRequest,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    if str(payload.person_id) != auth["person_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return auth_flow_service.auth_flow.mfa_setup(
        db, auth["person_id"], payload.label
    )


@router.post(
    "/mfa/confirm",
    response_model=MFAMethodRead,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def mfa_confirm(
    payload: MfaConfirmRequest,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_service.auth_flow.mfa_confirm(
        db, str(payload.method_id), payload.code, auth["person_id"]
    )


@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def mfa_verify(payload: MfaVerifyRequest, request: Request, db: Session = Depends(get_db)):
    return auth_flow_service.auth_flow.mfa_verify_response(
        db, payload.mfa_token, payload.code, request
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
    },
)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    return auth_flow_service.auth_flow.refresh_response(
        db, payload.refresh_token, request
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse},
    },
)
def logout(payload: LogoutRequest, request: Request, db: Session = Depends(get_db)):
    return auth_flow_service.auth_flow.logout_response(
        db, payload.refresh_token, request
    )


@router.get(
    "/me",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
    },
)
def get_me(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.get_me(auth, db)


@router.patch(
    "/me",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
    },
)
def update_me(
    payload: MeUpdateRequest,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.update_me(auth, payload, db)


@router.post(
    "/me/avatar",
    response_model=AvatarUploadResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
async def upload_avatar(
    file: UploadFile,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return await auth_flow_api_service.upload_avatar(file, auth, db)


@router.delete(
    "/me/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse},
    },
)
def delete_avatar(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    auth_flow_api_service.delete_avatar(auth, db)


@router.get(
    "/me/sessions",
    response_model=SessionListResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
    },
)
def list_sessions(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.list_sessions(auth, db)


@router.delete(
    "/me/sessions/{session_id}",
    response_model=SessionRevokeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def revoke_session(
    session_id: str,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.revoke_session(session_id, auth, db)


@router.delete(
    "/me/sessions",
    response_model=SessionRevokeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
    },
)
def revoke_all_other_sessions(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.revoke_all_other_sessions(auth, db)


@router.post(
    "/me/password",
    response_model=PasswordChangeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def change_password(
    payload: PasswordChangeRequest,
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return auth_flow_api_service.change_password(payload, auth, db)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Request a password reset email.
    Always returns success to prevent email enumeration.
    """
    return auth_flow_api_service.forgot_password(payload, db)


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Reset password using the token from forgot-password email.
    """
    reset_at = auth_flow_api_service.reset_password(payload, db)
    return ResetPasswordResponse(reset_at=reset_at)
