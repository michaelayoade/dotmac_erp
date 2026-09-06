"""Fail-closed mapping tests for Self-Care tax facts."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


def _resolve(harness: BaseSyncMixin):
    return harness._resolve_source_sales_tax_code(
        source_tax_rate_id="source-vat",
        tax_application="exclusive",
        effective_date=date(2026, 9, 6),
    )


def test_unambiguous_semantic_mapping_does_not_require_shared_display_code() -> None:
    erp_code = SimpleNamespace(tax_code="NG-VAT-7.5")
    harness = _harness([erp_code])

    assert _resolve(harness) is erp_code
    statement = str(harness.db.scalars.call_args.args[0])
    assert "is_fixed_amount IS false" in statement


def test_source_code_breaks_tie_between_semantically_equivalent_codes() -> None:
    preferred = SimpleNamespace(tax_code="VAT75")
    harness = _harness([SimpleNamespace(tax_code="NG-VAT-7.5"), preferred])

    assert _resolve(harness) is preferred


def test_ambiguous_semantic_mapping_fails_closed_with_candidate_codes() -> None:
    harness = _harness(
        [SimpleNamespace(tax_code="VAT-A"), SimpleNamespace(tax_code="VAT-B")]
    )

    with pytest.raises(TaxMappingConfigurationError, match="VAT-A, VAT-B") as caught:
        _resolve(harness)

    assert caught.value.dedupe_key == ("source-vat", "exclusive")


def test_missing_semantic_mapping_names_the_effective_date() -> None:
    harness = _harness([])

    with pytest.raises(TaxMappingConfigurationError, match="2026-09-06"):
        _resolve(harness)
