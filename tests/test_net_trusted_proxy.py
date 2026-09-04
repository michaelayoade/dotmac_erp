"""Parity tests for `app/net.py`, written while it is still the production code.

`app/net.py` decides whether a forwarded client address, scheme or host may be
believed. It is used by rate limiting, CSRF, the auth flow, the HR API and the
employee web surface — five production consumers — and it had no tests at all.

They are written HERE, against the shipped implementation, rather than against
the kernel copy that will replace it. That ordering is what makes them parity
tests: they record what production does today, so the extraction can be shown to
preserve it. Tests written against the port would only prove the port agrees
with itself.

## The property that matters most is the closed one

`is_from_trusted_proxy` returns False when NO proxy network is configured. So an
unconfigured deployment believes no forwarded header at all, and the failure mode
of forgetting to configure `TRUSTED_PROXY_IPS` is that the peer address is used —
never that an attacker-supplied `X-Forwarded-For` becomes authoritative. Every
test below that asserts a forwarded value is honoured first establishes that the
peer is inside a configured trusted network; without that step it would be
asserting the default, which is the same shape as a check satisfied by the
absence of the thing it measures.

## `_TRUSTED_PROXY_NETWORKS` is read once, at import

It is a module-level constant computed from `os.getenv` when `app.net` is first
imported, so `monkeypatch.setenv` after import changes nothing and a test that
used it would pass or fail for reasons unrelated to its subject. These tests
patch the parsed constant directly and say so.

Recorded as a finding rather than repaired here: an import-time environment read
means the trusted set cannot be changed without restarting the process, and a
kernel contract that carries this behaviour should take the networks as
configuration rather than reading the environment at import.
"""

from __future__ import annotations

import ipaddress

import pytest
from starlette.datastructures import Headers

from app import net


def _request(
    *, client: str | None = "203.0.113.9", headers: dict[str, str] | None = None
):
    """A Starlette request with a chosen peer address and headers."""
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "scheme": "http",
        "server": ("erp.example", 80),
        "headers": raw,
        "query_string": b"",
        "client": (client, 51234) if client else None,
    }
    request = Request(scope)
    assert isinstance(request.headers, Headers)
    return request


@pytest.fixture()
def trusting(monkeypatch):
    """Trust exactly 203.0.113.0/24, patched at the parsed constant.

    See the module docstring: the environment is read at import, so setting it
    here would be inert.
    """
    monkeypatch.setattr(
        net,
        "_TRUSTED_PROXY_NETWORKS",
        [ipaddress.ip_network("203.0.113.0/24")],
    )


@pytest.fixture()
def trusting_nobody(monkeypatch):
    monkeypatch.setattr(net, "_TRUSTED_PROXY_NETWORKS", [])


# ── the closed default ──────────────────────────────────────────────────────


def test_no_configured_network_trusts_no_proxy(trusting_nobody):
    """The default an unconfigured deployment gets, and it is the safe one."""
    assert net.is_from_trusted_proxy(_request()) is False


def test_an_untrusted_peer_cannot_supply_a_client_address(trusting_nobody):
    """The header is present, well-formed and ignored."""
    request = _request(
        client="198.51.100.7", headers={"x-forwarded-for": "1.2.3.4"}
    )
    assert net.get_client_ip(request) == "198.51.100.7"


def test_an_untrusted_peer_cannot_supply_a_scheme_or_host(trusting_nobody):
    request = _request(
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "attacker.example",
            "host": "erp.example",
        }
    )
    assert net.get_request_scheme(request) == "http"
    assert net.get_request_host(request) == "erp.example"


# ── the open case, and it is only open to a configured peer ─────────────────


def test_a_trusted_peer_is_recognised(trusting):
    assert net.is_from_trusted_proxy(_request(client="203.0.113.9")) is True


def test_a_peer_outside_the_configured_network_is_not(trusting):
    """SENSITIVITY. Without this the fixture could trust everything and every
    assertion above would still pass."""
    assert net.is_from_trusted_proxy(_request(client="198.51.100.7")) is False


def test_a_trusted_peer_supplies_the_client_address(trusting):
    request = _request(
        client="203.0.113.9", headers={"x-forwarded-for": "1.2.3.4"}
    )
    assert net.get_client_ip(request) == "1.2.3.4"


def test_the_first_forwarded_hop_wins(trusting):
    """`X-Forwarded-For` accumulates left to right, so the original client is
    first. Taking the last would take the nearest proxy."""
    request = _request(
        client="203.0.113.9",
        headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9, 203.0.113.10"},
    )
    assert net.get_client_ip(request) == "1.2.3.4"


def test_x_real_ip_is_the_fallback_not_the_preference(trusting):
    """Both present: `X-Forwarded-For` decides. It carries the chain; `X-Real-IP`
    carries one hop's opinion of it."""
    both = _request(
        client="203.0.113.9",
        headers={"x-forwarded-for": "1.2.3.4", "x-real-ip": "9.9.9.9"},
    )
    assert net.get_client_ip(both) == "1.2.3.4"

    only_real = _request(client="203.0.113.9", headers={"x-real-ip": "9.9.9.9"})
    assert net.get_client_ip(only_real) == "9.9.9.9"


def test_a_trusted_peer_supplies_scheme_and_host(trusting):
    request = _request(
        client="203.0.113.9",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "public.example",
            "host": "erp.example",
        },
    )
    assert net.get_request_scheme(request) == "https"
    assert net.get_request_host(request) == "public.example"


# ── degenerate inputs, none of which may open the gate ──────────────────────


def test_a_request_with_no_client_is_not_trusted(trusting):
    """An ASGI scope may carry no client at all. Absent is not trusted."""
    assert net.is_from_trusted_proxy(_request(client=None)) is False
    assert net.get_client_ip(_request(client=None)) == "unknown"


def test_an_unparseable_peer_address_is_not_trusted(trusting):
    assert net.is_from_trusted_proxy(_request(client="not-an-address")) is False


def test_an_empty_forwarded_header_falls_back_to_the_peer(trusting):
    request = _request(client="203.0.113.9", headers={"x-forwarded-for": "  "})
    assert net.get_client_ip(request) == "203.0.113.9"


def test_a_bare_address_is_parsed_as_a_single_host_network():
    """`TRUSTED_PROXY_IPS` accepts `10.0.0.1` as well as `10.0.0.1/32`."""
    parsed = net._parse_trusted_proxy_networks("10.0.0.1, 10.1.0.0/16")
    assert ipaddress.ip_address("10.0.0.1") in parsed[0]
    assert ipaddress.ip_address("10.1.2.3") in parsed[1]
    assert ipaddress.ip_address("10.0.0.2") not in parsed[0]


def test_an_invalid_entry_is_dropped_and_the_rest_survive():
    """Recorded as the shipped behaviour, not endorsed — and the DIRECTION of
    the failure is the part worth stating precisely.

    A typo'd network is silently ignored. That **fails closed for forwarded-
    header trust**: the malformed entry trusts nobody, so no header is honoured
    that would not have been honoured anyway, and no trust is widened.

    What it fails at is **deployment correctness and client provenance**. An
    operator believes they configured a proxy they did not; from then on every
    downstream client address is the proxy's rather than the client's, and
    nothing reports it. Rate limiting, CSRF and the audit trail all read that
    address.

    So the kernel contract must REFUSE a malformed non-empty entry — for
    provenance, not because trust would otherwise leak. And it must keep empty
    configuration VALID, trusting nobody: a deployment that configured nothing
    has made a choice, and one that configured garbage has not.
    """
    parsed = net._parse_trusted_proxy_networks("nonsense, 10.1.0.0/16, ")
    assert len(parsed) == 1
    assert ipaddress.ip_address("10.1.2.3") in parsed[0]
