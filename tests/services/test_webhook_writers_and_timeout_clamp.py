"""The tenant writers are closed, and BOTH outbound timeout channels clamp.

Step 1a declared the ownership and built the refusal. This is the other half:
every writer that could put an organization row on a platform-owned key ends in
a stated fate, and the platform timeout maximum is applied where the request is
made rather than only where the setting is read.

Two properties are worth naming, because a test that only checked the happy
path would pass against a broken version of each:

* the finance automation-settings form skips PLATFORM specs *derived from the
  spec*, not from a literal key set — so the assertions below check that a
  tenant-owned key in the SAME submission still gets written. A blanket "write
  nothing" regression would otherwise look identical to a correct skip;
* the timeout clamp is asserted on the value actually handed to ``httpx``, and
  each channel has a companion case with a high ceiling proving the request's
  own value survives when it is under the maximum. Without that, a call site
  that hardcoded the ceiling would pass the clamp test.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.domain_settings import SettingDomain
from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.services.finance.automation import webhook_policy
from app.services.finance.automation.webhook_policy import (
    TenantWebhookRestriction,
    WebhookCeiling,
)
from app.services.finance.settings_web import settings_web_service
from app.services.setting_scopes import is_platform_owned
from app.services.settings_spec import (
    DOMAIN_SETTINGS_SERVICE,
    SettingScopeAuthority,
    get_spec,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The set that used to live inside `app/web/finance/settings.py` as a literal,
# and what each member's fate is now. This is the acceptance list for §3.1 and
# §9.7: nothing that was in that set may end up less protected than it was.
FORMERLY_RESTRICTED_NOW_PLATFORM_OWNED = (
    "webhook_allowed_hosts",
    "webhook_allowed_domains",
    "webhook_allow_insecure",
    "webhook_allow_localhost",
    # The fifth key. It was in the same literal set and is the same failure
    # class — a tenant-writable row that disables TLS verification against the
    # secret store — so deleting the set without carrying it would have made it
    # strictly less protected than before.
    "openbao_allow_insecure",
)

# Deliberately released from that set: a timeout is a preference, not an SSRF
# control. It stays organization-owned and is bounded at the point of use.
FORMERLY_RESTRICTED_NOW_TENANT_OWNED = ("webhook_timeout_seconds",)


class _RecordingSettingService:
    """Stands in for the automation `DomainSettingService`.

    Records what was written and with which scope, so the test can assert on
    the scope the caller STATED rather than on the row a database happened to
    produce.
    """

    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []

    def upsert_by_key(self, db, key, payload, *args, organization_id=..., **kwargs):
        self.writes.append((key, organization_id))
        return MagicMock()

    @property
    def written_keys(self) -> set[str]:
        return {key for key, _ in self.writes}


@pytest.fixture()
def recording_service():
    original = DOMAIN_SETTINGS_SERVICE[SettingDomain.automation]
    service = _RecordingSettingService()
    DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = service
    try:
        yield service
    finally:
        DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = original


# ---------------------------------------------------------------------------
# W1 — the finance automation-settings form
# ---------------------------------------------------------------------------


class TestTheAutomationSettingsFormCannotWriteAPlatformKey:
    def test_platform_keys_are_skipped_and_tenant_keys_are_not(self, recording_service):
        db = MagicMock()
        org = uuid4()

        submission = {
            # Every platform-owned control the form renders, submitted the way
            # a real browser submits them — the page shows them, so a normal
            # save always carries them back.
            "webhook_allowed_hosts": "evil.example.net",
            "webhook_allowed_domains": "example.net",
            "webhook_allow_insecure": "true",
            "webhook_allow_localhost": "true",
            "openbao_allow_insecure": "true",
            # ... alongside two the organization genuinely owns.
            "webhook_timeout_seconds": "30",
            "recurring_lookback_days": "14",
        }

        ok, error = settings_web_service.update_automation_settings(db, org, submission)

        assert (ok, error) == (True, None)
        assert recording_service.written_keys == {
            "webhook_timeout_seconds",
            "recurring_lookback_days",
        }, "a platform-owned key reached the write path"

    def test_the_write_states_its_organization_scope(self, recording_service):
        """`organization_id` is passed, not left to the ambient session.

        The method already received the scope and used to discard it, letting
        `_resolve_operation_scope` re-derive the same value from `db.info` —
        one more ambient-scope call site for no benefit.
        """
        db = MagicMock()
        org = uuid4()

        settings_web_service.update_automation_settings(
            db, org, {"recurring_lookback_days": "14"}
        )

        assert recording_service.writes == [("recurring_lookback_days", org)]

    def test_the_route_carries_no_literal_restricted_key_set(self):
        """The convention is gone, and stays gone.

        This is the one assertion that would catch someone re-adding the
        hand-maintained set as a "belt and braces" measure: a second, silently
        diverging list of which keys are protected is exactly what this change
        exists to remove.
        """
        source = (REPO_ROOT / "app" / "web" / "finance" / "settings.py").read_text()

        assert "restricted_keys" not in source
        for key in FORMERLY_RESTRICTED_NOW_PLATFORM_OWNED:
            assert key not in source, (
                f"{key} is named in the route again; which keys an organization "
                f"may own is declared on the spec, not listed in a handler"
            )

    def test_the_context_reports_platform_ownership_per_key(self):
        """The template renders a platform-owned control read-only from this."""
        with patch(
            "app.services.finance.settings_web.resolve_value", return_value=None
        ):
            context = settings_web_service.get_automation_settings_context(
                MagicMock(), uuid4()
            )

        settings = context["settings"]
        for key in FORMERLY_RESTRICTED_NOW_PLATFORM_OWNED:
            assert settings[key]["platform_owned"] is True
        for key in FORMERLY_RESTRICTED_NOW_TENANT_OWNED:
            assert settings[key]["platform_owned"] is False


# ---------------------------------------------------------------------------
# §9.7 — the adjacent control, carried rather than regressed
# ---------------------------------------------------------------------------


class TestNothingInTheDeletedSetLostProtection:
    @pytest.mark.parametrize("key", FORMERLY_RESTRICTED_NOW_PLATFORM_OWNED)
    def test_each_protected_key_is_now_platform_owned(self, key):
        spec = get_spec(SettingDomain.automation, key)
        assert spec is not None
        assert spec.scope is SettingScopeAuthority.PLATFORM
        assert is_platform_owned(SettingDomain.automation, key) is True

    @pytest.mark.parametrize("key", FORMERLY_RESTRICTED_NOW_TENANT_OWNED)
    def test_the_released_key_is_deliberately_not_platform_owned(self, key):
        """The sensitivity proof for the parametrisation above.

        `webhook_timeout_seconds` sat in the same literal set and is
        deliberately NOT carried: a timeout is a preference. If this assertion
        ever flips, the test above stopped distinguishing anything.
        """
        spec = get_spec(SettingDomain.automation, key)
        assert spec is not None
        assert spec.scope is SettingScopeAuthority.TENANT
        assert is_platform_owned(SettingDomain.automation, key) is False

    def test_openbao_allow_insecure_is_read_at_platform_scope(self):
        """The declaration has to reach the READ, which does not use `resolve_value`.

        `app/services/secrets.py` issues its own settings read, so the scope
        override `resolve_value` performs for platform-owned keys never applied
        to it. Before this change the org listener rewrote that query into
        `organization_id == <session org>` — the tenant's row decided whether
        the deployment verifies TLS against its own secret store.
        """
        from app.services.secrets import _openbao_allow_insecure

        captured: dict[str, object] = {}

        class _Reader:
            def get_by_key(self, db, key, *, organization_id=..., inherit=True):
                captured["key"] = key
                captured["organization_id"] = organization_id
                return MagicMock(value_json=True, value_text=None)

        original = DOMAIN_SETTINGS_SERVICE[SettingDomain.automation]
        DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = _Reader()
        try:
            assert _openbao_allow_insecure(MagicMock()) is True
        finally:
            DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = original

        assert captured["key"] == "openbao_allow_insecure"
        assert captured["organization_id"] is None


# ---------------------------------------------------------------------------
# §5 / §9.6 — both timeout channels
# ---------------------------------------------------------------------------


def _clamped_ceiling(monkeypatch, *, maximum: float, requested: float) -> None:
    """Install a ceiling and an organization request, both real objects.

    The two READS are stubbed; the composition and the clamp are the genuine
    article, so these tests exercise the code they claim to.
    """
    monkeypatch.setattr(
        webhook_policy,
        "read_platform_webhook_ceiling",
        lambda db: WebhookCeiling(
            allowed_hosts=frozenset({"example.com"}),
            max_timeout_seconds=maximum,
        ),
    )
    monkeypatch.setattr(
        webhook_policy,
        "read_tenant_restriction",
        lambda db, organization_id: TenantWebhookRestriction(timeout_seconds=requested),
    )


class TestChannelOneTheWorkflowWebhookAction:
    """`webhook_timeout_seconds` — the settings channel."""

    @pytest.mark.parametrize(
        ("maximum", "requested", "expected"),
        [
            # Over the maximum: clamped down to it.
            (5.0, 120.0, 5.0),
            # Under the maximum: the organization's own value survives. This is
            # the non-vacuity case — a call site that simply used the ceiling
            # would fail here while passing the case above.
            (300.0, 30.0, 30.0),
        ],
    )
    def test_the_timeout_handed_to_httpx_is_clamped(
        self, monkeypatch, maximum, requested, expected
    ):
        from app.services.finance.automation import workflow

        _clamped_ceiling(monkeypatch, maximum=maximum, requested=requested)
        monkeypatch.setattr(
            workflow,
            "_validate_webhook_target",
            lambda url, db, organization_id=None: (True, None),
        )

        context = MagicMock()
        context.organization_id = uuid4()
        context.entity_type = "Invoice"
        context.entity_id = uuid4()
        context.event.value = "created"
        context.new_values = {}

        with patch("httpx.Client") as client_cls:
            client = MagicMock()
            client.request.return_value = MagicMock(
                status_code=200, headers={}, raise_for_status=MagicMock()
            )
            client_cls.return_value.__enter__.return_value = client

            workflow.WorkflowService()._action_webhook(
                MagicMock(),
                {"url": "https://example.com/hook", "method": "POST"},
                context,
            )

        timeout = client_cls.call_args.kwargs["timeout"]
        assert timeout.read == expected

    def test_the_unbounded_reader_is_gone(self):
        """`_webhook_timeout` returned the setting unclamped.

        Leaving it beside a clamped call site would be a second, unclamped
        reader of the same value waiting for the next caller.
        """
        from app.services.finance.automation import workflow

        assert not hasattr(workflow, "_webhook_timeout")


class TestChannelTwoTheServiceHookDispatcher:
    """`ServiceHook.handler_config["timeout_seconds"]` — the other channel.

    A different store, a different owner, and validated only by a Pydantic
    bound. A ceiling that clamped only the settings channel would be bypassed
    by picking this one.
    """

    @pytest.mark.parametrize(
        ("maximum", "requested", "expected"),
        [(5.0, 120.0, 5.0), (300.0, 30.0, 30.0)],
    )
    def test_the_timeout_handed_to_httpx_is_clamped(
        self, monkeypatch, maximum, requested, expected
    ):
        from app.services.hooks import registry

        # The tenant restriction is irrelevant on this channel: the requested
        # value comes from the hook row, so the ceiling is stubbed and the
        # organization's setting deliberately left empty.
        monkeypatch.setattr(
            webhook_policy,
            "read_platform_webhook_ceiling",
            lambda db: WebhookCeiling(
                allowed_hosts=frozenset({"example.com"}),
                max_timeout_seconds=maximum,
            ),
        )
        monkeypatch.setattr(
            webhook_policy,
            "read_tenant_restriction",
            lambda db, organization_id: TenantWebhookRestriction(),
        )
        monkeypatch.setattr(
            registry,
            "_validate_webhook_target",
            lambda url, db, allow_localhost=False, organization_id=None: (True, None),
        )

        hook = ServiceHook(
            event_name="sales.order.confirmed",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.SYNC,
            handler_config={
                "url": "https://example.com/hook",
                "method": "POST",
                "timeout_seconds": requested,
            },
            conditions={},
            name="Clamp test",
            organization_id=None,
        )
        event = registry.HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid4(),
            actor_user_id=None,
            payload={},
        )

        with patch("httpx.Client") as client_cls:
            client = MagicMock()
            response = MagicMock(status_code=202, text="ok")
            response.raise_for_status.return_value = None
            client.post.return_value = response
            client.request.return_value = response
            client_cls.return_value.__enter__.return_value = client

            registry._execute_hook_handler(MagicMock(), hook, event)

        assert client_cls.call_args.kwargs["timeout"] == expected
