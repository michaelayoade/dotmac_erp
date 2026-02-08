from __future__ import annotations

import ipaddress
import os

from starlette.requests import Request


def _parse_trusted_proxy_networks(raw: str) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            if "/" in value:
                networks.append(ipaddress.ip_network(value, strict=False))
            else:
                networks.append(ipaddress.ip_network(f"{value}/32", strict=False))
        except ValueError:
            # Ignore invalid entries; callers should log if needed.
            continue
    return networks


_TRUSTED_PROXY_NETWORKS = _parse_trusted_proxy_networks(
    os.getenv("TRUSTED_PROXY_IPS", "")
)


def _first_header_value(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(",")[0].strip() or None


def is_from_trusted_proxy(request: Request) -> bool:
    if not _TRUSTED_PROXY_NETWORKS:
        return False
    if not request.client:
        return False
    try:
        client_ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    return any(client_ip in net for net in _TRUSTED_PROXY_NETWORKS)


def get_request_scheme(request: Request) -> str:
    if is_from_trusted_proxy(request):
        forwarded_proto = _first_header_value(request.headers.get("x-forwarded-proto"))
        if forwarded_proto:
            return forwarded_proto
    return request.url.scheme


def get_request_host(request: Request) -> str:
    if is_from_trusted_proxy(request):
        forwarded_host = _first_header_value(request.headers.get("x-forwarded-host"))
        if forwarded_host:
            return forwarded_host
    return request.headers.get("host") or request.url.netloc


def get_client_ip(request: Request) -> str:
    if is_from_trusted_proxy(request):
        forwarded_for = _first_header_value(request.headers.get("x-forwarded-for"))
        if forwarded_for:
            return forwarded_for
        real_ip = _first_header_value(request.headers.get("x-real-ip"))
        if real_ip:
            return real_ip
    if request.client:
        return request.client.host
    return "unknown"
