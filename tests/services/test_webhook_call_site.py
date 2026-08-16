"""The gate asks the CONJUNCTION, and asking it makes an organization reach less.

`app/services/finance/automation/workflow.py::_validate_webhook_target` is the
one host/scheme/loopback gate for BOTH outbound channels — the workflow webhook
action calls it directly, and the service-hook dispatcher calls it through
`app/services/hooks/registry.py`. These tests are about that call site, not
about the algebra: `tests/services/test_webhook_policy.py` already proves the
composition cannot widen, and would keep passing if nothing called it.

The scenario that makes this file necessary is the one in the middle. Moving
the four SSRF keys to PLATFORM ownership gave every read-side path a scope
override that resolves them at `organization_id IS NULL` alone. Applied to the
old readers, that override is a WIDENING: an organization whose own row was
NARROWER than the platform value stopped being narrowed by it and silently
inherited the ceiling. The fix is not to weaken the override — it is that the
narrowing moved to its own key and is composed here, as a second conjunct.

So every assertion below is about reaching FEWER hosts, and each has a
non-vacuity partner showing the same call reaching a host when nothing narrows
it — a gate that denied everything would pass a one-sided version of all of
this.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.services.finance.automation import webhook_policy
from app.services.finance.automation import workflow as workflow_module
from app.services.setting_scopes import platform_owned_keys
from app.services.settings_cache import settings_cache

CEILING_HOST = "selfcare.dotmac.io"
NARROWED_HOST = "internal.example.com"

# Every ceiling read goes through this address, so no test here touches DNS.
# A public, non-private literal: `_is_private_address` must not be the reason a
# target is refused, or a test meant to be about the allowlist would pass for
# the wrong reason.
_PUBLIC_IP = "203.0.113.10"


@pytest.fixture(autouse=True)
def _no_ambient_environment(monkeypatch):
    """The ceiling comes from rows in these tests, never from the process.

    `read_platform_webhook_ceiling` falls back to the environment when a key
    has no row. A `WEBHOOK_ALLOWED_HOSTS` left in the runner's environment
    would silently become the ceiling and every narrowing assertion below
    would be measuring something else.
    """
    for name in (
        "WEBHOOK_ALLOWED_HOSTS",
        "WEBHOOK_ALLOWED_DOMAINS",
        "WEBHOOK_ALLOW_INSECURE",
        "WEBHOOK_ALLOW_LOCALHOST",
        "WEBHOOK_MAX_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture(autouse=True)
def _resolve_every_host_to_one_public_address(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", (_PUBLIC_IP, 443))],
    )


@pytest.fixture()
def org_id():
    return uuid.uuid4()


@pytest.fixture()
def rows(db_session):
    """Settings rows made by a test, removed afterwards whatever happened."""
    made: list[DomainSetting] = []

    def _add(key: str, value: str, organization_id: uuid.UUID | None) -> None:
        row = DomainSetting(
            domain=SettingDomain.automation,
            key=key,
            organization_id=organization_id,
            scope=(
                SettingScope.GLOBAL
                if organization_id is None
                else SettingScope.ORG_SPECIFIC
            ),
            value_type=SettingValueType.string,
            value_text=value,
        )
        db_session.add(row)
        made.append(row)
        db_session.commit()
        settings_cache.clear_inmemory()

    yield _add

    db_session.rollback()
    ids = [row.id for row in made if row.id is not None]
    if ids:
        db_session.execute(
            DomainSetting.__table__.delete().where(DomainSetting.id.in_(ids))
        )
        db_session.commit()
    settings_cache.clear_inmemory()


def _permits(db, host: str, organization_id: uuid.UUID | None = None) -> bool:
    allowed, _ = workflow_module._validate_webhook_target(
        f"https://{host}/hook", db, organization_id=organization_id
    )
    return allowed


# ---------------------------------------------------------------------------
# The regression this branch exists to close
# ---------------------------------------------------------------------------


class TestANarrowingOrganizationReachesFewerHostsThanTheCeiling:
    """The exact scenario named in the ruling, driven through the real gate.

    Ceiling `["selfcare.dotmac.io"]`, organization narrowing
    `["internal.example.com"]`. The organization must reach FEWER hosts than
    the ceiling — here, none at all, because its narrowing and the ceiling are
    disjoint. Reaching `selfcare.dotmac.io` would mean the narrowing had been
    dropped; reaching `internal.example.com` would mean the narrowing had been
    treated as a grant.
    """

    @pytest.fixture(autouse=True)
    def _policy(self, rows, org_id):
        rows("webhook_allowed_hosts", CEILING_HOST, None)
        rows("webhook_tenant_allowed_hosts", NARROWED_HOST, org_id)

    def test_the_ceiling_host_is_no_longer_reachable(self, db_session, org_id):
        assert _permits(db_session, CEILING_HOST, org_id) is False, (
            "the organization reached the whole ceiling despite holding a "
            "narrower list — the gate is reading the ceiling keys without the "
            "narrowing conjunct"
        )

    def test_the_narrowed_host_is_not_reachable_either(self, db_session, org_id):
        assert _permits(db_session, NARROWED_HOST, org_id) is False, (
            "an organization listed a host the platform never allowed and "
            "reached it — the narrowing is being read as a grant"
        )

    def test_the_ceiling_host_is_reachable_without_the_narrowing(self, db_session):
        """Non-vacuity. A gate that denied everything would pass both above."""
        assert _permits(db_session, CEILING_HOST, None) is True

    def test_another_organization_is_unaffected(self, db_session):
        """One organization's narrowing is not a fleet-wide narrowing."""
        assert _permits(db_session, CEILING_HOST, uuid.uuid4()) is True


class TestANarrowingInsideTheCeilingStillWorks:
    """The ordinary case: a subset narrowing keeps working exactly as before.

    Per-list set INTERSECTION would break this and the suite would still be
    green without it — the ceiling here is a domain and the narrowing a host,
    so both list intersections are empty while the conjunction plainly holds.
    """

    @pytest.fixture(autouse=True)
    def _policy(self, rows, org_id):
        rows("webhook_allowed_domains", "dotmac.io", None)
        rows("webhook_tenant_allowed_hosts", CEILING_HOST, org_id)

    def test_the_narrowed_host_is_reachable(self, db_session, org_id):
        assert _permits(db_session, CEILING_HOST, org_id) is True

    def test_a_sibling_under_the_same_ceiling_domain_is_not(self, db_session, org_id):
        assert _permits(db_session, "billing.dotmac.io", org_id) is False

    def test_the_sibling_is_reachable_without_the_narrowing(self, db_session):
        assert _permits(db_session, "billing.dotmac.io", None) is True


# ---------------------------------------------------------------------------
# The two channels reach the same conjunction
# ---------------------------------------------------------------------------


class TestBothChannelsPassTheOrganizationThrough:
    """A gate only one channel scopes is not a gate.

    Channel 1 is `WorkflowService._action_webhook`; channel 2 is
    `hooks.registry._execute_hook_handler`. Each is asserted to hand its own
    organization to the gate — not to hand nothing and rely on the session's
    ambient value, which a caller can forget to prime.
    """

    def test_channel_one_passes_the_context_organization(self, monkeypatch):
        seen: dict[str, object] = {}

        def _record(url, db=None, *, allow_localhost=None, organization_id=None):
            seen["organization_id"] = organization_id
            return False, "denied by the stub"

        monkeypatch.setattr(workflow_module, "_validate_webhook_target", _record)

        context = MagicMock()
        context.organization_id = uuid.uuid4()
        result = workflow_module.WorkflowService()._action_webhook(
            MagicMock(), {"url": "https://example.com/hook"}, context
        )

        assert result.success is False
        assert seen["organization_id"] == context.organization_id

    def test_channel_two_passes_the_event_organization(self, monkeypatch):
        from app.models.finance.platform.service_hook import (
            HookExecutionMode,
            HookHandlerType,
            ServiceHook,
        )
        from app.services.hooks import registry

        seen: dict[str, object] = {}

        def _record(url, db, *, allow_localhost=False, organization_id=None):
            seen["organization_id"] = organization_id
            return False, "denied by the stub"

        monkeypatch.setattr(registry, "_validate_webhook_target", _record)

        hook = ServiceHook(
            event_name="sales.order.confirmed",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.SYNC,
            handler_config={"url": "https://example.com/hook", "method": "POST"},
            conditions={},
            name="Call-site test",
            organization_id=None,
        )
        event = registry.HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid.uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid.uuid4(),
            actor_user_id=None,
            payload={},
        )

        with pytest.raises(ValueError, match="denied by the stub"):
            registry._execute_hook_handler(MagicMock(), hook, event)

        assert seen["organization_id"] == event.organization_id


# ---------------------------------------------------------------------------
# The in-process argument cannot widen either
# ---------------------------------------------------------------------------


class TestTheCallerArgumentIsNarrowingOnly:
    """`allow_localhost=True` used to REPLACE the policy, not narrow it.

    An in-process argument that can turn loopback back on in a deployment that
    turned it off is the same widening as a tenant row, arriving by a shorter
    route. It is `AND`-ed now.
    """

    @pytest.fixture(autouse=True)
    def _loopback_target(self, monkeypatch, rows):
        rows("webhook_allowed_hosts", "localhost", None)
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
        )

    def test_the_argument_cannot_turn_loopback_on(self, db_session):
        allowed, reason = workflow_module._validate_webhook_target(
            "https://localhost/hook", db_session, allow_localhost=True
        )
        assert allowed is False, (
            "a caller argument re-enabled loopback against a platform ceiling "
            "that forbids it"
        )
        assert reason == "Webhook target is not allowed"

    def test_the_argument_still_turns_loopback_off(self, db_session, rows):
        """Non-vacuity, and the direction that must keep working."""
        rows("webhook_allow_localhost", "true", None)

        assert (
            workflow_module._validate_webhook_target(
                "https://localhost/hook", db_session
            )[0]
            is True
        )
        assert (
            workflow_module._validate_webhook_target(
                "https://localhost/hook", db_session, allow_localhost=False
            )[0]
            is False
        )


# ---------------------------------------------------------------------------
# No second reader of the ceiling keys survives
# ---------------------------------------------------------------------------


# DERIVED, never restated. The previous version of this guard copied FOUR
# literals out of a declaration that had FIVE members, so `webhook_max_timeout_
# seconds` — the key that bounds BOTH organization timeout channels — was
# outside the scan, and a second reader of it would not have been caught.
# Deriving is the only version of this guard that cannot drift: a sixth ceiling
# key is scanned the moment it is declared, without anyone remembering to come
# here.
CEILING_KEY_LITERALS = frozenset(key for key, _env in webhook_policy.PLATFORM_KEYS)

# `platform_owned_keys()` is the WIDER registry — every `scope=PLATFORM` spec,
# not just the webhook ceiling. Pinning the difference keeps a platform-owned
# key from sitting outside every enumeration, which is exactly where
# `openbao_allow_insecure` sat: platform-owned, refused to organizations by the
# same listener, and named in no guard. It is deliberately NOT in the ceiling —
# `webhook_policy` neither reads nor composes it — so it is named here as a
# stated remainder rather than folded in.
NOT_A_CEILING_KEY = {
    "openbao_allow_insecure": (
        "platform-owned for the same reason (a tenant-writable row that turns "
        "off TLS verification against the secret store), but not composed by "
        "webhook_policy and not part of the webhook ceiling"
    ),
}

# Every file under `app/` allowed to name one of the five ceiling keys, and
# why. Two DECLARE them and one READS them; nothing else may, because a reader
# of the ceiling that does not also compose the narrowing is a widening.
# This list only shrinks. Adding to it means adding a second answer to "what
# may this deployment send a webhook to".
ACCOUNTED_FOR = {
    "app/services/settings_spec.py": "declares the specs, including scope=PLATFORM",
    "app/services/settings_seed.py": "seeds the platform rows from the environment",
    "app/services/finance/automation/webhook_policy.py": (
        "read_platform_webhook_ceiling — the one reader, and the only place "
        "the narrowing is composed onto them"
    ),
}


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_derived_ceiling_set_is_not_vacuous():
    """A derived set can silently become empty; an empty scan passes.

    `test_only_the_policy_module_reads_the_ceiling_keys` compares two sets, and
    if `CEILING_KEY_LITERALS` were empty both would be empty and it would pass
    while checking nothing at all. So the derivation is pinned to its size and
    to the one member the hand-copied version dropped.
    """
    assert len(CEILING_KEY_LITERALS) == 5, (
        "the webhook ceiling changed size. That is allowed — update this "
        "number — but the scan below and the reader ledger both widen with it, "
        f"so look at what is now in it: {sorted(CEILING_KEY_LITERALS)}"
    )
    assert "webhook_max_timeout_seconds" in CEILING_KEY_LITERALS, (
        "the timeout ceiling bounds BOTH organization timeout channels "
        "(the setting and ServiceHook.handler_config); it is a ceiling key"
    )
    assert CEILING_KEY_LITERALS == frozenset(
        key for key, _env in webhook_policy.PLATFORM_KEYS
    )


def test_every_platform_owned_key_is_in_exactly_one_enumeration():
    """No platform-owned key sits outside both the ceiling and the remainder.

    The registry is the wider truth — it drives the write listener and all four
    read overrides — and a key can join it by a one-line `scope=PLATFORM` on a
    spec, with no edit here. This asserts the partition, so such a key lands in
    the ceiling scan or is named in `NOT_A_CEILING_KEY` with its reason, and
    never in neither.
    """
    automation = {
        key
        for domain, key in platform_owned_keys()
        if domain == str(SettingDomain.automation)
    }
    assert automation, "no platform-owned keys registered — the check fails open"
    assert CEILING_KEY_LITERALS <= automation, (
        "a webhook ceiling key is not declared scope=PLATFORM, so an "
        "organization row for it would be accepted and would answer: "
        f"{sorted(CEILING_KEY_LITERALS - automation)}"
    )
    assert automation - CEILING_KEY_LITERALS == set(NOT_A_CEILING_KEY), (
        "a platform-owned automation key is in no enumeration. Either it "
        "belongs to the webhook ceiling (add it to webhook_policy."
        "PLATFORM_KEYS, which this file derives from) or it does not (name it "
        "in NOT_A_CEILING_KEY with the reason). unaccounted: "
        f"{sorted(automation - CEILING_KEY_LITERALS - set(NOT_A_CEILING_KEY))}"
    )


def test_only_the_policy_module_reads_the_ceiling_keys():
    found: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        hits = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in CEILING_KEY_LITERALS
        }
        if hits:
            found[path.relative_to(REPO_ROOT).as_posix()] = hits

    assert set(found) == set(ACCOUNTED_FOR), (
        "the set of files naming a webhook SSRF ceiling key changed. Every one "
        "of them is a potential second reader of the ceiling WITHOUT the "
        "organization's narrowing, which is the widening this branch closes. "
        f"unaccounted: {sorted(set(found) - set(ACCOUNTED_FOR))}; "
        f"gone: {sorted(set(ACCOUNTED_FOR) - set(found))}"
    )


def test_the_retired_readers_are_gone_not_merely_unused():
    """Each of these resolved a ceiling key at whatever scope it was handed.

    Left in place they would answer with the ceiling alone — the wrong answer,
    in the direction of more reachable hosts — for the next caller to find.
    """
    for name in (
        "_allowed_webhook_hosts",
        "_allowed_webhook_domains",
        "_allow_insecure_webhooks",
        "_allow_localhost_webhooks",
        "_host_matches_allowlist",
        "_webhook_timeout",
        "_db_setting",
    ):
        assert not hasattr(workflow_module, name), (
            f"workflow.{name} still exists; it reads a platform-owned key "
            "without composing the organization's narrowing"
        )


def test_the_gate_takes_an_organization():
    """The signature itself, so the parameter cannot be quietly dropped."""
    import inspect

    parameters = inspect.signature(workflow_module._validate_webhook_target).parameters
    assert "organization_id" in parameters
    assert parameters["organization_id"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_gate_resolves_exactly_one_policy_per_call(db_session, rows, org_id):
    """One object, not four settings reads that can disagree mid-call."""
    rows("webhook_allowed_hosts", CEILING_HOST, None)

    calls: list[object] = []
    real = workflow_module.effective_policy

    def _counting(db, organization_id):
        calls.append(organization_id)
        return real(db, organization_id)

    with patch.object(workflow_module, "effective_policy", _counting):
        assert _permits(db_session, CEILING_HOST, org_id) is True

    assert calls == [org_id]
