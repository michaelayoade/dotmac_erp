"""Configuration helpers for Mailcow offboarding."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class MailcowOffboardingConfig:
    enabled: bool
    base_url: str
    api_key: str | None
    request_timeout: float
    inactive_forward_to: str
    autoresponder_subject: str
    autoresponder_template: str
    sieve_host: str
    sieve_port: int
    sieve_master_user: str | None
    sieve_master_password: str | None
    sieve_script_name: str
    sieve_use_starttls: bool
    sogo_db_host: str
    sogo_db_port: int
    sogo_db_name: str
    sogo_db_user: str | None
    sogo_db_password: str | None

    @property
    def mailcow_api_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def sieve_configured(self) -> bool:
        return bool(
            self.sieve_host and self.sieve_master_user and self.sieve_master_password
        )

    @property
    def sogo_db_configured(self) -> bool:
        return bool(self.sogo_db_host and self.sogo_db_user and self.sogo_db_password)


def get_mailcow_offboarding_config() -> MailcowOffboardingConfig:
    return MailcowOffboardingConfig(
        enabled=settings.mailcow_offboarding_enabled,
        base_url=settings.mailcow_base_url,
        api_key=settings.mailcow_api_key,
        request_timeout=settings.mailcow_request_timeout,
        inactive_forward_to=settings.mailcow_inactive_forward_to,
        autoresponder_subject=settings.mailcow_autoresponder_subject,
        autoresponder_template=settings.mailcow_autoresponder_template,
        sieve_host=settings.mailcow_sieve_host,
        sieve_port=settings.mailcow_sieve_port,
        sieve_master_user=settings.mailcow_sieve_master_user,
        sieve_master_password=settings.mailcow_sieve_master_password,
        sieve_script_name=settings.mailcow_sieve_script_name,
        sieve_use_starttls=settings.mailcow_sieve_use_starttls,
        sogo_db_host=settings.mailcow_sogo_db_host,
        sogo_db_port=settings.mailcow_sogo_db_port,
        sogo_db_name=settings.mailcow_sogo_db_name,
        sogo_db_user=settings.mailcow_sogo_db_user,
        sogo_db_password=settings.mailcow_sogo_db_password,
    )
