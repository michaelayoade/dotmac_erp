"""
Remediate the GL postings of Splynx-synced AR credit notes.

Companion to ``scripts/fix_splynx_credit_note_signs.py`` (which already fixed the
sub-ledger SIGN). Investigation 2026-06-04 found that of 67 Splynx credit notes:

  * 30 have a PROPER GL journal (Cr Trade Receivables / Dr revenue) — correct.
  * 19 have a NET-ZERO JUNK journal: posted while ``ar_control_account_id`` pointed
    at the revenue account (4000), so both legs landed on 4000 → zero GL effect,
    no AR line. (The invoice rows were later corrected to 1400 but never re-posted.)
  * 18 have NO journal at all.

So 37 credit notes (~₦1.22M gross) never reduced AR in the GL — the Splynx slice of
the AR sub-ledger↔control break (project_audit_tieout_2026-06-03). All 67 now carry
the correct ``ar_control_account_id`` (1400 Trade Receivables), so re-posting via the
standard AR poster produces correct entries (Cr 1400 / Dr revenue / Dr VAT).

Remediation per affected note:
  1. (junk only) Reverse the net-zero junk journal (``GLJournalService.reverse_entry``)
     and clear ``journal_entry_id`` / ``posting_status`` so the note looks unposted.
  2. Re-post via ``ARInvoiceService.ensure_gl_posted`` (uses the now-correct 1400).

⚠️  THIS MUTATES THE GENERAL LEDGER in the reconciliation-sensitive current FY.
    DO NOT run with --apply without finance sign-off. Dry-run is the default and
    only reports the plan; it changes nothing.

Usage:
    # dry run (default) — prints the plan, writes nothing
    docker exec dotmac_erp_app python -m scripts.remediate_splynx_credit_note_gl
    # apply (requires finance sign-off)
    docker exec dotmac_erp_app python -m scripts.remediate_splynx_credit_note_gl --apply
"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")

# Splynx credit notes that lack a real AR posting:
#   no_journal  -> journal_entry_id IS NULL
#   junk_journal-> has a journal, but that journal has NO Trade-Receivables (AR) line
_AFFECTED_SQL = """
SELECT i.invoice_id,
       i.invoice_number,
       i.organization_id,
       i.invoice_date,
       abs(i.total_amount) AS gross,
       abs(i.subtotal)     AS net,
       abs(i.tax_amount)   AS vat,
       i.journal_entry_id,
       CASE WHEN i.journal_entry_id IS NULL THEN 'no_journal'
            ELSE 'junk_journal' END AS klass
FROM ar.invoice i
WHERE i.invoice_type = 'CREDIT_NOTE'
  AND i.source_document_type = 'splynx_credit_note'
  AND (
        i.journal_entry_id IS NULL
        OR i.journal_entry_id NOT IN (
            SELECT jel.journal_entry_id
            FROM gl.journal_entry_line jel
            JOIN gl.account a ON a.account_id = jel.account_id
            JOIN gl.account_category ac ON ac.category_id = a.category_id
            WHERE ac.category_code = 'AR'
        )
      )
ORDER BY i.invoice_date, i.invoice_number
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reverse junk journals and re-post. Requires finance sign-off.",
    )
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.db.session_context import allow_cross_org, bypass_rls_sync

    with SessionLocal() as db:
        with bypass_rls_sync(db), allow_cross_org(db):
            rows = db.execute(text(_AFFECTED_SQL)).all()

            junk = [r for r in rows if r.klass == "junk_journal"]
            none = [r for r in rows if r.klass == "no_journal"]
            gross = sum((r.gross for r in rows), Decimal("0"))
            net = sum((r.net for r in rows), Decimal("0"))
            vat = sum((r.vat for r in rows), Decimal("0"))

            logger.info("Splynx credit notes lacking a real AR posting: %s", len(rows))
            logger.info("  junk net-zero journals to reverse: %s", len(junk))
            logger.info("  no journal at all:                 %s", len(none))
            logger.info(
                "Corrective GL once re-posted (aggregate): "
                "Cr Trade Receivables %s ; Dr Revenue %s ; Dr VAT %s",
                gross,
                net,
                vat,
            )
            logger.info(
                "Effect: reduces GL AR control by %s, closing the Splynx-CN slice "
                "of the AR sub-ledger<->control gap.",
                gross,
            )

            if not rows:
                logger.info("Nothing to remediate. (idempotent no-op)")
                return

            if not args.apply:
                logger.info(
                    "DRY RUN — no GL changes written. Junk journals that WOULD be "
                    "reversed: %s",
                    ", ".join(str(r.journal_entry_id) for r in junk) or "(none)",
                )
                logger.info("Re-run with --apply ONLY after finance sign-off.")
                return

        # ---- apply path (mutates GL; runs outside the read-only context above) ----
        from app.models.finance.ar.invoice import Invoice
        from app.services.finance.ar.invoice import ARInvoiceService
        from app.services.finance.gl.journal import GLJournalService

        reversed_n = 0
        reposted_n = 0
        failed: list[str] = []
        with bypass_rls_sync(db), allow_cross_org(db):
            for r in rows:
                invoice = db.get(Invoice, r.invoice_id)
                if invoice is None:
                    failed.append(f"{r.invoice_number}: invoice vanished")
                    continue
                try:
                    if r.klass == "junk_journal" and invoice.journal_entry_id:
                        GLJournalService.reverse_entry(
                            db=db,
                            organization_id=invoice.organization_id,
                            entry_id=invoice.journal_entry_id,
                            reversal_date=invoice.invoice_date,
                            reversed_by_user_id=SYSTEM_USER_ID,
                        )
                        invoice.journal_entry_id = None
                        invoice.posting_batch_id = None
                        invoice.posting_status = "NOT_POSTED"
                        db.flush()
                        reversed_n += 1

                    if ARInvoiceService.ensure_gl_posted(
                        db, invoice, posted_by_user_id=SYSTEM_USER_ID
                    ):
                        reposted_n += 1
                    else:
                        failed.append(
                            f"{r.invoice_number}: ensure_gl_posted returned False"
                        )
                except Exception as exc:  # noqa: BLE001 - report per note, continue batch
                    db.rollback()
                    failed.append(f"{r.invoice_number}: {exc}")
                    continue
            db.commit()

        logger.info(
            "Applied: reversed %s junk journals, re-posted %s credit notes. Failures: %s",
            reversed_n,
            reposted_n,
            len(failed),
        )
        for f in failed:
            logger.warning("  FAILED %s", f)


if __name__ == "__main__":
    main()
