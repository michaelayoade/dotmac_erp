"""Demote every organization-held webhook SSRF row to a narrowing restriction.

The four outbound-webhook keys — ``webhook_allowed_hosts``,
``webhook_allowed_domains``, ``webhook_allow_insecure``,
``webhook_allow_localhost`` — are now PLATFORM-owned
(``app/services/setting_scopes.py``). No organization may hold a row for them,
because a constrained party that can rewrite its own constraint is not
constrained.

Deployments that already have such rows must not be left with data that is
present, unwritable and silently ignored, so this migration MOVES each of them
rather than deleting it. Every organization row is renamed in place to the
matching narrowing key:

    webhook_allowed_hosts    ->  webhook_tenant_allowed_hosts
    webhook_allowed_domains  ->  webhook_tenant_allowed_domains
    webhook_allow_insecure   ->  webhook_tenant_allow_insecure
    webhook_allow_localhost  ->  webhook_tenant_allow_localhost

``UPDATE ... SET key`` and not INSERT+DELETE, so the row's ``id`` survives and
every ``domain_setting_history.setting_id`` still points at its setting.
``organization_id``, ``value_type``, ``value_text``, ``value_json``,
``is_secret`` and ``is_active`` are carried across untouched.

What the demotion means for each value, under the conjunction in
``app/services/finance/automation/webhook_policy.py``:

* a list NARROWER than the platform ceiling keeps working identically — it is
  now a formal narrowing rather than an override that happened to be narrower;
* a list BROADER than the ceiling keeps its extra entries as data, but they no
  longer permit anything: the conjunction drops them at read time. This is the
  vulnerability being closed;
* a list DISJOINT from the ceiling leaves that organization sending no
  webhooks;
* ``allow_insecure``/``allow_localhost`` compose narrow-only, so an
  organization may still force a protection ON and can no longer turn one OFF.

Those four are claims about RUNTIME, not about this file, and they are true
only because the demoted key is actually composed onto the ceiling at the point
a request is made. That is
``workflow._validate_webhook_target``, the single host/scheme/loopback gate for
both outbound channels, which resolves one ``effective_policy`` per call;
``tests/services/test_webhook_call_site.py`` drives the disjoint case above end
to end. Were a reader of the ceiling keys to reappear beside it, this migration
would move values into a key nothing consults, and all four bullets would
quietly become false — which is why that test also enumerates every file under
``app/`` allowed to name one of the four keys.

Two limits on the third bullet, stated because an earlier draft called it
"loud": a disjoint narrowing surfaces as an ordinary per-request
"Webhook host is not in the allowlist" failure on the workflow execution, the
same message an unconfigured ceiling produces. Nothing logs "this
organization's narrowing is disjoint from the ceiling", and no screen renders
the composed policy. It is VISIBLE — the row is present and the failures are
recorded — but an operator has to go and look.

Each move is recorded as an ``UPDATE`` row in ``public.domain_setting_history``
with the OLD ``(domain, key)`` in the denormalised columns, so it surfaces in
``GET /api/v1/settings/history`` beside every other settings change. No side-channel
report file.

ANNOUNCED BEHAVIOUR CHANGE — read this before deploying
-------------------------------------------------------
This migration deliberately does NOT synthesise a platform ceiling from the
organization values it demotes. That union is exactly the broadening being
removed, and performing it here would launder it into the ceiling.

So: **a deployment whose only webhook allowlist lived in an organization row
will deny outbound webhooks once this runs**, until an operator writes the
platform rows. That is not a regression — the previous behaviour was a tenant
choosing its own SSRF boundary — but it is a live outage for such a deployment
and must not be discovered in production. This migration ``RAISE NOTICE``s one
line per moved ``(organization, key, value)``, so the deploy log carries the
values an operator composes a ceiling from, and one line per key that ends up
with narrowing rows and no active platform row.

Recovery — the ONE supported operation
--------------------------------------
Write the platform row through the admin settings API. Exact route, exact
keys, admin credentials (``require_admin_bypass``)::

    PUT /api/v1/settings/automation/webhook_allowed_hosts
        {"value_text": "hooks.example.net,api.example.net", "is_active": true}

    PUT /api/v1/settings/automation/webhook_allowed_domains
        {"value_text": "example.net", "is_active": true}

The ``/api/v1`` prefix is load-bearing and is the whole route: ``app/main.py``
mounts ``app.api.settings.router`` with ``prefix="/api/v1"`` directly, NOT
through ``_include_api_router``, which is the helper that also registers the
bare alias. There is no bare ``PUT /settings/automation/<key>``: the unprefixed
``/settings`` namespace belongs to the HTML router ``app/web/settings.py``,
which serves ``GET``/``POST /settings/{module_key}`` only, so the unprefixed
form answers 405/404 and writes nothing. An earlier draft of this paragraph
printed it without the prefix; an operator following that stayed down.

``app/api/settings.py::upsert_automation_setting`` hands
``organization_id=None`` to the service unconditionally, so this route writes
the PLATFORM row and has no form in which it writes an organization one. The
same route and body shape restore ``webhook_allow_localhost``,
``webhook_allow_insecure`` and ``webhook_max_timeout_seconds``. The next
``read_platform_webhook_ceiling`` sees it; no restart, no redeploy.

Setting ``WEBHOOK_ALLOWED_HOSTS`` / ``WEBHOOK_ALLOWED_DOMAINS`` in the process
environment does NOT end this outage, and an earlier draft of this paragraph
wrongly offered it as an alternative. The environment is a BOOTSTRAP source
only: ``app/services/settings_seed.py`` reads it once through ``ensure_by_key``,
which is create-if-missing. On a database that has completed the normal seed,
the platform row therefore already exists, empty value and all. The runtime
reader never consults ``os.getenv``: changing a variable after bootstrap does
nothing, and only the admin API changes the live ceiling. Do not add a runtime
environment fallback; it would put a second, unaudited writer on a control
whose whole purpose is that only the platform may set it.

That second notice is deliberately NOT one per unconfigured key: a key with no
organization rows and no ceiling is the ordinary unconfigured state of a
deployment that never used it, and saying so once per migration per key would
train an operator to skip the notices that matter. The boot-time
``warn_unconfigured_webhook_allowlist`` is what keeps saying it, at every start,
for as long as active webhook rules exist without a ceiling.

The pre-deploy dry run is::

    SELECT organization_id, key, value_type, value_text, value_json,
           is_secret, is_active
      FROM public.domain_settings
     WHERE domain = 'automation'
       AND key IN ('webhook_allowed_hosts', 'webhook_allowed_domains',
                   'webhook_allow_insecure', 'webhook_allow_localhost')
       AND organization_id IS NOT NULL;

No schema change: no column is added, dropped or retyped. Which keys the
platform owns is a Python declaration indexed by
``app/services/setting_scopes.py``, deliberately not a column of the table it
governs — a row that could change the rule is not a rule.

No stored setting value is discarded in either direction; values are only
reinterpreted. The rename collides only if an organization somehow already
holds the narrowing key; the guard below aborts loudly rather than merging or
skipping a row silently.

Revision ID: 20260816_platform_owned_webhook_ssrf_policy
Revises: 20260815_tenant_catalog_discovery
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op

revision = "20260816_platform_owned_webhook_ssrf_policy"
down_revision = "20260815_tenant_catalog_discovery"
branch_labels = None
depends_on = None


_DOMAIN = "automation"

# The platform ceiling key and the narrowing key it demotes to. Both halves are
# declared as `SettingSpec`s in `app/services/settings_spec.py`; this pairing is
# the data migration's half of that declaration.
_KEY_MOVES = (
    ("webhook_allowed_hosts", "webhook_tenant_allowed_hosts"),
    ("webhook_allowed_domains", "webhook_tenant_allowed_domains"),
    ("webhook_allow_insecure", "webhook_tenant_allow_insecure"),
    ("webhook_allow_localhost", "webhook_tenant_allow_localhost"),
)

# `app/schemas/settings.py::MASKED_VALUE`. A secret's `value_text` is ciphertext
# at rest and the service masks it before it reaches history; a migration that
# wrote the raw column would be the one place the trail leaked it.
_MASKED_VALUE = "***MASKED***"

_UPGRADE_REASON = (
    "Webhook SSRF policy is platform-owned; this tenant value is preserved "
    "as a narrowing restriction and can no longer broaden the platform "
    "ceiling."
)

_DOWNGRADE_REASON = (
    "Downgrade of the platform-owned webhook SSRF policy; this narrowing "
    "value is restored as an organization override of the platform value."
)


def _values_list(reverse: bool) -> str:
    """The key pairs as a PL/pgSQL ``VALUES`` list: source key, target key."""
    return ",\n                       ".join(
        "('{}', '{}')".format(*(reversed(pair) if reverse else pair))
        for pair in _KEY_MOVES
    )


def _move_block(*, reverse: bool, reason: str, headline: str) -> str:
    """The rename, its collision guard, its notices and its history rows.

    One block for both directions: the upgrade moves ceiling key -> narrowing
    key, the downgrade moves it back. The guard is symmetric because either
    direction can only be safe if the target key is free for that organization,
    and a raw-SQL writer can have created a row the ORM listener would refuse.
    """
    return """
        DO $$
        DECLARE
            v_move      RECORD;
            v_row       RECORD;
            v_collision INTEGER;
            v_moved     INTEGER := 0;
        BEGIN
            FOR v_move IN
                SELECT *
                  FROM (VALUES
                       {values}
                       ) AS m(source_key, target_key)
            LOOP
                SELECT COUNT(*)
                  INTO v_collision
                  FROM public.domain_settings AS src
                  JOIN public.domain_settings AS dst
                    ON dst.domain = '{domain}'
                   AND dst.key = v_move.target_key
                   AND dst.organization_id = src.organization_id
                 WHERE src.domain = '{domain}'
                   AND src.key = v_move.source_key
                   AND src.organization_id IS NOT NULL;

                IF v_collision > 0 THEN
                    RAISE EXCEPTION
                        'public.domain_settings already holds % organization row(s) for %/% that collide with the move of %/%. Resolve them by hand: this migration will not merge two values or discard either one.',
                        v_collision, '{domain}', v_move.target_key,
                        '{domain}', v_move.source_key;
                END IF;

                FOR v_row IN
                    SELECT id, organization_id, value_type, value_text,
                           value_json, is_secret, is_active
                      FROM public.domain_settings
                     WHERE domain = '{domain}'
                       AND key = v_move.source_key
                       AND organization_id IS NOT NULL
                     ORDER BY organization_id
                LOOP
                    RAISE NOTICE
                        '{headline}: organization % held %/% = % (active=%). Moved to %/%.',
                        v_row.organization_id, '{domain}', v_move.source_key,
                        CASE
                            WHEN v_row.is_secret THEN '{masked}'
                            ELSE COALESCE(v_row.value_text,
                                          v_row.value_json::text, '<null>')
                        END,
                        v_row.is_active, '{domain}', v_move.target_key;

                    -- The value does not change, only which key holds it, so
                    -- old_* and new_* are identical and the move is legible
                    -- from the denormalised (domain, key) naming the OLD key.
                    INSERT INTO public.domain_setting_history (
                        setting_id, domain, key, organization_id, action,
                        old_value_type, old_value_text, old_value_json,
                        old_is_secret, old_is_active,
                        new_value_type, new_value_text, new_value_json,
                        new_is_secret, new_is_active,
                        changed_by_id, change_reason
                    )
                    VALUES (
                        v_row.id, '{domain}', v_move.source_key,
                        v_row.organization_id, 'UPDATE',
                        v_row.value_type::text,
                        CASE
                            WHEN v_row.is_secret AND v_row.value_text IS NOT NULL
                            THEN '{masked}'
                            ELSE v_row.value_text
                        END,
                        v_row.value_json, v_row.is_secret, v_row.is_active,
                        v_row.value_type::text,
                        CASE
                            WHEN v_row.is_secret AND v_row.value_text IS NOT NULL
                            THEN '{masked}'
                            ELSE v_row.value_text
                        END,
                        v_row.value_json, v_row.is_secret, v_row.is_active,
                        NULL, '{reason}'
                    );

                    v_moved := v_moved + 1;
                END LOOP;

                UPDATE public.domain_settings
                   SET key = v_move.target_key,
                       updated_at = now()
                 WHERE domain = '{domain}'
                   AND key = v_move.source_key
                   AND organization_id IS NOT NULL;
            END LOOP;

            RAISE NOTICE '{headline}: % organization row(s) moved.', v_moved;
        END $$;
    """.format(
        values=_values_list(reverse),
        domain=_DOMAIN,
        masked=_MASKED_VALUE,
        reason=reason.replace("'", "''"),
        headline=headline,
    )


def _missing_ceiling_notice() -> str:
    """Name every key that now has narrowing rows but no platform value.

    Deliberately a notice and not a synthesised ceiling: building a platform
    value out of the union of the organization values would launder exactly the
    broadening this change removes into the ceiling itself. The operator
    composes it, with the demoted values printed above to compose it from.
    """
    tenant_pairs = _values_list(reverse=False)
    return f"""
        DO $$
        DECLARE
            v_move RECORD;
            v_narrowed INTEGER;
            v_ceiling INTEGER;
        BEGIN
            FOR v_move IN
                SELECT *
                  FROM (VALUES
                       {tenant_pairs}
                       ) AS m(platform_key, tenant_key)
            LOOP
                SELECT COUNT(*) INTO v_narrowed
                  FROM public.domain_settings
                 WHERE domain = '{_DOMAIN}'
                   AND key = v_move.tenant_key
                   AND organization_id IS NOT NULL;

                -- `is_active` counts here: an inactive ceiling row is not read
                -- by the resolver, so the deployment is unconfigured and must
                -- be told so rather than reassured by a row's mere existence.
                SELECT COUNT(*) INTO v_ceiling
                  FROM public.domain_settings
                 WHERE domain = '{_DOMAIN}'
                   AND key = v_move.platform_key
                   AND organization_id IS NULL
                   AND is_active;

                IF v_narrowed > 0 AND v_ceiling = 0 THEN
                    RAISE NOTICE
                        'webhook SSRF policy: % organization row(s) now narrow %/%, but this deployment has NO active platform row for it. The ceiling is NOT synthesised from them. Outbound webhooks stay denied for that key until an admin writes and activates the platform row: PUT /api/v1/settings/automation/%, JSON body with value_text and is_active=true. Do not rely on the process environment: it is a bootstrap-only seed input and never overwrites an existing row.',
                        v_narrowed, '{_DOMAIN}', v_move.platform_key,
                        v_move.platform_key;
                END IF;
            END LOOP;
        END $$;
    """


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # The unit lane runs on SQLite with no settings rows to move, and the
        # block below is PL/pgSQL. Same guard as `20260815_tenant_catalog_
        # discovery`.
        return

    op.execute(
        _move_block(
            reverse=False,
            reason=_UPGRADE_REASON,
            headline="webhook SSRF policy is now platform-owned",
        )
    )
    op.execute(_missing_ceiling_notice())


def downgrade() -> None:
    """Move every narrowing row back to the key it came from.

    Reverses the upgrade's EFFECT on ``public.domain_settings``: every value
    ends up under the key it started at, with the same row id. It does not
    reverse the audit trail, and deliberately so — it APPENDS its own
    ``UPDATE`` rows rather than deleting the upgrade's. A history table a
    migration can erase from is not an audit trail, and both moves genuinely
    happened.

    On a database that has changed since, an organization row written against a
    ``webhook_tenant_*`` key after the upgrade is indistinguishable from a
    demoted one and is restored as an override — which is what the pre-upgrade
    schema meant by that value anyway. No value is discarded in either
    direction.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        _move_block(
            reverse=True,
            reason=_DOWNGRADE_REASON,
            headline="webhook SSRF policy reverted to organization-writable",
        )
    )
