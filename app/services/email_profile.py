"""
Email Profile Service - Profile resolution and management.

Handles resolution of email profiles for different modules and organizations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_profile import EmailModule, EmailProfile, ModuleEmailRouting

if TYPE_CHECKING:
    from app.services.email import SMTPConfig

logger = logging.getLogger(__name__)


class EmailProfileService:
    """
    Service for email profile management and resolution.

    Resolution chain (highest to lowest priority):
    1. Module-specific routing for organization
    2. Organization default profile
    3. System default profile (organization_id=NULL)
    4. Environment variables (fallback)
    """

    def __init__(self, db: Session):
        self.db = db

    def get_profile_for_module(
        self,
        organization_id: Optional[UUID],
        module: EmailModule = EmailModule.ADMIN,
    ) -> Optional[EmailProfile]:
        """
        Get the email profile to use for a specific module and organization.

        Resolution order:
        1. Module-specific routing for this organization
        2. Organization's default profile
        3. System default profile (organization_id=NULL)

        Args:
            organization_id: Organization ID (None for system-level operations)
            module: Module sending the email

        Returns:
            EmailProfile if found, None if should fall back to env vars
        """
        # Step 1: Try module-specific routing for this org
        if organization_id:
            routing = self.db.scalar(
                select(ModuleEmailRouting).where(
                    ModuleEmailRouting.organization_id == organization_id,
                    ModuleEmailRouting.module == module,
                )
            )

            if routing:
                if routing.use_default:
                    logger.debug(
                        "Module routing set to use defaults: %s (org=%s)",
                        module.value,
                        organization_id,
                    )
                    return None
                profile = self.db.get(EmailProfile, routing.email_profile_id)
                if profile and profile.is_active:
                    logger.debug(
                        "Resolved email profile via module routing: %s -> %s",
                        module.value,
                        profile.name,
                    )
                    return profile
                if routing.email_profile_id:
                    logger.warning(
                        "Module routing profile missing/inactive: %s (org=%s, profile_id=%s)",
                        module.value,
                        organization_id,
                        routing.email_profile_id,
                    )

        # Step 2: Try organization's default profile
        if organization_id:
            org_default = self.db.scalar(
                select(EmailProfile).where(
                    EmailProfile.organization_id == organization_id,
                    EmailProfile.is_default == True,
                    EmailProfile.is_active == True,
                )
            )

            if org_default:
                logger.debug(
                    "Resolved email profile via org default: %s", org_default.name
                )
                return org_default

        # Step 3: Try system default profile
        system_default = self.db.scalar(
            select(EmailProfile).where(
                EmailProfile.organization_id.is_(None),
                EmailProfile.is_default == True,
                EmailProfile.is_active == True,
            )
        )

        if system_default:
            logger.debug(
                "Resolved email profile via system default: %s", system_default.name
            )
            return system_default

        # Step 4: No profile found - will fall back to env vars
        logger.debug("No email profile found, will use environment variables")
        return None

    def get_profile_config(
        self,
        organization_id: Optional[UUID] = None,
        module: EmailModule = EmailModule.ADMIN,
    ) -> Optional["SMTPConfig"]:
        """
        Get the config dict for an email profile.

        Returns None if no profile found (caller should fall back to env vars).
        """
        profile = self.get_profile_for_module(organization_id, module)
        if profile:
            return profile.to_config_dict()
        return None

    def create_profile(
        self,
        name: str,
        smtp_host: str,
        smtp_port: int,
        from_email: str,
        from_name: str = "Dotmac ERP",
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: bool = True,
        use_ssl: bool = False,
        reply_to: Optional[str] = None,
        organization_id: Optional[UUID] = None,
        is_default: bool = False,
        created_by_id: Optional[UUID] = None,
        validate_smtp: bool = True,
    ) -> EmailProfile:
        """
        Create a new email profile.

        Args:
            name: Display name for the profile
            smtp_host: SMTP server hostname
            smtp_port: SMTP server port
            from_email: Sender email address
            from_name: Sender display name
            smtp_username: SMTP auth username (optional)
            smtp_password: SMTP auth password (optional)
            use_tls: Enable STARTTLS
            use_ssl: Enable SSL/TLS
            reply_to: Reply-to address (optional)
            organization_id: Organization ID (None for system-level)
            is_default: Set as default profile for org
            created_by_id: User who created the profile
            validate_smtp: If True, validate SMTP settings before saving

        Returns:
            Created EmailProfile instance

        Raises:
            ValueError: If SMTP validation fails (when validate_smtp=True)
        """
        # Validate SMTP settings before creating profile
        if validate_smtp:
            from app.services.email import validate_smtp_config

            config: SMTPConfig = {
                "host": smtp_host,
                "port": smtp_port,
                "username": smtp_username,
                "password": smtp_password,
                "use_tls": use_tls,
                "use_ssl": use_ssl,
                "from_email": from_email,
                "from_name": from_name,
                "reply_to": reply_to,
            }
            is_valid, error_message = validate_smtp_config(config)
            if not is_valid:
                raise ValueError(f"SMTP validation failed: {error_message}")

        # If setting as default, unset other defaults for this org
        if is_default:
            # Build org filter: match specific org_id, or NULL for system-level
            if organization_id:
                org_filter = EmailProfile.organization_id == organization_id
            else:
                org_filter = EmailProfile.organization_id.is_(None)

            existing_defaults = self.db.scalars(
                select(EmailProfile).where(org_filter, EmailProfile.is_default == True)
            ).all()

            for existing in existing_defaults:
                existing.is_default = False

        profile = EmailProfile(
            name=name,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            use_tls=use_tls,
            use_ssl=use_ssl,
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            organization_id=organization_id,
            is_default=is_default,
            created_by_id=created_by_id,
        )

        self.db.add(profile)
        self.db.flush()

        logger.info(
            "Created email profile: %s (org=%s, default=%s)",
            profile.name,
            organization_id,
            is_default,
        )

        return profile

    def set_module_routing(
        self,
        organization_id: UUID,
        module: EmailModule,
        profile_id: UUID | None,
        *,
        use_default: bool = False,
    ) -> ModuleEmailRouting:
        """Set or update the email profile for a module.

        Raises:
            ValueError: If the specified profile_id does not exist.
        """
        # Validate that the profile exists before setting routing
        if profile_id is not None:
            profile = self.db.get(EmailProfile, profile_id)
            if not profile:
                raise ValueError(f"Email profile {profile_id} not found")

        # Check if routing exists
        existing = self.db.scalar(
            select(ModuleEmailRouting).where(
                ModuleEmailRouting.organization_id == organization_id,
                ModuleEmailRouting.module == module,
            )
        )

        if existing:
            existing.email_profile_id = profile_id
            existing.use_default = use_default
            self.db.flush()
            logger.info(
                "Updated module routing: %s -> profile %s", module.value, profile_id
            )
            return existing

        routing = ModuleEmailRouting(
            organization_id=organization_id,
            module=module,
            email_profile_id=profile_id,
            use_default=use_default,
        )

        self.db.add(routing)
        self.db.flush()

        logger.info(
            "Created module routing: %s -> profile %s", module.value, profile_id
        )

        return routing

    def delete_module_routing(
        self,
        organization_id: UUID,
        module: EmailModule,
    ) -> bool:
        """Remove a module routing (will fall back to org default)."""
        routing = self.db.scalar(
            select(ModuleEmailRouting).where(
                ModuleEmailRouting.organization_id == organization_id,
                ModuleEmailRouting.module == module,
            )
        )

        if routing:
            self.db.delete(routing)
            self.db.flush()
            logger.info("Deleted module routing: %s", module.value)
            return True

        return False

    def list_profiles(
        self,
        organization_id: Optional[UUID] = None,
        include_system: bool = True,
    ) -> list[EmailProfile]:
        """List email profiles for an organization."""
        stmt = select(EmailProfile).where(EmailProfile.is_active == True)

        if organization_id:
            if include_system:
                stmt = stmt.where(
                    (EmailProfile.organization_id == organization_id)
                    | (EmailProfile.organization_id.is_(None))
                )
            else:
                stmt = stmt.where(EmailProfile.organization_id == organization_id)
        else:
            stmt = stmt.where(EmailProfile.organization_id.is_(None))

        stmt = stmt.order_by(EmailProfile.name)

        return list(self.db.scalars(stmt).all())

    def list_module_routings(
        self,
        organization_id: UUID,
    ) -> list[ModuleEmailRouting]:
        """List all module routings for an organization."""
        return list(
            self.db.scalars(
                select(ModuleEmailRouting)
                .where(ModuleEmailRouting.organization_id == organization_id)
                .order_by(ModuleEmailRouting.module)
            ).all()
        )


# Module-level singleton constructor
email_profile_service = EmailProfileService
