"""Fail-closed mapping tests for Self-Care tax facts."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.finance.tax.tax_code import TaxCode
from app.services.dotmac_sub.client import TaxRateRecord
from app.services.dotmac_sub.sync._base import (
    BaseSyncMixin,
    TaxMappingConfigurationError,
)


def _harness(candidates: list[object]) -> BaseSyncMixin:
    harness = object.__new__(BaseSyncMixin)
    harness.organization_id = uuid.uuid4()
    harness.db = MagicMock()
    harness.db.scalars.return_value.all.return_value = candidates
    harness._source_tax_rates = {
        "source-vat": TaxRateRecord(
            id="source-vat",
            name="VAT 7.5%",
            rate=Decimal("7.5"),
            code="VAT75",
        )
    }
    harness._source_tax_code_cache = {}
    return harness


def _candidate(code: str) -> SimpleNamespace:
    return SimpleNamespace(tax_code_id=uuid.uuid4(), tax_code=code)


def _resolve(harness: BaseSyncMixin):
    return harness._resolve_source_sales_tax_code(
        source_tax_rate_id="source-vat",
        tax_application="exclusive",
        effective_date=date(2026, 9, 6),
    )


def test_unambiguous_semantic_mapping_does_not_require_shared_display_code() -> None:
    erp_code = _candidate("NG-VAT-7.5")
    harness = _harness([erp_code])

    assert _resolve(harness) is erp_code
    statement = str(harness.db.scalars.call_args.args[0])
    assert "is_fixed_amount IS false" in statement


def test_source_code_breaks_tie_between_semantically_equivalent_codes() -> None:
    preferred = _candidate("VAT75")
    harness = _harness([_candidate("NG-VAT-7.5"), preferred])

    assert _resolve(harness) is preferred


def test_ambiguous_semantic_mapping_fails_closed_with_candidate_codes() -> None:
    harness = _harness([_candidate("VAT-A"), _candidate("VAT-B")])

    with pytest.raises(TaxMappingConfigurationError, match="VAT-A, VAT-B") as caught:
        _resolve(harness)

    assert caught.value.dedupe_key == ("source-vat", "exclusive")


def test_missing_semantic_mapping_names_the_effective_date() -> None:
    harness = _harness([])

    with pytest.raises(TaxMappingConfigurationError, match="2026-09-06"):
        _resolve(harness)


def test_cached_tax_mapping_rehydrates_from_the_active_session() -> None:
    original = _candidate("VAT75")
    refreshed = _candidate("VAT75")
    refreshed.tax_code_id = original.tax_code_id
    harness = _harness([original])

    assert _resolve(harness) is original

    # A batch commit/expunge detaches ORM instances. The cache therefore keeps
    # only the stable UUID and resolves the row again through the current session.
    harness.db.scalars.return_value.all.return_value = []
    harness.db.get.return_value = refreshed

    assert _resolve(harness) is refreshed
    harness.db.get.assert_called_once_with(TaxCode, original.tax_code_id)
