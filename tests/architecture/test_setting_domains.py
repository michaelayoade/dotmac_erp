"""Setting domains are module-declared and registry-validated, never enumerated.

Governance ADR 0007: a vocabulary whose members belong to modules is declared by
those modules and validated by a registry; the layer that stores it never
enumerates them. ERP previously did the opposite — a 21-member Python enum backed
by a PostgreSQL `settingdomain` type — so adding a domain cost an
`ALTER TYPE ... ADD VALUE` migration.

These are structural rather than behavioural on purpose: the failure mode being
guarded is a future change quietly reintroducing a closed list, which no
behavioural test would notice until a product needed a domain it didn't have.
"""

from __future__ import annotations

import ast
import enum
import pathlib

import pytest
import sqlalchemy as sa

from app.models.domain_settings import DomainSetting, SettingDomain, SettingDomainType
from app.services.setting_domains import (
    SETTING_DOMAIN_OWNERS,
    DuplicateSettingDomainError,
    SettingDomainRegistry,
    UndeclaredSettingDomainError,
    registry,
)
from app.services.settings_spec import SETTINGS_SPECS

_MODEL = pathlib.Path("app/models/domain_settings.py")


# ── The type is open, and the column is not an enum ─────────────────────────


def test_setting_domain_is_not_a_python_enum() -> None:
    assert not issubclass(SettingDomain, enum.Enum)
    assert issubclass(SettingDomain, str)


def test_an_undeclared_domain_can_still_be_CONSTRUCTED() -> None:
    """The type is open by design — which is exactly why construction stopped
    being validation and `registry().require()` had to take over."""
    assert str(SettingDomain("anything-at-all")) == "anything-at-all"


def test_value_still_reads_like_the_enum_did() -> None:
    """~331 call sites read `domain.value`; the port must not break them."""
    assert SettingDomain.payments.value == "payments"
    assert SettingDomain("fleet") == "fleet"


def test_the_column_stores_a_string_not_a_database_enum() -> None:
    column = DomainSetting.__table__.c.domain
    assert isinstance(column.type, SettingDomainType)
    assert isinstance(column.type.impl, sa.String)
    assert column.type.impl.length == 120
    assert not isinstance(column.type, sa.Enum)


def test_the_model_source_declares_no_enum_for_the_domain() -> None:
    """A source-level check as well as a runtime one: someone reintroducing
    `Enum(SettingDomain)` should fail here even if the mapped type still looks
    right through some indirection."""
    tree = ast.parse(_MODEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SettingDomain":
            bases = {getattr(b, "id", getattr(b, "attr", None)) for b in node.bases}
            assert "Enum" not in bases, "SettingDomain must not subclass an enum"
    assert "Enum(SettingDomain)" not in _MODEL.read_text(encoding="utf-8")


def test_the_loaded_type_round_trips_to_setting_domain() -> None:
    """Plain `String` would hand back an ordinary `str` and every `.value` read
    would break — that is what `SettingDomainType` exists to prevent."""
    column_type = SettingDomainType()
    stored = column_type.process_bind_param(SettingDomain.gl, None)
    assert stored == "gl" and type(stored) is str
    loaded = column_type.process_result_value("gl", None)
    assert isinstance(loaded, SettingDomain)
    assert loaded.value == "gl"


# ── Ownership ───────────────────────────────────────────────────────────────


def test_every_owner_declares_something() -> None:
    active = registry()
    assert len(active.domains()) == len(SETTING_DOMAIN_OWNERS)


def test_duplicate_ownership_fails_at_construction(tmp_path) -> None:
    """A domain with two owners has no owner. Construction IS validation."""
    import sys
    import types

    first = types.ModuleType("_probe_owner_a")
    first.SETTING_DOMAINS = ("shared",)
    second = types.ModuleType("_probe_owner_b")
    second.SETTING_DOMAINS = ("shared",)
    sys.modules["_probe_owner_a"] = first
    sys.modules["_probe_owner_b"] = second
    try:
        with pytest.raises(DuplicateSettingDomainError):
            SettingDomainRegistry.from_owners(["_probe_owner_a", "_probe_owner_b"])
    finally:
        del sys.modules["_probe_owner_a"], sys.modules["_probe_owner_b"]


def test_declared_and_consumed_domains_match() -> None:
    """Both directions. A spec naming an undeclared domain would never resolve;
    a declared domain nothing uses is a dead declaration."""
    active = registry()
    declared = {str(d) for d in active.domains()}
    spec_domains = {str(spec.domain) for spec in SETTINGS_SPECS}

    undeclared = sorted(spec_domains - declared)
    assert not undeclared, f"spec(s) name undeclared domain(s): {undeclared}"

    # `features` is the documented exception in the other direction — see below.
    unused = sorted(declared - spec_domains - {"features"})
    assert not unused, f"declared domain(s) no spec uses: {unused}"


def test_features_is_declared_even_though_no_spec_names_it() -> None:
    """A domain is real because something WRITES it, not because a spec
    describes it: `FeatureFlagService.toggle()` writes `domain_settings` rows
    under `features` directly, and the settings APIs expose them."""
    active = registry()
    assert active.is_declared("features")
    assert active.owner("features") == "app.services.feature_flag_service"
    assert not any(str(spec.domain) == "features" for spec in SETTINGS_SPECS)


def test_operations_is_undeclared() -> None:
    """Zero specs and zero references anywhere — the one genuinely dead member
    of the old enum. Existing rows survive the migration as VARCHAR and simply
    become unwritable; nothing is deleted."""
    active = registry()
    assert not active.is_declared("operations")
    with pytest.raises(UndeclaredSettingDomainError):
        active.require("operations")


def test_the_accessors_are_exactly_the_declared_domains() -> None:
    """The class attributes are convenience, not authority. Asserting the two
    sets are equal is what stops them becoming a second, quieter list."""
    accessors = {
        name
        for name in vars(SettingDomain)
        if not name.startswith("_") and isinstance(getattr(SettingDomain, name), SettingDomain)
    }
    assert accessors == {str(d) for d in registry().domains()}


# ── The write boundary ──────────────────────────────────────────────────────


def test_an_undeclared_domain_is_rejected_at_the_orm_boundary(db_session) -> None:
    """Checked on the model, not in `DomainSettings`: there are eight direct
    `DomainSetting(...)` constructors across six modules and only two live in
    that service, so a service-level check would miss six of them."""
    db_session.add(
        DomainSetting(
            domain=SettingDomain("not-a-real-domain"),
            key="probe",
            value_type="string",
            value_text="x",
        )
    )
    with pytest.raises(UndeclaredSettingDomainError):
        db_session.flush()
    db_session.rollback()


def test_a_declared_domain_writes_normally(db_session) -> None:
    """Sensitivity companion — proves the rejection above is about the DOMAIN
    and not about the row shape."""
    db_session.add(
        DomainSetting(
            domain=SettingDomain.fleet,
            key="probe",
            value_type="string",
            value_text="x",
        )
    )
    db_session.flush()
    db_session.rollback()


def test_require_rejects_untrusted_input_that_construction_accepts() -> None:
    """The regression this whole registry exists to prevent: once the type is
    open, `SettingDomain(user_input)` no longer raises on a typo."""
    active = registry()
    assert str(SettingDomain("payment")) == "payment"  # typo, constructs fine
    with pytest.raises(UndeclaredSettingDomainError):
        active.require("payment")
    assert active.require("payments") == SettingDomain.payments


def test_undeclared_is_a_value_error() -> None:
    """Eight call sites parse a domain inside `except ValueError`, because
    construction used to be the validation. They must keep catching it."""
    assert issubclass(UndeclaredSettingDomainError, ValueError)
