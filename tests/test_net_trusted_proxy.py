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
never that an attacker-supplied `X-Forwarded-For` becomes authoritative.

That was true of an ABSENT or MALFORMED entry and FALSE of a bare IPv6 one,
which parsed cleanly into a `/32` supernet. Both directions are now closed; the
last section of this docstring records how. Every
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

## What HAS changed since these were first written

The other finding these tests recorded — that an invalid entry was silently
dropped — is repaired, and the test that recorded it is replaced by the pair at
the foot of this file. A malformed non-empty entry now refuses at import.
`test_an_invalid_entry_is_dropped_and_the_rest_survive` is deliberately gone
rather than inverted in place: it asserted the defect, and a reader finding both
names in the history should see that the behaviour was replaced, not that a
tolerance was widened.

## The second defect, which failed OPEN, and why it survived a test named for it

A BARE IPv6 address was parsed as `<address>/32` by the same rule that makes a
bare IPv4 address a `/32` host route, so `::1` became `::/32` — 7.9e28 addresses
— and it parsed cleanly, so the refusal below never saw it. That direction did
not fail closed. It failed OPEN, silently, on the most natural thing an operator
would write for a loopback proxy on a dual-stack host.

A bare address is now one host in both families, derived from the address rather
than appended, so the two cannot drift apart again. Explicit IPv6 prefixes are
still honoured as written.

The part worth carrying forward is HOW it survived.
`test_a_bare_address_is_parsed_as_a_single_host_network` already existed and
already claimed the family-neutral property in its name — while asserting IPv4
only. A reader checking whether bare addresses were handled found a test with
the right name and stopped. The cases are now as wide as the name, and
`test_a_bare_ipv6_address_cannot_widen_into_a_supernet` states the security
direction separately so it cannot be narrowed again without the name changing.

## What is still open, and it is NOT a typo case

Nothing here tests that the declared plan reaches the process.
`ProductDeploymentSpec.v1`'s `[ingress] trusted_proxies` in
`deploy/product.toml` declares `["172.16.0.0/12", "127.0.0.1"]`, the live
`docker-compose.yml:69` sets it, and `deploy/rendered/docker-compose.yml` does
not carry `TRUSTED_PROXY_IPS` at all — so the rendered path starts the app with
the variable unset and reaches the lost-provenance state with NO typo, from a
plan that reads as correct. The refusal below cannot catch that, because unset
is empty and empty is valid by design. Recorded as an unmonitored region in
`docs/inventories/2026-09-04-erp-proxy-trust-readback-hole.md`; no guard here
claims to cover it.
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
    request = _request(client="198.51.100.7", headers={"x-forwarded-for": "1.2.3.4"})
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
    request = _request(client="203.0.113.9", headers={"x-forwarded-for": "1.2.3.4"})
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
    """`TRUSTED_PROXY_IPS` accepts `10.0.0.1` as well as `10.0.0.1/32` — and the
    same is now true of IPv6, which it was NOT.

    THE CASES ARE NOW AS WIDE AS THE NAME. This test asserted only IPv4 while
    claiming a family-neutral property, and that gap is exactly how the defect
    below survived: a reader checking whether bare addresses were handled found
    a test with the right name and stopped.
    """
    v4 = net._parse_trusted_proxy_networks("10.0.0.1, 10.1.0.0/16")
    assert ipaddress.ip_address("10.0.0.1") in v4[0]
    assert ipaddress.ip_address("10.1.2.3") in v4[1]
    assert ipaddress.ip_address("10.0.0.2") not in v4[0]

    v6 = net._parse_trusted_proxy_networks("::1, 2001:db8::1, 2001:db8::/32")
    assert [str(n) for n in v6] == ["::1/128", "2001:db8::1/128", "2001:db8::/32"]
    assert ipaddress.ip_address("::1") in v6[0]
    assert ipaddress.ip_address("2001:db8::1") in v6[1]
    # An EXPLICIT prefix is still honoured as written. Without this the fix
    # could have been "refuse every IPv6 entry", which satisfies the host-route
    # assertions above and quietly removes a legitimate configuration.
    assert ipaddress.ip_address("2001:db8:ffff::9") in v6[2]


def test_a_bare_ipv6_address_cannot_widen_into_a_supernet():
    """THE FAIL-OPEN REPAIR, and it is the opposite direction from the refusal.

    The parsing rule appended `/32` to any entry without a prefix, whatever the
    family. A bare `::1` therefore became `::/32` — 79 228 162 514 264 337 593
    543 950 336 addresses — and `strict=False` masked the host bits without
    complaint. It parsed CLEANLY, so the malformed-entry refusal above never saw
    it, and every peer inside `::/32` had its `X-Forwarded-For` believed.

    `::1` is not a contrived entry: it is the natural loopback proxy on a
    dual-stack host, and `docker-compose.yml:59` already publishes the `app`
    service with `host_ip: "::1"`.

    SENSITIVITY. Each address below sits INSIDE the old `::/32` and OUTSIDE the
    correct `::1/128`, so each assertion fails against the previous rule. The
    first assertion — that the network is `::1/128` — would fail on its own, but
    only these show what the difference actually admitted.
    """
    parsed = net._parse_trusted_proxy_networks("::1")
    assert str(parsed[0]) == "::1/128"
    assert parsed[0].num_addresses == 1

    for trusted_by_the_old_rule in ("::2", "0:0:1::", "::ffff:1"):
        assert ipaddress.ip_address(trusted_by_the_old_rule) not in parsed[0], (
            f"{trusted_by_the_old_rule} is inside ::/32; a bare ::1 must be a "
            f"host route, not a supernet"
        )


def test_a_trusted_ipv6_peer_is_recognised_through_the_request_path(monkeypatch):
    """The host route reaches the actual decision, not just the parser.

    `_parse_trusted_proxy_networks` returning `::1/128` proves nothing about
    `is_from_trusted_proxy` unless the two are wired together. This drives the
    real request path with the parsed value, and checks BOTH directions: the
    host itself is trusted, and its neighbour — trusted under the old `::/32` —
    is not.
    """
    monkeypatch.setattr(
        net, "_TRUSTED_PROXY_NETWORKS", net._parse_trusted_proxy_networks("::1")
    )
    assert net.is_from_trusted_proxy(_request(client="::1")) is True
    assert net.is_from_trusted_proxy(_request(client="::2")) is False
    assert (
        net.get_client_ip(
            _request(client="::1", headers={"x-forwarded-for": "1.2.3.4"})
        )
        == "1.2.3.4"
    )
    assert (
        net.get_client_ip(
            _request(client="::2", headers={"x-forwarded-for": "1.2.3.4"})
        )
        == "::2"
    )


# ── a malformed entry refuses, and an absent one does not ───────────────────
#
# SENSITIVITY, both directions, and the pair is the proof.
#
# The DEFECT this guard targets is planted below in
# `test_a_malformed_entry_refuses_loudly`: an entry that carries characters and
# does not parse. Before the repair `_parse_trusted_proxy_networks` caught the
# `ValueError` and `continue`d, so it returned a list and NEVER raised — the
# refusal could not be observed and `TrustedProxyConfigurationError` did not
# exist in `app.net` at all, so the import at the top of this module would fail
# first. Both of these tests therefore fail against the shipped code, for two
# independent reasons.
#
# The NEAR-MISS is planted in `test_configuring_no_proxy_at_all_stays_valid`:
# empty and separator-only input. A guard that refused every input it could not
# turn into a network would fire there too, and would refuse the default
# configuration of every deployment that has not set the variable. It must not
# be named. Keeping both is what distinguishes "configured garbage" from
# "configured nothing", which are different acts with different correct
# answers.


def test_a_malformed_entry_refuses_loudly():
    """THE REPAIR. A typo'd network stops the process instead of vanishing.

    The shipped behaviour dropped it in silence, and the DIRECTION of that
    failure is the part worth stating precisely.

    A dropped entry **fails closed for forwarded-header trust**: the malformed
    entry trusts nobody, so no header is honoured that would not have been
    honoured anyway, and no trust is widened.

    What it fails at is **deployment correctness and client provenance**. The
    operator believes they configured a proxy they did not; from then on every
    downstream client address is the proxy's rather than the client's, and
    nothing reports it. Rate limiting (`app/middleware/rate_limit.py`), CSRF
    (`app/web/csrf.py`), the password-reset host (`app/api/auth_flow.py`) and
    the audit trail (`app/services/audit_listener.py`) all read that address.

    So this refuses for PROVENANCE, not because trust would otherwise leak.
    """
    with pytest.raises(net.TrustedProxyConfigurationError):
        net._parse_trusted_proxy_networks("nonsense, 10.1.0.0/16")


def test_a_malformed_entry_refuses_even_when_it_is_the_only_one():
    """Not only when a good entry sits beside it.

    Without this, an implementation that refused only a PARTIALLY parseable
    list — and quietly returned `[]` for a wholly malformed one — would satisfy
    the test above while leaving the worst case silent.
    """
    with pytest.raises(net.TrustedProxyConfigurationError):
        net._parse_trusted_proxy_networks("172.16.0.0/99")


def test_the_refusal_names_the_offending_entry():
    """The message has to be actionable at 3am, and it must name the ENTRY.

    Naming the whole setting would say only that something in a comma-separated
    list is wrong. It deliberately does not echo the full raw value: the entry
    is enough to fix it.
    """
    with pytest.raises(net.TrustedProxyConfigurationError) as raised:
        net._parse_trusted_proxy_networks("10.0.0.0/8, 172.16.0.0/99, 127.0.0.1")
    message = str(raised.value)
    assert "172.16.0.0/99" in message
    assert net.TRUSTED_PROXY_ENV_VAR in message


def test_configuring_no_proxy_at_all_stays_valid():
    """THE NEAR-MISS, and it must NOT be named.

    Configuring nothing and configuring garbage are different acts. Every
    deployment that has not set `TRUSTED_PROXY_IPS` reaches this path on every
    boot, so a guard that refused unparseable input indiscriminately would
    refuse them all. Separator-only and whitespace-only input are the same act
    written untidily.
    """
    assert net._parse_trusted_proxy_networks("") == []
    assert net._parse_trusted_proxy_networks("   ") == []
    assert net._parse_trusted_proxy_networks(" , ,, ") == []


def test_the_committed_production_value_still_parses():
    """The regression the refusal could plausibly cause: refusing a deployment
    that was correct all along.

    This is the exact string `docker-compose.yml` sets for the production `app`
    service and the exact pair `deploy/product.toml`'s `[ingress]
    trusted_proxies` declares. If the two ever disagree this test is not what
    catches it — see
    `docs/inventories/2026-09-04-erp-proxy-trust-readback-hole.md`, which
    records that nothing compares them and that the RENDERED compose does not
    carry the variable at all.
    """
    parsed = net._parse_trusted_proxy_networks("172.16.0.0/12,127.0.0.1")
    assert [str(n) for n in parsed] == ["172.16.0.0/12", "127.0.0.1/32"]
