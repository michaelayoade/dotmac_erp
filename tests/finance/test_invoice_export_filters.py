"""List and export share the same tenant-scoped invoice filter query."""

from __future__ import annotations

from uuid import uuid4

import pytest

# These builders import the finance web layer, which needs the private module
# packages installed by the full application environment.
pytest.importorskip("dotmac_files")
pytest.importorskip("dotmac_kernel")

from app.services.finance.ap.invoice_query import build_invoice_query as ap_query  # noqa: E402
from app.services.finance.ar.invoice_query import build_invoice_query as ar_query  # noqa: E402


@pytest.mark.parametrize(
    "query,party_key", [(ap_query, "supplier_id"), (ar_query, "customer_id")]
)
@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"search": "Acme"}, {"search"}),
        ({"status": "POSTED"}, {"status"}),
        ({"start_date": "2026-01-01"}, {"start_date"}),
        ({"end_date": "2026-02-01"}, {"end_date"}),
        ({"party": True}, {"party"}),
        (
            {
                "search": "Acme",
                "status": "POSTED",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "party": True,
            },
            {"search", "status", "start_date", "end_date", "party"},
        ),
    ],
)
def test_invoice_filter_query_covers_each_filter_and_combinations(
    query, party_key, filters, expected
):
    organization_id = uuid4()
    party_id = uuid4()
    kwargs = {key: value for key, value in filters.items() if key != "party"}
    if filters.get("party"):
        kwargs[party_key] = str(party_id)
    statement = query(None, str(organization_id), **kwargs)
    sql = str(statement).lower()
    params = statement.compile().params
    assert organization_id in params.values()
    assert "organization_id" in sql
    if "search" in expected:
        assert any("Acme" in str(value) for value in params.values())
    if "status" in expected:
        assert any(
            getattr(value, "value", None) == "POSTED" for value in params.values()
        )
    if "start_date" in expected:
        assert any(str(value) == "2026-01-01" for value in params.values())
    if "end_date" in expected:
        assert any(str(value) == "2026-02-01" for value in params.values())
    if "party" in expected:
        assert party_id in params.values()
    assert " limit " not in sql and " offset " not in sql
