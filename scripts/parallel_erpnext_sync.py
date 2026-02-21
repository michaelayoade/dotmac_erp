#!/usr/bin/env python3
"""
Parallel ERPNext Financial Document Sync.

Syncs Sales Invoices, Purchase Invoices, Payment Entries, and Journal Entries
from ERPNext using parallel API fetching + sequential DB writes.

Architecture:
  1. List all document names via paginated batch API (fast)
  2. Filter out names already synced (loaded from sync_entity into a set)
  3. ThreadPoolExecutor (N workers) fetches full documents in parallel
  4. Queue feeds fetched docs to a single DB writer (avoids deadlocks)
  5. Commits every BATCH_SIZE records

Usage:
    python scripts/parallel_erpnext_sync.py sales_invoices
    python scripts/parallel_erpnext_sync.py purchase_invoices
    python scripts/parallel_erpnext_sync.py payment_entries
    python scripts/parallel_erpnext_sync.py journal_entries
    python scripts/parallel_erpnext_sync.py all
    python scripts/parallel_erpnext_sync.py all --workers 30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Protocol

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────
ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erp.dotmac.ng")
ERPNEXT_API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")
ERPNEXT_COMPANY = "Dotmac Technologies"
ERPNEXT_SQL_HOST = os.environ.get("ERPNEXT_SQL_HOST", "127.0.0.1")
ERPNEXT_SQL_PORT = int(os.environ.get("ERPNEXT_SQL_PORT", "3307"))
ERPNEXT_SQL_USER = os.environ.get("ERPNEXT_SQL_USER", "root")
ERPNEXT_SQL_PASSWORD = os.environ.get("ERPNEXT_SQL_PASSWORD", "root")
ERPNEXT_SQL_DATABASE = os.environ.get("ERPNEXT_SQL_DATABASE", "erpnext_temp")

WORKERS = int(os.environ.get("SYNC_WORKERS", "20"))
BATCH_SIZE = int(os.environ.get("SYNC_BATCH_SIZE", "200"))


# ── Thread-local ERPNext clients ────────────────────────────────────
_thread_local = threading.local()


class ERPNextSource(Protocol):
    """Read-only ERPNext source used by sync fetch phase."""

    def list_documents(
        self,
        doctype: str,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        limit_start: int = 0,
        limit_page_length: int = 500,
    ) -> list[dict[str, Any]]: ...

    def get_document(self, doctype: str, name: str) -> dict[str, Any] | None: ...

    def test_connection(self) -> dict[str, Any]: ...


class APIERPNextSource:
    """ERPNext API-based source."""

    @staticmethod
    def _make_config():
        from app.services.erpnext.client import ERPNextConfig

        return ERPNextConfig(
            url=ERPNEXT_URL,
            api_key=ERPNEXT_API_KEY,
            api_secret=ERPNEXT_API_SECRET,
            company=ERPNEXT_COMPANY,
            timeout=60.0,
        )

    def _get_thread_client(self):
        from app.services.erpnext.client import ERPNextClient

        if not hasattr(_thread_local, "client"):
            _thread_local.client = ERPNextClient(self._make_config())
            _ = _thread_local.client.client
        return _thread_local.client

    def list_documents(
        self,
        doctype: str,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        limit_start: int = 0,
        limit_page_length: int = 500,
    ) -> list[dict[str, Any]]:
        from app.services.erpnext.client import ERPNextClient

        with ERPNextClient(self._make_config()) as client:
            return client.list_documents(
                doctype=doctype,
                filters=filters or {},
                fields=fields or ["name"],
                order_by=order_by or "name asc",
                limit_start=limit_start,
                limit_page_length=limit_page_length,
            )

    def get_document(self, doctype: str, name: str) -> dict[str, Any] | None:
        return self._get_thread_client().get_document(doctype, name)

    def test_connection(self) -> dict[str, Any]:
        from app.services.erpnext.client import ERPNextClient

        with ERPNextClient(self._make_config()) as client:
            return client.test_connection()


class SQLEngineERPNextSource:
    """ERPNext SQL source (for local imported dump)."""

    _DOC_TABLE = {
        "Sales Invoice": "tabSales Invoice",
        "Purchase Invoice": "tabPurchase Invoice",
        "Payment Entry": "tabPayment Entry",
        "Journal Entry": "tabJournal Entry",
    }
    _CHILD_TABLES = {
        "Sales Invoice": {"items": "tabSales Invoice Item"},
        "Purchase Invoice": {"items": "tabPurchase Invoice Item"},
        "Payment Entry": {"references": "tabPayment Entry Reference"},
        "Journal Entry": {"accounts": "tabJournal Entry Account"},
    }

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    def _get_thread_conn(self) -> Any:
        import pymysql

        if not hasattr(_thread_local, "sql_conn"):
            _thread_local.sql_conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=20,
            )
        return _thread_local.sql_conn

    @staticmethod
    def _table_for_doctype(doctype: str) -> str:
        if doctype in SQLEngineERPNextSource._DOC_TABLE:
            return SQLEngineERPNextSource._DOC_TABLE[doctype]
        return f"tab{doctype}"

    def list_documents(
        self,
        doctype: str,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        limit_start: int = 0,
        limit_page_length: int = 500,
    ) -> list[dict[str, Any]]:
        table = self._table_for_doctype(doctype)
        select_fields = fields or ["name"]
        cols = ", ".join(f"`{col}`" for col in select_fields)
        query = [f"SELECT {cols} FROM `{table}`"]
        params: list[Any] = []

        where_parts: list[str] = []
        for key, value in (filters or {}).items():
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and isinstance(value[0], str)
            ):
                operator = value[0].strip()
                if operator not in {"=", "!=", ">", ">=", "<", "<="}:
                    raise ValueError(f"Unsupported SQL filter operator: {operator}")
                where_parts.append(f"`{key}` {operator} %s")
                params.append(value[1])
            else:
                where_parts.append(f"`{key}` = %s")
                params.append(value)

        if where_parts:
            query.append("WHERE " + " AND ".join(where_parts))

        query.append(f"ORDER BY {order_by or 'name asc'}")
        query.append("LIMIT %s OFFSET %s")
        params.extend([limit_page_length, limit_start])

        sql = " ".join(query)
        with self._get_thread_conn().cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])

    def get_document(self, doctype: str, name: str) -> dict[str, Any] | None:
        table = self._table_for_doctype(doctype)
        with self._get_thread_conn().cursor() as cur:
            cur.execute(f"SELECT * FROM `{table}` WHERE `name` = %s", (name,))
            doc = cur.fetchone()
            if not doc:
                return None

            child_tables = self._CHILD_TABLES.get(doctype, {})
            for child_key, child_table in child_tables.items():
                cur.execute(
                    f"SELECT * FROM `{child_table}` WHERE `parent` = %s "
                    "ORDER BY `idx` ASC",
                    (name,),
                )
                doc[child_key] = list(cur.fetchall() or [])

            return doc

    def test_connection(self) -> dict[str, Any]:
        with self._get_thread_conn().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM `tabSales Invoice` WHERE `docstatus` = 1"
            )
            row = cur.fetchone() or {"total": 0}
            return {
                "user": self.user,
                "database": self.database,
                "sales_invoice_submitted": int(row["total"]),
            }


# ── Parallel fetch infrastructure ───────────────────────────────────


def list_all_names(
    source: ERPNextSource,
    doctype: str,
    filters: dict[str, Any] | None = None,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List all document names/fields via paginated source."""
    all_docs: list[dict[str, Any]] = []
    offset = 0
    page_size = 500
    while True:
        batch = source.list_documents(
            doctype=doctype,
            filters=filters or {},
            fields=fields or ["name"],
            order_by="name asc",
            limit_start=offset,
            limit_page_length=page_size,
        )
        if not batch:
            break
        all_docs.extend(batch)
        offset += page_size
        if len(batch) < page_size:
            break
        if offset % 5000 == 0:
            logger.info("  Listed %d %s so far...", offset, doctype)
    return all_docs


def fetch_full_doc(
    source: ERPNextSource, doctype: str, name: str
) -> dict[str, Any] | None:
    """Fetch a single full document (runs in thread pool)."""
    try:
        return source.get_document(doctype, name)
    except Exception as e:
        logger.debug("Failed to fetch %s %s: %s", doctype, name, e)
        return None


def parallel_fetch_docs(
    source: ERPNextSource,
    doctype: str,
    names: list[str],
    max_workers: int = WORKERS,
) -> list[dict[str, Any]]:
    """Fetch full documents in parallel using ThreadPoolExecutor."""
    results: list[dict[str, Any]] = []
    total = len(names)
    fetched = 0
    errors = 0
    t0 = time.time()

    logger.info(
        "Fetching %d %s documents with %d workers...", total, doctype, max_workers
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {
            executor.submit(fetch_full_doc, source, doctype, name): name
            for name in names
        }
        for future in as_completed(future_to_name):
            fetched += 1
            try:
                doc = future.result()
                if doc:
                    results.append(doc)
                else:
                    errors += 1
            except Exception:
                errors += 1

            if fetched % 1000 == 0:
                elapsed = time.time() - t0
                rate = fetched / elapsed if elapsed > 0 else 0
                logger.info(
                    "  Fetched %d/%d (%.0f/s, %d errors, ETA %.0fs)",
                    fetched,
                    total,
                    rate,
                    errors,
                    (total - fetched) / rate if rate > 0 else 0,
                )

    elapsed = time.time() - t0
    logger.info(
        "Fetch complete: %d/%d documents in %.1fs (%.0f/s, %d errors)",
        len(results),
        total,
        elapsed,
        len(results) / elapsed if elapsed > 0 else 0,
        errors,
    )
    return results


# ── Sync functions per doctype ──────────────────────────────────────


def _get_already_synced(db, org_id: uuid.UUID, source_doctype: str) -> set[str]:
    """Load names with SYNCED status (FAILED rows must be retried)."""
    from sqlalchemy import select

    from app.models.sync import SyncEntity

    rows = (
        db.execute(
            select(SyncEntity.source_name).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.sync_status == "SYNCED",
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def _resolve_accounts(
    db, org_id: uuid.UUID
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Resolve AR/AP control account IDs."""
    from sqlalchemy import select

    from app.models.finance.gl.account import Account

    ar = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == org_id,
            Account.account_code == "1400",
        )
    )
    ap = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == org_id,
            Account.account_code == "2000",
        )
    )
    return ar, ap


def sync_sales_invoices(
    source: ERPNextSource,
    db,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    workers: int,
    from_date: date | None = None,
) -> dict[str, int]:
    """Sync Sales Invoices with parallel fetch."""
    from app.services.erpnext.sync.base import SyncResult
    from app.services.erpnext.sync.sales_invoice import SalesInvoiceSyncService

    ar_account, _ = _resolve_accounts(db, org_id)
    stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0, "deduped": 0}

    # Phase 1: List all Sales Invoice names
    logger.info("Phase 1: Listing Sales Invoice names from ERPNext...")
    filters: dict[str, Any] = {}
    if ERPNEXT_COMPANY:
        filters["company"] = ERPNEXT_COMPANY
    if from_date:
        filters["posting_date"] = [">=", from_date.isoformat()]
    docs_list = list_all_names(
        source,
        "Sales Invoice",
        filters=filters,
        fields=[
            "name",
            "customer",
            "posting_date",
            "due_date",
            "currency",
            "net_total",
            "total_taxes_and_charges",
            "grand_total",
            "base_grand_total",
            "outstanding_amount",
            "conversion_rate",
            "status",
            "docstatus",
            "is_return",
            "return_against",
            "cost_center",
            "project",
            "company",
            "modified",
        ],
    )
    logger.info("  Found %d Sales Invoices in ERPNext", len(docs_list))

    # Phase 2: Filter out already synced
    already_synced = _get_already_synced(db, org_id, "Sales Invoice")
    names_to_fetch = [d["name"] for d in docs_list if d["name"] not in already_synced]
    logger.info(
        "  %d already synced, %d to fetch", len(already_synced), len(names_to_fetch)
    )

    if not names_to_fetch:
        logger.info("Nothing to sync!")
        return stats

    # Phase 3: Parallel fetch full documents
    full_docs = parallel_fetch_docs(source, "Sales Invoice", names_to_fetch, workers)

    # Phase 4: Sequential DB write
    logger.info("Phase 4: Writing %d records to database...", len(full_docs))
    svc = SalesInvoiceSyncService(db, org_id, user_id)
    svc.ar_control_account_id = ar_account

    result = SyncResult(entity_type="Sales Invoice")
    batch_count = 0
    t0 = time.time()

    for i, doc in enumerate(full_docs):
        stats["total"] += 1
        source_name = doc.get("name", "")

        try:
            savepoint = db.begin_nested()
            try:
                svc._sync_single_record(doc, result)
                batch_count += 1
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise

            if batch_count >= BATCH_SIZE:
                db.commit()
                batch_count = 0

        except Exception as e:
            err_msg = str(e)[:200]
            if "ForeignKeyViolation" in err_msg:
                stats["errors"] += 1
            else:
                logger.warning("Error syncing %s: %s", source_name, err_msg)
                stats["errors"] += 1

        if (i + 1) % 1000 == 0:
            elapsed = time.time() - t0
            logger.info(
                "  Processed %d/%d (%.0f/s) — synced=%d skip=%d err=%d",
                i + 1,
                len(full_docs),
                (i + 1) / elapsed,
                result.synced_count,
                result.skipped_count,
                stats["errors"],
            )

    # Final commit
    if batch_count:
        db.commit()

    stats["synced"] = result.synced_count
    stats["skipped"] = result.skipped_count
    stats["deduped"] = result.skipped_count  # Splynx dedup shows as skips

    elapsed = time.time() - t0
    logger.info(
        "Sales Invoice sync complete: %d synced, %d skipped, %d errors in %.1fs",
        stats["synced"],
        stats["skipped"],
        stats["errors"],
        elapsed,
    )
    return stats


def sync_purchase_invoices(
    source: ERPNextSource,
    db,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    workers: int,
    from_date: date | None = None,
) -> dict[str, int]:
    """Sync Purchase Invoices with parallel fetch."""
    from app.services.erpnext.sync.base import SyncResult
    from app.services.erpnext.sync.purchase_invoice import PurchaseInvoiceSyncService

    _, ap_account = _resolve_accounts(db, org_id)
    stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0}

    logger.info("Phase 1: Listing Purchase Invoice names...")
    filters: dict[str, Any] = {}
    if ERPNEXT_COMPANY:
        filters["company"] = ERPNEXT_COMPANY
    if from_date:
        filters["posting_date"] = [">=", from_date.isoformat()]
    docs_list = list_all_names(source, "Purchase Invoice", filters=filters)
    logger.info("  Found %d Purchase Invoices", len(docs_list))

    already_synced = _get_already_synced(db, org_id, "Purchase Invoice")
    names_to_fetch = [d["name"] for d in docs_list if d["name"] not in already_synced]
    logger.info(
        "  %d already synced, %d to fetch", len(already_synced), len(names_to_fetch)
    )

    if not names_to_fetch:
        logger.info("Nothing to sync!")
        return stats

    full_docs = parallel_fetch_docs(source, "Purchase Invoice", names_to_fetch, workers)

    logger.info("Phase 4: Writing %d records to database...", len(full_docs))
    svc = PurchaseInvoiceSyncService(db, org_id, user_id)
    svc.ap_control_account_id = ap_account

    result = SyncResult(entity_type="Purchase Invoice")
    batch_count = 0
    t0 = time.time()

    for i, doc in enumerate(full_docs):
        stats["total"] += 1
        source_name = doc.get("name", "")

        try:
            savepoint = db.begin_nested()
            try:
                svc._sync_single_record(doc, result)
                batch_count += 1
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise

            if batch_count >= BATCH_SIZE:
                db.commit()
                batch_count = 0

        except Exception as e:
            err_msg = str(e)[:200]
            if "ForeignKeyViolation" not in err_msg:
                logger.warning("Error syncing %s: %s", source_name, err_msg)
            stats["errors"] += 1

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            logger.info(
                "  Processed %d/%d — synced=%d err=%d",
                i + 1,
                len(full_docs),
                result.synced_count,
                stats["errors"],
            )

    if batch_count:
        db.commit()

    stats["synced"] = result.synced_count
    stats["skipped"] = result.skipped_count

    elapsed = time.time() - t0
    logger.info(
        "Purchase Invoice sync: %d synced, %d skipped, %d errors in %.1fs",
        stats["synced"],
        stats["skipped"],
        stats["errors"],
        elapsed,
    )
    return stats


def sync_payment_entries(
    source: ERPNextSource,
    db,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    workers: int,
    from_date: date | None = None,
) -> dict[str, int]:
    """Sync Payment Entries (AR + AP) with parallel fetch."""
    from app.services.erpnext.sync.base import SyncResult
    from app.services.erpnext.sync.payment_entry import PaymentEntrySyncService

    stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0, "ar": 0, "ap": 0}

    logger.info("Phase 1: Listing Payment Entry names...")
    filters: dict[str, Any] = {"docstatus": 1}  # Only submitted
    if ERPNEXT_COMPANY:
        filters["company"] = ERPNEXT_COMPANY
    if from_date:
        filters["posting_date"] = [">=", from_date.isoformat()]
    docs_list = list_all_names(source, "Payment Entry", filters=filters)
    logger.info("  Found %d Payment Entries", len(docs_list))

    already_synced = _get_already_synced(db, org_id, "Payment Entry")
    names_to_fetch = [d["name"] for d in docs_list if d["name"] not in already_synced]
    logger.info(
        "  %d already synced, %d to fetch", len(already_synced), len(names_to_fetch)
    )

    if not names_to_fetch:
        logger.info("Nothing to sync!")
        return stats

    full_docs = parallel_fetch_docs(source, "Payment Entry", names_to_fetch, workers)

    logger.info("Phase 4: Writing %d records to database...", len(full_docs))
    svc = PaymentEntrySyncService(db, org_id, user_id)

    result = SyncResult(entity_type="Payment Entry")
    batch_count = 0
    t0 = time.time()

    for i, doc in enumerate(full_docs):
        stats["total"] += 1
        source_name = doc.get("name", "")

        try:
            savepoint = db.begin_nested()
            try:
                svc._sync_single_record(doc, result)
                batch_count += 1
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise

            if batch_count >= BATCH_SIZE:
                db.commit()
                batch_count = 0

        except Exception as e:
            err_msg = str(e)[:200]
            if "ForeignKeyViolation" not in err_msg:
                logger.warning("Error syncing %s: %s", source_name, err_msg)
            stats["errors"] += 1

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            logger.info(
                "  Processed %d/%d — synced=%d err=%d",
                i + 1,
                len(full_docs),
                result.synced_count,
                stats["errors"],
            )

    if batch_count:
        db.commit()

    stats["synced"] = result.synced_count
    stats["skipped"] = result.skipped_count

    elapsed = time.time() - t0
    logger.info(
        "Payment Entry sync: %d synced, %d skipped, %d errors in %.1fs",
        stats["synced"],
        stats["skipped"],
        stats["errors"],
        elapsed,
    )
    return stats


def sync_journal_entries(
    source: ERPNextSource,
    db,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    workers: int,
    from_date: date | None = None,
) -> dict[str, int]:
    """Sync Journal Entries with parallel fetch."""
    from app.services.erpnext.sync.base import SyncResult
    from app.services.erpnext.sync.journal_entry import JournalEntrySyncService

    stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0}

    logger.info("Phase 1: Listing Journal Entry names...")
    filters: dict[str, Any] = {"docstatus": 1}  # Only submitted
    if ERPNEXT_COMPANY:
        filters["company"] = ERPNEXT_COMPANY
    if from_date:
        filters["posting_date"] = [">=", from_date.isoformat()]
    docs_list = list_all_names(source, "Journal Entry", filters=filters)
    logger.info("  Found %d Journal Entries", len(docs_list))

    already_synced = _get_already_synced(db, org_id, "Journal Entry")
    names_to_fetch = [d["name"] for d in docs_list if d["name"] not in already_synced]
    logger.info(
        "  %d already synced, %d to fetch", len(already_synced), len(names_to_fetch)
    )

    if not names_to_fetch:
        logger.info("Nothing to sync!")
        return stats

    full_docs = parallel_fetch_docs(source, "Journal Entry", names_to_fetch, workers)

    logger.info("Phase 4: Writing %d records to database...", len(full_docs))
    svc = JournalEntrySyncService(db, org_id, user_id)

    result = SyncResult(entity_type="Journal Entry")
    batch_count = 0
    t0 = time.time()

    for i, doc in enumerate(full_docs):
        stats["total"] += 1
        source_name = doc.get("name", "")

        try:
            savepoint = db.begin_nested()
            try:
                svc._sync_single_record(doc, result)
                batch_count += 1
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise

            if batch_count >= BATCH_SIZE:
                db.commit()
                batch_count = 0

        except Exception as e:
            err_msg = str(e)[:200]
            if "ForeignKeyViolation" not in err_msg:
                logger.warning("Error syncing %s: %s", source_name, err_msg)
            stats["errors"] += 1

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            logger.info(
                "  Processed %d/%d — synced=%d err=%d",
                i + 1,
                len(full_docs),
                result.synced_count,
                stats["errors"],
            )

    if batch_count:
        db.commit()

    stats["synced"] = result.synced_count
    stats["skipped"] = result.skipped_count

    elapsed = time.time() - t0
    logger.info(
        "Journal Entry sync: %d synced, %d skipped, %d errors in %.1fs",
        stats["synced"],
        stats["skipped"],
        stats["errors"],
        elapsed,
    )
    return stats


# ── Main ────────────────────────────────────────────────────────────


SYNC_FUNCTIONS = {
    "sales_invoices": sync_sales_invoices,
    "purchase_invoices": sync_purchase_invoices,
    "payment_entries": sync_payment_entries,
    "journal_entries": sync_journal_entries,
}

# Order matters: invoices before payments (for allocation FK resolution)
SYNC_ORDER = [
    "sales_invoices",
    "purchase_invoices",
    "payment_entries",
    "journal_entries",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parallel ERPNext Financial Document Sync",
    )
    parser.add_argument(
        "entities",
        nargs="+",
        choices=list(SYNC_FUNCTIONS.keys()) + ["all"],
        help="Entity types to sync",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=WORKERS,
        help=f"Number of parallel API fetch workers (default: {WORKERS})",
    )
    parser.add_argument(
        "--org-id",
        help="Organization ID (UUID), auto-detected if omitted",
    )
    parser.add_argument(
        "--user-id",
        help="User ID (UUID), auto-detected if omitted",
    )
    parser.add_argument(
        "--source",
        choices=["sql"],
        default="sql",
        help="Read ERPNext from SQL dump DB (default: sql)",
    )
    parser.add_argument("--sql-host", default=ERPNEXT_SQL_HOST)
    parser.add_argument("--sql-port", type=int, default=ERPNEXT_SQL_PORT)
    parser.add_argument("--sql-user", default=ERPNEXT_SQL_USER)
    parser.add_argument("--sql-password", default=ERPNEXT_SQL_PASSWORD)
    parser.add_argument("--sql-database", default=ERPNEXT_SQL_DATABASE)
    parser.add_argument(
        "--from-date",
        help="Only sync records on/after this date (YYYY-MM-DD)",
    )

    args = parser.parse_args()

    # Resolve entities
    if "all" in args.entities:
        entities = SYNC_ORDER
    else:
        entities = [e for e in SYNC_ORDER if e in args.entities]

    from_date: date | None = None
    if args.from_date:
        try:
            from_date = date.fromisoformat(args.from_date)
        except ValueError:
            logger.error(
                "Invalid --from-date format (expected YYYY-MM-DD): %s", args.from_date
            )
            sys.exit(2)

    # Connect to DB
    from app.db import SessionLocal

    db = SessionLocal()

    try:
        # Auto-detect org + user
        org_id = uuid.UUID(args.org_id) if args.org_id else None
        user_id = uuid.UUID(args.user_id) if args.user_id else None

        if not org_id:
            from app.models.finance.core_org import Organization

            org = db.query(Organization).first()
            if not org:
                logger.error("No organization found")
                sys.exit(1)
            org_id = org.organization_id

        if not user_id:
            from app.models.person import Person

            person = (
                db.query(Person)
                .filter(Person.organization_id == org_id, Person.is_active.is_(True))
                .first()
            )
            if not person:
                logger.error("No active person found")
                sys.exit(1)
            user_id = person.id

        logger.info("=" * 60)
        logger.info("Parallel ERPNext Sync")
        logger.info("=" * 60)
        logger.info("Organization: %s", org_id)
        logger.info("User: %s", user_id)
        logger.info("Workers: %d", args.workers)
        logger.info("Source: %s", args.source)
        logger.info("From date: %s", from_date.isoformat() if from_date else "none")
        logger.info("Entities: %s", ", ".join(entities))
        logger.info("=" * 60)

        source: ERPNextSource
        source = SQLEngineERPNextSource(
            host=args.sql_host,
            port=args.sql_port,
            user=args.sql_user,
            password=args.sql_password,
            database=args.sql_database,
        )

        result = source.test_connection()
        logger.info("ERPNext source connection: %s", result)

        # Run syncs
        all_stats: dict[str, dict[str, int]] = {}
        for entity in entities:
            logger.info("\n" + "=" * 60)
            logger.info("SYNCING: %s", entity.upper())
            logger.info("=" * 60)

            t0 = time.time()
            stats = SYNC_FUNCTIONS[entity](
                source,
                db,
                org_id,
                user_id,
                args.workers,
                from_date=from_date,
            )
            elapsed = time.time() - t0

            all_stats[entity] = stats
            logger.info("  Done in %.1fs", elapsed)

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 60)
        for entity, stats in all_stats.items():
            logger.info(
                "  %-20s synced=%-6d skipped=%-6d errors=%-6d",
                entity,
                stats.get("synced", 0),
                stats.get("skipped", 0),
                stats.get("errors", 0),
            )
        logger.info("=" * 60)

    except Exception as e:
        db.rollback()
        logger.exception("Sync failed: %s", e)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
