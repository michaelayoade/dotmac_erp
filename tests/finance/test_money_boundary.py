"""Unit tests for the E4 exact Money/FX boundary adapter.

Pins the adapter's contract: exact round-trips at maximum minor-unit
precision, byte-exact connector serialization, every fail-closed rejection
(float, missing currency, currency mismatch, excess precision, ambiguous
defaults), the centralized rounding decision, and FX conversion that only ever
uses an immutable ERP-owned observation snapshot — never a live lookup.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from dotmac_kernel.money import Currency, Money

from app.models.finance.core_fx.exchange_rate import (
    ExchangeRate as ErpExchangeRate,
)
from app.models.finance.core_fx.exchange_rate import (
    ExchangeRateSource,
)
from app.services.finance.money_boundary import (
    BOUNDARY_ROUNDING,
    SUPPORTED_CURRENCIES,
    CurrencyRegistry,
    MoneyBoundaryError,
    boundary_currency,
    check_canonical_money_lexeme,
    check_canonical_money_string,
    check_settlement_identity,
    convert_with_snapshot,
    from_money,
    minor_unit,
    rate_snapshot_from_observation,
    require_same_currency,
    round_to_minor_units,
    serialize_amount,
    serialize_money,
    to_boundary_money,
)

# Test-scoped registry: keeps the zero-minor-unit (JPY) and three-minor-unit
# (BHD) contracts exercised WITHOUT provisioning those currencies in the
# production SUPPORTED_CURRENCIES set (which ships NGN and USD only).
_TEST_REGISTRY = CurrencyRegistry({"NGN": 2, "JPY": 0, "BHD": 3})


# ---------------------------------------------------------------------------
# Exact round-trips (Decimal + currency_code -> Money -> Decimal + code)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.01"),  # max NGN minor-unit precision
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("999999999999.99"),  # large value, full precision
        Decimal("48375.00"),
        Decimal("100"),  # fewer places than minor units is fine
    ],
)
def test_round_trip_is_exact_for_ngn(amount: Decimal) -> None:
    money = to_boundary_money(amount, "NGN")
    back, code = from_money(money)
    assert code == "NGN"
    assert back == amount  # numerically identical — nothing lost or invented


def test_round_trip_zero_minor_unit_currency() -> None:
    money = to_boundary_money(Decimal("1500"), "JPY", registry=_TEST_REGISTRY)
    assert from_money(money) == (Decimal("1500"), "JPY")


def test_round_trip_three_minor_unit_currency() -> None:
    # BHD via the test registry (3 minor units) — exact at full precision.
    money = to_boundary_money(Decimal("1.234"), "BHD", registry=_TEST_REGISTRY)
    assert from_money(money) == (Decimal("1.234"), "BHD")


def test_round_trip_accepts_int_and_str() -> None:
    assert from_money(to_boundary_money(100, "NGN"))[0] == Decimal("100")
    assert from_money(to_boundary_money("100.45", "NGN"))[0] == Decimal("100.45")


# ---------------------------------------------------------------------------
# Byte-exact connector serialization
# ---------------------------------------------------------------------------


def test_serialization_is_byte_exact() -> None:
    money = to_boundary_money(Decimal("48375.00"), "NGN")
    assert serialize_amount(money) == "48375.00"
    assert serialize_money(money) == {"amount": "48375.00", "currency": "NGN"}


def test_serialization_never_uses_scientific_notation() -> None:
    money = to_boundary_money(Decimal("1E+2"), "NGN")
    assert serialize_amount(money) == "100.00"
    big = to_boundary_money(Decimal("123456789012345678.99"), "NGN")
    assert serialize_amount(big) == "123456789012345678.99"


def test_serialization_pins_minor_units() -> None:
    # Money quantizes to the currency's minor units — "100" wires as "100.00".
    assert serialize_amount(to_boundary_money(Decimal("100"), "NGN")) == "100.00"
    assert (
        serialize_amount(
            to_boundary_money(Decimal("1500"), "JPY", registry=_TEST_REGISTRY)
        )
        == "1500"
    )
    assert (
        serialize_amount(
            to_boundary_money(Decimal("1.2"), "BHD", registry=_TEST_REGISTRY)
        )
        == "1.200"
    )


# ---------------------------------------------------------------------------
# Fail-closed rejections
# ---------------------------------------------------------------------------


def test_rejects_float() -> None:
    with pytest.raises(MoneyBoundaryError, match="float"):
        to_boundary_money(100.45, "NGN")


def test_rejects_bool() -> None:
    with pytest.raises(MoneyBoundaryError, match="bool"):
        to_boundary_money(True, "NGN")


def test_rejects_none_amount() -> None:
    with pytest.raises(MoneyBoundaryError, match="required"):
        to_boundary_money(None, "NGN")


def test_rejects_missing_currency() -> None:
    with pytest.raises(MoneyBoundaryError, match="currency code is required"):
        to_boundary_money(Decimal("1"), None)
    with pytest.raises(MoneyBoundaryError, match="currency code is required"):
        to_boundary_money(Decimal("1"), "")
    with pytest.raises(MoneyBoundaryError, match="currency code is required"):
        to_boundary_money(Decimal("1"), "   ")


def test_rejects_invalid_currency_code() -> None:
    with pytest.raises(MoneyBoundaryError, match="invalid ISO-4217"):
        boundary_currency("N1N")
    with pytest.raises(MoneyBoundaryError, match="invalid ISO-4217"):
        boundary_currency("NAIRA")


def test_rejects_unprovisioned_currency_codes() -> None:
    # kernel_currency() silently gives unknown 3-letter codes 2 minor units;
    # the boundary registry refuses them instead — a fake code must never
    # become transactable money.
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        boundary_currency("ZZZ")
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("10.00"), "ZZZ")


def test_rejects_three_decimal_currency_until_provisioned() -> None:
    # BHD legitimately has 3 minor units; accepting it with an assumed 2
    # would misrepresent it (1.234 BHD would be "excess precision"). The
    # PRODUCTION registry rejects it outright until provisioned with
    # minor_units=3 (tests use _TEST_REGISTRY for the 3-minor-unit contract).
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("1.234"), "BHD")
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("1.23"), "BHD")


def test_supported_currency_registry_is_the_minor_unit_authority() -> None:
    # The explicit production provisioned set: NGN and USD ONLY (E4 review
    # ruling). EUR/GBP arrive later behind checked-in provisioning plus a
    # core_fx.currency database consistency test; extending the set is a
    # deliberate code change, never an inferred default.
    assert dict(SUPPORTED_CURRENCIES.by_code) == {"NGN": 2, "USD": 2}
    assert boundary_currency("ngn").minor_units == 2
    assert boundary_currency(" USD ").minor_units == 2
    # JPY/EUR/GBP are NOT provisioned for production money.
    for unprovisioned in ("JPY", "EUR", "GBP"):
        with pytest.raises(MoneyBoundaryError, match="not provisioned"):
            boundary_currency(unprovisioned)


def test_injected_test_registry_never_leaks_into_production_default() -> None:
    # Passing a registry is per-call; the default path still fails closed.
    assert to_boundary_money(
        Decimal("1500"), "JPY", registry=_TEST_REGISTRY
    ).amount == Decimal("1500")
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("1500"), "JPY")


def test_rejects_excess_minor_unit_precision() -> None:
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        to_boundary_money(Decimal("10.005"), "NGN")
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        to_boundary_money(Decimal("1500.5"), "JPY", registry=_TEST_REGISTRY)
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        to_boundary_money(Decimal("1.2345"), "BHD", registry=_TEST_REGISTRY)


def test_rejects_non_finite_values() -> None:
    # NaN/Infinity parse as valid Decimals but are not money — refused with
    # the typed boundary error, never allowed to reach quantization.
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(MoneyBoundaryError, match="non-finite"):
            to_boundary_money(bad, "NGN")
        with pytest.raises(MoneyBoundaryError, match="non-finite"):
            to_boundary_money(Decimal(bad), "NGN")
    # As floats they are refused by type before finiteness even matters.
    with pytest.raises(MoneyBoundaryError, match="float"):
        to_boundary_money(float("nan"), "NGN")
    with pytest.raises(MoneyBoundaryError, match="float"):
        to_boundary_money(float("inf"), "NGN")


def test_accepts_trailing_zero_extra_places() -> None:
    # "10.0500" carries no information beyond 2dp — not excess precision.
    assert from_money(to_boundary_money(Decimal("10.0500"), "NGN"))[0] == Decimal(
        "10.05"
    )


def test_rejects_unparseable_and_unsupported_types() -> None:
    with pytest.raises(MoneyBoundaryError, match="non-canonical money string"):
        to_boundary_money("not-money", "NGN")
    with pytest.raises(MoneyBoundaryError, match="unsupported monetary type"):
        to_boundary_money([1], "NGN")


# ---------------------------------------------------------------------------
# Canonical money-string grammar (the ONE lexical wire representation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    ["1e3", " 100.00 ", "+100.00", "01.00", ".50", "100.", "1_000.00", "1,000.00"],
)
def test_string_backstop_rejects_non_canonical_lexemes(literal: str) -> None:
    with pytest.raises(MoneyBoundaryError, match="non-canonical money string"):
        to_boundary_money(literal, "NGN")
    with pytest.raises(MoneyBoundaryError, match="non-canonical money string"):
        check_canonical_money_lexeme(literal)


@pytest.mark.parametrize("literal", ["100", "100.0", "100.000"])
def test_string_backstop_requires_exact_minor_unit_digits(literal: str) -> None:
    # The currency-aware half: exactly minor_units fractional digits.
    with pytest.raises(MoneyBoundaryError, match="fractional digits"):
        to_boundary_money(literal, "NGN")


def test_zero_minor_unit_canonical_form_takes_no_fraction() -> None:
    to_boundary_money("1500", "JPY", registry=_TEST_REGISTRY)  # canonical
    with pytest.raises(MoneyBoundaryError, match="no fractional part"):
        to_boundary_money("1500.0", "JPY", registry=_TEST_REGISTRY)
    with pytest.raises(MoneyBoundaryError, match="fractional digits"):
        to_boundary_money("1.23", "BHD", registry=_TEST_REGISTRY)
    to_boundary_money("1.230", "BHD", registry=_TEST_REGISTRY)  # canonical


def test_canonical_grammar_matches_serialize_amount_exactly() -> None:
    # Round-trip alignment: whatever serialize_amount emits (negatives
    # included, as a single leading "-") is accepted by the grammar and
    # re-parses to the same value.
    cases = [
        (Decimal("48375.00"), "NGN", None),
        (Decimal("-0.01"), "NGN", None),
        (Decimal("0"), "NGN", None),
        (Decimal("-123456789.99"), "NGN", None),
        (Decimal("1500"), "JPY", _TEST_REGISTRY),
        (Decimal("-1.234"), "BHD", _TEST_REGISTRY),
    ]
    for amount, code, registry in cases:
        money = to_boundary_money(amount, code, registry=registry)
        wire = serialize_amount(money)
        cur = boundary_currency(code, registry=registry)
        check_canonical_money_string(wire, minor_units=cur.minor_units)
        assert to_boundary_money(wire, code, registry=registry).amount == money.amount


def test_currency_mismatch_fails_closed() -> None:
    ngn = to_boundary_money(Decimal("1"), "NGN")
    usd = to_boundary_money(Decimal("1"), "USD")
    with pytest.raises(MoneyBoundaryError, match="currency mismatch"):
        require_same_currency(ngn, usd)
    assert require_same_currency(ngn, ngn).code == "NGN"


# ---------------------------------------------------------------------------
# Centralized rounding decision
# ---------------------------------------------------------------------------


def test_round_to_minor_units_is_half_up() -> None:
    assert round_to_minor_units(Decimal("10.005"), "NGN") == Decimal("10.01")
    assert round_to_minor_units(Decimal("10.004"), "NGN") == Decimal("10.00")


def test_round_to_minor_units_allows_explicit_mode() -> None:
    assert round_to_minor_units(
        Decimal("10.005"), "NGN", rounding=ROUND_HALF_EVEN
    ) == Decimal("10.00")


def test_round_to_minor_units_rejects_float() -> None:
    with pytest.raises(MoneyBoundaryError, match="float"):
        round_to_minor_units(10.005, "NGN")  # type: ignore[arg-type]


def test_minor_unit() -> None:
    assert minor_unit("NGN") == Decimal("0.01")
    assert minor_unit("JPY", registry=_TEST_REGISTRY) == Decimal("1")
    assert minor_unit("BHD", registry=_TEST_REGISTRY) == Decimal("0.001")
    assert BOUNDARY_ROUNDING == "ROUND_HALF_UP"


# ---------------------------------------------------------------------------
# WHT settlement identity (net + WHT = gross)
# ---------------------------------------------------------------------------


def _ngn(value: str) -> Money:
    return to_boundary_money(Decimal(value), "NGN")


def test_settlement_identity_balanced() -> None:
    check_settlement_identity(
        gross=_ngn("107.50"), net=_ngn("100.00"), withheld=_ngn("7.50")
    )


def test_settlement_identity_tolerates_one_minor_unit() -> None:
    check_settlement_identity(
        gross=_ngn("107.51"), net=_ngn("100.00"), withheld=_ngn("7.50")
    )


def test_settlement_identity_rejects_beyond_tolerance() -> None:
    with pytest.raises(MoneyBoundaryError, match="do not balance"):
        check_settlement_identity(
            gross=_ngn("107.52"), net=_ngn("100.00"), withheld=_ngn("7.50")
        )


def test_settlement_identity_exact_mode() -> None:
    with pytest.raises(MoneyBoundaryError, match="do not balance"):
        check_settlement_identity(
            gross=_ngn("107.51"),
            net=_ngn("100.00"),
            withheld=_ngn("7.50"),
            tolerance_minor_units=0,
        )


def test_settlement_identity_rejects_currency_mismatch() -> None:
    with pytest.raises(MoneyBoundaryError, match="currency mismatch"):
        check_settlement_identity(
            gross=to_boundary_money(Decimal("107.50"), "USD"),
            net=_ngn("100.00"),
            withheld=_ngn("7.50"),
        )


# ---------------------------------------------------------------------------
# FX: immutable ERP-owned observation snapshots only — never a live lookup
# ---------------------------------------------------------------------------


def _observation(**overrides: object) -> ErpExchangeRate:
    defaults: dict = {
        "exchange_rate_id": uuid.UUID("00000000-0000-0000-0000-00000000f00d"),
        "organization_id": uuid.uuid4(),
        "from_currency_code": "USD",
        "to_currency_code": "NGN",
        "rate_type_id": uuid.uuid4(),
        "effective_date": date(2026, 7, 1),
        "exchange_rate": Decimal("1500.5000000000"),
        "source": ExchangeRateSource.MANUAL,
    }
    defaults.update(overrides)
    return ErpExchangeRate(**defaults)


def test_rate_snapshot_carries_full_observation_identity() -> None:
    snapshot = rate_snapshot_from_observation(_observation(), rate_type_code="SPOT")
    assert snapshot.base == Currency("USD", 2)
    assert snapshot.quote == Currency("NGN", 2)
    assert snapshot.rate == Decimal("1500.5000000000")
    assert snapshot.as_of == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert snapshot.source == "erp:core_fx:MANUAL:SPOT"
    assert snapshot.rate_id == "00000000-0000-0000-0000-00000000f00d"


def test_convert_with_snapshot_is_exact() -> None:
    snapshot = rate_snapshot_from_observation(_observation())
    result = convert_with_snapshot(
        to_boundary_money(Decimal("100.00"), "USD"), snapshot
    )
    assert from_money(result) == (Decimal("150050.00"), "NGN")


def test_convert_with_snapshot_rejects_wrong_base_currency() -> None:
    snapshot = rate_snapshot_from_observation(_observation())
    with pytest.raises(MoneyBoundaryError, match="cannot convert"):
        convert_with_snapshot(to_boundary_money(Decimal("100.00"), "NGN"), snapshot)


def test_rate_snapshot_rejects_unpersisted_observation() -> None:
    with pytest.raises(MoneyBoundaryError, match="no persisted identity"):
        rate_snapshot_from_observation(_observation(exchange_rate_id=None))


def test_rate_snapshot_rejects_identity_pair() -> None:
    with pytest.raises(MoneyBoundaryError, match="must differ"):
        rate_snapshot_from_observation(
            _observation(from_currency_code="NGN", to_currency_code="NGN")
        )


def test_rate_snapshot_rejects_nonpositive_rate() -> None:
    with pytest.raises(MoneyBoundaryError, match="positive"):
        rate_snapshot_from_observation(_observation(exchange_rate=Decimal("0")))
