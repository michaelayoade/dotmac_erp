#!/usr/bin/env python3
"""Report and optionally clear suspicious bank auto-match suggestions.

This script focuses on weak fallback matches, especially date/amount-only
suggestions that are more likely to need human review.

Default mode is report-only. Use ``--clear-suggested`` to delete only
suggested rows that meet the suspicious criteria. Confirmed matches are never
auto-reversed here; they are reported for manual review through the normal
unmatch flow.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import UUID

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db.session_context import session_for_org
from app.services.finance.banking.suspicious_matches import (
    SuspiciousMatch,
    clear_suspicious_suggested_matches,
    collect_suspicious_matches,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Organization UUID")
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help="Maximum suspicious rows to print",
    )
    parser.add_argument(
        "--clear-suggested",
        action="store_true",
        help="Delete suspicious suggested rows after reporting them",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of plain text",
    )
    return parser.parse_args(argv)


def _render_json(matches: list[SuspiciousMatch], cleared: int) -> None:
    payload = {
        "total_suspicious": len(matches),
        "suggested": sum(1 for match in matches if match.match_state == "suggested"),
        "confirmed": sum(1 for match in matches if match.match_state == "confirmed"),
        "cleared_suggested": cleared,
        "matches": [
            {
                "statement_line_id": str(match.statement_line_id),
                "journal_line_id": str(match.journal_line_id),
                "statement_number": match.statement_number,
                "transaction_date": str(match.transaction_date),
                "amount": str(match.amount),
                "description": match.description,
                "match_state": match.match_state,
                "confidence_score": match.confidence_score,
                "explanation": match.explanation,
                "matched_at": (
                    match.matched_at.isoformat() if match.matched_at else None
                ),
            }
            for match in matches
        ],
    }
    print(json.dumps(payload, indent=2))


def _render_text(matches: list[SuspiciousMatch], cleared: int, limit: int) -> None:
    suggested = sum(1 for match in matches if match.match_state == "suggested")
    confirmed = sum(1 for match in matches if match.match_state == "confirmed")
    print(f"suspicious matches: {len(matches)}")
    print(f"suggested: {suggested}")
    print(f"confirmed: {confirmed}")
    if cleared:
        print(f"cleared suggested: {cleared}")
    print("")
    for match in matches[:limit]:
        print(
            f"[{match.match_state}] stmt_line={match.statement_line_id} "
            f"journal_line={match.journal_line_id} amount={match.amount} "
            f"date={match.transaction_date} conf={match.confidence_score} "
            f"statement={match.statement_number or '-'}"
        )
        if match.explanation:
            print(f"  reason: {match.explanation}")
        if match.description:
            print(f"  desc: {match.description[:160]}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    org_id = UUID(args.org_id)

    with session_for_org(org_id) as db:
        matches = collect_suspicious_matches(db, org_id)
        cleared = 0
        if args.clear_suggested:
            cleared = clear_suspicious_suggested_matches(db, org_id)
            db.commit()

    if args.json:
        _render_json(matches, cleared)
    else:
        _render_text(matches, cleared, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
