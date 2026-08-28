"""Resolve an ambiguous Employment Type activation migration outcome.

The deploy script calls this only after the Alembic container reports failure.
It never treats an unavailable or unfamiliar database state as permission to
restart legacy writers.  Exit status 3 is the sole rollback-safe answer: the
activation revision is absent *and* the pre-activation bootstrap fence still
exists, which PostgreSQL commits or rolls back atomically with the switch.
"""

from __future__ import annotations

import os
from enum import IntEnum

from sqlalchemy import create_engine, text

ACTIVATION_REVISION = "20260828_people_et_activation"
MIGRATION_DATABASE_URL = "MIGRATION_DATABASE_URL"


class ProbeExit(IntEnum):
    ACTIVATED = 0
    DEFINITELY_PRE_ACTIVATION = 3
    AMBIGUOUS = 4


def classify(*, revision_recorded: bool, bootstrap_fence_present: bool) -> ProbeExit:
    """Return the only rollback-safe classification for the observed state."""
    if revision_recorded:
        return ProbeExit.ACTIVATED
    if bootstrap_fence_present:
        return ProbeExit.DEFINITELY_PRE_ACTIVATION
    return ProbeExit.AMBIGUOUS


def main() -> int:
    database_url = os.environ.get(MIGRATION_DATABASE_URL)
    if not database_url:
        print("ambiguous: migration database material is unavailable")
        return ProbeExit.AMBIGUOUS

    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT "
                    "EXISTS ("
                    "SELECT 1 FROM public.alembic_version "
                    "WHERE version_num = :revision"
                    ") AS revision_recorded, "
                    "to_regprocedure("
                    "'hr.lock_employment_type_bootstrap()'"
                    ") IS NOT NULL AS bootstrap_fence_present"
                ),
                {"revision": ACTIVATION_REVISION},
            ).one()
    except Exception:
        # A probe error is deliberately opaque: connection details and database
        # exceptions can contain secret material. The deploy remains stopped.
        print("ambiguous: activation outcome could not be read")
        return ProbeExit.AMBIGUOUS

    outcome = classify(
        revision_recorded=bool(row.revision_recorded),
        bootstrap_fence_present=bool(row.bootstrap_fence_present),
    )
    if outcome is ProbeExit.ACTIVATED:
        print("activated: activation revision is committed")
    elif outcome is ProbeExit.DEFINITELY_PRE_ACTIVATION:
        print("pre-activation: bootstrap fence is committed and activation is absent")
    else:
        print("ambiguous: activation revision and bootstrap fence are both absent")
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
