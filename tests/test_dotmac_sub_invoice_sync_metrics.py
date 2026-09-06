"""Low-cardinality Prometheus signals for invoice sync containment."""

from app.metrics import (
    DOTMAC_SUB_INVOICE_SYNC_LIMITS,
    DOTMAC_SUB_INVOICE_SYNC_ROWS,
    observe_dotmac_sub_invoice_sync_limit,
    observe_dotmac_sub_invoice_sync_row,
)


def _value(metric, suffix: str, labels: dict[str, str] | None = None) -> float:
    labels = labels or {}
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith(suffix) and sample.labels == labels:
                return float(sample.value)
    raise AssertionError(f"metric sample {suffix} {labels} was not found")


def test_invoice_sync_row_outcome_is_normalized_and_counted() -> None:
    DOTMAC_SUB_INVOICE_SYNC_ROWS.labels(outcome="source_accounting_mismatch")
    before = _value(
        DOTMAC_SUB_INVOICE_SYNC_ROWS,
        "_total",
        {"outcome": "source_accounting_mismatch"},
    )

    observe_dotmac_sub_invoice_sync_row("Source Accounting Mismatch")

    assert (
        _value(
            DOTMAC_SUB_INVOICE_SYNC_ROWS,
            "_total",
            {"outcome": "source_accounting_mismatch"},
        )
        == before + 1
    )


def test_invoice_sync_attempt_limit_is_counted() -> None:
    before = _value(DOTMAC_SUB_INVOICE_SYNC_LIMITS, "_total")

    observe_dotmac_sub_invoice_sync_limit()

    assert _value(DOTMAC_SUB_INVOICE_SYNC_LIMITS, "_total") == before + 1
