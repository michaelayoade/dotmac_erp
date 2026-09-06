"""Prepare worker metrics storage before importing Celery or Prometheus."""

from __future__ import annotations

import os

from app.prometheus_multiprocess import prepare_configured_multiprocess_directory


def main() -> None:
    prepare_configured_multiprocess_directory()
    os.execvp(  # noqa: S606 -- replace this process with the locked CLI  # nosec B606 B607
        "celery",  # noqa: S607 -- resolved from the locked image virtualenv
        ("celery", "-A", "app.celery_app", "worker", "-l", "info"),
    )


if __name__ == "__main__":
    main()
