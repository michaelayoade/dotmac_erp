"""Fleet tracking integration boundary."""

from app.services.fleet.tracking.traccar_client import (
    TraccarAuthenticationError,
    TraccarClient,
    TraccarClientError,
    TraccarConfig,
    TraccarConfigurationError,
    TraccarRequestError,
    TraccarUnavailableError,
)
from app.services.fleet.tracking.traccar_service import FleetTrackingService

__all__ = [
    "FleetTrackingService",
    "TraccarAuthenticationError",
    "TraccarClient",
    "TraccarClientError",
    "TraccarConfig",
    "TraccarConfigurationError",
    "TraccarRequestError",
    "TraccarUnavailableError",
]
