"""
Integration Configuration Service.

Manages per-organization external system credentials with encryption.
Supports both encrypted storage and OpenBao/Vault references.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sync import IntegrationConfig, IntegrationType
from app.services.secrets import is_openbao_ref, resolve_secret

logger = logging.getLogger(__name__)

# Prefix for encrypted values to distinguish from plaintext/OpenBao refs
ENCRYPTED_PREFIX = "enc:"


def _get_encryption_key(db: Session | None = None) -> bytes:
    """
    Get the encryption key for integration credentials.

    Uses the same TOTP_ENCRYPTION_KEY as MFA secrets for consistency.
    Falls back to a dedicated INTEGRATION_ENCRYPTION_KEY if set.
    """
    # Try dedicated key first
    key = os.getenv("INTEGRATION_ENCRYPTION_KEY")
    if not key:
        # Fall back to TOTP key (same Fernet requirements)
        key = os.getenv("TOTP_ENCRYPTION_KEY")

    if not key:
        raise ValueError(
            "No encryption key configured. Set INTEGRATION_ENCRYPTION_KEY or TOTP_ENCRYPTION_KEY"
        )

    # Resolve OpenBao reference if needed
    resolved = resolve_secret(key, db)
    if not resolved:
        raise ValueError("Failed to resolve encryption key")

    return resolved.encode()


def _get_fernet(db: Session | None = None) -> Fernet:
    """Get Fernet cipher for encryption/decryption."""
    return Fernet(_get_encryption_key(db))


def encrypt_credential(value: str, db: Session | None = None) -> str:
    """
    Encrypt a credential for storage.

    Returns prefixed encrypted string: "enc:<base64_ciphertext>"
    """
    if not value:
        return value

    # Don't encrypt OpenBao references - they're resolved at runtime
    if is_openbao_ref(value):
        return value

    fernet = _get_fernet(db)
    encrypted = fernet.encrypt(value.encode())
    return f"{ENCRYPTED_PREFIX}{encrypted.decode()}"


def decrypt_credential(value: str | None, db: Session | None = None) -> str | None:
    """
    Decrypt a stored credential.

    Handles:
    - Encrypted values (enc: prefix)
    - OpenBao references (bao://, vault://, openbao://)
    - Plaintext values (legacy/migration)
    """
    if not value:
        return value

    # OpenBao reference - resolve from vault
    if is_openbao_ref(value):
        return resolve_secret(value, db)

    # Encrypted value - decrypt
    if value.startswith(ENCRYPTED_PREFIX):
        encrypted = value[len(ENCRYPTED_PREFIX) :]
        try:
            fernet = _get_fernet(db)
            decrypted = fernet.decrypt(encrypted.encode())
            return decrypted.decode()
        except InvalidToken:
            raise ValueError("Failed to decrypt credential - invalid token or key")

    # Plaintext (legacy) - return as-is but log warning
    # In production, this should trigger migration
    return value


class IntegrationConfigService:
    """
    Service for managing integration configurations.

    Provides CRUD operations with automatic credential encryption.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_config(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
        active_only: bool = True,
    ) -> Optional[IntegrationConfig]:
        """Get integration config for an organization."""
        query = select(IntegrationConfig).where(
            IntegrationConfig.organization_id == organization_id,
            IntegrationConfig.integration_type == integration_type,
        )
        if active_only:
            query = query.where(IntegrationConfig.is_active == True)  # noqa: E712

        return self.db.execute(query).scalar_one_or_none()

    def get_decrypted_credentials(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
    ) -> Optional[dict]:
        """
        Get decrypted credentials for an integration.

        Returns dict with: base_url, api_key, api_secret, company
        Returns None if not configured.
        """
        config = self.get_config(organization_id, integration_type)
        if not config:
            return None

        return {
            "base_url": config.base_url,
            "api_key": decrypt_credential(config.api_key, self.db),
            "api_secret": decrypt_credential(config.api_secret, self.db),
            "company": config.company,
        }

    def create_config(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
        base_url: str,
        api_key: str,
        api_secret: str,
        company: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        encrypt: bool = True,
    ) -> IntegrationConfig:
        """
        Create a new integration configuration.

        Args:
            organization_id: Organization UUID
            integration_type: Type of integration (ERPNEXT, etc.)
            base_url: Base URL for the external system
            api_key: API key (will be encrypted unless it's an OpenBao ref)
            api_secret: API secret (will be encrypted unless it's an OpenBao ref)
            company: Optional company/tenant identifier
            user_id: User creating the config
            encrypt: Whether to encrypt credentials (default True)
        """
        # Deactivate any existing config for this org/type
        existing = self.get_config(organization_id, integration_type, active_only=False)
        if existing:
            existing.is_active = False

        # Encrypt credentials if requested and not OpenBao refs
        stored_key = encrypt_credential(api_key, self.db) if encrypt else api_key
        stored_secret = (
            encrypt_credential(api_secret, self.db) if encrypt else api_secret
        )

        config = IntegrationConfig(
            organization_id=organization_id,
            integration_type=integration_type,
            base_url=base_url.rstrip("/"),
            api_key=stored_key,
            api_secret=stored_secret,
            company=company,
            is_active=True,
            created_by_user_id=user_id,
        )

        self.db.add(config)
        self.db.flush()

        return config

    def update_credentials(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        company: Optional[str] = None,
    ) -> Optional[IntegrationConfig]:
        """Update credentials for an existing config."""
        config = self.get_config(organization_id, integration_type)
        if not config:
            return None

        if api_key is not None:
            config.api_key = encrypt_credential(api_key, self.db)
        if api_secret is not None:
            config.api_secret = encrypt_credential(api_secret, self.db)
        if base_url is not None:
            config.base_url = base_url.rstrip("/")
        if company is not None:
            config.company = company

        config.updated_at = datetime.now(timezone.utc)
        self.db.flush()

        return config

    def verify_connection(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
    ) -> tuple[bool, Optional[str]]:
        """
        Verify connection to the external system.

        Returns (success, error_message)
        """
        creds = self.get_decrypted_credentials(organization_id, integration_type)
        if not creds:
            return False, "Integration not configured"

        if integration_type == IntegrationType.ERPNEXT:
            return self._verify_erpnext(creds)

        return False, f"Verification not implemented for {integration_type}"

    def _verify_erpnext(self, creds: dict) -> tuple[bool, Optional[str]]:
        """Verify ERPNext connection."""
        from app.services.erpnext.client import (
            ERPNextClient,
            ERPNextConfig,
            ERPNextError,
        )

        config = ERPNextConfig(
            url=creds["base_url"],
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            company=creds.get("company"),
        )

        try:
            with ERPNextClient(config) as client:
                result = client.test_connection()
                # Update last_verified_at
                return True, None
        except ERPNextError as e:
            return False, e.message
        except Exception as e:
            return False, str(e)

    def mark_verified(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
    ) -> None:
        """Mark integration as verified."""
        config = self.get_config(organization_id, integration_type)
        if config:
            config.last_verified_at = datetime.now(timezone.utc)
            self.db.flush()

    def deactivate(
        self,
        organization_id: uuid.UUID,
        integration_type: IntegrationType,
    ) -> bool:
        """Deactivate an integration config."""
        config = self.get_config(organization_id, integration_type)
        if config:
            config.is_active = False
            self.db.flush()
            return True
        return False

    def list_configs(
        self,
        organization_id: uuid.UUID,
        active_only: bool = True,
    ) -> list[IntegrationConfig]:
        """List all integration configs for an organization."""
        query = select(IntegrationConfig).where(
            IntegrationConfig.organization_id == organization_id,
        )
        if active_only:
            query = query.where(IntegrationConfig.is_active == True)  # noqa: E712

        return list(self.db.execute(query).scalars().all())
