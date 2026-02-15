#!/usr/bin/env python3
"""
Probe unmatched Paystack credit statement lines against Paystack API.

Goal: For leftover unmatched Paystack bank statement credits (e.g. "Payment: <token>"),
use Paystack verify endpoint to fetch customer email/phone/metadata so we can map
back to Splynx customers/invoices.

This script is read-only (it does not write to DB).

Requires:
  - PAYSTACK_SECRET_KEY set (env var or .env already loaded by app config)

Usage:
  ./.venv/bin/python scripts/paystack_probe_unmatched_statement_refs.py --org-id <uuid>
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, "/root/dotmac")

from app.db import SessionLocal
from app.services.finance.payments.paystack_client import (
    PaystackClient,
    PaystackConfig,
    PaystackError,
)

_HEX_TOKEN_RE = re.compile(r"\b[0-9a-f]{12,14}\b", re.IGNORECASE)
_POS_TOKEN_RE = re.compile(r"\bpos_[0-9a-z]{6,}\b", re.IGNORECASE)


def _extract_candidate_refs(
    reference: str | None, description: str | None
) -> list[str]:
    """
    Extract possible Paystack transaction references from bank statement fields.

    We try:
      - the full reference field (often the Paystack reference)
      - embedded hex tokens (12-14 chars) from reference/description
      - embedded pos_* tokens
      - some normalized variants (strip leading 'T' for T123..., strip trailing token from UUID-like refs)
    """
    raw_ref = (reference or "").strip()
    candidates: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        vv = v.strip()
        if not vv or vv.lower().startswith("ob-"):
            return
        if vv not in seen:
            seen.add(vv)
            candidates.append(vv)

    if raw_ref:
        add(raw_ref)
        # Common variant: "T5529..." -> "5529..."
        if raw_ref.startswith("T") and raw_ref[1:].isdigit():
            add(raw_ref[1:])
        # UUID-ish ref that ends with a hex token: keep tail as well.
        if "-" in raw_ref:
            tail = raw_ref.split("-")[-1]
            if _HEX_TOKEN_RE.fullmatch(tail):
                add(tail)

    blob = f"{reference or ''} {description or ''}"
    for m in _HEX_TOKEN_RE.finditer(blob):
        add(m.group(0))
    for m in _POS_TOKEN_RE.finditer(blob):
        add(m.group(0))

    return candidates


def _load_paystack_secret_key(db: Any, *, org_id: UUID) -> str | None:
    """
    Prefer org-specific setting; fall back to global setting.
    """
    row = db.execute(
        text(
            """
            SELECT value_text
            FROM domain_settings
            WHERE domain = 'payments'
              AND key = 'paystack_secret_key'
              AND is_active = true
              AND (organization_id = :org_id OR organization_id IS NULL)
            ORDER BY (organization_id IS NOT NULL) DESC, updated_at DESC
            LIMIT 1
            """
        ),
        {"org_id": org_id},
    ).fetchone()
    if not row:
        return None
    return row.value_text


def _resolve_customer_by_email(
    db: Any, *, org_id: UUID, email: str
) -> dict[str, str] | None:
    if not email:
        return None
    row = db.execute(
        text(
            """
            SELECT customer_id::text AS customer_id,
                   COALESCE(trading_name, legal_name, customer_code) AS name
            FROM ar.customer
            WHERE organization_id = :org_id
              AND LOWER(COALESCE(primary_contact->>'email','')) = LOWER(:email)
            ORDER BY created_at ASC
            LIMIT 1
            """
        ),
        {"org_id": org_id, "email": email},
    ).fetchone()
    if not row:
        return None
    return {"customer_id": row.customer_id, "name": row.name}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org-id", required=True, type=str)
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max unmatched statement lines to probe (default: 200)",
    )
    args = parser.parse_args()

    org_id = UUID(args.org_id)

    with SessionLocal() as db:
        secret = os.getenv("PAYSTACK_SECRET_KEY") or _load_paystack_secret_key(
            db, org_id=org_id
        )
        if not secret:
            raise SystemExit(
                "Paystack secret key not found. Set PAYSTACK_SECRET_KEY or configure "
                "domain_settings(payments/paystack_secret_key)."
            )

        client = PaystackClient(
            PaystackConfig(secret_key=secret, public_key="", webhook_secret="")
        )

        lines = db.execute(
            text(
                """
                WITH paystack_accounts AS (
                    SELECT bank_account_id
                    FROM banking.bank_accounts
                    WHERE organization_id = :org_id
                      AND (LOWER(account_name) LIKE '%paystack%' OR LOWER(bank_name) LIKE '%paystack%')
                )
                SELECT
                    bsl.line_id,
                    bsl.transaction_date,
                    bsl.amount,
                    bsl.reference,
                    bsl.description
                FROM banking.bank_statement_lines bsl
                JOIN banking.bank_statements bs ON bs.statement_id = bsl.statement_id
                WHERE bs.organization_id = :org_id
                  AND bs.bank_account_id IN (SELECT bank_account_id FROM paystack_accounts)
                  AND bsl.transaction_type = 'credit'
                  AND bsl.is_matched = false
                ORDER BY bsl.transaction_date ASC, bsl.amount ASC, bsl.created_at ASC
                LIMIT :limit
                """
            ),
            {"org_id": org_id, "limit": args.limit},
        ).fetchall()

        print(f"unmatched_lines={len(lines)}")

        matched = 0
        for line in lines:
            ref = getattr(line, "reference", None)
            desc = getattr(line, "description", None)
            candidates = _extract_candidate_refs(ref, desc)
            if not candidates:
                continue

            verified = None
            verify_ref = None
            error = None
            for c in candidates[:6]:
                try:
                    verified = client.verify_transaction(c)
                    verify_ref = c
                    break
                except PaystackError as e:
                    error = e.message

            if not verified:
                print(
                    "NO_MATCH|"
                    f"line_id={line.line_id}|date={line.transaction_date}|amount={line.amount}|"
                    f"reference={ref}|candidates={candidates}|error={error}"
                )
                continue

            # Basic guards: only treat as likely match when amount aligns.
            # Paystack returns amount in kobo; our statement is Decimal in major units.
            line_amount_kobo = int(Decimal(str(line.amount)) * 100)
            amount_ok = int(verified.amount) == line_amount_kobo

            local_customer = _resolve_customer_by_email(
                db, org_id=org_id, email=verified.customer_email
            )
            matched += 1
            payload: dict[str, Any] = {
                "line_id": str(line.line_id),
                "line_date": str(line.transaction_date),
                "line_amount": str(line.amount),
                "line_reference": ref,
                "verify_reference_used": verify_ref,
                "paystack": asdict(verified),
                "amount_ok": amount_ok,
                "local_customer": local_customer,
            }
            # Keep output readable: one JSON-ish dict per line.
            print("MATCH|" + str(payload))

        print(f"verified_matches={matched}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
