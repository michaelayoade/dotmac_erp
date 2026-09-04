"""The per-request observability context: always written, always cleared.

## The defect these tests were written against

`ObservabilityMiddleware` called `actor_id_var.set()` only ``if actor_id:`` and
never reset any variable it set. An anonymous request therefore did not clear the
previous request's actor — it declined to write, and a reader saw whoever was
authenticated before. Every audit event, log line and field-tracker attribution
taken during that request could be attributed to the wrong person.

## Two proofs, and neither substitutes for the other

**Sequential anonymous-after-authenticated** catches the `if actor_id:` bug: an
authenticated request followed by an anonymous one, asserting the second does not
see the first's actor. A concurrency test would NOT reliably catch this, because
the two requests might never share a context.

**Concurrent isolation** catches token mishandling: resetting the wrong token, or
resetting to a default instead of the token's own value. It needs REAL
concurrency — two requests genuinely in flight at once, each parked mid-request
while the other runs. A sequential fixture calling the middleware twice proves
only that the second call overwrote the first, which is true even of the broken
version.

Both are here because they fail for different reasons, and a suite carrying only
one of them would have passed against the code as it shipped.

## Measured, 2026-09-04: eight of these twelve fail against the unrepaired code

Run against a variant with the same module surface and the old behaviour, eight
fail and FOUR PASS. Which four is the useful part, and each is paired with one
that discriminates:

* `test_concurrent_requests_cannot_inherit_each_others_actor` passes against the
  BROKEN code. `BaseHTTPMiddleware` runs each request in its own task and
  therefore its own context, so concurrent isolation held even with no resets at
  all. **The concurrency test does not catch this bug.** It is kept because it
  catches a different one — token mishandling — and because that isolation is a
  property of the current middleware base class rather than of this code, so it
  would stop holding the day someone changes the base class. It is a guard on an
  assumption, not on the defect.
* `test_an_untrusted_forwarded_address_cannot_become_the_client_ip` passes,
  because `request.client.host` happens to give the right answer when nothing is
  trusted. Only the trusted case shows the resolver was never reached.

* `test_a_trusted_peer_may_supply_the_request_id` passes, because the old code
  accepted ANY inbound id including a trusted one. Only its untrusted twin
  discriminates.

A suite of only those four would have been fully green against a live
cross-request identity leak.
"""

from __future__ import annotations

import anyio
import pytest
from starlette.responses import Response

from app import net, observability
from app.observability import (
    ANONYMOUS_ACTOR,
    ObservabilityMiddleware,
    actor_id_var,
    ip_address_var,
    request_id_var,
)


def _request(*, client="198.51.100.7", headers=None, actor=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/thing",
        "scheme": "http",
        "server": ("erp.example", 80),
        "headers": raw,
        "query_string": b"",
        "client": (client, 4321) if client else None,
        "state": {},
    }
    request = Request(scope)
    if actor is not None:
        request.state.actor_id = actor
    return request


@pytest.fixture()
def middleware():
    return ObservabilityMiddleware(app=None)


@pytest.fixture()
def trusting(monkeypatch):
    """Trust 203.0.113.0/24. Patched at the parsed constant — `app.net` reads the
    environment once, at import."""
    import ipaddress

    monkeypatch.setattr(
        net, "_TRUSTED_PROXY_NETWORKS", [ipaddress.ip_network("203.0.113.0/24")]
    )


@pytest.fixture()
def trusting_nobody(monkeypatch):
    monkeypatch.setattr(net, "_TRUSTED_PROXY_NETWORKS", [])


async def _run(middleware, request, *, seen=None):
    """Drive one request and capture what the context held INSIDE it."""

    async def call_next(_request):
        if seen is not None:
            seen.append(
                {
                    "actor": actor_id_var.get(),
                    "request_id": request_id_var.get(),
                    "ip": ip_address_var.get(),
                }
            )
        return Response(status_code=200)

    return await middleware.dispatch(request, call_next)


# ── the repair: every variable, every request ───────────────────────────────


@pytest.mark.asyncio
async def test_an_anonymous_request_writes_anonymous(middleware, trusting_nobody):
    """Not an empty string, and not silence. `unset` and `anonymous` are
    different facts and a default cannot tell them apart at the read site."""
    seen: list[dict] = []
    await _run(middleware, _request(), seen=seen)
    assert seen[0]["actor"] == ANONYMOUS_ACTOR


@pytest.mark.asyncio
async def test_an_anonymous_request_after_an_authenticated_one_is_anonymous(
    middleware, trusting_nobody
):
    """THE REGRESSION. This is the bug as it shipped: the second request saw
    `alice`, because `if actor_id:` declined to write and nothing had reset."""
    first: list[dict] = []
    await _run(middleware, _request(actor="alice"), seen=first)
    assert first[0]["actor"] == "alice"

    second: list[dict] = []
    await _run(middleware, _request(), seen=second)
    assert second[0]["actor"] == ANONYMOUS_ACTOR, (
        "an anonymous request inherited the previous request's actor"
    )


@pytest.mark.asyncio
async def test_the_context_is_restored_to_the_outer_value_after_the_request(
    middleware, trusting_nobody
):
    """`reset(token)` restores the value from BEFORE this middleware's `set()`.

    Not a blunt clear to empty, and the difference is load-bearing. Forcing empty
    values would discard whatever an outer scope had established — and it is
    precisely token semantics that make a LATER `set()` by a dependency still get
    discarded at request end, which is the property the repair depends on.

    So this seeds a known outer value and asserts restoration to THAT.

    CI found the earlier version of this test, not the implementation: the suite
    carried a leaked actor UUID from a previous test, and a postcondition of
    `== ""` failed against correct code. The leak itself is worth noting — it is
    this same defect class inside the test suite, because `app/web/deps.py` and
    `auth_dependencies.py` both `set()` the actor without resetting.
    """
    outer = actor_id_var.set("outer-actor")
    try:
        await _run(middleware, _request(actor="alice"))
        assert actor_id_var.get() == "outer-actor", (
            "the middleware clobbered an outer context instead of restoring it"
        )
    finally:
        actor_id_var.reset(outer)


@pytest.mark.asyncio
async def test_the_request_actor_does_not_survive_the_request(
    middleware, trusting_nobody
):
    """The half the restoration assertion does not make on its own.

    Restoring to an outer value and never writing the request's actor at all
    would both satisfy the test above. This one requires that `alice` was
    genuinely visible INSIDE the request and genuinely gone after it.
    """
    seen: list[dict] = []
    outer = actor_id_var.set("outer-actor")
    try:
        await _run(middleware, _request(actor="alice"), seen=seen)
        assert seen[0]["actor"] == "alice"
        assert actor_id_var.get() != "alice"
    finally:
        actor_id_var.reset(outer)


@pytest.mark.asyncio
async def test_the_context_is_restored_when_the_request_raises(
    middleware, trusting_nobody
):
    """The path where inheritance is most likely, because the failing request is
    the one that leaves state behind."""

    async def exploding(_request):
        raise RuntimeError("boom")

    outer = actor_id_var.set("outer-actor")
    try:
        with pytest.raises(RuntimeError):
            await middleware.dispatch(_request(actor="alice"), exploding)
        assert actor_id_var.get() == "outer-actor"
    finally:
        actor_id_var.reset(outer)


@pytest.mark.asyncio
async def test_a_later_set_by_a_dependency_is_still_discarded(
    middleware, trusting_nobody
):
    """Why token reset rather than a forced clear, stated as a test.

    `app/web/deps.py` and `auth_dependencies.py` both call `actor_id_var.set()`
    during dependency resolution, AFTER this middleware ran. `reset(token)`
    restores the value from before the middleware's own `set()` and discards
    every intervening one, so the context is torn down whoever wrote to it.

    A blunt clear-to-empty would also discard it — and would additionally
    discard the outer value, which is the behaviour the test above forbids. Both
    tests together pin the semantics; either alone admits the wrong fix.
    """
    outer = actor_id_var.set("outer-actor")
    try:

        async def sets_actor_midway(_request):
            actor_id_var.set("set-by-a-dependency")
            return Response(status_code=200)

        await middleware.dispatch(_request(), sets_actor_midway)
        assert actor_id_var.get() == "outer-actor"
    finally:
        actor_id_var.reset(outer)


# ── concurrency, and it has to be real ──────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_inherit_each_others_actor(middleware):
    """REAL concurrency: both requests are in flight at once.

    Each parks inside `call_next` until the other has also entered, so the two
    contexts genuinely overlap. A sequential fixture would prove only that the
    second call overwrote the first — which the BROKEN version also did.
    """
    both_entered = anyio.Event()
    entered = []
    observed: dict[str, str] = {}

    async def park(name):
        async def call_next(_request):
            entered.append(name)
            if len(entered) == 2:
                both_entered.set()
            await both_entered.wait()
            # Read AFTER the other request has set its own values.
            observed[name] = actor_id_var.get()
            return Response(status_code=200)

        return call_next

    async def drive(name, actor):
        await middleware.dispatch(_request(actor=actor), await park(name))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(drive, "alice", "alice")
        tasks.start_soon(drive, "bob", "bob")

    assert observed == {"alice": "alice", "bob": "bob"}, (
        "a concurrent request saw another's actor: contexts are shared"
    )


# ── untrusted input cannot become authoritative ─────────────────────────────


@pytest.mark.asyncio
async def test_an_untrusted_peer_cannot_choose_the_request_id(
    middleware, trusting_nobody
):
    """Any caller could previously pick the correlation identity its request was
    logged under, and collide it with somebody else's deliberately."""
    seen: list[dict] = []
    await _run(
        middleware,
        _request(headers={"x-request-id": "attacker-chosen"}),
        seen=seen,
    )
    assert seen[0]["request_id"] != "attacker-chosen"
    assert seen[0]["request_id"]


@pytest.mark.asyncio
async def test_a_trusted_peer_may_supply_the_request_id(middleware, trusting):
    """The other direction. Without this the assertion above would pass against
    a middleware that ignored the header unconditionally, which is a different
    behaviour that happens to satisfy the same test."""
    seen: list[dict] = []
    await _run(
        middleware,
        _request(client="203.0.113.9", headers={"x-request-id": "edge-abc"}),
        seen=seen,
    )
    assert seen[0]["request_id"] == "edge-abc"


@pytest.mark.asyncio
async def test_an_untrusted_forwarded_address_cannot_become_the_client_ip(
    middleware, trusting_nobody
):
    seen: list[dict] = []
    await _run(
        middleware,
        _request(client="198.51.100.7", headers={"x-forwarded-for": "1.2.3.4"}),
        seen=seen,
    )
    assert seen[0]["ip"] == "198.51.100.7"


@pytest.mark.asyncio
async def test_a_trusted_forwarded_address_is_used(middleware, trusting):
    """The resolver is reached at all — this middleware used to read
    `request.client.host` directly and bypass it."""
    seen: list[dict] = []
    await _run(
        middleware,
        _request(client="203.0.113.9", headers={"x-forwarded-for": "1.2.3.4"}),
        seen=seen,
    )
    assert seen[0]["ip"] == "1.2.3.4"


def test_the_middleware_does_not_read_the_peer_address_directly():
    """STRUCTURAL. The behavioural tests above would still pass if someone
    reintroduced `request.client.host` for a case they thought was safe."""
    import ast
    import inspect

    # AST, not a text scan. The class docstring names `request.client` on
    # purpose — it explains why the middleware must not read it — and a
    # substring check refuses its own rationale. That is the third time in this
    # programme a guard has matched the prose that justifies it; scan the code.
    tree = ast.parse(inspect.getsource(observability.ObservabilityMiddleware))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "client"
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    ]
    assert not reads, (
        "the context creator must reach the trusted-origin resolver, not the "
        "peer address; ERP already had the resolver and bypassed it once"
    )
