"""Validate vmagent's effective Compose command without exposing credentials.

Read `docker compose config --format json` from stdin. This deliberately uses
Compose's own interpolation rather than evaluating .env as shell code. Internal
receiver names and private IP addresses are supported. No network request is
made: ingestion must still be verified against the actual receiver after deploy.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
import sys
from urllib.parse import unquote, urlsplit


class MetricsConfigurationError(ValueError):
    """Only credential-free diagnostic messages may cross this boundary."""


def validate_remote_write_url(value: str) -> None:
    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise MetricsConfigurationError(
            "Metrics destination must be a nonempty absolute URL"
        )
    if any(marker in value for marker in ("${", "<", ">", "\\")):
        raise MetricsConfigurationError(
            "Metrics destination contains an unresolved placeholder"
        )
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        raise MetricsConfigurationError(
            "Metrics destination has an invalid host or port"
        ) from None
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.fragment:
        raise MetricsConfigurationError(
            "Metrics destination requires HTTP(S), a host, and no fragment"
        )
    if port is not None and not 1 <= port <= 65535:
        raise MetricsConfigurationError("Metrics destination port is invalid")
    decoded_host = unquote(hostname)
    if (
        decoded_host == "invalid"
        or decoded_host.endswith(".invalid")
        or "is-unset" in decoded_host
        or "vm_remote_write_url" in decoded_host
        or decoded_host in {"changeme", "replace-me", "example.com", "example.org"}
        or decoded_host.endswith((".example.com", ".example.org"))
    ):
        raise MetricsConfigurationError(
            "Metrics destination is a placeholder, not a configured receiver"
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?", hostname):
            raise MetricsConfigurationError(
                "Metrics destination hostname is invalid"
            ) from None
        if any(not label or len(label) > 63 for label in hostname.split(".")):
            raise MetricsConfigurationError("Metrics destination hostname is invalid")


def validate_compose_metrics(config: object) -> None:
    if not isinstance(config, dict):
        raise MetricsConfigurationError("Expected effective Compose JSON configuration")
    services = config.get("services")
    service = services.get("vmagent") if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise MetricsConfigurationError(
            "Effective Compose configuration is missing vmagent"
        )
    command = service.get("command", [])
    if isinstance(command, str):
        try:
            command = shlex.split(command)
        except ValueError:
            raise MetricsConfigurationError("vmagent command is malformed") from None
    if not isinstance(command, list) or not all(
        isinstance(arg, str) for arg in command
    ):
        raise MetricsConfigurationError("vmagent command is malformed")
    urls = []
    for index, argument in enumerate(command):
        name, separator, value = argument.partition("=")
        if name.lstrip("-") != "remoteWrite.url":
            continue
        if not separator:
            if index + 1 == len(command):
                raise MetricsConfigurationError("vmagent remoteWrite.url has no value")
            value = command[index + 1]
        urls.append(value)
    if not urls:
        raise MetricsConfigurationError("vmagent remoteWrite.url is missing")
    for value in urls:
        validate_remote_write_url(value)


def main() -> int:
    try:
        validate_compose_metrics(json.load(sys.stdin))
    except (MetricsConfigurationError, ValueError, TypeError):
        # Do not echo the effective config or destination: both can carry auth.
        print(
            "ERROR: invalid metrics remote-write configuration; set a real HTTP(S) receiver URL and retry deployment.",
            file=sys.stderr,
        )
        return 2
    print(
        "Metrics remote-write destination configuration is valid (ingestion not yet verified)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
