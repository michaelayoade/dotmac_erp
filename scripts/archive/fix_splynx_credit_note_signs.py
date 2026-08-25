"""
Repair the sign of Splynx-synced AR credit notes.

Splynx sends credit-note totals as POSITIVE values, and the historical sync
(`app/services/splynx/sync/_credit_notes.py`) stored them verbatim. The rest of
DotMac uses a NEGATIVE convention for ``invoice_type=CREDIT_NOTE`` (see
``app/services/finance/ar/invoice.py``), which the AR poster and the day-book
reports assume. The result: Splynx credit notes are stored positive, so they
overstate AR and flip sign vs ERPNext-era credit notes in the Sales Returns Day
Book.

This script normalises the sub-ledger sign to the canonical negative convention:
  * header  : subtotal, tax_amount, total_amount, functional_currency_amount,
              withholding_tax_amount, stamp_duty_amount
  * lines   : line_amount, tax_amount
  * line tax: base_amount, tax_amount, recoverable_amount

Scope guard: only rows still stored POSITIVE are touched
(``source_document_type='splynx_credit_note'``), so the script is IDEMPOTENT —
re-running it changes 0 rows.

NOTE — this only fixes the sub-ledger SIGN. It does NOT create or reverse GL
journals. 49 of these notes already have correct (Cr AR) journals; 18 have none.
The GL-posting of the unposted notes and the mis-tagged journal cleanup are a
separate, finance-signed-off step tied to the AR control reconciliation
(see memory: project_splynx_credit_note_sign_bug / project_audit_tieout_2026-06-03).

Usage:
    # dry run (default) — reports what WOULD change, commits nothing
    docker exec dotmac_erp_app python -m scripts.fix_splynx_credit_note_signs
    # apply
    docker exec dotmac_erp_app python -m scripts.fix_splynx_credit_note_signs --apply
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_HEADER_SQL = """
UPDATE ar.invoice
SET subtotal = -abs(subtotal),
    tax_amount = -abs(tax_amount),
    total_amount = -abs(total_amount),
    functional_currency_amount = -abs(functional_currency_amount),
    withholding_tax_amount = -abs(withholding_tax_amount),
    stamp_duty_amount = -abs(stamp_duty_amount)
WHERE invoice_type = 'CREDIT_NOTE'
  AND source_document_type = 'splynx_credit_note'
  AND total_amount > 0
"""

_LINE_SQL = """
UPDATE ar.invoice_line l
SET line_amount = -abs(l.line_amount),
    tax_amount = -abs(l.tax_amount)
FROM ar.invoice i
WHERE l.invoice_id = i.invoice_id
  AND i.invoice_type = 'CREDIT_NOTE'
  AND i.source_document_type = 'splynx_credit_note'
  AND l.line_amount > 0
"""

_LINE_TAX_SQL = """
UPDATE ar.invoice_line_tax lt
SET base_amount = -abs(lt.base_amount),
    tax_amount = -abs(lt.tax_amount),
    recoverable_amount = -abs(lt.recoverable_amount)
FROM ar.invoice_line l
JOIN ar.invoice i ON i.invoice_id = l.invoice_id
WHERE lt.line_id = l.line_id
  AND i.invoice_type = 'CREDIT_NOTE'
  AND i.source_document_type = 'splynx_credit_note'
  AND lt.base_amount > 0
"""

_COUNT_SQL = """
SELECT
  (SELECT count(*) FROM ar.invoice
     WHERE invoice_type='CREDIT_NOTE' AND source_document_type='splynx_credit_note'
       AND total_amount > 0) AS headers,
  (SELECT count(*) FROM ar.invoice_line l JOIN ar.invoice i ON i.invoice_id=l.invoice_id
     WHERE i.invoice_type='CREDIT_NOTE' AND i.source_document_type='splynx_credit_note'
       AND l.line_amount > 0) AS lines,
  (SELECT count(*) FROM ar.invoice_line_tax lt
     JOIN ar.invoice_line l ON l.line_id=lt.line_id
     JOIN ar.invoice i ON i.invoice_id=l.invoice_id
     WHERE i.invoice_type='CREDIT_NOTE' AND i.source_document_type='splynx_credit_note'
       AND lt.base_amount > 0) AS line_taxes,
  COALESCE((SELECT sum(total_amount) FROM ar.invoice
     WHERE invoice_type='CREDIT_NOTE' AND source_document_type='splynx_credit_note'
       AND total_amount > 0), 0) AS positive_gross
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the sign flip. Without this flag the script only reports.",
    )
    args = parser.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as db:
        counts = db.execute(text(_COUNT_SQL)).one()
        logger.info(
            "Positive (wrong-sign) Splynx credit notes: "
            "%s headers, %s lines, %s line-tax rows; gross +%s",
            counts.headers,
            counts.lines,
            counts.line_taxes,
            counts.positive_gross,
        )

        if counts.headers == 0 and counts.lines == 0 and counts.line_taxes == 0:
            logger.info("Nothing to fix — already normalised. (idempotent no-op)")
            return

        if not args.apply:
            logger.info("DRY RUN — no changes written. Re-run with --apply to commit.")
            return

        h = db.execute(text(_HEADER_SQL)).rowcount
        ln = db.execute(text(_LINE_SQL)).rowcount
        lt = db.execute(text(_LINE_TAX_SQL)).rowcount
        db.commit()
        logger.info(
            "Applied: flipped %s headers, %s lines, %s line-tax rows.", h, ln, lt
        )


if __name__ == "__main__":
    main()
