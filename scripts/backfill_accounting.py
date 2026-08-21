#!/usr/bin/env python3
"""Extract one organization's general ledger for the `dotmac-accounting` backfill.

Read-only in every mode that runs today.  The extraction half of the backfill is
finished and can be run against a live database now; the load half refuses until
the module is pinned and `ACCOUNTING_COMPOSITION_ENABLED` is set, because a
loader written against an unreleased signature is guesswork that a passing test
cannot catch.

Running it before the module exists is the point: the plan it prints is the
review artifact for the cutover.  It answers the questions that decide whether
the backfill is a day or a quarter — how many accounts, how many periods, how
many posted lines per period, and whether anything in ERP's chart of accounts
has a classification the module has no mapping for.

    # what would be backfilled, and is any of it unmappable?
    python3 scripts/backfill_accounting.py --org-id <uuid> --out plan.json

    # per-period work list with ERP's acceptance digest for each period
    python3 scripts/backfill_accounting.py --org-id <uuid> --periods

`--periods` digests every posted line in the organization and is proportionate
to ledger size; `--year` narrows it to one fiscal year while iterating.

Exit codes: 0 plan produced, 2 bad arguments, 3 ERP holds something the
extraction cannot faithfully represent (the message names it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Any
from uuid import UUID

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.accounting_adoption import (  # noqa: E402
    AccountingCompositionNotReady,
    composition_state,
)
from app.db.session_context import session_for_org  # noqa: E402
from app.services.finance.gl.accounting_backfill import (  # noqa: E402
    AccountingBackfillExtractor,
    BackfillNotPossible,
    MasterBackfill,
    PeriodWorkItem,
    load_masters,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Organization UUID")
    parser.add_argument(
        "--periods",
        action="store_true",
        help=(
            "Include the per-period work list with ERP's acceptance digest. "
            "Digests every posted line; cost is proportionate to ledger size."
        ),
    )
    parser.add_argument(
        "--year",
        help="Restrict the period work list to one fiscal year code (e.g. FY2026)",
    )
    parser.add_argument("--out", help="Write JSON here instead of stdout")
    parser.add_argument(
        "--load",
        action="store_true",
        help=(
            "Write the extracted masters into the module. Refuses unless the "
            "module is installed AND ACCOUNTING_COMPOSITION_ENABLED is true."
        ),
    )
    return parser.parse_args(argv)


def _masters_payload(masters: MasterBackfill) -> dict[str, Any]:
    return {
        "organization_id": str(masters.organization_id),
        "counts": masters.counts(),
        "categories": [asdict(row) for row in masters.categories],
        "accounts": [asdict(row) for row in masters.accounts],
        "fiscal_years": [asdict(row) for row in masters.fiscal_years],
        "fiscal_periods": [asdict(row) for row in masters.fiscal_periods],
        "dimensions": [asdict(binding) for binding in masters.dimensions],
        "dimension_values": [asdict(row) for row in masters.dimension_values],
    }


def _period_payload(item: PeriodWorkItem) -> dict[str, Any]:
    """Counts and the digest — never the lines themselves.

    A plan holding every posted line would be a copy of the ledger with none of
    its guarantees.  What a reviewer needs is the size of each period and the
    value the module will have to reproduce.
    """
    return {
        "fiscal_year_code": item.scope.fiscal_year_code,
        "period_number": item.scope.period_number,
        "erp_fiscal_period_id": str(item.fiscal_period_id),
        "journal_count": item.journal_count,
        "posted_line_count": item.posted_line_count,
        "erp_digest": {
            "version": item.erp_digest.digest_version,
            "line_count": item.erp_digest.line_count,
            "total_debit": f"{item.erp_digest.total_debit:f}",
            "total_credit": f"{item.erp_digest.total_credit:f}",
            "balanced": item.erp_digest.is_balanced,
            "ordered_line_digest": item.erp_digest.ordered_line_digest,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        organization_id = UUID(args.org_id)
    except ValueError:
        print(f"--org-id is not a UUID: {args.org_id!r}", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {"composition": composition_state()}

    # session_for_org primes BOTH tenant layers — the ORM listener and the
    # PostgreSQL RLS GUC.  An unprimed session here would not fail; it would
    # return zero rows and print an empty plan that looks like a clean ledger.
    with session_for_org(organization_id) as db:
        extractor = AccountingBackfillExtractor(db)
        try:
            masters = extractor.extract_masters(organization_id)
            payload["masters"] = _masters_payload(masters)
            if args.periods:
                items = extractor.period_work_list(organization_id)
                if args.year:
                    items = tuple(
                        item
                        for item in items
                        if item.scope.fiscal_year_code == args.year
                    )
                payload["periods"] = [_period_payload(item) for item in items]
                payload["period_totals"] = {
                    "periods": len(items),
                    "journals": sum(item.journal_count for item in items),
                    "posted_lines": sum(item.posted_line_count for item in items),
                    "unbalanced_periods": [
                        item.scope.label()
                        for item in items
                        if not item.erp_digest.is_balanced
                    ],
                }
        except BackfillNotPossible as exc:
            print(f"backfill cannot represent ERP faithfully: {exc}", file=sys.stderr)
            return 3

        if args.load:
            try:
                load_masters(masters)
            except AccountingCompositionNotReady as exc:
                print(f"--load refused: {exc}", file=sys.stderr)
                return 3

    rendered = json.dumps(payload, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
