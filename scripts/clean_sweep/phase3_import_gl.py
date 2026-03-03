"""
Phase 3: Import ERPNext GL entries into DotMac journal system.

Reads 167K GL entries from ERPNext `tabGL Entry`, groups by
(voucher_type, voucher_no), creates:
  - gl.journal_entry       (1 per voucher, ~68K)
  - gl.journal_entry_line  (1 per GL row, ~167K)
  - gl.posted_ledger_line  (1 per GL row, ~167K)
  - gl.posting_batch       (1 per batch of 500 vouchers)

Uses deterministic JE-2025-NNNNN numbering. Commits every 500 vouchers.
Idempotent: checks correlation_id before inserting.

Output: scripts/clean_sweep/_voucher_je_map.json

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase3_import_gl
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from scripts.clean_sweep.config import (
    CURRENCY_CODE,
    DATE_END,
    DATE_START,
    GL_BATCH_SIZE,
    ORG_ID,
    PE_SUBTYPE_MAP,
    USER_ID,
    VOUCHER_TYPE_MAP,
    mysql_connect,
    norm_text,
    setup_logging,
    to_date,
    to_decimal,
)
from scripts.clean_sweep.phase2_accounts import load_account_map

logger = setup_logging("phase3_import_gl")

OUTPUT_FILE = Path(__file__).parent / "_voucher_je_map.json"


def _fetch_gl_entries(mysql_conn: Any) -> list[dict[str, Any]]:
    """Fetch all active GL entries from ERPNext for the date range."""
    query = """
        SELECT
            name, posting_date, account, debit, credit,
            voucher_type, voucher_no, party_type, party,
            cost_center, remarks, fiscal_year, company,
            is_cancelled, against_voucher_type, against_voucher
        FROM `tabGL Entry`
        WHERE is_cancelled = 0
          AND posting_date >= %s
          AND posting_date < %s
          AND company = 'Dotmac Technologies'
        ORDER BY voucher_type, voucher_no, name
    """
    with mysql_conn.cursor() as cur:
        cur.execute(query, (str(DATE_START), str(DATE_END)))
        rows = cur.fetchall() or []
    logger.info("Fetched %d GL entries from ERPNext", len(rows))
    return rows


def _fetch_payment_entries(mysql_conn: Any) -> dict[str, dict[str, str]]:
    """Fetch Payment Entry metadata for subtype classification."""
    query = """
        SELECT name, payment_type, party_type
        FROM `tabPayment Entry`
        WHERE docstatus = 1
          AND posting_date >= %s
          AND posting_date < %s
          AND company = 'Dotmac Technologies'
    """
    result: dict[str, dict[str, str]] = {}
    with mysql_conn.cursor() as cur:
        cur.execute(query, (str(DATE_START), str(DATE_END)))
        for row in cur.fetchall() or []:
            result[str(row["name"])] = {
                "payment_type": str(row.get("payment_type") or ""),
                "party_type": str(row.get("party_type") or ""),
            }
    logger.info("Fetched %d Payment Entry metadata rows", len(result))
    return result


def _resolve_voucher_type(
    voucher_type: str,
    voucher_no: str,
    pe_metadata: dict[str, dict[str, str]],
) -> tuple[str, str]:
    """Map ERPNext voucher_type → (source_module, source_document_type)."""
    if voucher_type == "Payment Entry":
        meta = pe_metadata.get(voucher_no, {})
        payment_type = meta.get("payment_type", "")
        party_type = meta.get("party_type", "")
        key = (payment_type, party_type)
        if key in PE_SUBTYPE_MAP:
            return PE_SUBTYPE_MAP[key]
        # Internal Transfer has empty party_type
        if payment_type == "Internal Transfer":
            return ("banking", "INTERBANK_TRANSFER")
        # Default for unclassified PEs
        return ("ar", "CUSTOMER_PAYMENT")

    if voucher_type in VOUCHER_TYPE_MAP:
        return VOUCHER_TYPE_MAP[voucher_type]

    return ("gl", "JOURNAL")


def _get_or_create_period(
    db: Session,
    target_date: date,
    period_cache: dict[str, UUID],
) -> UUID:
    """Get fiscal_period_id for a date, using PeriodGuardService for creation."""
    key = target_date.strftime("%Y-%m")
    if key in period_cache:
        return period_cache[key]

    from app.services.finance.gl.period_guard import PeriodGuardService

    result = PeriodGuardService.can_post_to_date(db, ORG_ID, target_date)
    if result.fiscal_period_id:
        period_cache[key] = result.fiscal_period_id
        return result.fiscal_period_id

    # If period is closed, still get its ID for import purposes
    period = PeriodGuardService.get_period_for_date(db, ORG_ID, target_date)
    if period:
        period_cache[key] = period.fiscal_period_id
        return period.fiscal_period_id

    raise RuntimeError(f"Cannot resolve fiscal period for {target_date}")


def _get_account_code(
    db: Session, account_id: UUID, code_cache: dict[UUID, str]
) -> str:
    """Get account_code for an account_id (cached)."""
    if account_id in code_cache:
        return code_cache[account_id]

    from app.models.finance.gl.account import Account

    account = db.get(Account, account_id)
    code = account.account_code if account else "0000"
    code_cache[account_id] = code
    return code


def main() -> None:
    from app.db import SessionLocal
    from app.models.finance.gl.journal_entry import (
        JournalEntry,
        JournalStatus,
        JournalType,
    )
    from app.models.finance.gl.journal_entry_line import JournalEntryLine
    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
    from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch

    # Load account map from Phase 2
    account_map = load_account_map()
    logger.info("Loaded account map: %d entries", len(account_map))

    # Check for existing correlation_ids to support idempotency
    mysql_conn = mysql_connect()

    try:
        gl_entries = _fetch_gl_entries(mysql_conn)
        pe_metadata = _fetch_payment_entries(mysql_conn)
    finally:
        mysql_conn.close()

    # Group GL entries by (voucher_type, voucher_no)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in gl_entries:
        key = (str(row["voucher_type"]), str(row["voucher_no"]))
        grouped[key].append(row)

    logger.info("Grouped into %d vouchers", len(grouped))

    # Sort vouchers by posting_date for sequential numbering
    voucher_keys = sorted(
        grouped.keys(),
        key=lambda k: (to_date(grouped[k][0]["posting_date"]) or DATE_START, k),
    )

    voucher_je_map: dict[str, str] = {}  # voucher_no → journal_entry_id
    now = datetime.now(UTC)

    with SessionLocal() as db:
        # Load existing correlation_ids for idempotency
        existing_corr = set(
            db.scalars(
                select(JournalEntry.correlation_id).where(
                    JournalEntry.organization_id == ORG_ID,
                    JournalEntry.correlation_id.is_not(None),
                )
            ).all()
        )
        logger.info(
            "Found %d existing journals (idempotency check)", len(existing_corr)
        )

        # Find max existing journal number for numbering
        max_num_result = db.execute(
            text("""
                SELECT MAX(CAST(SUBSTRING(journal_number FROM 10) AS INTEGER))
                FROM gl.journal_entry
                WHERE organization_id = :org_id
                  AND journal_number LIKE 'JE-2025-%%'
            """),
            {"org_id": str(ORG_ID)},
        )
        je_counter = max_num_result.scalar() or 0

        period_cache: dict[str, UUID] = {}
        code_cache: dict[UUID, str] = {}
        unmapped_accounts: set[str] = set()

        total_journals = 0
        total_lines = 0
        batch_vouchers: list[tuple[str, str]] = []

        # Resume batch numbering from max existing batch
        max_batch_result = db.execute(
            text("""
                SELECT COALESCE(MAX(
                    CAST(SUBSTRING(idempotency_key FROM 'batch_(\\d+)') AS INTEGER)
                ), 0)
                FROM gl.posting_batch
                WHERE organization_id = :org_id
                  AND idempotency_key LIKE :pattern
            """),
            {
                "org_id": str(ORG_ID),
                "pattern": f"{ORG_ID}:clean_sweep:gl:batch_%",
            },
        )
        batch_num: int = max_batch_result.scalar() or 0

        # Pre-create the first batch so PLLs can reference it
        current_batch_id: UUID | None = None

        def _create_next_batch(period_id: UUID) -> UUID:
            nonlocal batch_num, current_batch_id
            batch_num += 1
            bid = uuid4()
            batch = PostingBatch(
                batch_id=bid,
                organization_id=ORG_ID,
                fiscal_period_id=period_id,
                idempotency_key=f"{ORG_ID}:clean_sweep:gl:batch_{batch_num}",
                source_module="gl",
                batch_description=f"Clean sweep GL import batch {batch_num}",
                total_entries=0,  # updated at commit
                posted_entries=0,
                failed_entries=0,
                status=BatchStatus.POSTED,
                submitted_by_user_id=USER_ID,
                completed_at=now,
                correlation_id=f"clean_sweep:gl:batch_{batch_num}",
            )
            db.add(batch)
            db.flush()  # ensure batch_id is available
            current_batch_id = bid
            return bid

        for idx, (voucher_type, voucher_no) in enumerate(voucher_keys):
            corr_id = f"erpnext:{voucher_type}:{voucher_no}"

            # Skip already-imported vouchers
            if corr_id in existing_corr:
                # Still record the mapping for Phase 4
                existing_je = db.scalar(
                    select(JournalEntry.journal_entry_id).where(
                        JournalEntry.correlation_id == corr_id,
                        JournalEntry.organization_id == ORG_ID,
                    )
                )
                if existing_je:
                    voucher_je_map[voucher_no] = str(existing_je)
                continue

            gl_lines = grouped[(voucher_type, voucher_no)]
            batch_vouchers.append((voucher_type, voucher_no))

            # Resolve fiscal period
            posting_date = to_date(gl_lines[0]["posting_date"]) or DATE_START
            fiscal_period_id = _get_or_create_period(db, posting_date, period_cache)

            # Ensure a PostingBatch exists for PLLs
            if current_batch_id is None:
                _create_next_batch(fiscal_period_id)

            # Resolve source type
            source_module, source_doc_type = _resolve_voucher_type(
                voucher_type, voucher_no, pe_metadata
            )

            # Compute totals
            total_debit = sum(to_decimal(r["debit"]) for r in gl_lines)
            total_credit = sum(to_decimal(r["credit"]) for r in gl_lines)

            # Description from first entry's remarks
            description = (
                norm_text(gl_lines[0].get("remarks")) or f"{voucher_type}: {voucher_no}"
            )
            if len(description) > 1000:
                description = description[:997] + "..."

            # Journal number
            je_counter += 1
            journal_number = f"JE-2025-{je_counter:05d}"

            # Create JournalEntry
            je_id = uuid4()
            journal = JournalEntry(
                journal_entry_id=je_id,
                organization_id=ORG_ID,
                journal_number=journal_number,
                journal_type=JournalType.STANDARD,
                entry_date=posting_date,
                posting_date=posting_date,
                fiscal_period_id=fiscal_period_id,
                description=description,
                reference=voucher_no[:100] if voucher_no else None,
                currency_code=CURRENCY_CODE,
                exchange_rate=Decimal("1"),
                total_debit=total_debit,
                total_credit=total_credit,
                total_debit_functional=total_debit,
                total_credit_functional=total_credit,
                status=JournalStatus.POSTED,
                source_module=source_module,
                source_document_type=source_doc_type,
                created_by_user_id=USER_ID,
                posted_by_user_id=USER_ID,
                posted_at=now,
                correlation_id=corr_id,
            )
            db.add(journal)

            # Create lines + posted ledger lines
            for line_num, gle in enumerate(gl_lines, start=1):
                erpnext_account = str(gle["account"])
                account_id = account_map.get(erpnext_account)

                if not account_id:
                    if erpnext_account not in unmapped_accounts:
                        unmapped_accounts.add(erpnext_account)
                        logger.warning(
                            "  UNMAPPED account: %s (voucher %s)",
                            erpnext_account,
                            voucher_no,
                        )
                    continue

                debit = to_decimal(gle["debit"])
                credit = to_decimal(gle["credit"])
                line_desc = norm_text(gle.get("remarks")) or description
                if len(line_desc) > 1000:
                    line_desc = line_desc[:997] + "..."

                line_id = uuid4()

                # JournalEntryLine
                jel = JournalEntryLine(
                    line_id=line_id,
                    journal_entry_id=je_id,
                    line_number=line_num,
                    account_id=account_id,
                    description=line_desc,
                    debit_amount=debit,
                    credit_amount=credit,
                    debit_amount_functional=debit,
                    credit_amount_functional=credit,
                )
                db.add(jel)

                # PostedLedgerLine
                account_code = _get_account_code(db, account_id, code_cache)
                pll = PostedLedgerLine(
                    ledger_line_id=uuid4(),
                    posting_year=posting_date.year,
                    organization_id=ORG_ID,
                    journal_entry_id=je_id,
                    journal_line_id=line_id,
                    posting_batch_id=current_batch_id,
                    fiscal_period_id=fiscal_period_id,
                    account_id=account_id,
                    account_code=account_code,
                    entry_date=posting_date,
                    posting_date=posting_date,
                    description=line_desc,
                    journal_reference=voucher_no[:100] if voucher_no else None,
                    debit_amount=debit,
                    credit_amount=credit,
                    source_module=source_module,
                    source_document_type=source_doc_type,
                    posted_by_user_id=USER_ID,
                    correlation_id=corr_id,
                )
                db.add(pll)
                total_lines += 1

            voucher_je_map[voucher_no] = str(je_id)
            total_journals += 1

            # Batch commit
            if len(batch_vouchers) >= GL_BATCH_SIZE:
                # Update current batch entry counts
                db.execute(
                    text("""
                        UPDATE gl.posting_batch
                        SET total_entries = :cnt, posted_entries = :cnt
                        WHERE batch_id = :bid
                    """),
                    {"cnt": len(batch_vouchers), "bid": str(current_batch_id)},
                )

                # Set batch on journals
                for vt, vn in batch_vouchers:
                    je_uuid = voucher_je_map.get(vn)
                    if je_uuid:
                        db.execute(
                            text("""
                                UPDATE gl.journal_entry
                                SET posting_batch_id = :batch_id
                                WHERE journal_entry_id = :je_id
                                  AND posting_batch_id IS NULL
                            """),
                            {
                                "batch_id": str(current_batch_id),
                                "je_id": je_uuid,
                            },
                        )

                db.commit()
                logger.info(
                    "  Batch %d committed: %d vouchers (%d total so far)",
                    batch_num,
                    len(batch_vouchers),
                    total_journals,
                )
                batch_vouchers = []
                # Create next batch upfront for the next window
                current_batch_id = None  # will be created on next voucher

        # Final batch
        if batch_vouchers and current_batch_id is not None:
            db.execute(
                text("""
                    UPDATE gl.posting_batch
                    SET total_entries = :cnt, posted_entries = :cnt,
                        batch_description = batch_description || ' (final)'
                    WHERE batch_id = :bid
                """),
                {"cnt": len(batch_vouchers), "bid": str(current_batch_id)},
            )

            for vt, vn in batch_vouchers:
                je_uuid = voucher_je_map.get(vn)
                if je_uuid:
                    db.execute(
                        text("""
                            UPDATE gl.journal_entry
                            SET posting_batch_id = :batch_id
                            WHERE journal_entry_id = :je_id
                              AND posting_batch_id IS NULL
                        """),
                        {
                            "batch_id": str(current_batch_id),
                            "je_id": je_uuid,
                        },
                    )

            db.commit()
            logger.info(
                "  Final batch %d committed: %d vouchers",
                batch_num,
                len(batch_vouchers),
            )

    # Write voucher→JE mapping for Phase 4
    OUTPUT_FILE.write_text(json.dumps(voucher_je_map, indent=2))

    if unmapped_accounts:
        logger.warning(
            "  %d unique unmapped accounts encountered", len(unmapped_accounts)
        )

    logger.info("=" * 60)
    logger.info("Phase 3 complete.")
    logger.info("  Journals created: %d", total_journals)
    logger.info("  Lines created: %d", total_lines)
    logger.info("  Batches: %d", batch_num)
    logger.info("  JE counter ended at: JE-2025-%05d", je_counter)
    logger.info("  Output: %s", OUTPUT_FILE)


def load_voucher_je_map() -> dict[str, UUID]:
    """Load the voucher→JE map from JSON (used by Phase 4+)."""
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(
            f"Voucher JE map not found: {OUTPUT_FILE}. Run phase3_import_gl first."
        )
    raw = json.loads(OUTPUT_FILE.read_text())
    return {name: UUID(uid) for name, uid in raw.items()}


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 3 failed")
        sys.exit(1)
