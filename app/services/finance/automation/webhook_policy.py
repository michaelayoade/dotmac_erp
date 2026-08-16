"""The one owner of outbound-webhook target policy.

Two layers, and they compose by CONJUNCTION, never by union:

* the platform CEILING — ``webhook_allowed_hosts``, ``webhook_allowed_domains``,
  ``webhook_allow_insecure``, ``webhook_allow_localhost`` and
  ``webhook_max_timeout_seconds``, all declared
  ``scope=SettingScopeAuthority.PLATFORM``, read at platform scope only and
  writable only through the platform settings route;
* an optional organization RESTRICTION — the ``webhook_tenant_*`` keys plus
  ``webhook_timeout_seconds``, which may only NARROW the ceiling.

There is deliberately no code path by which an organization value widens a
platform value. :func:`compose_webhook_policy` is the only composition, and
``tests/services/test_webhook_policy.py`` is its proof.

Status: this module is the DECLARED owner. The existing readers in
``app/services/finance/automation/workflow.py`` and ``app/services/hooks/
registry.py`` still resolve these settings themselves and are cut over to it
in a following change; until then the two coexist, and only the ownership of
the KEYS (the write refusal and the read-side scope override) is in force.

Why the narrowing gets its own keys
-----------------------------------

ERP's settings resolver is an OVERRIDE algebra: a more specific row outranks a
less specific one (``DomainSettings.get_by_key``'s
``ORDER BY (organization_id = :org) DESC``). Narrow-only is a CONJUNCTION
algebra. ``resolve_value`` returns one row's value; it cannot express a
composition of two, and teaching it to intersect for four keys would put a
per-key special case inside the generic resolver while every other reader of
the same key — the settings API, the export, the admin form — still saw
override semantics. Two algebras behind one name is how this reappears in a
year. So the narrowing is a distinct set of keys, composed here.

What this module does NOT claim
-------------------------------

``public.domain_settings`` has no RLS policy, in production or at migration
heads, and cannot straightforwardly get the standard one: every policy
``add_rls_policies`` writes is ``organization_id = get_current_organization_id()``
and a platform row's ``organization_id`` is NULL, which matches no GUC value.
Ownership here is therefore APPLICATION-enforced — an ORM write listener plus a
read-side scope override — not database-enforced. Do not describe it otherwise.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

# The platform ceiling's keys, and the process-environment fallback for each.
# A deployment-level environment variable IS a platform-level source, so it
# stays the fallback; it is never merged with an organization's value.
PLATFORM_KEYS: tuple[tuple[str, str | None], ...] = (
    ("webhook_allowed_hosts", "WEBHOOK_ALLOWED_HOSTS"),
    ("webhook_allowed_domains", "WEBHOOK_ALLOWED_DOMAINS"),
    ("webhook_allow_insecure", "WEBHOOK_ALLOW_INSECURE"),
    ("webhook_allow_localhost", "WEBHOOK_ALLOW_LOCALHOST"),
    ("webhook_max_timeout_seconds", "WEBHOOK_MAX_TIMEOUT_SECONDS"),
)

# The organization's optional narrowing. No environment fallback: a per-tenant
# narrowing has no meaningful process-environment form.
TENANT_KEYS: tuple[str, ...] = (
    "webhook_tenant_allowed_hosts",
    "webhook_tenant_allowed_domains",
    "webhook_tenant_allow_insecure",
    "webhook_tenant_allow_localhost",
    "webhook_timeout_seconds",
)

# Floor for any effective timeout. A clamp that could reach 0 would turn a
# misconfigured maximum into "every outbound call fails immediately".
MIN_TIMEOUT_SECONDS = 1.0

# Used only when the ceiling cannot be read at all (no session, no environment
# value). 300 is `webhook_timeout_seconds`' existing max_value, so a deployment
# that never sets the new key keeps exactly today's behaviour.
DEFAULT_MAX_TIMEOUT_SECONDS = 300.0


# ─────────────────────────────────────────────────────────────────────────────
# Coercion
#
# The `_optional_*` forms differ from their plain counterparts in exactly one
# way: an absent value becomes None rather than a default, because None is the
# identity element of the conjunction below and a default is not.
# ─────────────────────────────────────────────────────────────────────────────


def _coerce_csv(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    return [item.strip() for item in items if item and str(item).strip()]


def _coerce_bool(value: object | None, default: bool) -> bool:
    coerced = _coerce_optional_bool(value)
    return default if coerced is None else coerced


def _coerce_optional_bool(value: object | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _coerce_float(value: object | None, default: float) -> float:
    coerced = _coerce_optional_float(value)
    return default if coerced is None else coerced


def _coerce_optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def normalize_host(host: str) -> str:
    """Fold a hostname to the form the allowlists are compared in.

    Lowercase, and the FQDN's trailing dot removed: ``ACME.COM.`` and
    ``acme.com`` are the same target, and a matcher that treated them
    differently would be trivially evadable.
    """
    return host.strip().lower().rstrip(".")


def _normalize_hosts(raw: object | None) -> frozenset[str]:
    return frozenset(
        normalized for item in _coerce_csv(raw) if (normalized := normalize_host(item))
    )


def _normalize_domains(raw: object | None) -> frozenset[str]:
    return frozenset(
        normalized
        for item in _coerce_csv(raw)
        if (normalized := normalize_host(item).lstrip("."))
    )


def _matches(host: str, hosts: frozenset[str], domains: frozenset[str]) -> bool:
    """Default-deny host matching over ONE (hosts, domains) pair.

    An empty pair denies everything. That is the whole reason a tenant
    restriction cannot be expressed as a bare `_matches` call — see
    :meth:`TenantWebhookRestriction.permits_host`, where "no opinion" has to be
    the identity `True` rather than this function's `False`.
    """
    host = normalize_host(host)
    if not hosts and not domains:
        return False
    if host in hosts:
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def narrow_only(ceiling: bool, tenant: bool | None, *, control: str) -> bool:
    """Combine one platform flag with one organization flag, narrowing ONLY.

    Written as four named branches rather than as ``ceiling and tenant``,
    because the direction is the whole point and an ``and`` states it only by
    implication — a later edit to ``or`` reads as a typo, not as the removal of
    a security boundary.

    Both flags this is used for — ``allow_insecure`` and ``allow_localhost`` —
    are PERMISSIONS: ``True`` means the protection is OFF. So, in terms of
    protection rather than of permission:

    * an organization may always turn the protection ON  (permission -> False);
    * an organization may NEVER turn the protection OFF  (permission -> True
      against a ``False`` ceiling is refused here, loudly);
    * no opinion (``None``) leaves the ceiling exactly as it stands, which is
      the identity element of the conjunction — NOT ``False``, or every
      deployment would lose its permissions on the day the keys appeared.

    ``control`` names the setting in the refusal log; it is not used in the
    decision.
    """
    if tenant is None:
        return ceiling
    if tenant is False:
        # Narrowing. Always honoured, whatever the ceiling says.
        return False
    if ceiling is False:
        # The one widening attempt this whole module exists to refuse. It is
        # reachable only through a stored row, so it is logged rather than
        # raised: an outbound webhook is not the place to surface a settings
        # misconfiguration as a crash.
        logger.warning(
            "Ignoring organization %s=true: the platform ceiling forbids it. "
            "An organization setting may narrow the webhook policy, never "
            "widen it.",
            control,
        )
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# The two layers and their composition
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WebhookCeiling:
    """The deployment's maximum permission. Nothing may exceed this."""

    allowed_hosts: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()
    allow_insecure: bool = False
    allow_localhost: bool = False
    max_timeout_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.allowed_hosts or self.allowed_domains)

    def permits_host(self, host: str) -> bool:
        return _matches(host, self.allowed_hosts, self.allowed_domains)


@dataclass(frozen=True)
class TenantWebhookRestriction:
    """One organization's optional narrowing. Absent fields are the identity.

    ``None`` for a boolean means "no opinion", NOT "false". An organization
    that has never touched the setting must not silently force a platform
    ``True`` off — that would be the conjunction's identity element chosen
    wrongly, and it would break every deployment on the day the keys appear.
    """

    allowed_hosts: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()
    allow_insecure: bool | None = None
    allow_localhost: bool | None = None
    timeout_seconds: float | None = None

    @property
    def narrows_hosts(self) -> bool:
        """Has this organization actually stated a host narrowing?

        Both empty lists is the ABSENCE of a narrowing. It has exactly one
        meaning, and it is neither of the two it could be mistaken for:

        * it is NOT "an allowlist of nothing", which would deny every target
          for every organization that has not opted in — the ceiling's empty
          lists mean deny-all, this layer's do not;
        * it is NOT "inherit the platform lists as this organization's own".
          Nothing here ever copies from the ceiling. The ceiling is a separate
          conjunct that is consulted directly, so there is no inheritance step
          for a later edit to get backwards.
        """
        return bool(self.allowed_hosts or self.allowed_domains)

    def permits_host(self, host: str) -> bool:
        if not self.narrows_hosts:
            return True  # identity for the conjunction, not a default-deny
        return _matches(host, self.allowed_hosts, self.allowed_domains)


# The identity element. Named, so a call site says which of the two possible
# meanings of "empty" it intends; `compose_webhook_policy(ceiling, None)` and
# `compose_webhook_policy(ceiling, NO_NARROWING)` are the same policy.
NO_NARROWING = TenantWebhookRestriction()


@dataclass(frozen=True)
class EffectiveWebhookPolicy:
    """What one organization may actually do: ``ceiling AND restriction``."""

    ceiling: WebhookCeiling
    restriction: TenantWebhookRestriction

    def permits_host(self, host: str) -> bool:
        # Predicate conjunction, NOT per-list set intersection. Intersecting
        # the lists is wrong and the counter-example is ordinary: ceiling
        # domains {acme.com}, no ceiling hosts, and an organization narrowing
        # to the single host api.acme.com. Both list intersections are empty,
        # so intersection would deny a target the ceiling plainly permits and
        # the organization plainly asked for. Conjunction answers True, and is
        # still incapable of widening because `ceiling.permits_host` is one of
        # its conjuncts.
        if not self.ceiling.permits_host(host):
            # The ceiling is evaluated FIRST and can only remove. Nothing below
            # this line can put a host back.
            return False
        if not self.restriction.narrows_hosts:
            # No narrowing stated. The ceiling's own answer stands — see
            # `TenantWebhookRestriction.narrows_hosts` for why this is not
            # "deny everything" and is not "inherit the ceiling's lists".
            return True
        return _matches(
            host, self.restriction.allowed_hosts, self.restriction.allowed_domains
        )

    @property
    def allow_insecure(self) -> bool:
        return narrow_only(
            self.ceiling.allow_insecure,
            self.restriction.allow_insecure,
            control="webhook_tenant_allow_insecure",
        )

    @property
    def allow_localhost(self) -> bool:
        return narrow_only(
            self.ceiling.allow_localhost,
            self.restriction.allow_localhost,
            control="webhook_tenant_allow_localhost",
        )

    @property
    def timeout_seconds(self) -> float:
        requested = self.restriction.timeout_seconds
        if requested is None:
            requested = self.ceiling.max_timeout_seconds
        return self.clamp_timeout(requested)

    def clamp_timeout(self, requested: float) -> float:
        """Bound any organization-supplied timeout by the platform maximum.

        Public because there is a SECOND organization-owned timeout channel —
        ``ServiceHook.handler_config["timeout_seconds"]``, stored per hook and
        validated only by a Pydantic bound — and a ceiling that clamped only
        the settings channel would be bypassed by picking the other one.
        """
        return max(
            MIN_TIMEOUT_SECONDS, min(requested, self.ceiling.max_timeout_seconds)
        )

    def as_ceiling(self) -> WebhookCeiling:
        """This policy re-expressed as a ceiling, for idempotence proofs.

        Hosts and domains are carried over unchanged from the ceiling: the
        restriction is a PREDICATE, and there is no finite list that
        represents ``C ∧ R`` exactly. Re-composing the same restriction over
        this is therefore the identity, which is precisely what L4 asserts.
        """
        return WebhookCeiling(
            allowed_hosts=self.ceiling.allowed_hosts,
            allowed_domains=self.ceiling.allowed_domains,
            allow_insecure=self.allow_insecure,
            allow_localhost=self.allow_localhost,
            max_timeout_seconds=self.ceiling.max_timeout_seconds,
        )


def compose_webhook_policy(
    ceiling: WebhookCeiling,
    restriction: TenantWebhookRestriction | None = None,
) -> EffectiveWebhookPolicy:
    """THE composition. Conjunction only; there is no union anywhere in it.

    One function, one owner: every consumer of webhook policy is to reach it
    through this or through :func:`effective_policy` below, so "may an
    organization widen the ceiling?" has exactly one place to be answered and
    exactly one place to be got wrong.

    The consumers themselves — the workflow webhook action, the service-hook
    dispatcher, the startup check — are NOT yet cut over; each still reads the
    settings for itself. This module is the owner as declared, not yet as
    called, and saying otherwise here would be the kind of claim that outlives
    the plan that justified it.
    """
    return EffectiveWebhookPolicy(
        ceiling=ceiling,
        restriction=restriction if restriction is not None else NO_NARROWING,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reading the two layers
# ─────────────────────────────────────────────────────────────────────────────


def read_platform_webhook_ceiling(db: Session | None) -> WebhookCeiling:
    """The deployment's maximum permission, at PLATFORM scope only.

    ``organization_id=None`` is STATED, not implied. Every key read here is
    ``scope=PLATFORM``, so ``resolve_value`` would discard an organization
    anyway — saying it at the call site keeps the read honest rather than
    correct by accident of a rule declared elsewhere. The retired code path
    read the platform row only because startup happened to have no ambient
    organization context, which is not the same thing.

    ``db is None`` (a caller with no session at all) falls back to the process
    environment, the same platform-level source the seed reads.
    """

    def _setting(key: str, env: str | None) -> object | None:
        if db is not None:
            try:
                value = resolve_value(
                    db, SettingDomain.automation, key, organization_id=None
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug(
                    "Falling back to the environment for automation/%s",
                    key,
                    exc_info=True,
                )
                value = None
            if value is not None:
                return value
        return os.getenv(env) if env else None

    return WebhookCeiling(
        allowed_hosts=_normalize_hosts(_setting(*PLATFORM_KEYS[0])),
        allowed_domains=_normalize_domains(_setting(*PLATFORM_KEYS[1])),
        allow_insecure=_coerce_bool(_setting(*PLATFORM_KEYS[2]), default=False),
        allow_localhost=_coerce_bool(_setting(*PLATFORM_KEYS[3]), default=False),
        max_timeout_seconds=_coerce_float(
            _setting(*PLATFORM_KEYS[4]), default=DEFAULT_MAX_TIMEOUT_SECONDS
        ),
    )


def read_tenant_restriction(
    db: Session, organization_id: UUID
) -> TenantWebhookRestriction:
    """This organization's narrowing, or the identity if it has none."""

    def _tenant(key: str) -> object | None:
        try:
            return resolve_value(
                db, SettingDomain.automation, key, organization_id=organization_id
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "Could not read automation/%s for organization %s; treating it "
                "as no narrowing",
                key,
                organization_id,
                exc_info=True,
            )
            return None

    return TenantWebhookRestriction(
        allowed_hosts=_normalize_hosts(_tenant(TENANT_KEYS[0])),
        allowed_domains=_normalize_domains(_tenant(TENANT_KEYS[1])),
        allow_insecure=_coerce_optional_bool(_tenant(TENANT_KEYS[2])),
        allow_localhost=_coerce_optional_bool(_tenant(TENANT_KEYS[3])),
        timeout_seconds=_coerce_optional_float(_tenant(TENANT_KEYS[4])),
    )


def effective_policy(
    db: Session | None, organization_id: UUID | None
) -> EffectiveWebhookPolicy:
    """The policy in force for one organization on one session."""
    ceiling = read_platform_webhook_ceiling(db)
    if db is None or organization_id is None:
        return compose_webhook_policy(ceiling, None)
    return compose_webhook_policy(ceiling, read_tenant_restriction(db, organization_id))


__all__ = [
    "DEFAULT_MAX_TIMEOUT_SECONDS",
    "MIN_TIMEOUT_SECONDS",
    "NO_NARROWING",
    "PLATFORM_KEYS",
    "TENANT_KEYS",
    "EffectiveWebhookPolicy",
    "TenantWebhookRestriction",
    "WebhookCeiling",
    "compose_webhook_policy",
    "effective_policy",
    "narrow_only",
    "normalize_host",
    "read_platform_webhook_ceiling",
    "read_tenant_restriction",
]
