"""Whether a forwarded client address, scheme or host may be believed.

Read by rate limiting (`app/middleware/rate_limit.py`), CSRF
(`app/web/csrf.py`), the password-reset URL (`app/api/auth_flow.py`), the HR
API (`app/api/people/hr.py`), the employee web surface
(`app/services/people/hr/web/employee_web.py`) and — since the request-context
repair — the audit trail, which records `ip_address_var` through
`app/services/audit_listener.py` and `app/services/audit_dispatcher.py`.

## Two different failure directions, and only one of them was closed

`is_from_trusted_proxy` returns False when no proxy network is configured, so
an unconfigured deployment believes no forwarded header at all. That direction
is closed: forgetting to configure `TRUSTED_PROXY_IPS` cannot make an
attacker-supplied `X-Forwarded-For` authoritative.

The other direction was open, and it is the one that bit. A malformed entry was
caught and `continue`d — silently dropped — so a deployment could believe it had
configured a proxy it had not. Nothing failed, nothing logged, and from that
moment every client address the five consumers above read was the PROXY's rather
than the client's. Rate limits were shared across every real client behind that
proxy, CSRF's origin reasoning and the reset-URL host lost their forwarded
input, and every audit row recorded the wrong originator. The configuration
failed closed for trust and silently for deployment correctness, and it
destroyed client provenance.

So a malformed non-empty entry now REFUSES, at import, naming the entry it could
not parse. The blast direction is deliberate: the process does not start, the
health check fails and `scripts/deploy.sh` rolls back — which is recoverable and
visible, unlike a running deployment that has quietly lost provenance.

## A bare address is one host, in both families

The rule that turns a bare `10.0.0.1` into a `/32` host route appended `/32`
regardless of address family, so a bare `::1` became `::/32` — 7.9e28 addresses,
trusted, silently, with `strict=False` masking the host bits without complaint.
It parsed cleanly, so the refusal above never saw it.

That direction fails OPEN, which is the opposite of the closed failure the
refusal was written for, and worse: `::1` is the natural loopback entry on a
dual-stack host, and `docker-compose.yml:59` already publishes the `app` service
on `::1`. A bare address now yields `/32` for IPv4 and `/128` for IPv6, derived
from the address rather than appended, so the two families cannot drift apart
again.

## Empty configuration stays valid

Configuring nothing and configuring garbage are different acts. A deployment
that sets `TRUSTED_PROXY_IPS` to the empty string, or to nothing but separators,
has chosen to trust no proxy, and that choice is honoured without complaint. The
refusal is for an entry that carries characters and does not parse — the shape a
typo takes.

## The trusted set is still read once, at import

`_TRUSTED_PROXY_NETWORKS` is computed from the environment when this module is
first imported, so the trusted set cannot change without restarting the process,
and `monkeypatch.setenv` after import is inert (the tests patch the parsed
constant and say so). That is a SEPARATE finding from the one repaired here and
it is NOT repaired here: taking the networks as typed configuration rather than
reading the environment at import is the Foundation-plan question, recorded in
`docs/inventories/2026-09-04-erp-proxy-trust-readback-hole.md`. This module is
still configured by manual environment state.

The typed declaration that exists today is `ProductDeploymentSpec.v1`'s
`[ingress] trusted_proxies` in `deploy/product.toml`. Nothing compares it with
what this module actually parsed, and the RENDERED compose does not carry
`TRUSTED_PROXY_IPS` at all — so the rendered path reaches the lost-provenance
state above with no typo whatsoever, and the refusal cannot catch it because
unset is empty and empty is valid by design.
"""

from __future__ import annotations

import ipaddress
import os

from starlette.requests import Request

#: The environment variable this module is configured by. Named once so the
#: refusal below can quote it without a second spelling drifting from the read.
TRUSTED_PROXY_ENV_VAR = "TRUSTED_PROXY_IPS"


class TrustedProxyConfigurationError(ValueError):
    """`TRUSTED_PROXY_IPS` named a proxy network that cannot be parsed.

    Raised at import, so a deployment that mistyped its proxy list does not
    start. The alternative — the behaviour this replaces — was to drop the
    entry and serve every subsequent request with the proxy's address recorded
    as the client's.
    """


def _parse_trusted_proxy_networks(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse `TRUSTED_PROXY_IPS`, refusing anything it cannot understand.

    Empty and separator-only input yields an empty list: trusting no proxy is a
    valid configuration. A non-empty entry that does not parse raises, naming
    that entry — not the whole setting, so the message says which one is wrong.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            if "/" in value:
                # An explicit prefix is honoured as written. `strict=False`
                # keeps the shipped tolerance for host bits inside a prefixed
                # entry, which is a separate question from the bare case below.
                networks.append(ipaddress.ip_network(value, strict=False))
            else:
                # A BARE address is ONE host, in BOTH families. Deriving the
                # width from the address — rather than appending a literal —
                # is the whole repair: neither `32` nor `128` is written down
                # here, so neither family's width can drift from the other's.
                networks.append(ipaddress.ip_network(value))
        except ValueError as exc:
            raise TrustedProxyConfigurationError(
                f"{TRUSTED_PROXY_ENV_VAR} entry {value!r} is not an IP address "
                f"or CIDR network. It was previously dropped in silence, which "
                f"left every client address recorded as the proxy's: rate "
                f"limiting, CSRF, the password-reset host and the audit trail "
                f"all read that address. Fix the entry or remove it; setting "
                f"{TRUSTED_PROXY_ENV_VAR} to nothing at all is valid and trusts "
                f"no proxy."
            ) from exc
    return networks


_TRUSTED_PROXY_NETWORKS = _parse_trusted_proxy_networks(
    os.getenv(TRUSTED_PROXY_ENV_VAR, "")
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
