"""
One-off backfill: shift all Mono-sourced bank statement line dates by +1 day.

Background — Mono's API ships every transaction timestamp as Lagos midnight
expressed in UTC (e.g. ``2026-05-14T23:00:00.000Z`` means "business day
May 15 in Africa/Lagos"). The previous ``MonoSyncService._parse_date``
implementation took ``.date()`` on the raw UTC datetime, dropping back to
the previous calendar day. As a result every historical Mono line was
stored one day earlier than the date Mono's own UI displays.

The parser was fixed in mono_sync.py; this script realigns the 278 rows
that landed before the fix.

Safety:
- Touches only rows where ``transaction_id LIKE 'mono_%'`` — CSV imports
  use user-supplied dates and are left alone.
- Junction-table GL matches are keyed by line_id/journal_line_id, so the
  date shift does NOT unlink reconciled rows.
- Refuses to run a second time: stamps a ``lagos_backfilled`` timestamp
  into each touched row's ``raw_data``; aborts if any Mono row already
  carries that marker.
- Single transaction; rolls back on any inconsistency between expected
  and observed row count.

Run:
    docker exec dotmac_erp_app python -m app.tools.backfill_mono_lagos_date
    # or with --dry-run to preview without committing
    docker exec dotmac_erp_app python -m app.tools.backfill_mono_lagos_date --dry-run
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

from app.db.session_context import cross_org_session

logger = logging.getLogger(__name__)


def run(*, dry_run: bool) -> int:
    """Return the row count shifted (or that would be shifted on dry-run)."""
    # Cross-org by design: shifts every Mono-sourced banking row regardless of
    # tenant. cross_org_session() makes that application-layer intent explicit;
    # the raw SQL targets a banking table that does not yet have RLS. This must
    # be dispositioned when that domain gains database protection.
    with cross_org_session() as db:
        before = db.execute(
            text(
                "SELECT COUNT(*) FROM banking.bank_statement_lines "
                "WHERE transaction_id LIKE 'mono_%'"
            )
        ).scalar_one()
        print(f"Mono rows in DB: {before}")
        if before == 0:
            print("Nothing to do.")
            return 0

        # Sentinel — every committed row gets a ``lagos_backfilled`` marker
        # in raw_data. If any Mono row already carries it, a previous run
        # committed and we must not double-shift.
        already_shifted = db.execute(
            text(
                "SELECT COUNT(*) FROM banking.bank_statement_lines "
                "WHERE transaction_id LIKE 'mono_%' "
                "AND raw_data ? 'lagos_backfilled'"
            )
        ).scalar_one()
        if already_shifted > 0:
            print(
                f"ABORT: {already_shifted} Mono rows already carry the "
                "lagos_backfilled marker — backfill ran previously. "
                "Re-running would double-shift dates."
            )
            return 0

        # Spot-check a couple of rows before
        samples = db.execute(
            text(
                "SELECT line_id, transaction_date, value_date, amount, "
                "LEFT(description, 40) AS d "
                "FROM banking.bank_statement_lines "
                "WHERE transaction_id LIKE 'mono_%' "
                "ORDER BY transaction_date DESC LIMIT 3"
            )
        ).all()
        print("\nSample (3 newest) BEFORE:")
        for r in samples:
            print(
                f"  amount={r.amount:>12} txn={r.transaction_date} "
                f"val={r.value_date} | {r.d}"
            )

        # The marker in raw_data is what makes the sentinel above work on
        # re-run — without it we cannot tell a backfilled row from one that
        # happened to land on the right Lagos date.
        result = db.execute(
            text(
                """
                UPDATE banking.bank_statement_lines
                SET transaction_date = transaction_date + INTERVAL '1 day',
                    value_date       = CASE WHEN value_date IS NULL THEN NULL
                                            ELSE value_date + INTERVAL '1 day' END,
                    raw_data         = COALESCE(raw_data, '{}'::jsonb)
                                       || jsonb_build_object(
                                            'lagos_backfilled',
                                            to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SSOF')
                                          )
                WHERE transaction_id LIKE 'mono_%'
                """
            )
        )
        affected = result.rowcount
        print(f"\nShifted {affected} rows by +1 day")

        if affected != before:
            print(f"ABORT: affected ({affected}) != expected ({before}); rolling back.")
            db.rollback()
            return 0

        # Spot-check after — same rows by line_id
        after = db.execute(
            text(
                "SELECT line_id, transaction_date, value_date, amount, "
                "LEFT(description, 40) AS d "
                "FROM banking.bank_statement_lines "
                "WHERE line_id = ANY(:ids)"
            ),
            {"ids": [r.line_id for r in samples]},
        ).all()
        print("\nSame rows AFTER:")
        for r in after:
            print(
                f"  amount={r.amount:>12} txn={r.transaction_date} "
                f"val={r.value_date} | {r.d}"
            )

        if dry_run:
            db.rollback()
            print("\nDRY RUN — rolled back, no changes persisted.")
        else:
            db.commit()
            print("\nCOMMITTED.")

        return affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the UPDATE inside a transaction but roll back instead of "
        "committing. Use to preview the row count and spot-check samples.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
