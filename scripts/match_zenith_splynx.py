"""
Match Zenith bank statement lines to Splynx customer payments.

Uses date + amount to find candidate matches, then description similarity
to disambiguate when multiple candidates exist for the same date/amount pair.

For each match, looks up the GL journal via the payment's correlation_id
and delegates to BankReconciliationService.match_statement_line().

Usage:
    poetry run python scripts/match_zenith_splynx.py --dry-run     # Preview matches
    poetry run python scripts/match_zenith_splynx.py --execute      # Apply matches
    poetry run python scripts/match_zenith_splynx.py --execute --account "Zenith 523"  # Specific account
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import joinedload

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NARRATION_RE = re.compile(r"Splynx payment via .+? Bank\.\s*(.+)", re.DOTALL)
WHITESPACE_RE = re.compile(r"\s+")


def extract_narration(desc: str | None) -> str:
    """Extract bank narration from Splynx payment description."""
    if not desc:
        return ""
    m = NARRATION_RE.search(desc)
    return m.group(1).strip() if m else ""


def normalize(s: str | None) -> str:
    """Lowercase, collapse whitespace."""
    if not s:
        return ""
    return WHITESPACE_RE.sub(" ", s.strip().lower())


def description_score(narration: str, stmt_desc: str) -> int:
    """Score how well a Splynx narration matches a statement description.

    Returns 0 if no match, higher is better.
    """
    norm_narr = normalize(narration)
    norm_desc = normalize(stmt_desc)
    if not norm_narr or not norm_desc:
        return 0

    # Strategy 1: first 25 chars of narration found in statement desc
    snippet = norm_narr[:25]
    if snippet in norm_desc:
        return 100

    # Strategy 2: count matching words (>4 chars)
    words = [w for w in norm_narr.split() if len(w) > 4]
    word_matches = sum(1 for w in words[:8] if w in norm_desc)
    if word_matches >= 2:
        return word_matches

    return 0


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------


def find_matches(db, account_name: str) -> list[tuple]:
    """Find (payment, statement_line) pairs that should be matched.

    Returns list of (payment_row, line_row, method_str) tuples.
    """

    # All Splynx payments for this bank account with a GL correlation_id
    payments = db.execute(
        text("""
        SELECT cp.payment_id::text, cp.payment_date::text, cp.amount,
               cp.description, cp.correlation_id, cp.organization_id::text
        FROM ar.customer_payment cp
        JOIN banking.bank_accounts ba ON cp.bank_account_id = ba.bank_account_id
        WHERE cp.splynx_id IS NOT NULL
          AND ba.account_name = :acct
          AND cp.correlation_id IS NOT NULL
    """),
        {"acct": account_name},
    ).fetchall()

    # All unmatched credit lines for this bank account
    lines = db.execute(
        text("""
        SELECT sl.line_id::text, sl.transaction_date::text, sl.amount,
               sl.description, sl.statement_id::text
        FROM banking.bank_statement_lines sl
        JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
        JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
        WHERE ba.account_name = :acct
          AND sl.transaction_type = 'credit'
          AND sl.is_matched = false
    """),
        {"acct": account_name},
    ).fetchall()

    logger.info(
        "%s: %d Splynx payments, %d unmatched credit lines",
        account_name,
        len(payments),
        len(lines),
    )

    # Index lines by (date, rounded_amount)
    lines_by_key: dict[tuple[str, float], list] = defaultdict(list)
    for line in lines:
        key = (line[1], round(float(line[2]), 2))
        lines_by_key[key].append(line)

    matched: list[tuple] = []
    used_line_ids: set[str] = set()

    for p in payments:
        pdate = p[1]
        pamt = round(float(p[2]), 2)
        narration = extract_narration(p[3])

        # Find candidates: exact date + amount match (within 0.50 tolerance)
        available = []
        for key, cands in lines_by_key.items():
            if key[0] == pdate and abs(key[1] - pamt) <= 0.50:
                available.extend(c for c in cands if c[0] not in used_line_ids)

        if not available:
            continue

        if len(available) == 1:
            # Unambiguous — only one statement line matches
            matched.append((p, available[0], "date+amount"))
            used_line_ids.add(available[0][0])
        elif narration:
            # Multiple candidates — use description to disambiguate
            best_line = None
            best_score = 0
            for c in available:
                score = description_score(narration, c[3] or "")
                if score > best_score:
                    best_line = c
                    best_score = score
            if best_line and best_score >= 2:
                matched.append((p, best_line, f"date+amount+desc({best_score})"))
                used_line_ids.add(best_line[0])

    return matched


def execute_matches(
    db, matches: list[tuple], *, dry_run: bool = True
) -> dict[str, int]:
    """Execute the matches using BankReconciliationService.

    For each match:
    1. Look up the GL journal entry via correlation_id
    2. Find the journal line that hits the bank GL account
    3. Call match_statement_line() with force_match=True
    """
    from app.models.finance.banking.bank_statement import BankStatement
    from app.models.finance.gl.journal_entry import (
        JournalEntry,
        JournalStatus,
    )
    from app.services.finance.banking.bank_reconciliation import (
        BankReconciliationService,
    )

    stats = {
        "matched": 0,
        "no_gl": 0,
        "no_gl_line": 0,
        "already_matched": 0,
        "error": 0,
    }

    recon_svc = BankReconciliationService()

    # Cache: bank_account_id → gl_account_id
    gl_account_cache: dict[str, set[UUID]] = {}

    def get_bank_gl_accounts(statement_id: str) -> set[UUID]:
        if statement_id in gl_account_cache:
            return gl_account_cache[statement_id]
        stmt = db.get(BankStatement, UUID(statement_id))
        if stmt and stmt.bank_account:
            accts = set()
            if stmt.bank_account.gl_account_id:
                accts.add(stmt.bank_account.gl_account_id)
            gl_account_cache[statement_id] = accts
            return accts
        gl_account_cache[statement_id] = set()
        return set()

    for i, (payment, line, method) in enumerate(matches):
        payment_id = payment[0]
        correlation_id = payment[4]
        org_id = payment[5]
        line_id = line[0]
        statement_id = line[4]

        if dry_run and i < 20:
            narr = extract_narration(payment[3])
            logger.info(
                "  [%s] PMT %s → Line %s | %s NGN %s | %s",
                method,
                correlation_id,
                line_id[:8],
                payment[1],
                f"{float(payment[2]):,.2f}",
                narr[:60],
            )

        if dry_run:
            stats["matched"] += 1
            continue

        # Look up GL journal via correlation_id
        gl_stmt = (
            select(JournalEntry)
            .options(joinedload(JournalEntry.lines))
            .where(
                JournalEntry.organization_id == UUID(org_id),
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
        journal = db.execute(gl_stmt).unique().scalar_one_or_none()
        if not journal:
            stats["no_gl"] += 1
            if i < 50:
                logger.warning("  No GL journal for correlation_id=%s", correlation_id)
            continue

        # Find the journal line that hits the bank's GL account
        gl_account_ids = get_bank_gl_accounts(statement_id)
        target_gl_line = None
        for jl in journal.lines:
            if jl.account_id in gl_account_ids:
                target_gl_line = jl
                break

        if not target_gl_line:
            stats["no_gl_line"] += 1
            if i < 50:
                logger.warning(
                    "  No bank GL line in journal for %s (accounts: %s)",
                    correlation_id,
                    gl_account_ids,
                )
            continue

        # Execute the match
        try:
            recon_svc.match_statement_line(
                db=db,
                organization_id=UUID(org_id),
                statement_line_id=UUID(line_id),
                journal_line_id=target_gl_line.line_id,
                matched_by=None,
                force_match=True,
            )
            stats["matched"] += 1
        except Exception as e:
            err_msg = str(e)
            if "already matched" in err_msg.lower():
                stats["already_matched"] += 1
            else:
                stats["error"] += 1
                if stats["error"] <= 10:
                    logger.error("  Match failed for %s: %s", line_id[:8], e)
                # Rollback to recover from DB errors (e.g., statement_timeout)
                try:
                    db.rollback()
                except Exception:
                    pass

        # Commit in batches of 50
        if stats["matched"] > 0 and stats["matched"] % 50 == 0:
            db.commit()
            logger.info("  Committed batch — %d matched so far", stats["matched"])

    if not dry_run:
        db.commit()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match Zenith Splynx payments to bank statements"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview matches without executing"
    )
    mode.add_argument("--execute", action="store_true", help="Execute matches")
    parser.add_argument(
        "--account",
        default=None,
        help="Specific account name (default: all Zenith accounts)",
    )
    args = parser.parse_args()

    from sqlalchemy import text as sa_text

    from app.db import SessionLocal

    accounts = [args.account] if args.account else ["Zenith 461", "Zenith 523"]
    dry_run = args.dry_run

    with SessionLocal() as db:
        # Increase statement timeout for bulk matching (default may be too short)
        db.execute(sa_text("SET statement_timeout = '60s'"))
        db.commit()
        grand_total = {
            "matched": 0,
            "no_gl": 0,
            "no_gl_line": 0,
            "already_matched": 0,
            "error": 0,
        }

        for acct in accounts:
            logger.info("=" * 60)
            logger.info("Processing %s (%s)", acct, "DRY RUN" if dry_run else "EXECUTE")
            logger.info("=" * 60)

            matches = find_matches(db, acct)
            logger.info("Found %d matchable pairs", len(matches))

            if not matches:
                continue

            stats = execute_matches(db, matches, dry_run=dry_run)

            logger.info("")
            logger.info("Results for %s:", acct)
            for k, v in stats.items():
                logger.info("  %s: %d", k, v)
                grand_total[k] += v

        # Fix stale statement counters after bulk matching
        if not dry_run and grand_total["matched"] > 0:
            logger.info("Fixing statement counters...")
            db.execute(
                sa_text("""
                UPDATE banking.bank_statements s
                SET matched_lines = sub.matched,
                    unmatched_lines = sub.unmatched
                FROM (
                    SELECT sl.statement_id,
                           count(*) FILTER (WHERE sl.is_matched = true) AS matched,
                           count(*) FILTER (WHERE sl.is_matched = false) AS unmatched
                    FROM banking.bank_statement_lines sl
                    GROUP BY sl.statement_id
                ) sub
                WHERE s.statement_id = sub.statement_id
                  AND (s.matched_lines <> sub.matched OR s.unmatched_lines <> sub.unmatched)
            """)
            )
            db.commit()
            logger.info("Statement counters updated.")

        logger.info("")
        logger.info("=" * 60)
        logger.info("GRAND TOTAL")
        logger.info("=" * 60)
        for k, v in grand_total.items():
            logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    main()
