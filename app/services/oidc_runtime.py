"""Process-held, explicitly installed shared OIDC client."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from dotmac_auth_oidc import OIDCClient, PER_REQUEST_STATE_STORE, RelyingPartyConfig


@dataclass(frozen=True, slots=True, repr=False)
class OIDCProviderConfig:
    provider_binding: str
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    discovery_url: str | None
    timeout_seconds: float
    ceremony_ttl_seconds: int
    clock_skew_seconds: int


_held_config: OIDCProviderConfig | None = None
_held_client: OIDCClient | None = None
_held_lock = RLock()


def install(config: OIDCProviderConfig) -> None:
    global _held_client, _held_config
    client = OIDCClient(
        RelyingPartyConfig(
            provider_binding=config.provider_binding,
            issuer=config.issuer,
            client_id=config.client_id,
            client_secret=config.client_secret,
            redirect_uri=config.redirect_uri,
            scopes=("openid",),
            discovery_url=config.discovery_url,
        ),
        state_store=PER_REQUEST_STATE_STORE,
        timeout=config.timeout_seconds,
        leeway=config.clock_skew_seconds,
    )
    with _held_lock:
        _held_config = config
        _held_client = client


def clear() -> None:
    global _held_client, _held_config
    with _held_lock:
        _held_client = None
        _held_config = None


def installation() -> tuple[OIDCProviderConfig, OIDCClient]:
    """Return one internally consistent held registration/client snapshot."""
    with _held_lock:
        if _held_config is None or _held_client is None:
            raise RuntimeError("ERP OIDC relying party is not installed")
        return _held_config, _held_client


def client() -> OIDCClient:
    return installation()[1]


def provider_config() -> OIDCProviderConfig | None:
    with _held_lock:
        return _held_config


def configuration_matches(*, provider_binding: str, issuer: str) -> bool:
    with _held_lock:
        config = _held_config
        return bool(
            config is not None
            and config.provider_binding == provider_binding
            and config.issuer == issuer
        )


__all__ = [
    "OIDCProviderConfig",
    "clear",
    "client",
    "configuration_matches",
    "install",
    "installation",
    "provider_config",
]
