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
    MoneyBoundaryError,
    boundary_currency,
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
    money = to_boundary_money(Decimal("1500"), "JPY")
    assert from_money(money) == (Decimal("1500"), "JPY")


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
    assert serialize_amount(to_boundary_money(Decimal("1500"), "JPY")) == "1500"


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
    # would misrepresent it (1.234 BHD would be "excess precision"). It is
    # rejected outright until provisioned with minor_units=3 in the registry.
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("1.234"), "BHD")
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        to_boundary_money(Decimal("1.23"), "BHD")


def test_supported_currency_registry_is_the_minor_unit_authority() -> None:
    # The explicit provisioned set — extending it is a deliberate code (and
    # core_fx.currency) change, never an inferred default.
    assert dict(SUPPORTED_CURRENCIES) == {
        "NGN": 2,
        "USD": 2,
        "EUR": 2,
        "GBP": 2,
        "JPY": 0,
    }
    assert boundary_currency("ngn").minor_units == 2
    assert boundary_currency(" JPY ").minor_units == 0


def test_rejects_excess_minor_unit_precision() -> None:
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        to_boundary_money(Decimal("10.005"), "NGN")
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        to_boundary_money(Decimal("1500.5"), "JPY")


def test_accepts_trailing_zero_extra_places() -> None:
    # "10.0500" carries no information beyond 2dp — not excess precision.
    assert from_money(to_boundary_money(Decimal("10.0500"), "NGN"))[0] == Decimal(
        "10.05"
    )


def test_rejects_unparseable_and_unsupported_types() -> None:
    with pytest.raises(MoneyBoundaryError, match="not a valid amount"):
        to_boundary_money("not-money", "NGN")
    with pytest.raises(MoneyBoundaryError, match="unsupported monetary type"):
        to_boundary_money([1], "NGN")


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
    assert minor_unit("JPY") == Decimal("1")
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
