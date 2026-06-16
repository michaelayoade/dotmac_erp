"""Canonical ``JournalEntry.source_document_type`` values for the invoice family.

These tie the GL posting layer to the books-of-prime-entry reports. A posted
journal carrying one of these tags is represented in the Sales / Purchases day
books, so it MUST be excluded from the Journal Proper (see
``app/services/finance/rpt/day_books.py``). The two sides used to agree only by
duplicated string literals: the live AR/AP invoice posters wrote ``"INVOICE"`` /
``"SUPPLIER_INVOICE"``, but the dormant AR posting saga wrote ``"CUSTOMER_INVOICE"``
— had it ever been activated, every customer invoice journal would have escaped
the exclusion and been double-counted (Sales Day Book *and* Journal Proper).

Importing these constants in both the posting services and the report keeps the
partition correct by construction. The string VALUES are unchanged from what is
already in the database (26k+ ``INVOICE`` / 1k+ ``SUPPLIER_INVOICE`` journals),
so this is a code-only change — no data migration.
"""

from __future__ import annotations

# The trade invoice itself (Sales / Purchases day books).
AR_INVOICE_SOURCE = "INVOICE"
AP_INVOICE_SOURCE = "SUPPLIER_INVOICE"

# Cash-basis VAT deferral legs auto-generated as a companion to each invoice
# (see feedback_vat_cash_basis). Present as legacy/backfill data — no current
# code writes them — but still part of the invoice family for partition purposes.
AR_INVOICE_VAT_DEFERRAL_SOURCE = "AR_INVOICE_VAT_DEFERRAL"
AP_INVOICE_VAT_DEFERRAL_SOURCE = "SUPPLIER_INVOICE_VAT_DEFERRAL"

# Every invoice-family source represented in the Sales/Purchases day books, and
# therefore excluded from the Journal Proper.
INVOICE_JOURNAL_SOURCE_TYPES: tuple[str, ...] = (
    AR_INVOICE_SOURCE,
    AP_INVOICE_SOURCE,
    AR_INVOICE_VAT_DEFERRAL_SOURCE,
    AP_INVOICE_VAT_DEFERRAL_SOURCE,
)
