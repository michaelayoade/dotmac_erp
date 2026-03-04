"""
Link Splynx reseller partners to parent-child hierarchy in ar.customer.

Reads all customers from Splynx API, groups by partner_id, identifies the
parent account for each reseller group, then sets:
  - splynx_partner_id on the parent account
  - parent_customer_id on all child accounts

Also links ERPNext-only reseller accounts (no splynx_id) to the same parent
when they match by name pattern.

Usage:
    # Dry run (default) — shows what would change
    docker exec dotmac_erp_app python3 scripts/link_reseller_parent_child.py

    # Apply changes
    docker exec dotmac_erp_app python3 scripts/link_reseller_parent_child.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, "/app")

from uuid import UUID

from sqlalchemy import text

from app.db import SessionLocal
from app.services.splynx.client import SplynxClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Manually curated parent accounts per Splynx partner_id ──────────────
# Each entry: partner_id -> (parent_customer_code, parent_description)
# These were identified by reviewing the Splynx partner groups and picking
# the most representative "main" account for each reseller.

PARENT_MAP: dict[int, str] = {
    # partner_id: customer_code of the parent account
    2: "CUST-05009",  # Idelta - "Idelta-Alh Umar Gana" (oldest/first account)
    3: "CUST-00318",  # Hyperia - "HYPERIA (C.E.O)" — the CEO/main account
    4: "CUST-05041",  # 2dotcom - "2dotcom Solutions ( Egypt Air)" (oldest splynx_id=202)
    5: "CUST-00259",  # ISN - "ISN Kewalrams Abuja" (oldest splynx_id=54)
    6: "CUST-04998",  # Pedery - "Pedery Global Concept (GolfTech)" (oldest splynx_id=165)
    7: "CUST-05095",  # Wanserver - "Aimuan .A. Harrison (House1)" (oldest splynx_id=511)
    8: "CUST-00342",  # Hausba/Gboyega - "Gboyega(Mar and Mor)" (oldest splynx_id=108)
    9: "CUST-00266",  # Cheerymoon - "Cheerymoon global concept Ltd" (splynx_id=222, the base company)
    10: "CUST-00370",  # Chikalvia - "Chikalvia Integrated Nigeria Ltd (Flower Gate Apo)" (oldest)
    11: "CUST-02533",  # Hallowgate - single account, is its own parent
    12: "CUST-03650",  # Quantum Construct - "Quantum Construct" (oldest splynx_id=13022)
    13: "CUST-02649",  # VCIT - "VCIT (Main)" — literally says Main
    14: "CUST-04223",  # Tehilah - single account
    16: "CUST-00900",  # CARE Nigeria - "CARE Nigeria (7th Avenue)" (oldest)
    17: "CUST-00509",  # SkyPro - "SkyPro Internet Bowoto Tobi" (oldest splynx_id=962)
    18: "CUST-04503",  # National Pension - "National Pension commission (Blantyre Cres)" (oldest)
    19: "CUST-02114",  # Metronet - "Metronet Systems Ltd" (bare company name)
    20: "CUST-05026",  # Heritage - "Heritage worldwide synergy (Rotiba)" (oldest splynx_id=271)
    21: "CUST-04036",  # Heritage RAAMP - single account
    22: "CUST-03747",  # MegaMore - "MegaMore Apo" (only active one)
    23: "CUST-01929",  # Voggnet - "Elevate Africa(voggnet)" (first in Splynx)
}

# ERPNext-only accounts to link to a parent (no splynx_id, identified by name pattern)
# Format: customer_code -> parent_customer_code
ERPNEXT_ONLY_LINKS: dict[str, str] = {
    # Hyperia ERPNext-only -> Hyperia parent (CUST-00318)
    "CUST-00005": "CUST-00318",  # Hyperia Quantun
    "CUST-00024": "CUST-00318",  # Hyperia Churchgate
    "CUST-00069": "CUST-00318",  # Hyperia PWC
    "CUST-00141": "CUST-00318",  # Hyperia Netzence
    # 2dotcom ERPNext-only -> 2dotcom parent (CUST-05041)
    "CUST-00018": "CUST-05041",  # 2dotcom Solutions (Coutonou Crescent)
    "CUST-00022": "CUST-05041",  # 2dotcom (mohammed)
    "CUST-00048": "CUST-05041",  # Mr Hassan 2dotcom
    "CUST-00084": "CUST-05041",  # 2dotcom Solutions (Bogana Chambers)
    "CUST-00099": "CUST-05041",  # 2dotcom Solutions (Mr Femi)
    "CUST-00126": "CUST-05041",  # 2dotcom Solutions (Mrs Oyebadejo)
    "CUST-00136": "CUST-05041",  # 2dotcom solutions(Banex MFB)
    # Pedery ERPNext-only -> Pedery parent (CUST-04998)
    "CUST-00021": "CUST-04998",  # Pedery Global Concept (Asokoro)
    # Voggnet ERPNext-only -> Voggnet parent (CUST-01929)
    "CUST-00096": "CUST-01929",  # Voggnet (Joy Onutor)
}


def run(*, apply: bool = False) -> None:
    """Main entry point."""
    client = SplynxClient()

    # ── Step 1: Fetch all Splynx customers and group by partner_id ──
    logger.info("Fetching Splynx customers...")
    splynx_by_partner: dict[int, list[int]] = {}  # partner_id -> [splynx_id, ...]
    for c in client.get_customers():
        splynx_by_partner.setdefault(c.partner_id, []).append(c.id)
    client.close()

    total_splynx = sum(len(v) for v in splynx_by_partner.values())
    logger.info(
        "Fetched %d Splynx customers across %d partners",
        total_splynx,
        len(splynx_by_partner),
    )

    with SessionLocal() as db:
        # ── Step 2: Build ERP lookup: customer_code -> customer_id ──
        rows = db.execute(
            text(
                "SELECT customer_id, customer_code, splynx_id "
                "FROM ar.customer ORDER BY customer_code"
            )
        ).fetchall()

        code_to_id: dict[str, UUID] = {}
        splynx_to_id: dict[str, UUID] = {}
        for cid, code, sid in rows:
            code_to_id[code] = cid
            if sid:
                for s in sid.split(","):
                    s = s.strip()
                    if s:
                        splynx_to_id[s] = cid

        # ── Step 3: For each partner group, set parent and children ──
        total_parent_updates = 0
        total_child_updates = 0
        total_erpnext_links = 0

        for partner_id, splynx_ids in sorted(splynx_by_partner.items()):
            if partner_id <= 1:
                continue  # Direct customers, no parent

            parent_code = PARENT_MAP.get(partner_id)
            if not parent_code:
                logger.warning(
                    "No parent mapping for partner_id=%d (%d customers) — skipping",
                    partner_id,
                    len(splynx_ids),
                )
                continue

            parent_uuid = code_to_id.get(parent_code)
            if not parent_uuid:
                logger.error(
                    "Parent %s not found in DB — skipping partner %d",
                    parent_code,
                    partner_id,
                )
                continue

            # Set splynx_partner_id on the parent
            logger.info(
                "Partner %d: parent=%s, %d Splynx children",
                partner_id,
                parent_code,
                len(splynx_ids),
            )

            if apply:
                db.execute(
                    text(
                        "UPDATE ar.customer SET splynx_partner_id = :pid "
                        "WHERE customer_id = :cid"
                    ),
                    {"pid": str(partner_id), "cid": parent_uuid},
                )
            total_parent_updates += 1

            # Set parent_customer_id on all children in this group
            for sid in splynx_ids:
                child_uuid = splynx_to_id.get(str(sid))
                if not child_uuid:
                    continue
                if child_uuid == parent_uuid:
                    continue  # Don't self-reference

                total_child_updates += 1
                if apply:
                    db.execute(
                        text(
                            "UPDATE ar.customer SET parent_customer_id = :parent_id "
                            "WHERE customer_id = :child_id AND "
                            "(parent_customer_id IS NULL OR parent_customer_id != :parent_id)"
                        ),
                        {"parent_id": parent_uuid, "child_id": child_uuid},
                    )

        # ── Step 4: Link ERPNext-only accounts ──
        for child_code, parent_code in ERPNEXT_ONLY_LINKS.items():
            child_uuid = code_to_id.get(child_code)
            parent_uuid = code_to_id.get(parent_code)
            if not child_uuid or not parent_uuid:
                logger.warning(
                    "ERPNext link %s -> %s: record not found", child_code, parent_code
                )
                continue

            logger.info("ERPNext-only link: %s -> parent %s", child_code, parent_code)
            total_erpnext_links += 1

            if apply:
                db.execute(
                    text(
                        "UPDATE ar.customer SET parent_customer_id = :parent_id "
                        "WHERE customer_id = :child_id AND "
                        "(parent_customer_id IS NULL OR parent_customer_id != :parent_id)"
                    ),
                    {"parent_id": parent_uuid, "child_id": child_uuid},
                )

        # ── Summary ──
        logger.info("=" * 60)
        logger.info("Summary:")
        logger.info(
            "  Parent accounts to set splynx_partner_id: %d", total_parent_updates
        )
        logger.info(
            "  Child accounts to set parent_customer_id:  %d", total_child_updates
        )
        logger.info(
            "  ERPNext-only accounts to link:             %d", total_erpnext_links
        )
        logger.info(
            "  Total updates:                             %d",
            total_parent_updates + total_child_updates + total_erpnext_links,
        )

        if apply:
            db.commit()
            logger.info("COMMITTED — changes applied.")
        else:
            logger.info("DRY RUN — no changes made. Use --apply to commit.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Link reseller parent-child hierarchy")
    parser.add_argument(
        "--apply", action="store_true", help="Apply changes (default: dry run)"
    )
    args = parser.parse_args()
    run(apply=args.apply)
