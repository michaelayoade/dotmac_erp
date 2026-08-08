"""Setting-domain registry — which domains are real, and which module owns each.

`domain_settings.domain` used to be a PostgreSQL `settingdomain` enum with 21
members, so adding a domain meant an `ALTER TYPE ... ADD VALUE` migration
(`alembic/versions/20260224_add_settingdomain_banking.py` is exactly that, and
nothing else). Governance ADR 0007 makes the rule fleet-wide: a vocabulary whose
members belong to modules is DECLARED by those modules and validated by a
registry; the layer that stores it never enumerates them.

So ownership lives with the code that owns the settings — `app.services.fleet`
declares `fleet`, `app.services.finance.banking` declares `banking` — and this
module is the only thing that answers "is this domain real, and whose is it?".

## Where the check happens

* **At startup** — `validate_registry()` builds the registry (construction fails
  on a domain claimed by two owners) and proves every registered `SettingSpec`
  names a declared domain. A typo becomes a boot failure, not a setting that
  silently never resolves.
* **At every write** — an ORM `before_insert`/`before_update` listener on
  `DomainSetting`, NOT the `DomainSettings` service. There are eight direct
  `DomainSetting(...)` constructors across six modules; only two are in that
  service, so a service-level check would miss six of them. The listener sits
  beside the existing encryption listener, which is there for the same reason.
* **At every parse of untrusted input** — `require()`. This matters more than it
  looks: `SettingDomain` is now an open `str`, so `SettingDomain(user_input)` no
  longer raises on a typo the way the enum did. Every place that used
  construction as validation calls `require()` instead.

## Why the INSTALLED owner set, not `ENABLED_MODULES`

A stored row outlives any deployment's enabled set. Disabling a module must not
turn a real domain into an undeclared one for the rows that already exist, or
for whatever is still reading them.

## Retirement

This registry is ERP-local ON PURPOSE and temporary. `dotmac_kernel` carries the
same contract from `0.1.0a14` (`dotmac_kernel.setting_domains`); when ERP adopts
it, this module and the local settings paths retire in the same cutover, so no
parallel authority survives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType

from app.models.domain_settings import SettingDomain

# The modules that OWN setting domains. Each declares a `SETTING_DOMAINS` tuple;
# this list says which modules are installed, never which domains exist — that
# answer belongs to the modules themselves.
#
# `operations` is deliberately absent from every declaration: it had no
# `SettingSpec` and no reference in the codebase. Its column is now
# `VARCHAR(120)`, so any row that exists under it survives untouched and simply
# becomes unwritable.
SETTING_DOMAIN_OWNERS: tuple[str, ...] = (
    "app.services.auth",
    "app.services.audit",
    "app.services.scheduler",
    "app.services.automation",
    "app.services.email",
    "app.services.feature_flag_service",
    "app.services.finance.rpt",
    "app.services.finance.payments",
    "app.services.support",
    "app.services.inventory",
    "app.services.pm",
    "app.services.fleet",
    "app.services.procurement",
    "app.services.settings",
    "app.services.people.payroll",
    "app.services.finance.banking",
    "app.services.coach",
    "app.services.nextcloud",
    "app.services.expense",
    "app.services.finance.gl",
)


class SettingDomainError(Exception):
    """Base for setting-domain declaration and validation failures."""


class DuplicateSettingDomainError(SettingDomainError):
    """Two modules declared the same domain — there is no single owner."""


class UndeclaredSettingDomainError(SettingDomainError, ValueError):
    """A domain was used that no installed module declares.

    Subclasses `ValueError` deliberately. Every site that parsed a domain used
    `SettingDomain(value)` inside `except ValueError` — construction WAS the
    validation. Now that the type is open, `require()` does that job, and
    inheriting from `ValueError` means those handlers keep catching it instead
    of eight of them silently ceasing to.
    """


@dataclass(frozen=True)
class SettingDomainRegistry:
    """The immutable set of declared domains, by owning module.

    Construction IS validation: a domain claimed by two owners raises here,
    rather than at whichever write happens to trip over it first.
    """

    owner_by_domain: Mapping[str, str]

    def __post_init__(self) -> None:
        # A frozen dataclass holding a `dict` is not immutable — the field cannot
        # be rebound, but the mapping it points at can still be mutated, which
        # does not satisfy the Dotmac typed-contract standard. Wrap a private
        # copy in a read-only view: the source dict is built here and reachable
        # from nowhere else, so the result is immutable in fact and not merely
        # by convention.
        object.__setattr__(
            self, "owner_by_domain", MappingProxyType(dict(self.owner_by_domain))
        )

    @classmethod
    def from_owners(cls, owners: Iterable[str]) -> SettingDomainRegistry:
        owner_by_domain: dict[str, str] = {}
        for owner in owners:
            module = import_module(owner)
            declared: tuple[str, ...] = getattr(module, "SETTING_DOMAINS", ())
            if not declared:
                raise SettingDomainError(
                    f"{owner!r} is listed as a setting-domain owner but declares "
                    "no SETTING_DOMAINS — remove it from SETTING_DOMAIN_OWNERS "
                    "or declare what it owns"
                )
            for domain in declared:
                existing = owner_by_domain.get(str(domain))
                if existing is not None and existing != owner:
                    raise DuplicateSettingDomainError(
                        f"setting domain {str(domain)!r} declared by both "
                        f"{existing!r} and {owner!r} — a domain has one owner"
                    )
                owner_by_domain[str(domain)] = owner
        return cls(owner_by_domain)

    def is_declared(self, domain: SettingDomain | str) -> bool:
        return str(domain) in self.owner_by_domain

    def require(self, domain: SettingDomain | str) -> SettingDomain:
        """Return `domain` as a `SettingDomain`, or raise if undeclared.

        Use this wherever untrusted input names a domain. `SettingDomain(value)`
        no longer rejects a typo — the type is open by design — so construction
        is not validation any more.
        """
        if str(domain) not in self.owner_by_domain:
            raise UndeclaredSettingDomainError(
                f"setting domain {str(domain)!r} is not declared by any installed "
                "module — declare it in the owning module's SETTING_DOMAINS "
                "tuple rather than inventing it at the use site"
            )
        return SettingDomain(domain)

    def owner(self, domain: SettingDomain | str) -> str | None:
        return self.owner_by_domain.get(str(domain))

    def domains(self) -> tuple[SettingDomain, ...]:
        """Every declared domain, sorted — the replacement for iterating the enum."""
        return tuple(SettingDomain(d) for d in sorted(self.owner_by_domain))


_registry: SettingDomainRegistry | None = None


def registry() -> SettingDomainRegistry:
    """The process registry, built on first use from the installed owner set."""
    global _registry
    if _registry is None:
        _registry = SettingDomainRegistry.from_owners(SETTING_DOMAIN_OWNERS)
    return _registry


def reset_registry() -> None:
    """Drop the memo. For tests that install a different owner set."""
    global _registry
    _registry = None


def validate_registry() -> list[str]:
    """Build the registry and check every registered spec. Startup gate.

    Returns operator-readable errors rather than raising, so the caller decides
    severity — the same split `app.startup` already applies elsewhere. Building
    the registry itself still raises: a duplicate owner is a code defect, not a
    configuration one.
    """
    from app.services.settings_spec import SETTINGS_SPECS

    active = registry()
    undeclared = sorted(
        {
            str(spec.domain)
            for spec in SETTINGS_SPECS
            if not active.is_declared(spec.domain)
        }
    )
    return [
        f"setting spec domain {domain!r} is not declared by any installed module"
        for domain in undeclared
    ]


__all__ = [
    "SETTING_DOMAIN_OWNERS",
    "DuplicateSettingDomainError",
    "SettingDomainError",
    "SettingDomainRegistry",
    "UndeclaredSettingDomainError",
    "registry",
    "reset_registry",
    "validate_registry",
]
