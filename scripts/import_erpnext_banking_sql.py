#!/usr/bin/env python3
"""
Import ERPNext banking records (SQL source) into Dotmac banking.

Imports:
- Bank Account -> banking.bank_accounts
- Bank Transaction -> banking.bank_statements + banking.bank_statement_lines
- Bank Transaction Payments -> attaches ERPNext payment linkage metadata and
  attempts journal-line matching where resolvable.

Notes:
- Read-only from ERPNext MySQL; writes to Dotmac Postgres.
- Designed for one-way migration/backfill.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pymysql
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.ap.supplier_payment import SupplierPayment
from app.models.finance.ar.customer_payment import CustomerPayment
from app.models.finance.banking.bank_account import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
)
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    StatementLineType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.sync import SyncEntity
from app.services.finance.banking.bank_statement import (
    StatementLineInput,
    bank_statement_service,
)

logger = logging.getLogger("import_erpnext_banking_sql")


@dataclass
class Stats:
    bank_accounts_created: int = 0
    bank_accounts_updated: int = 0
    bank_accounts_skipped: int = 0
    statements_created: int = 0
    statement_lines_imported: int = 0
    statement_lines_skipped: int = 0
    statement_groups_skipped_existing: int = 0
    payment_links_attached: int = 0
    matched_lines: int = 0
    match_errors: int = 0


def _mysql_connect() -> pymysql.Connection:
    return pymysql.connect(
        host=os.getenv("ERPNEXT_SQL_HOST", "127.0.0.1"),
        port=int(os.getenv("ERPNEXT_SQL_PORT", "3307")),
        user=os.getenv("ERPNEXT_SQL_USER", "root"),
        password=os.getenv("ERPNEXT_SQL_PASSWORD", "root"),
        database=os.getenv("ERPNEXT_SQL_DATABASE", "erpnext_temp"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.fromisoformat(str(value)).date()


def _statement_number(erp_bank_account: str, month_key: str) -> str:
    digest = hashlib.sha1(erp_bank_account.encode("utf-8")).hexdigest()[:10]
    return f"ERPBTXN-{digest}-{month_key}"


def _account_type(value: str | None) -> BankAccountType:
    text = (value or "").strip().lower()
    if text in {"saving", "savings"}:
        return BankAccountType.savings
    if text in {"checking", "current"}:
        return BankAccountType.checking
    return BankAccountType.other


def _get_or_create_sync_entity(
    db: Session,
    organization_id: uuid.UUID,
    source_doctype: str,
    source_name: str,
) -> SyncEntity:
    existing = db.execute(
        select(SyncEntity).where(
            SyncEntity.organization_id == organization_id,
            SyncEntity.source_system == "erpnext",
            SyncEntity.source_doctype == source_doctype,
            SyncEntity.source_name == source_name,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    entity = SyncEntity(
        organization_id=organization_id,
        source_system="erpnext",
        source_doctype=source_doctype,
        source_name=source_name,
        target_table="",
    )
    db.add(entity)
    db.flush()
    return entity


def _sync_bank_accounts(
    db: Session,
    mysql_conn: pymysql.Connection,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    company: str | None,
    dry_run: bool,
    stats: Stats,
) -> dict[str, uuid.UUID]:
    # ERPNext Account (source_name) -> Dotmac GL account_id
    gl_account_map = {
        str(source_name): target_id
        for source_name, target_id in db.execute(
            select(SyncEntity.source_name, SyncEntity.target_id).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Account",
                SyncEntity.target_id.is_not(None),
            )
        )
        .all()
    }

    query = "SELECT * FROM `tabBank Account` WHERE `docstatus` < 2"
    params: list[Any] = []
    if company:
        query += " AND `company` = %s"
        params.append(company)
    query += " ORDER BY `name`"

    erp_to_dotmac: dict[str, uuid.UUID] = {}

    with mysql_conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() or []

    for row in rows:
        source_name = str(row["name"])
        gl_account_source = _norm_text(row.get("account"))
        gl_account_id = gl_account_map.get(gl_account_source or "")
        if not gl_account_id:
            stats.bank_accounts_skipped += 1
            logger.warning(
                "Skipping Bank Account %s: GL account mapping missing for source account %r",
                source_name,
                gl_account_source,
            )
            continue

        account_number = _norm_text(row.get("bank_account_no")) or source_name
        bank_name = _norm_text(row.get("bank")) or "Unknown Bank"
        bank_code = _norm_text(row.get("custom_sort_code")) or _norm_text(
            row.get("custom_bank_sort_code")
        )
        account_name = _norm_text(row.get("account_name")) or account_number
        status = (
            BankAccountStatus.inactive
            if int(row.get("disabled") or 0) == 1
            else BankAccountStatus.active
        )

        sync_entity = _get_or_create_sync_entity(
            db, organization_id, "Bank Account", source_name
        )
        existing: BankAccount | None = None
        if sync_entity.target_id:
            existing = db.get(BankAccount, sync_entity.target_id)
        if existing is None:
            existing = db.execute(
                select(BankAccount).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.account_number == account_number,
                    BankAccount.bank_code == bank_code,
                )
            ).scalar_one_or_none()

        if existing:
            existing.bank_name = bank_name
            existing.account_name = account_name
            existing.account_type = _account_type(_norm_text(row.get("account_type")))
            existing.bank_code = bank_code
            existing.branch_code = _norm_text(row.get("branch_code"))
            existing.iban = _norm_text(row.get("iban"))
            existing.gl_account_id = gl_account_id
            existing.currency_code = _norm_text(row.get("currency")) or "NGN"
            existing.status = status
            existing.is_primary = int(row.get("is_default") or 0) == 1
            existing.updated_by = user_id
            sync_entity.target_table = "banking.bank_accounts"
            sync_entity.mark_synced(existing.bank_account_id)
            erp_to_dotmac[source_name] = existing.bank_account_id
            stats.bank_accounts_updated += 1
            continue

        new_account = BankAccount(
            organization_id=organization_id,
            bank_name=bank_name,
            bank_code=bank_code,
            branch_code=_norm_text(row.get("branch_code")),
            branch_name=None,
            account_number=account_number,
            account_name=account_name,
            account_type=_account_type(_norm_text(row.get("account_type"))),
            iban=_norm_text(row.get("iban")),
            currency_code=_norm_text(row.get("currency")) or "NGN",
            gl_account_id=gl_account_id,
            status=status,
            is_primary=int(row.get("is_default") or 0) == 1,
            created_by=user_id,
            updated_by=user_id,
            notes=f"Imported from ERPNext Bank Account: {source_name}",
        )
        db.add(new_account)
        db.flush()
        sync_entity.target_table = "banking.bank_accounts"
        sync_entity.mark_synced(new_account.bank_account_id)
        erp_to_dotmac[source_name] = new_account.bank_account_id
        stats.bank_accounts_created += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return erp_to_dotmac


def _sync_bank_transactions(
    db: Session,
    mysql_conn: pymysql.Connection,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    erp_bank_to_dotmac_bank: dict[str, uuid.UUID],
    company: str | None,
    dry_run: bool,
    stats: Stats,
) -> dict[str, uuid.UUID]:
    # ERP bank transaction name -> imported statement line id
    line_map: dict[str, uuid.UUID] = {}

    query = "SELECT * FROM `tabBank Transaction` WHERE `docstatus` < 2"
    params: list[Any] = []
    if company:
        query += " AND `company` = %s"
        params.append(company)
    query += " ORDER BY `date`, `name`"

    with mysql_conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() or []

    grouped: dict[tuple[uuid.UUID, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source_name = str(row["name"])
        erp_bank = _norm_text(row.get("bank_account"))
        bank_account_id = erp_bank_to_dotmac_bank.get(erp_bank or "")
        if not bank_account_id:
            logger.warning(
                "Skipping Bank Transaction %s: bank account %r not imported",
                source_name,
                erp_bank,
            )
            continue
        txn_date = _to_date(row.get("date"))
        if txn_date is None:
            continue
        month_key = txn_date.strftime("%Y%m")
        grouped[(bank_account_id, month_key)].append(row)

    for (bank_account_id, month_key), txns in grouped.items():
        statement_no = _statement_number(str(bank_account_id), month_key)
        existing_statement = db.execute(
            select(BankStatement).where(
                BankStatement.organization_id == organization_id,
                BankStatement.bank_account_id == bank_account_id,
                BankStatement.statement_number == statement_no,
            )
        ).scalar_one_or_none()
        if existing_statement:
            stats.statement_groups_skipped_existing += 1
            continue

        txns_sorted = sorted(txns, key=lambda t: (_to_date(t.get("date")), str(t["name"])))
        period_start = _to_date(txns_sorted[0].get("date")) or date.today()
        period_end = _to_date(txns_sorted[-1].get("date")) or period_start

        line_inputs: list[StatementLineInput] = []
        source_names: list[str] = []
        for idx, t in enumerate(txns_sorted, start=1):
            deposit = _to_decimal(t.get("deposit"))
            withdrawal = _to_decimal(t.get("withdrawal"))
            if deposit > 0:
                txn_type = StatementLineType.credit
                amount = deposit
            elif withdrawal > 0:
                txn_type = StatementLineType.debit
                amount = withdrawal
            else:
                continue

            source_name = str(t["name"])
            source_names.append(source_name)
            line_inputs.append(
                StatementLineInput(
                    line_number=idx,
                    transaction_date=_to_date(t.get("date")) or period_start,
                    transaction_type=txn_type,
                    amount=amount,
                    description=_norm_text(t.get("description")),
                    reference=_norm_text(t.get("reference_number")),
                    payee_payer=_norm_text(t.get("bank_party_name"))
                    or _norm_text(t.get("party")),
                    bank_reference=_norm_text(t.get("transaction_id")),
                    transaction_id=_norm_text(t.get("transaction_id")),
                    raw_data={
                        "erpnext_bank_transaction": source_name,
                        "erpnext_status": _norm_text(t.get("status")),
                        "erpnext_party_type": _norm_text(t.get("party_type")),
                        "erpnext_party": _norm_text(t.get("party")),
                        "erpnext_allocated_amount": str(
                            _to_decimal(t.get("allocated_amount"))
                        ),
                        "erpnext_unallocated_amount": str(
                            _to_decimal(t.get("unallocated_amount"))
                        ),
                    },
                )
            )

        if not line_inputs:
            continue

        result = bank_statement_service.import_statement(
            db=db,
            organization_id=organization_id,
            bank_account_id=bank_account_id,
            statement_number=statement_no,
            statement_date=period_end,
            period_start=period_start,
            period_end=period_end,
            opening_balance=None,
            closing_balance=None,
            lines=line_inputs,
            import_source="ERPNEXT_SQL",
            import_filename=f"erpnext_bank_txn_{month_key}.sql",
            imported_by=user_id,
            check_duplicates=True,
            skip_duplicates=True,
        )
        stats.statements_created += 1
        stats.statement_lines_imported += result.lines_imported
        stats.statement_lines_skipped += result.lines_skipped

        created_lines = db.execute(
            select(BankStatementLine).where(
                BankStatementLine.statement_id == result.statement.statement_id
            )
        ).scalars()
        by_line_number = {line.line_number: line for line in created_lines}

        for item in line_inputs:
            source_name = (item.raw_data or {}).get("erpnext_bank_transaction")
            if not source_name:
                continue
            line = by_line_number.get(item.line_number)
            if not line:
                continue
            line_map[source_name] = line.line_id

            sync_entity = _get_or_create_sync_entity(
                db, organization_id, "Bank Transaction", source_name
            )
            sync_entity.target_table = "banking.bank_statement_lines"
            sync_entity.mark_synced(line.line_id)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return line_map


def _attach_payment_matches(
    db: Session,
    mysql_conn: pymysql.Connection,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    line_map: dict[str, uuid.UUID],
    dry_run: bool,
    stats: Stats,
) -> None:
    with mysql_conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `tabBank Transaction Payments` WHERE `docstatus` < 2 ORDER BY `parent`, `idx`"
        )
        rows = cur.fetchall() or []

    # ERP Payment Entry name -> (target_table, target_id)
    payment_sync = {
        source_name: (target_table or "", target_id)
        for source_name, target_table, target_id in db.execute(
            select(
                SyncEntity.source_name,
                SyncEntity.target_table,
                SyncEntity.target_id,
            ).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Payment Entry",
                SyncEntity.target_id.is_not(None),
            )
        )
        .all()
    }
    journal_sync = {
        source_name: target_id
        for source_name, target_id in db.execute(
            select(SyncEntity.source_name, SyncEntity.target_id).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Journal Entry",
                SyncEntity.target_id.is_not(None),
            )
        ).all()
    }

    # Preload target payment -> journal_entry_id maps in bulk.
    ar_payment_ids = [tid for table, tid in payment_sync.values() if table == "ar.customer_payment" and tid]
    ap_payment_ids = [tid for table, tid in payment_sync.values() if table == "ap.supplier_payment" and tid]

    ar_journal_by_payment: dict[uuid.UUID, uuid.UUID | None] = {}
    ap_journal_by_payment: dict[uuid.UUID, uuid.UUID | None] = {}
    if ar_payment_ids:
        for pid, jeid in db.execute(
            select(CustomerPayment.payment_id, CustomerPayment.journal_entry_id).where(
                CustomerPayment.payment_id.in_(ar_payment_ids)
            )
        ).all():
            ar_journal_by_payment[pid] = jeid
    if ap_payment_ids:
        for pid, jeid in db.execute(
            select(SupplierPayment.payment_id, SupplierPayment.journal_entry_id).where(
                SupplierPayment.payment_id.in_(ap_payment_ids)
            )
        ).all():
            ap_journal_by_payment[pid] = jeid

    # Preload all imported statement lines keyed by ERPNext bank transaction name.
    imported_lines = db.execute(
        select(BankStatementLine, BankStatement.bank_account_id)
        .join(BankStatement, BankStatement.statement_id == BankStatementLine.statement_id)
        .where(
            BankStatement.organization_id == organization_id,
            BankStatement.import_source == "ERPNEXT_SQL",
        )
    ).all()
    line_by_txn: dict[str, BankStatementLine] = {}
    line_bank_account_id: dict[uuid.UUID, uuid.UUID] = {}
    for line, bank_account_id in imported_lines:
        tx_name = (line.raw_data or {}).get("erpnext_bank_transaction")
        if tx_name:
            line_by_txn[str(tx_name)] = line
            line_bank_account_id[line.line_id] = bank_account_id

    # Preload bank account -> gl account.
    bank_gl_map = {
        ba_id: gl_id
        for ba_id, gl_id in db.execute(
            select(BankAccount.bank_account_id, BankAccount.gl_account_id).where(
                BankAccount.bank_account_id.in_(set(line_bank_account_id.values()))
            )
        ).all()
    }

    # Preload relevant journal lines once.
    journal_entry_ids: set[uuid.UUID] = set()
    for table, tid in payment_sync.values():
        if table == "ar.customer_payment" and tid in ar_journal_by_payment:
            jeid = ar_journal_by_payment.get(tid)
            if jeid:
                journal_entry_ids.add(jeid)
        elif table == "ap.supplier_payment" and tid in ap_journal_by_payment:
            jeid = ap_journal_by_payment.get(tid)
            if jeid:
                journal_entry_ids.add(jeid)

    journal_lines_by_entry: dict[uuid.UUID, list[JournalEntryLine]] = defaultdict(list)
    if journal_entry_ids:
        for jel in db.execute(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id.in_(journal_entry_ids)
            )
        ).scalars():
            journal_lines_by_entry[jel.journal_entry_id].append(jel)

    # Group payment rows by ERP bank transaction so line raw_data updates happen once.
    links_by_bank_txn: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    rows_by_bank_txn: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bank_txn_name = str(row.get("parent") or "")
        payment_entry_name = _norm_text(row.get("payment_entry"))
        if not bank_txn_name or not payment_entry_name:
            continue
        link = {
            "payment_document": _norm_text(row.get("payment_document")),
            "payment_entry": payment_entry_name,
            "allocated_amount": str(_to_decimal(row.get("allocated_amount"))),
            "clearance_date": str(_to_date(row.get("clearance_date"))),
        }
        links_by_bank_txn[bank_txn_name].append(link)
        rows_by_bank_txn[bank_txn_name].append(row)

    # Existing line-match pairs for idempotency.
    existing_pairs = {
        (sid, jid)
        for sid, jid in db.execute(
            select(
                BankStatementLineMatch.statement_line_id,
                BankStatementLineMatch.journal_line_id,
            ).where(
                BankStatementLineMatch.statement_line_id.in_(
                    [line.line_id for line in line_by_txn.values()]
                )
            )
        ).all()
    }

    for bank_txn_name, links in links_by_bank_txn.items():
        line = line_by_txn.get(bank_txn_name)
        if not line:
            line_id = line_map.get(bank_txn_name)
            if line_id:
                line = db.get(BankStatementLine, line_id)
        if not line:
            continue

        raw = dict(line.raw_data or {})
        existing_links = list(raw.get("erpnext_payment_links") or [])
        dedup: dict[tuple[str | None, str | None, str | None, str | None], dict[str, str | None]] = {}
        for link in existing_links + links:
            key = (
                link.get("payment_document"),
                link.get("payment_entry"),
                link.get("allocated_amount"),
                link.get("clearance_date"),
            )
            dedup[key] = link
        raw["erpnext_payment_links"] = list(dedup.values())
        line.raw_data = raw
        stats.payment_links_attached += len(links)

        bank_account_id = line_bank_account_id.get(line.line_id)
        gl_account_id = bank_gl_map.get(bank_account_id) if bank_account_id else None
        if not gl_account_id:
            continue

        for row in rows_by_bank_txn[bank_txn_name]:
            payment_entry_name = _norm_text(row.get("payment_entry"))
            if not payment_entry_name:
                continue
            payment_document = _norm_text(row.get("payment_document")) or ""

            link_sync = _get_or_create_sync_entity(
                db,
                organization_id,
                "Bank Transaction Payments",
                f"{bank_txn_name}:{payment_document}:{payment_entry_name}",
            )
            link_sync.target_table = "banking.bank_statement_lines"
            link_sync.mark_synced(line.line_id)

            # Best-effort match to GL journal line via mapped payment entry
            journal_entry_id: uuid.UUID | None = None
            if payment_document == "Payment Entry":
                target = payment_sync.get(payment_entry_name)
                if not target:
                    continue
                target_table, target_id = target
                if not target_id:
                    continue
                if target_table == "ar.customer_payment":
                    journal_entry_id = ar_journal_by_payment.get(target_id)
                elif target_table == "ap.supplier_payment":
                    journal_entry_id = ap_journal_by_payment.get(target_id)
            elif payment_document == "Journal Entry":
                journal_entry_id = journal_sync.get(payment_entry_name)
            if not journal_entry_id:
                continue

            allocated = _to_decimal(row.get("allocated_amount"))
            if allocated <= 0:
                allocated = line.amount

            candidates = [
                jel
                for jel in journal_lines_by_entry.get(journal_entry_id, [])
                if jel.account_id == gl_account_id
            ]
            matched_line: JournalEntryLine | None = None
            for cand in candidates:
                journal_amt = (
                    cand.debit_amount
                    if line.transaction_type == StatementLineType.credit
                    else cand.credit_amount
                )
                if abs((journal_amt or Decimal("0")) - allocated) <= Decimal("0.01"):
                    matched_line = cand
                    break
            if not matched_line:
                continue

            pair = (line.line_id, matched_line.line_id)
            if pair in existing_pairs:
                continue
            existing_pairs.add(pair)

            db.add(
                BankStatementLineMatch(
                    statement_line_id=line.line_id,
                    journal_line_id=matched_line.line_id,
                    matched_by=user_id,
                    is_primary=True,
                    match_type="ERPNEXT_LINK",
                    match_reason={
                        "source": "erpnext_bank_transaction_payments",
                        "payment_entry": payment_entry_name,
                    },
                    idempotency_key=f"erpnext:{bank_txn_name}:{payment_entry_name}:{matched_line.line_id}",
                )
            )
            line.is_matched = True
            line.matched_at = datetime.utcnow()
            line.matched_by = user_id
            line.matched_journal_line_id = matched_line.line_id
            stats.matched_lines += 1

            match_sync = _get_or_create_sync_entity(
                db,
                organization_id,
                "Bank Transaction Payments",
                f"{bank_txn_name}:{payment_document}:{payment_entry_name}:match",
            )
            match_sync.target_table = "banking.bank_statement_line_matches"
            match_sync.mark_synced(matched_line.line_id)

    if dry_run:
        db.rollback()
    else:
        db.commit()


def run(args: argparse.Namespace) -> int:
    org_id = uuid.UUID(args.org_id)
    user_id = uuid.UUID(args.user_id)
    company = args.company
    dry_run = args.dry_run

    stats = Stats()
    mysql_conn = _mysql_connect()

    try:
        with SessionLocal() as db:
            assert isinstance(db, Session)
            logger.info("Syncing ERPNext Bank Accounts...")
            erp_bank_to_dotmac = _sync_bank_accounts(
                db=db,
                mysql_conn=mysql_conn,
                organization_id=org_id,
                user_id=user_id,
                company=company,
                dry_run=dry_run,
                stats=stats,
            )
            logger.info("Resolved %d bank account mappings", len(erp_bank_to_dotmac))

        with SessionLocal() as db:
            assert isinstance(db, Session)
            logger.info("Syncing ERPNext Bank Transactions...")
            line_map = _sync_bank_transactions(
                db=db,
                mysql_conn=mysql_conn,
                organization_id=org_id,
                user_id=user_id,
                erp_bank_to_dotmac_bank=erp_bank_to_dotmac,
                company=company,
                dry_run=dry_run,
                stats=stats,
            )
            logger.info("Mapped %d bank transactions to statement lines", len(line_map))

        with SessionLocal() as db:
            assert isinstance(db, Session)
            logger.info("Attaching ERPNext Bank Transaction payment links...")
            _attach_payment_matches(
                db=db,
                mysql_conn=mysql_conn,
                organization_id=org_id,
                user_id=user_id,
                line_map=line_map,
                dry_run=dry_run,
                stats=stats,
            )

    finally:
        mysql_conn.close()

    logger.info("Done%s", " (dry-run)" if dry_run else "")
    logger.info("Bank accounts: created=%d updated=%d skipped=%d", stats.bank_accounts_created, stats.bank_accounts_updated, stats.bank_accounts_skipped)
    logger.info("Statements: created=%d groups_skipped_existing=%d", stats.statements_created, stats.statement_groups_skipped_existing)
    logger.info("Statement lines: imported=%d skipped=%d", stats.statement_lines_imported, stats.statement_lines_skipped)
    logger.info("Payment links attached=%d", stats.payment_links_attached)
    logger.info("Matched lines=%d (match errors=%d)", stats.matched_lines, stats.match_errors)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import ERPNext banking records from SQL into Dotmac.",
    )
    parser.add_argument(
        "--org-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Target organization UUID",
    )
    parser.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="User UUID for created_by/matched_by",
    )
    parser.add_argument(
        "--company",
        default=os.getenv("ERPNEXT_COMPANY", "Dotmac Technologies"),
        help="ERPNext company filter",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without committing changes",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    raise SystemExit(run(build_parser().parse_args()))
