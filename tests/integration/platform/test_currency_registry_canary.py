"""PostgreSQL canary: SUPPORTED_CURRENCIES ⇄ core_fx.currency consistency.

The E4 boundary resolves minor units ONLY through the static
``SUPPORTED_CURRENCIES`` registry, while ``core_fx.currency`` is ERP's
database provisioning surface (``decimal_places``). The two must agree for
every provisioned currency — this canary iterates the registry, so adding a
currency (e.g. EUR/GBP later, per the checked-in extension path) is
automatically covered: the new code must be seeded, active, and carry
EXACTLY the registry's minor units.

Runs against the real PostgreSQL integration database (self-skipping when
PG is absent, per tests/integration conventions) inside the rolled-back
``db`` fixture: it drives the production seeding path
(``ensure_supported_currencies`` — the same path that has always seeded
NGN, and which now provisions USD too) and then asserts row-level
consistency, including against pre-existing drifted rows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.finance.core_fx.currency import Currency
from app.services.finance.money_boundary import SUPPORTED_CURRENCIES
from app.services.finance.platform.currency_context import (
    ensure_supported_currencies,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_currency_table(engine) -> None:
    """Skip cleanly when the core_fx schema has not been migrated yet."""
    insp = inspect(engine)
    try:
        has_table = insp.has_table("currency", schema="core_fx")
    except Exception as exc:  # pragma: no cover - env-specific
        pytest.skip(f"core_fx.currency not inspectable: {exc}")
    if not has_table:
        pytest.skip(
            "core_fx.currency does not exist — run `alembic upgrade head` "
            "on the test DB"
        )


def test_every_supported_currency_is_provisioned_consistently(db: Session) -> None:
    """Every production registry entry exists, is active, and matches
    decimal_places EXACTLY after the production seeding path runs."""
    assert SUPPORTED_CURRENCIES.by_code, "registry must not be empty"
    ensure_supported_currencies(db)
    db.flush()

    for code, minor_units in SUPPORTED_CURRENCIES.by_code.items():
        row = db.get(Currency, code)
        assert row is not None, (
            f"{code} is in SUPPORTED_CURRENCIES but has no core_fx.currency "
            "row even after ensure_supported_currencies — the seeding path "
            "and the boundary registry have diverged"
        )
        assert row.is_active is True, f"{code} is provisioned but inactive"
        assert row.decimal_places == minor_units, (
            f"{code}: core_fx.currency.decimal_places={row.decimal_places} "
            f"disagrees with SUPPORTED_CURRENCIES minor_units={minor_units} "
            "— the boundary and the database would quantize differently"
        )


def test_seeding_is_idempotent(db: Session) -> None:
    """Second pass creates nothing (existing rows are never rewritten)."""
    ensure_supported_currencies(db)
    db.flush()
    assert ensure_supported_currencies(db) == 0
