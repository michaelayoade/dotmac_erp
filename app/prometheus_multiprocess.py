"""Safe startup ownership for Prometheus multiprocess scratch files."""

from __future__ import annotations

import os
from pathlib import Path

PROMETHEUS_MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"
PROMETHEUS_MULTIPROC_PATH = Path(
    "/tmp/dotmac-erp-prometheus"  # noqa: S108  # nosec B108 -- private container tmpfs
)


def clear_metric_files(directory: Path) -> int:
    """Remove only Prometheus mmap files from one already-selected directory."""

    removed = 0
    for metric_file in directory.glob("*.db"):
        if metric_file.is_file():
            metric_file.unlink()
            removed += 1
    return removed


def prepare_configured_multiprocess_directory() -> Path | None:
    """Create and clean the one reviewed container-local metrics directory."""

    configured = os.getenv(PROMETHEUS_MULTIPROC_ENV, "").strip()
    if not configured:
        return None
    directory = Path(configured)
    if directory != PROMETHEUS_MULTIPROC_PATH:
        raise RuntimeError(
            f"{PROMETHEUS_MULTIPROC_ENV} must be {PROMETHEUS_MULTIPROC_PATH}"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    clear_metric_files(directory)
    return directory
