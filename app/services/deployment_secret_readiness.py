"""ERP-owned, read-only boot-material policy for an isolated candidate probe.

The caller supplies the candidate's deployment environment. No database,
licence, RBAC, startup, token renewal, or optional connector check runs here.
Output suppression is process-global: invoke only in a dedicated probe process.
"""

from __future__ import annotations

import io
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import Enum


class MaterialStatus(str, Enum):
    READY = "ready"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"


class BootCheckName(str, Enum):
    CONFIG = "required_config"
    OPENBAO = "openbao_configuration"
    JWT = "jwt_secret"
    TOTP = "totp_encryption_key"
    EXECUTION = "probe_execution"


@dataclass(frozen=True)
class BootMaterialCheck:
    name: BootCheckName
    status: MaterialStatus


@dataclass(frozen=True)
class BootMaterialResult:
    checks: tuple[BootMaterialCheck, ...]

    def __post_init__(self) -> None:
        if not self.checks or len(self.checks) > len(BootCheckName):
            raise ValueError("Invalid boot check count")
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("Duplicate boot check")
        if any(
            not isinstance(check.name, BootCheckName)
            or not isinstance(check.status, MaterialStatus)
            for check in self.checks
        ):
            raise ValueError("Invalid boot check")

    @property
    def status(self) -> MaterialStatus:
        for status in (MaterialStatus.REFUSED, MaterialStatus.UNAVAILABLE):
            if any(check.status == status for check in self.checks):
                return status
        return MaterialStatus.READY


class _Discard(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)


@contextmanager
def suppress_probe_diagnostics() -> Iterator[None]:
    """Discard validator/import output without retaining it, restoring logging."""
    previous = logging.root.manager.disable
    try:
        logging.disable(2**63 - 1)
        with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
            yield
    finally:
        logging.disable(previous)


def inspect_boot_material() -> BootMaterialResult:
    """Reuse startup policy and the existing resolver with no database session."""
    with suppress_probe_diagnostics():
        try:
            return _inspect_boot_material()
        except Exception:
            return BootMaterialResult(
                (
                    BootMaterialCheck(
                        BootCheckName.EXECUTION, MaterialStatus.UNAVAILABLE
                    ),
                )
            )


def _inspect_boot_material() -> BootMaterialResult:
    from app.services.boot_secret_validation import (
        validate_openbao_connectivity,
        validate_required_config,
        validate_secret_value,
    )
    from app.services.secrets import (
        BootSecretResolutionError,
        is_openbao_ref,
        resolve_boot_secret,
    )

    checks = [
        BootMaterialCheck(
            BootCheckName.CONFIG,
            MaterialStatus.REFUSED
            if validate_required_config()
            else MaterialStatus.READY,
        ),
        BootMaterialCheck(
            BootCheckName.OPENBAO,
            MaterialStatus.REFUSED
            if validate_openbao_connectivity()
            else MaterialStatus.READY,
        ),
    ]
    for env_name, check_name in (
        ("JWT_SECRET", BootCheckName.JWT),
        ("TOTP_ENCRYPTION_KEY", BootCheckName.TOTP),
    ):
        value = os.getenv(env_name)
        status = MaterialStatus.REFUSED
        if value:
            if is_openbao_ref(value):
                try:
                    value = resolve_boot_secret(value)
                except BootSecretResolutionError as error:
                    # Resolver errors never cross this boundary. An explicit
                    # denied/missing HTTP result is refusal; transport failure
                    # or an unexpected response cannot establish readiness.
                    status = (
                        MaterialStatus.UNAVAILABLE
                        if error.unavailable
                        else MaterialStatus.REFUSED
                    )
                    checks.append(BootMaterialCheck(check_name, status))
                    continue
            if value and not validate_secret_value(env_name, value):
                status = MaterialStatus.READY
        checks.append(BootMaterialCheck(check_name, status))
    return BootMaterialResult(tuple(checks))


def serialize_candidate_result(result: BootMaterialResult) -> str:
    """Adapt the product result to Foundation's closed v1 wire contract."""
    return json.dumps(
        {
            "schema": "CandidateMaterialProbeResult.v1",
            "status": result.status.value,
            "checks": [
                {"name": check.name.value, "status": check.status.value}
                for check in result.checks
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
