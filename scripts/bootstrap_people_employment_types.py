#!/usr/bin/env python
"""Bootstrap one organization's Employment Types into ``dotmac-people``.

This is a pre-activation operator adapter, not a runtime synchronization path.
Exactly one mode is mandatory:

* ``--dry-run`` performs the full reconciliation and rolls the transaction back;
* ``--commit`` performs the first bootstrap and refuses a non-empty target;
* ``--replay`` advances an established target and is the only repeat-write mode.

The legacy ERP table remains authoritative in every mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session_context import for_each_organization
from app.services.people.hr.employment_type_bootstrap import (
    BootstrapMode,
    EmploymentTypeBootstrapResult,
    EmploymentTypeBootstrapService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap one organization's legacy Employment Types into the "
            "pre-activation dotmac-people target"
        )
    )
    parser.add_argument(
        "--organization-id",
        required=True,
        type=UUID,
        help="One explicit ERP organization UUID; fleet-wide execution is forbidden",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        choices=range(1, 201),
        metavar="1..200",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_const",
        const=BootstrapMode.DRY_RUN,
        dest="mode",
        help="Reconcile fully, emit evidence, and roll back",
    )
    mode.add_argument(
        "--commit",
        action="store_const",
        const=BootstrapMode.COMMIT,
        dest="mode",
        help="Commit the initial bootstrap; requires an empty target",
    )
    mode.add_argument(
        "--replay",
        action="store_const",
        const=BootstrapMode.REPLAY,
        dest="mode",
        help="Commit a repeat reconciliation against an established target",
    )
    return parser


def _evidence(
    result: EmploymentTypeBootstrapResult, *, committed: bool
) -> dict[str, object]:
    return {
        "committed": committed,
        "created": result.created,
        "mode": result.mode.value,
        "organization_id": str(result.organization_id),
        "source_count": result.source_count,
        "source_fingerprint_set_digest": result.source_fingerprint_set_digest,
        "target_after_count": result.target_after_count,
        "target_after_fingerprint_set_digest": (
            result.target_after_fingerprint_set_digest
        ),
        "target_before_count": result.target_before_count,
        "target_before_fingerprint_set_digest": (
            result.target_before_fingerprint_set_digest
        ),
        "tenant_id": str(result.tenant_id),
        "unchanged": result.unchanged,
        "updated": result.updated,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    found = False
    for organization_id, db in for_each_organization(
        include_inactive=True, only=args.organization_id
    ):
        found = True
        try:
            result = EmploymentTypeBootstrapService(
                db, organization_id=organization_id
            ).execute(mode=args.mode, page_size=args.page_size)
            committed = args.mode is not BootstrapMode.DRY_RUN
            if committed:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
        print(
            json.dumps(
                _evidence(result, committed=committed),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    if not found:
        raise SystemExit(f"organization {args.organization_id} was not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
