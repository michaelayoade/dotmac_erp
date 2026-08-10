"""The declaration record a module exports to claim its setting domains.

Separate from `app.services.setting_domains` to break a cycle: owning modules
import this to declare, and the registry imports the owning modules to collect.
This module imports only the domain type, so nothing imports back into it.

**Why a record rather than a bare tuple.** Governance schema v3 asks a profile to
name a `declaration_field`, and the engine looks for it as an ANNOTATED FIELD ON
A CLASS — a module-level `SETTING_DOMAINS: tuple[str, ...]` would not satisfy
that check, so declaring one and pointing the profile at it would be claiming a
gate that does not actually hold. `setting_domains` below is that annotated
field, and this file is what a v3 profile's `declaration_paths` names.

The record is frozen and normalises to a tuple of `SettingDomain`, so a
declaration cannot be mutated after import and cannot smuggle in a mutable
sequence — the Dotmac typed-contract standard requires deep immutability, not
just an unrebindable attribute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.domain_settings import SettingDomain


@dataclass(frozen=True)
class ModuleSettingDomains:
    """One module's claim over a set of setting domains.

    Exported as `SETTING_DOMAINS` by each owning module and read by
    `app.services.setting_domains.SettingDomainRegistry`.
    """

    setting_domains: tuple[SettingDomain, ...]

    def __init__(self, setting_domains: Iterable[SettingDomain | str]) -> None:
        normalised = tuple(SettingDomain(domain) for domain in setting_domains)
        if not normalised:
            raise ValueError(
                "a module that declares SETTING_DOMAINS must name at least one "
                "domain — drop the attribute instead of declaring nothing"
            )
        object.__setattr__(self, "setting_domains", normalised)


__all__ = ["ModuleSettingDomains"]
