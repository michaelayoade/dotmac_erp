"""Every domain that declares specs must have a settings service.

`resolve_value` looks the domain up in `DOMAIN_SETTINGS_SERVICE`:

    service = DOMAIN_SETTINGS_SERVICE.get(domain)
    setting = None
    if service:
        ...

A domain with no entry therefore resolves EVERY one of its keys to the spec
default — no exception, no log line, no failed query. The setting is simply
unreadable, and the caller cannot tell that from "nobody has configured it yet".

`SettingDomain.gl` sat in that state with two specs. `fx_revaluation` worked
only because it queried `DomainSetting` directly, which is also why nobody
noticed: the one reader had already routed around the gap. Attempting to move
it onto the resolver is what surfaced this.
"""

from __future__ import annotations

from app.services.setting_domains import SettingDomain
from app.services.settings_spec import DOMAIN_SETTINGS_SERVICE, SETTINGS_SPECS


def test_every_domain_with_specs_has_a_service() -> None:
    declared = {spec.domain for spec in SETTINGS_SPECS}
    served = set(DOMAIN_SETTINGS_SERVICE)
    missing = sorted(d.value for d in declared - served)
    assert not missing, (
        f"domain(s) {missing} declare settings specs but have no entry in "
        "DOMAIN_SETTINGS_SERVICE, so resolve_value silently returns the spec "
        "default for every key in them. Add `DomainSettings(SettingDomain.X)` "
        "in app/services/domain_settings.py and register it."
    )


def test_the_scan_is_not_vacuous() -> None:
    """A spec list that failed to import would make the check above pass while
    proving nothing.

    Asserted on the PROPERTY — both collections are populated and a domain
    known to be served is present — rather than on an invented count. A
    threshold like `> 50` encodes today's spec total as a rule and starts
    failing the day someone legitimately removes settings.
    """
    assert SETTINGS_SPECS, "no specs loaded — the module failed to import"
    assert DOMAIN_SETTINGS_SERVICE, "no services registered"
    assert SettingDomain.auth in DOMAIN_SETTINGS_SERVICE, (
        "the auth domain is unserved, which means the registry did not build"
    )
