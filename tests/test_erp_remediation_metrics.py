"""Low-cardinality signals used by the ERP remediation dashboard."""

from app.metrics import (
    AUDIT_DISPATCH_EVENTS,
    DOTMAC_SUB_INCREMENTAL_LOCK_CONTENTION,
    DOTMAC_SUB_STAFF_SYNC_ROWS,
    observe_audit_dispatch,
    observe_dotmac_sub_incremental_lock_contention,
    observe_dotmac_sub_staff_sync_row,
)


def _value(metric, suffix: str, labels: dict[str, str] | None = None) -> float:
    labels = labels or {}
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith(suffix) and sample.labels == labels:
                return float(sample.value)
    raise AssertionError(f"metric sample {suffix} {labels} was not found")


def test_staff_sync_failure_is_counted_without_entity_labels() -> None:
    labels = {"outcome": "permanent_failure"}
    DOTMAC_SUB_STAFF_SYNC_ROWS.labels(**labels)
    before = _value(DOTMAC_SUB_STAFF_SYNC_ROWS, "_total", labels)

    observe_dotmac_sub_staff_sync_row("permanent failure")

    assert _value(DOTMAC_SUB_STAFF_SYNC_ROWS, "_total", labels) == before + 1


def test_incremental_lock_contention_is_counted() -> None:
    before = _value(DOTMAC_SUB_INCREMENTAL_LOCK_CONTENTION, "_total")

    observe_dotmac_sub_incremental_lock_contention()

    assert _value(DOTMAC_SUB_INCREMENTAL_LOCK_CONTENTION, "_total") == before + 1


def test_audit_dispatch_failure_is_counted() -> None:
    labels = {"outcome": "failure"}
    AUDIT_DISPATCH_EVENTS.labels(**labels)
    before = _value(AUDIT_DISPATCH_EVENTS, "_total", labels)

    observe_audit_dispatch("failure")

    assert _value(AUDIT_DISPATCH_EVENTS, "_total", labels) == before + 1
