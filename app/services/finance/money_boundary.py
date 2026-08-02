"""Exact Money/FX boundary adapter (kernel-adoption slice E4).

The ONE adapter between kernel ``dotmac_kernel.money`` values
(``Money``/``Currency``/immutable ``ExchangeRate``) and ERP's existing
``Decimal + currency_code`` contracts at external boundaries (dotmac_sub /
dotmac_crm connector payloads, command schemas, acknowledgements).

Boundary #6 of the accepted kernel-adoption plan: kernel ``Money`` is a
BOUNDARY value, never a replacement for ERP internals. ERP's ``Numeric(20,6)``
posting/functional amounts, ``Numeric(20,10)`` FX rates, tax ratios,
quantities, unit prices and posted-line snapshots remain explicitly typed ERP
``Decimal``s and never pass through this module's minor-unit quantization.

Fail-closed rules (every rejection raises :class:`MoneyBoundaryError`):

- ``float``/``bool`` monetary inputs are refused (inexact / not money);
- a missing or invalid currency code is refused — no ambiguous default is
  applied here (callers own their documented defaulting, if any);
- a well-formed but unprovisioned currency code is refused: minor units
  resolve ONLY through :data:`SUPPORTED_CURRENCIES`, never through a
  two-decimal guess for an unknown code;
- a source amount carrying more precision than the currency's minor units is
  refused at the boundary (no minor unit may be silently lost or invented);
- mixing currencies is refused;
- FX conversion NEVER performs a live provider/database lookup: a kernel
  ``ExchangeRate`` snapshot is built only from an already-persisted, ERP-owned
  ``core_fx.exchange_rate`` observation row (ERP's ``FXService`` remains the
  only FX owner and the only rate resolver).

Rounding: :data:`BOUNDARY_ROUNDING` (round-half-up) is the single, centralized
rounding decision for the touched Sub-facing slice. Derived document-level
values (e.g. per-line tax splits mirrored from source facts) round through
:func:`round_to_minor_units`; nothing else in the slice may quantize boundary
money independently.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import TYPE_CHECKING

from dotmac_kernel.money import (
    DEFAULT_ROUNDING,
    Currency,
    CurrencyMismatchError,
    Money,
    MoneyError,
)
from dotmac_kernel.money import (
    ExchangeRate as KernelExchangeRate,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.finance.core_fx.exchange_rate import (
        ExchangeRate as ErpExchangeRateObservation,
    )

__all__ = [
    "BOUNDARY_ROUNDING",
    "SUPPORTED_CURRENCIES",
    "MoneyBoundaryError",
    "boundary_currency",
    "to_boundary_money",
    "from_money",
    "serialize_amount",
    "serialize_money",
    "require_same_currency",
    "round_to_minor_units",
    "minor_unit",
    "check_settlement_identity",
    "rate_snapshot_from_observation",
    "convert_with_snapshot",
]

# The centralized rounding decision for this slice: kernel round-half-up (the
# common commercial convention, and what the Sub sync already applied via
# ``ROUND_HALF_UP``). Anything needing a different mode must say so explicitly.
BOUNDARY_ROUNDING: str = DEFAULT_ROUNDING


class MoneyBoundaryError(ValueError):
    """Fail-closed rejection of a monetary value at an external boundary."""


# ---------------------------------------------------------------------------
# Supported-currency registry — the boundary's minor-unit authority
# ---------------------------------------------------------------------------
# ERP's provisioning surface for currencies is the ``core_fx.currency`` table
# (``currency_code`` + ``decimal_places``), but this adapter must resolve in
# session-free contexts (connector record parsing, pydantic command
# validation), so the boundary carries an explicit static registry of the
# currencies ERP actually transacts. ``dotmac_kernel.money.currency`` is
# deliberately NOT used as the minor-unit source: it silently defaults any
# unknown three-letter code to 2 minor units, which would accept fabricated
# codes (``ZZZ``) and misrepresent three-decimal currencies (``BHD``). Here an
# unprovisioned code fails closed instead.
#
# Extension point: to transact a new currency at the boundary, add
# ``code: minor_units`` below (with the correct ISO-4217 fraction digits —
# e.g. BHD must be added as 3, never assumed) and provision the matching
# ``core_fx.currency`` row with the same ``decimal_places``.
#
# Provisioning evidence: NGN is the functional default
# (``settings.default_functional_currency_code``); USD/EUR/GBP are the other
# locale-derived defaults in ``app/config.py`` and appear in ERP tax seeds
# and FX observations; JPY is provisioned to keep the zero-minor-unit
# contract exercised end to end.
SUPPORTED_CURRENCIES: Mapping[str, int] = MappingProxyType(
    {
        "NGN": 2,
        "USD": 2,
        "EUR": 2,
        "GBP": 2,
        "JPY": 0,
    }
)


def boundary_currency(code: str | None, *, field: str = "currency") -> Currency:
    """Resolve a mandatory ISO-4217 currency code against the boundary's
    supported-currency registry, failing closed.

    ``None``/empty/malformed codes are rejected, and so is any well-formed
    code not provisioned in :data:`SUPPORTED_CURRENCIES` — the boundary never
    assumes a default currency and never guesses a minor-unit count for an
    unknown code.
    """
    if code is None or not isinstance(code, str) or not code.strip():
        raise MoneyBoundaryError(
            f"{field}: a currency code is required at the money boundary"
        )
    try:
        # Shape validation only (3 alpha chars); minor units come from the
        # registry below, never from a default.
        candidate = Currency(code.strip())
    except MoneyError as exc:
        raise MoneyBoundaryError(f"{field}: {exc}") from exc
    minor_units = SUPPORTED_CURRENCIES.get(candidate.code)
    if minor_units is None:
        raise MoneyBoundaryError(
            f"{field}: currency {candidate.code!r} is not provisioned in the "
            "ERP supported-currency registry; add it (with its ISO-4217 "
            "minor units) to SUPPORTED_CURRENCIES and core_fx.currency "
            "before transacting it at the boundary"
        )
    return Currency(candidate.code, minor_units)


def to_boundary_money(
    amount: object,
    currency_code: str | None,
    *,
    field: str = "amount",
) -> Money:
    """Build a typed kernel ``Money`` from an ERP ``(Decimal, currency_code)``
    boundary pair, failing closed on every inexact or ambiguous input.

    Accepts ``Decimal``/``int``/``str`` amounts only. The amount must already
    be exactly representable in the currency's minor units — excess precision
    is rejected, never rounded (rounding a source FACT would invent or lose a
    minor unit; use :func:`round_to_minor_units` for values ERP itself
    derives).
    """
    cur = boundary_currency(currency_code, field=field)
    if amount is None:
        raise MoneyBoundaryError(f"{field}: a monetary amount is required")
    if isinstance(amount, (bool, float)):
        raise MoneyBoundaryError(
            f"{field}: refusing to accept {type(amount).__name__} "
            f"{amount!r} as money; use str/Decimal"
        )
    if not isinstance(amount, (int, str, Decimal)):
        raise MoneyBoundaryError(
            f"{field}: unsupported monetary type {type(amount).__name__}"
        )
    try:
        exact = Decimal(amount)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyBoundaryError(f"{field}: not a valid amount: {amount!r}") from exc
    try:
        money = Money.of(exact, cur, rounding=BOUNDARY_ROUNDING)
    except MoneyError as exc:  # pragma: no cover - guarded above
        raise MoneyBoundaryError(f"{field}: {exc}") from exc
    if money.amount != exact:
        raise MoneyBoundaryError(
            f"{field}: {exact} exceeds the {cur.code} minor-unit precision "
            f"({cur.minor_units} decimal places) at the boundary"
        )
    return money


def from_money(money: Money) -> tuple[Decimal, str]:
    """Convert kernel ``Money`` back to ERP's ``(Decimal, currency_code)``
    contract (the exact quantized amount; nothing is re-rounded)."""
    return money.amount, money.currency.code


def serialize_amount(money: Money) -> str:
    """Exact non-scientific string for a connector payload (``"48375.00"``)."""
    return format(money.amount, "f")


def serialize_money(money: Money) -> dict[str, str]:
    """Exact wire shape for a connector payload: amount string + currency."""
    return {"amount": serialize_amount(money), "currency": money.currency.code}


def require_same_currency(*moneys: Money, field: str = "amount") -> Currency:
    """Assert every value shares one currency; return it. Mismatch fails closed."""
    if not moneys:
        raise MoneyBoundaryError(f"{field}: no monetary values supplied")
    first = moneys[0].currency
    for money in moneys[1:]:
        if money.currency != first:
            raise MoneyBoundaryError(
                f"{field}: currency mismatch at the boundary: "
                f"{first.code} vs {money.currency.code}"
            )
    return first


def minor_unit(currency_code: str | None, *, field: str = "currency") -> Decimal:
    """One minor unit of the currency (``0.01`` for NGN/USD, ``1`` for JPY)."""
    cur = boundary_currency(currency_code, field=field)
    return Decimal(1).scaleb(-cur.minor_units)


def round_to_minor_units(
    amount: Decimal,
    currency_code: str | None,
    *,
    rounding: str = BOUNDARY_ROUNDING,
    field: str = "amount",
) -> Decimal:
    """Round an ERP-DERIVED document value to the currency's minor units.

    This is the slice's single sanctioned boundary rounding (default
    round-half-up). It is for values ERP computes itself (line tax splits,
    inclusive-tax extraction); source-provided FACTS go through
    :func:`to_boundary_money`, which rejects excess precision instead.
    """
    cur = boundary_currency(currency_code, field=field)
    if isinstance(amount, (bool, float)):
        raise MoneyBoundaryError(
            f"{field}: refusing to round {type(amount).__name__} "
            f"{amount!r} as money; use Decimal"
        )
    try:
        return Money.of(Decimal(amount), cur, rounding=rounding).amount
    except (MoneyError, InvalidOperation, ValueError) as exc:
        raise MoneyBoundaryError(f"{field}: not a valid amount: {amount!r}") from exc


def check_settlement_identity(
    *,
    gross: Money,
    net: Money,
    withheld: Money,
    tolerance_minor_units: int = 1,
    field: str = "settlement",
) -> None:
    """Fail closed unless ``net + withheld == gross`` within the tolerance.

    The WHT evidence identity from the Sub tax/accounting contract
    (``net amount + WHT amount = gross amount``). ``tolerance_minor_units``
    preserves the pre-existing one-minor-unit sync tolerance; pass ``0`` for
    an exact check.
    """
    cur = require_same_currency(gross, net, withheld, field=field)
    quantum = Decimal(1).scaleb(-cur.minor_units)
    difference = (net.amount + withheld.amount - gross.amount).copy_abs()
    if difference > quantum * tolerance_minor_units:
        raise MoneyBoundaryError(
            f"{field}: accounting facts do not balance: "
            f"net {net.amount} + WHT {withheld.amount} != gross {gross.amount}"
        )


def rate_snapshot_from_observation(
    observation: ErpExchangeRateObservation,
    *,
    rate_type_code: str | None = None,
) -> KernelExchangeRate:
    """Build an immutable kernel FX snapshot from ERP's canonical, persisted
    ``core_fx.exchange_rate`` observation.

    ERP's ``FXService`` / ``core_fx`` tables remain the ONLY FX owner. This
    never looks up a rate: the caller must already hold the ERP-owned
    observation row (resolved BEFORE any posting path runs, never during it).
    The snapshot carries the pair, rate, effective time, source system and the
    observation's row identity so a conversion is always attributable.
    """
    base = boundary_currency(observation.from_currency_code, field="fx.base")
    quote = boundary_currency(observation.to_currency_code, field="fx.quote")
    rate = observation.exchange_rate
    if rate is None:
        raise MoneyBoundaryError("fx: observation has no exchange rate")
    effective = observation.effective_date
    if effective is None:
        raise MoneyBoundaryError("fx: observation has no effective date")
    source_name = observation.source.value if observation.source else "MANUAL"
    source = f"erp:core_fx:{source_name}"
    if rate_type_code:
        source = f"{source}:{rate_type_code}"
    snapshot_id = (
        str(observation.exchange_rate_id)
        if observation.exchange_rate_id is not None
        else None
    )
    if snapshot_id is None:
        raise MoneyBoundaryError(
            "fx: observation has no persisted identity; flush the ERP-owned "
            "rate row before converting at the boundary"
        )
    try:
        return KernelExchangeRate(
            base=base,
            quote=quote,
            rate=Decimal(rate),
            as_of=datetime.combine(effective, time.min, tzinfo=timezone.utc),
            source=source,
            rate_id=snapshot_id,
        )
    except MoneyError as exc:
        raise MoneyBoundaryError(f"fx: {exc}") from exc


def convert_with_snapshot(
    money: Money,
    snapshot: KernelExchangeRate,
    *,
    rounding: str = BOUNDARY_ROUNDING,
) -> Money:
    """Convert boundary money using a pre-fetched immutable snapshot only."""
    try:
        return snapshot.convert(money, rounding=rounding)
    except CurrencyMismatchError as exc:
        raise MoneyBoundaryError(f"fx: {exc}") from exc
