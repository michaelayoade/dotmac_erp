"""
Phase 5: Import bank transactions and reconciliation links from ERPNext.

Reuses patterns from scripts/import_erpnext_banking_sql.py:
  1. Query tabBank Transaction (38,824) → group by (bank_account, year-month)
     → create bank_statements + bank_statement_lines
  2. Query tabBank Transaction Payments → create bank_statement_line_matches
     linking to journal lines from Phase 3
  3. Cross-year orphans: set is_matched=False, log for manual review

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase5_import_banking
"""

from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.clean_sweep.config import (
    DATE_END,
    DATE_START,
    ORG_ID,
    USER_ID,
    mysql_connect,
    norm_text,
    setup_logging,
    to_date,
    to_decimal,
)
from scripts.clean_sweep.phase3_import_gl import load_voucher_je_map

logger = setup_logging("phase5_import_banking")


def _statement_number(bank_account_id: UUID, month_key: str) -> str:
    """Generate a deterministic statement number for grouping."""
    digest = hashlib.sha1(str(bank_account_id).encode()).hexdigest()[:10]
    return f"CS25-{digest}-{month_key}"


def _build_bank_account_map(db: Session) -> dict[str, UUID]:
    """Build ERPNext bank account name → DotMac bank_account_id map."""
    from app.models.sync import SyncEntity

    return {
        str(name): tid
        for name, tid in db.execute(
            select(SyncEntity.source_name, SyncEntity.target_id).where(
                SyncEntity.organization_id == ORG_ID,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Bank Account",
                SyncEntity.target_id.is_not(None),
            )
        ).all()
    }


def _import_bank_transactions(
    db: Session,
    mysql_conn: Any,
    bank_map: dict[str, UUID],
) -> tuple[int, int, dict[str, UUID]]:
    """
    Import bank transactions, grouped into monthly statements.

    Returns:
        (statements_created, lines_created, line_map)
        where line_map is ERPNext BTN name → statement_line_id
    """
    from app.models.finance.banking.bank_statement import (
        BankStatement,
        BankStatementLine,
        StatementLineType,
    )

    logger.info("5a. Importing Bank Transactions...")

    with mysql_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM `tabBank Transaction`
            WHERE docstatus < 2
              AND `date` >= %s AND `date` < %s
              AND company = 'Dotmac Technologies'
            ORDER BY `date`, name
        """,
            (str(DATE_START), str(DATE_END)),
        )
        rows = cur.fetchall() or []

    logger.info("  Fetched %d Bank Transactions from ERPNext", len(rows))

    # Group by (bank_account_id, year-month)
    grouped: dict[tuple[UUID, str], list[dict[str, Any]]] = defaultdict(list)
    skipped_bank = 0

    for row in rows:
        erp_bank = norm_text(row.get("bank_account"))
        bank_account_id = bank_map.get(erp_bank or "")
        if not bank_account_id:
            skipped_bank += 1
            continue
        txn_date = to_date(row.get("date"))
        if not txn_date:
            continue
        month_key = txn_date.strftime("%Y%m")
        grouped[(bank_account_id, month_key)].append(row)

    if skipped_bank:
        logger.warning(
            "  %d transactions skipped (unmapped bank account)", skipped_bank
        )

    line_map: dict[str, UUID] = {}
    statements_created = 0
    lines_created = 0

    for (bank_account_id, month_key), txns in grouped.items():
        statement_no = _statement_number(bank_account_id, month_key)

        # Check idempotency
        existing = db.scalar(
            select(BankStatement.statement_id).where(
                BankStatement.organization_id == ORG_ID,
                BankStatement.bank_account_id == bank_account_id,
                BankStatement.statement_number == statement_no,
            )
        )
        if existing:
            # Load existing lines for mapping
            for line in db.scalars(
                select(BankStatementLine).where(
                    BankStatementLine.statement_id == existing,
                )
            ).all():
                btn_name = (line.raw_data or {}).get("erpnext_bank_transaction")
                if btn_name:
                    line_map[str(btn_name)] = line.line_id
            continue

        txns_sorted = sorted(
            txns, key=lambda t: (to_date(t.get("date")), str(t["name"]))
        )
        period_start = to_date(txns_sorted[0].get("date")) or date.today()
        period_end = to_date(txns_sorted[-1].get("date")) or period_start

        # Compute statement totals
        total_credits = Decimal("0")
        total_debits = Decimal("0")

        statement_id = uuid4()
        statement = BankStatement(
            statement_id=statement_id,
            organization_id=ORG_ID,
            bank_account_id=bank_account_id,
            statement_number=statement_no,
            statement_date=period_end,
            period_start=period_start,
            period_end=period_end,
            currency_code="NGN",
            status="imported",
            import_source="CLEAN_SWEEP",
            import_filename=f"clean_sweep_banking_{month_key}",
            imported_by=USER_ID,
            total_lines=0,
            matched_lines=0,
            unmatched_lines=0,
        )
        db.add(statement)

        line_count = 0
        for idx, t in enumerate(txns_sorted, start=1):
            deposit = to_decimal(t.get("deposit"))
            withdrawal = to_decimal(t.get("withdrawal"))

            if deposit > 0:
                txn_type = StatementLineType.credit
                amount = deposit
                total_credits += deposit
            elif withdrawal > 0:
                txn_type = StatementLineType.debit
                amount = withdrawal
                total_debits += withdrawal
            else:
                continue

            source_name = str(t["name"])
            erp_status = norm_text(t.get("status")) or ""

            line_id = uuid4()
            line = BankStatementLine(
                line_id=line_id,
                statement_id=statement_id,
                line_number=idx,
                transaction_id=norm_text(t.get("transaction_id")),
                transaction_date=to_date(t.get("date")) or period_start,
                value_date=to_date(t.get("date")),
                transaction_type=txn_type,
                amount=amount,
                description=norm_text(t.get("description")),
                reference=norm_text(t.get("reference_number")),
                payee_payer=norm_text(t.get("bank_party_name"))
                or norm_text(t.get("party")),
                bank_reference=norm_text(t.get("transaction_id")),
                is_matched=False,  # Set in reconciliation phase
                raw_data={
                    "erpnext_bank_transaction": source_name,
                    "erpnext_status": erp_status,
                    "erpnext_party_type": norm_text(t.get("party_type")),
                    "erpnext_party": norm_text(t.get("party")),
                    "erpnext_allocated_amount": str(
                        to_decimal(t.get("allocated_amount"))
                    ),
                    "erpnext_unallocated_amount": str(
                        to_decimal(t.get("unallocated_amount"))
                    ),
                },
            )
            db.add(line)
            line_map[source_name] = line_id
            line_count += 1

        # Update statement totals
        statement.total_credits = total_credits
        statement.total_debits = total_debits
        statement.total_lines = line_count
        statement.unmatched_lines = line_count

        # Create sync_entity
        from app.models.sync import SyncEntity

        for t in txns_sorted:
            source_name = str(t["name"])
            line_id_for_sync = line_map.get(source_name)
            if not line_id_for_sync:
                continue

            existing_sync = db.scalar(
                select(SyncEntity.sync_id).where(
                    SyncEntity.organization_id == ORG_ID,
                    SyncEntity.source_system == "erpnext",
                    SyncEntity.source_doctype == "Bank Transaction",
                    SyncEntity.source_name == source_name,
                )
            )
            if not existing_sync:
                db.add(
                    SyncEntity(
                        organization_id=ORG_ID,
                        source_system="erpnext",
                        source_doctype="Bank Transaction",
                        source_name=source_name,
                        target_table="banking.bank_statement_lines",
                        target_id=line_id_for_sync,
                        sync_status="SYNCED",
                        synced_at=datetime.now(UTC),
                    )
                )

        statements_created += 1
        lines_created += line_count

        if statements_created % 20 == 0:
            db.commit()
            logger.info(
                "    %d statements, %d lines so far",
                statements_created,
                lines_created,
            )

    db.commit()
    logger.info(
        "  Statements: %d, Lines: %d",
        statements_created,
        lines_created,
    )
    return statements_created, lines_created, line_map


def _attach_reconciliation_links(
    db: Session,
    mysql_conn: Any,
    line_map: dict[str, UUID],
    voucher_je_map: dict[str, UUID],
) -> int:
    """
    Create bank_statement_line_matches from ERPNext Bank Transaction Payments.
    Links statement lines to journal entry lines created in Phase 3.
    """
    from app.models.finance.banking.bank_statement import (
        BankStatementLine,
        BankStatementLineMatch,
    )
    from app.models.finance.gl.journal_entry_line import JournalEntryLine

    logger.info("5b. Attaching reconciliation links...")

    with mysql_conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM `tabBank Transaction Payments`
            WHERE docstatus < 2
            ORDER BY parent, idx
        """)
        rows = cur.fetchall() or []

    logger.info("  Fetched %d Bank Transaction Payments from ERPNext", len(rows))

    # Group by parent (bank transaction name)
    by_btn: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parent = str(row.get("parent") or "")
        if parent:
            by_btn[parent].append(row)

    # Preload existing match pairs
    existing_matches: set[tuple[UUID, UUID]] = set()
    all_line_ids = list(line_map.values())

    # Process in chunks to avoid too-large IN clauses
    chunk_size = 5000
    for i in range(0, len(all_line_ids), chunk_size):
        chunk = all_line_ids[i : i + chunk_size]
        for sid, jid in db.execute(
            select(
                BankStatementLineMatch.statement_line_id,
                BankStatementLineMatch.journal_line_id,
            ).where(BankStatementLineMatch.statement_line_id.in_(chunk))
        ).all():
            existing_matches.add((sid, jid))

    # Preload journal lines by journal_entry_id for matching
    je_ids = set(voucher_je_map.values())
    journal_lines_by_je: dict[UUID, list[JournalEntryLine]] = defaultdict(list)

    for i in range(0, len(list(je_ids)), chunk_size):
        chunk = list(je_ids)[i : i + chunk_size]
        for jel in db.scalars(
            select(JournalEntryLine).where(JournalEntryLine.journal_entry_id.in_(chunk))
        ).all():
            journal_lines_by_je[jel.journal_entry_id].append(jel)

    # Preload bank account GL account mappings
    from app.models.finance.banking.bank_account import BankAccount

    bank_gl_map: dict[UUID, UUID] = {}
    for ba in db.scalars(
        select(BankAccount).where(BankAccount.organization_id == ORG_ID)
    ).all():
        bank_gl_map[ba.bank_account_id] = ba.gl_account_id

    # Preload statement lines' bank account IDs
    from app.models.finance.banking.bank_statement import BankStatement

    line_bank: dict[UUID, UUID] = {}
    for line_id in line_map.values():
        line_obj = db.get(BankStatementLine, line_id)
        if line_obj:
            stmt = db.get(BankStatement, line_obj.statement_id)
            if stmt:
                line_bank[line_id] = stmt.bank_account_id

    matched_count = 0
    cross_year_orphans = 0

    for btn_name, payment_rows in by_btn.items():
        statement_line_id = line_map.get(btn_name)
        if not statement_line_id:
            continue

        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            continue

        bank_account_id = line_bank.get(statement_line_id)
        gl_account_id = bank_gl_map.get(bank_account_id) if bank_account_id else None

        for pay_row in payment_rows:
            payment_doc = norm_text(pay_row.get("payment_document")) or ""
            payment_entry_name = norm_text(pay_row.get("payment_entry"))
            if not payment_entry_name:
                continue

            # Find the journal for this payment
            je_id = voucher_je_map.get(payment_entry_name)
            if not je_id:
                # Could be cross-year (2026 payment)
                cross_year_orphans += 1
                continue

            # Find matching journal line (bank account line)
            candidates = journal_lines_by_je.get(je_id, [])
            matched_jel: JournalEntryLine | None = None

            if gl_account_id:
                # Prefer lines on the bank GL account
                for jel in candidates:
                    if jel.account_id == gl_account_id:
                        matched_jel = jel
                        break

            # Fallback: first line with matching amount direction
            if not matched_jel and candidates:
                allocated = to_decimal(pay_row.get("allocated_amount"))
                for jel in candidates:
                    if stmt_line.transaction_type.value == "credit":
                        if abs(
                            (jel.debit_amount or Decimal("0")) - allocated
                        ) <= Decimal("0.01"):
                            matched_jel = jel
                            break
                    else:
                        if abs(
                            (jel.credit_amount or Decimal("0")) - allocated
                        ) <= Decimal("0.01"):
                            matched_jel = jel
                            break

            if not matched_jel:
                continue

            pair = (statement_line_id, matched_jel.line_id)
            if pair in existing_matches:
                continue
            existing_matches.add(pair)

            match = BankStatementLineMatch(
                match_id=uuid4(),
                statement_line_id=statement_line_id,
                journal_line_id=matched_jel.line_id,
                matched_by=USER_ID,
                is_primary=True,
                match_type="CLEAN_SWEEP",
                match_reason={
                    "source": "clean_sweep_phase5",
                    "payment_document": payment_doc,
                    "payment_entry": payment_entry_name,
                },
                idempotency_key=f"cs25:{btn_name}:{payment_entry_name}:{matched_jel.line_id}",
            )
            db.add(match)

            stmt_line.is_matched = True
            stmt_line.matched_at = datetime.now(UTC)
            stmt_line.matched_by = USER_ID
            stmt_line.matched_journal_line_id = matched_jel.line_id
            matched_count += 1

    db.commit()

    # Update statement match counts
    from app.models.finance.banking.bank_statement import BankStatement as BS

    for stmt in db.scalars(
        select(BS).where(
            BS.organization_id == ORG_ID,
            BS.import_source == "CLEAN_SWEEP",
        )
    ).all():
        matched = db.scalar(
            select(BankStatementLine.line_id).where(
                BankStatementLine.statement_id == stmt.statement_id,
                BankStatementLine.is_matched.is_(True),
            )
        )
        total = stmt.total_lines or 0
        stmt.matched_lines = (
            db.execute(
                select(BankStatementLine.line_id).where(
                    BankStatementLine.statement_id == stmt.statement_id,
                    BankStatementLine.is_matched.is_(True),
                )
            )
            .all()
            .__len__()
        )
        stmt.unmatched_lines = total - stmt.matched_lines

    db.commit()

    logger.info(
        "  Matched: %d, Cross-year orphans: %d", matched_count, cross_year_orphans
    )
    return matched_count


def main() -> None:
    from app.db import SessionLocal

    logger.info("=" * 60)
    logger.info("Phase 5: Import bank transactions from ERPNext")
    logger.info("=" * 60)

    voucher_je_map = load_voucher_je_map()
    mysql_conn = mysql_connect()

    try:
        with SessionLocal() as db:
            bank_map = _build_bank_account_map(db)
            logger.info("Bank account mappings: %d", len(bank_map))

            stmt_count, line_count, line_map = _import_bank_transactions(
                db, mysql_conn, bank_map
            )

        with SessionLocal() as db:
            match_count = _attach_reconciliation_links(
                db, mysql_conn, line_map, voucher_je_map
            )
    finally:
        mysql_conn.close()

    logger.info("=" * 60)
    logger.info("Phase 5 complete.")
    logger.info("  Statements: %d", stmt_count)
    logger.info("  Lines: %d", line_count)
    logger.info("  Reconciliation matches: %d", match_count)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 5 failed")
        sys.exit(1)
