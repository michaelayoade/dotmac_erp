"""The webhook policy algebra: an organization may narrow, and never widen.

`app/services/finance/automation/webhook_policy.py` composes two layers — a
platform CEILING and an optional organization RESTRICTION — and the whole
point of the module is that the composition is a conjunction. These are the
proofs of the six laws the composition claims.

They are deliberately written against the OUTPUTS (`permits_host`,
`allow_insecure`, `allow_localhost`, `timeout_seconds`), not against the
internals, so a future rewrite of the matching that preserved the algebra
keeps passing and one that quietly introduced a union does not.

L1 and L6 are proved over a generated corpus rather than three hand-picked
strings: the interesting failures in host matching are the near-misses
(`evil-acme.com`, `acme.com.evil.net`), the case and trailing-dot forms, and
the IP literals — none of which a happy-path example ever reaches.
"""

from __future__ import annotations

import itertools

import pytest

from app.services.finance.automation.webhook_policy import (
    DEFAULT_MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    NO_NARROWING,
    EffectiveWebhookPolicy,
    TenantWebhookRestriction,
    WebhookCeiling,
    compose_webhook_policy,
    narrow_only,
    read_platform_webhook_ceiling,
)

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

# Hosts chosen so that every interesting confusion is represented: the exact
# match, a legitimate subdomain, the two classic suffix near-misses, an
# unrelated host, the case and trailing-dot forms of a match, a bare IP and a
# loopback name.
HOST_CORPUS = (
    "acme.com",
    "api.acme.com",
    "deep.api.acme.com",
    "evil-acme.com",  # shares a suffix textually, is not a subdomain
    "acme.com.evil.net",  # ends with the domain as a LABEL, not as a suffix
    "notacme.com",
    "ACME.COM",
    "api.acme.com.",  # FQDN trailing dot
    "10.0.0.7",
    "localhost",
    "internal",
)

CEILING_CORPUS = (
    WebhookCeiling(),  # unconfigured: deny-all
    WebhookCeiling(allowed_hosts=frozenset({"acme.com"})),
    WebhookCeiling(allowed_domains=frozenset({"acme.com"})),
    WebhookCeiling(
        allowed_hosts=frozenset({"other.example"}),
        allowed_domains=frozenset({"acme.com"}),
    ),
    WebhookCeiling(
        allowed_domains=frozenset({"acme.com"}),
        allow_insecure=True,
        allow_localhost=True,
        max_timeout_seconds=30.0,
    ),
)

RESTRICTION_CORPUS = (
    None,
    NO_NARROWING,
    TenantWebhookRestriction(allowed_hosts=frozenset({"api.acme.com"})),
    TenantWebhookRestriction(allowed_domains=frozenset({"acme.com"})),
    TenantWebhookRestriction(allowed_hosts=frozenset({"anything.example"})),
    TenantWebhookRestriction(allow_insecure=True, allow_localhost=True),
    TenantWebhookRestriction(allow_insecure=False, allow_localhost=False),
    TenantWebhookRestriction(timeout_seconds=1000.0),
    TenantWebhookRestriction(timeout_seconds=0.001),
    TenantWebhookRestriction(
        allowed_hosts=frozenset({"api.acme.com"}),
        allow_insecure=True,
        timeout_seconds=5.0,
    ),
)

PAIRS = tuple(itertools.product(CEILING_CORPUS, RESTRICTION_CORPUS))
PAIR_IDS = [
    f"ceiling{i}-restriction{j}"
    for i in range(len(CEILING_CORPUS))
    for j in range(len(RESTRICTION_CORPUS))
]


# ---------------------------------------------------------------------------
# L1 — ceiling dominance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("ceiling", "restriction"), PAIRS, ids=PAIR_IDS)
@pytest.mark.parametrize("host", HOST_CORPUS)
def test_tenant_restriction_never_widens_the_ceiling(ceiling, restriction, host):
    """L1: whatever the organization asks for, the ceiling still bounds it.

    The implication, not the equality: a restriction may deny a host the
    ceiling allows (that is narrowing), but no restriction may allow a host the
    ceiling denies.
    """
    policy = compose_webhook_policy(ceiling, restriction)

    if policy.permits_host(host):
        assert ceiling.permits_host(host), (
            f"{host!r} was permitted by the composed policy but not by the "
            f"platform ceiling {ceiling!r} — the organization restriction "
            f"{restriction!r} widened it, which is the one thing this algebra "
            "exists to prevent."
        )
    assert policy.allow_insecure <= ceiling.allow_insecure
    assert policy.allow_localhost <= ceiling.allow_localhost
    assert policy.timeout_seconds <= ceiling.max_timeout_seconds


def test_an_organization_cannot_grant_itself_a_host_by_listing_it():
    """The sharp case of L1, stated on its own so a regression names itself.

    An organization writes its own allowlist containing a host the platform
    never allowed. Under a union — or under "the tenant row overrides the
    platform row", which is what ERP's ordinary settings resolver would do
    with these keys — this is permitted. Under conjunction it is not.
    """
    ceiling = WebhookCeiling(allowed_hosts=frozenset({"selfcare.dotmac.io"}))
    restriction = TenantWebhookRestriction(
        allowed_hosts=frozenset({"attacker.example", "selfcare.dotmac.io"})
    )
    policy = compose_webhook_policy(ceiling, restriction)

    assert policy.permits_host("selfcare.dotmac.io") is True
    assert policy.permits_host("attacker.example") is False


# ---------------------------------------------------------------------------
# L2 — the absent restriction is the identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ceiling", CEILING_CORPUS)
@pytest.mark.parametrize("host", HOST_CORPUS)
def test_absent_restriction_is_the_identity(ceiling, host):
    """L2: `compose(C, None) == compose(C, NO_NARROWING) == C`, all five outputs."""
    from_none = compose_webhook_policy(ceiling, None)
    from_empty = compose_webhook_policy(ceiling, NO_NARROWING)

    assert from_none.permits_host(host) == ceiling.permits_host(host)
    assert from_empty.permits_host(host) == ceiling.permits_host(host)
    assert from_none.allow_insecure == ceiling.allow_insecure
    assert from_none.allow_localhost == ceiling.allow_localhost
    assert from_none.timeout_seconds == from_empty.timeout_seconds


def test_an_empty_tenant_list_is_absence_not_an_allowlist_of_nothing():
    """Condition 3: "empty" has exactly one meaning, and it is stated.

    An organization row holding "" — which is what the settings form produces
    for a cleared text field — normalizes to an empty frozenset, identical to
    having no row at all. Both mean "this organization stated no narrowing".

    Neither means "allow nothing" (which would break every organization that
    never opted in) and neither means "inherit the ceiling's lists as this
    organization's own" (the restriction's own lists stay EMPTY after
    composition — nothing is ever copied down from the ceiling).
    """
    ceiling = WebhookCeiling(allowed_domains=frozenset({"acme.com"}))
    policy = compose_webhook_policy(ceiling, TenantWebhookRestriction())

    assert policy.restriction.narrows_hosts is False
    assert policy.permits_host("api.acme.com") is True  # not "allow nothing"
    assert policy.restriction.allowed_hosts == frozenset()  # not inherited
    assert policy.restriction.allowed_domains == frozenset()


# ---------------------------------------------------------------------------
# L3 — the booleans, both directions
# ---------------------------------------------------------------------------


BOOLEAN_TABLE = (
    # (ceiling, tenant, effective, why)
    (False, None, False, "no opinion; the ceiling holds"),
    (False, False, False, "the organization agrees with the ceiling"),
    (False, True, False, "THE REFUSAL: an organization may not turn it on"),
    (True, None, True, "no opinion; the ceiling holds"),
    (True, False, False, "an organization may always turn it off"),
    (True, True, True, "both permit"),
)


@pytest.mark.parametrize(("ceiling", "tenant", "expected", "why"), BOOLEAN_TABLE)
def test_tenant_may_turn_a_boolean_off_but_never_on(ceiling, tenant, expected, why):
    """L3, on `narrow_only` itself — the function that states the direction."""
    assert narrow_only(ceiling, tenant, control="test") is expected, why


@pytest.mark.parametrize(("ceiling", "tenant", "expected", "why"), BOOLEAN_TABLE)
def test_the_boolean_table_holds_through_the_composition(
    ceiling, tenant, expected, why
):
    """L3 again, through the public surface, for both flags.

    Proving it on `narrow_only` alone would not catch a composition that
    stopped calling it, or called it with the arguments the wrong way round.
    """
    policy = compose_webhook_policy(
        WebhookCeiling(allow_insecure=ceiling, allow_localhost=ceiling),
        TenantWebhookRestriction(allow_insecure=tenant, allow_localhost=tenant),
    )
    assert policy.allow_insecure is expected, why
    assert policy.allow_localhost is expected, why


def test_the_law_1_corpus_is_not_vacuous():
    """L1 proves nothing over a corpus in which no restriction ever bites.

    A generated corpus that only ever contained no-opinion restrictions would
    make `test_tenant_restriction_never_widens_the_ceiling` pass by never
    exercising the composition. So: assert that the corpus contains at least
    one pair where the organization NARROWS (a host the ceiling permits is
    refused) and at least one where it ATTEMPTS TO WIDEN (a host only the
    organization lists is still refused). Both are the cases the law is about.
    """
    narrowed: list[tuple[object, object, str]] = []
    widening_refused: list[tuple[object, object, str]] = []

    for ceiling, restriction in PAIRS:
        policy = compose_webhook_policy(ceiling, restriction)
        for host in HOST_CORPUS:
            if ceiling.permits_host(host) and not policy.permits_host(host):
                narrowed.append((ceiling, restriction, host))
            if (
                restriction is not None
                and restriction.permits_host(host)
                and restriction.narrows_hosts
                and not ceiling.permits_host(host)
            ):
                assert policy.permits_host(host) is False
                widening_refused.append((ceiling, restriction, host))

    assert narrowed, "no pair in the corpus narrows; L1 would hold vacuously"
    assert widening_refused, (
        "no pair in the corpus attempts to widen; L1 would never be tested "
        "against the case it exists for"
    )


# ---------------------------------------------------------------------------
# L4 — idempotence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("ceiling", "restriction"), PAIRS, ids=PAIR_IDS)
@pytest.mark.parametrize("host", HOST_CORPUS)
def test_composition_is_idempotent(ceiling, restriction, host):
    """L4: applying the same restriction to the result changes nothing.

    A composition that leaked state — or that accumulated a widening a little
    at a time — would drift on the second application.
    """
    once = compose_webhook_policy(ceiling, restriction)
    twice = compose_webhook_policy(once.as_ceiling(), restriction)

    assert twice.permits_host(host) == once.permits_host(host)
    assert twice.allow_insecure == once.allow_insecure
    assert twice.allow_localhost == once.allow_localhost
    assert twice.timeout_seconds == once.timeout_seconds


# ---------------------------------------------------------------------------
# L5 — the timeout clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("ceiling", "restriction"), PAIRS, ids=PAIR_IDS)
def test_timeout_is_clamped_to_the_platform_maximum(ceiling, restriction):
    """L5: the effective timeout is inside [MIN, ceiling.max], always."""
    policy = compose_webhook_policy(ceiling, restriction)

    assert policy.timeout_seconds <= ceiling.max_timeout_seconds
    assert policy.timeout_seconds >= MIN_TIMEOUT_SECONDS


def test_the_second_timeout_channel_uses_the_same_clamp():
    """`clamp_timeout` is public because a per-hook timeout bypasses the setting.

    `ServiceHook.handler_config["timeout_seconds"]` is organization-owned and
    validated only by a Pydantic bound. A ceiling that clamped only the
    settings channel would be bypassed by choosing the other one, so the clamp
    is exposed for that caller to use — and answers identically.
    """
    policy = compose_webhook_policy(WebhookCeiling(max_timeout_seconds=15.0))

    assert policy.clamp_timeout(600.0) == 15.0
    assert policy.clamp_timeout(0.0) == MIN_TIMEOUT_SECONDS
    assert policy.clamp_timeout(9.0) == 9.0


def test_an_organization_timeout_below_the_maximum_is_honoured():
    """Narrowing a timeout is a legitimate organization choice, not a widening."""
    policy = compose_webhook_policy(
        WebhookCeiling(max_timeout_seconds=300.0),
        TenantWebhookRestriction(timeout_seconds=7.0),
    )
    assert policy.timeout_seconds == 7.0


def test_a_ceiling_read_from_nothing_still_bounds_the_timeout():
    """The default maximum is a real bound, not `inf`."""
    policy = compose_webhook_policy(
        WebhookCeiling(), TenantWebhookRestriction(timeout_seconds=10_000.0)
    )
    assert policy.timeout_seconds == DEFAULT_MAX_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# L6 — default deny survives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("restriction", RESTRICTION_CORPUS)
@pytest.mark.parametrize("host", HOST_CORPUS)
def test_an_unconfigured_ceiling_denies_every_host(restriction, host):
    """L6: no restriction can make an unconfigured deployment send anything.

    This is the case that matters most in practice: a deployment that has
    never set `webhook_allowed_hosts` sends no webhooks at all, and an
    organization cannot opt itself back in by writing its own list.
    """
    unconfigured = WebhookCeiling()
    assert unconfigured.is_configured is False

    policy = compose_webhook_policy(unconfigured, restriction)
    assert policy.permits_host(host) is False


# ---------------------------------------------------------------------------
# Matching details the laws above rely on
# ---------------------------------------------------------------------------


def test_domain_matching_is_by_label_not_by_suffix():
    """`evil-acme.com` is not a subdomain of `acme.com`, and neither is a prefix."""
    policy = compose_webhook_policy(
        WebhookCeiling(allowed_domains=frozenset({"acme.com"}))
    )

    assert policy.permits_host("acme.com") is True
    assert policy.permits_host("api.acme.com") is True
    assert policy.permits_host("evil-acme.com") is False
    assert policy.permits_host("acme.com.evil.net") is False
    assert policy.permits_host("notacme.com") is False


def test_case_and_trailing_dot_are_the_same_target():
    """Otherwise the allowlist is evaded by typing the host differently."""
    policy = compose_webhook_policy(
        WebhookCeiling(allowed_hosts=frozenset({"api.acme.com"}))
    )

    assert policy.permits_host("API.ACME.COM") is True
    assert policy.permits_host("api.acme.com.") is True


def test_the_policy_object_carries_both_layers():
    """The composed object keeps its inputs, so a refusal can be explained."""
    ceiling = WebhookCeiling(allowed_hosts=frozenset({"acme.com"}))
    restriction = TenantWebhookRestriction(allowed_hosts=frozenset({"acme.com"}))
    policy = compose_webhook_policy(ceiling, restriction)

    assert isinstance(policy, EffectiveWebhookPolicy)
    assert policy.ceiling is ceiling
    assert policy.restriction is restriction


# ---------------------------------------------------------------------------
# The ceiling's "is it configured?" question
#
# Moved here from `tests/services/test_workflow_engine.py::
# TestWebhookAllowlist`, where it was asked as
# `workflow_module.webhook_allowlist_configured()` — a function that answered
# from whatever scope its caller happened to be in. The question belongs to the
# ceiling, so it is asked of the ceiling.
# ---------------------------------------------------------------------------


class TestTheCeilingIsConfigured:
    def test_an_unconfigured_ceiling_reports_itself_unconfigured(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("WEBHOOK_ALLOWED_DOMAINS", raising=False)

        assert read_platform_webhook_ceiling(None).is_configured is False

    def test_a_domain_list_alone_configures_the_ceiling(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_ALLOWED_HOSTS", raising=False)
        monkeypatch.setenv("WEBHOOK_ALLOWED_DOMAINS", "example.com")

        ceiling = read_platform_webhook_ceiling(None)

        assert ceiling.is_configured is True
        assert ceiling.permits_host("example.com") is True
        assert ceiling.permits_host("api.example.com") is True
        assert ceiling.permits_host("api.other.com") is False
