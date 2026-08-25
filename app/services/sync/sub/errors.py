"""Transport-neutral errors for Dotmac Sub command intake."""

from dotmac_kernel.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
)


class SubValidationError(BadRequestError):
    """A validated Sub command cannot be accepted by the owning ERP domain."""


class SubNotFoundError(NotFoundError):
    """A tenant-scoped ERP record required by a Sub command does not exist."""


class SubPayloadTooLargeError(DomainError):
    """A Sub command payload exceeds its explicit application limit."""


class SubReplayConflictError(ConflictError):
    """A Sub source identity was reused for a different immutable command."""


__all__ = [
    "SubNotFoundError",
    "SubPayloadTooLargeError",
    "SubReplayConflictError",
    "SubValidationError",
]
