"""Parent multi-site / duplicate customer records under a single family.

Background: the customer master holds many sibling records for one real entity
(NTEL <site>, FAO <office>, BCN agents, Cheerymoon dups, …) that are NOT linked
by ``parent_customer_id``. This breaks the bank-reconciliation counterparty
guard (a legitimate one-deposit-for-many-sites batch reads as "many unrelated
payers") and prevents consolidated statements.

This script groups the *confirmed* real entities (curated, word-boundary matched
to avoid first-name collisions like "Ifaorumhe" ~ FAO):
  * GROUP entities  -> create a synthetic "<Entity> (Group)" parent, reassign
                       every matched sibling under it.
  * BASE entities   -> designate the canonical (un-suffixed, shortest) record as
                       the parent and reassign the numbered duplicates under it.

Idempotent: a group parent is found-or-created by exact name; children already
pointing at the right parent are skipped. Default is DRY-RUN; pass ``--apply``
to commit. Never self-parents and never creates a cycle.

    python scripts/migration/2026-06-04_parent_multisite_customers.py          # dry-run
    python scripts/migration/2026-06-04_parent_multisite_customers.py --apply  # commit
"""

from __future__ import annotations

import re
import sys

from sqlalchemy import func, select

from app.db import SessionLocal
from app.db.session_context import allow_cross_org
from app.models.finance.ar.customer import Customer, CustomerType
from app.services.finance.ar.customer import CustomerInput, CustomerService

APPLY = "--apply" in sys.argv

# (label, parent_name, include_regex, exclude_regex)
GROUP = [
    ("NTEL", "NTEL (Group)", r"\bNTEL\b", None),
    ("Dreylinks", "Dreylinks (Group)", r"\bDREYLINKS?\b", None),
    ("BCN", "BCN (Group)", r"\bBCN\b", None),
    ("Matrix Global", "Matrix Global (Group)", r"\bMATRIX\s+GLOBAL\b", None),
    ("Netflare", "Netflare (Group)", r"\bNETFLARE\b", None),
    ("FAO", "FAO (Group)", r"\bFAO\b", r"IFAORUMHE"),
    # Mimi's excluded — only 2 records, possibly distinct businesses (CRM review).
]
# (label, include_regex) — parent = canonical (no trailing number, shortest)
BASE = [
    ("CONLEK", r"\bCONLEK\b"),
    ("Cheerymoon", r"\bCHEERYMOON\b"),
]


def _match(custs: list[Customer], inc: str, exc: str | None) -> list[Customer]:
    out = []
    for c in custs:
        n = c.legal_name or ""
        if re.search(inc, n, re.I) and not (exc and re.search(exc, n, re.I)):
            out.append(c)
    return out


def main() -> None:
    created = reassigned = 0
    with SessionLocal() as db:  # noqa: SIM117 -- preserve archived flow
        with allow_cross_org(db):
            org_id = db.execute(
                select(Customer.organization_id, func.count())
                .group_by(Customer.organization_id)
                .order_by(func.count().desc())
            ).first()[0]
            custs = list(
                db.execute(select(Customer).where(Customer.organization_id == org_id))
                .scalars()
                .all()
            )
            by_name = {(c.legal_name or ""): c for c in custs}

            def set_parent(child: Customer, parent: Customer) -> None:
                nonlocal reassigned
                if child.customer_id == parent.customer_id:
                    return
                if child.parent_customer_id == parent.customer_id:
                    return
                print(f"      reassign {child.legal_name!r} -> {parent.legal_name!r}")
                if APPLY:
                    child.parent_customer_id = parent.customer_id
                reassigned += 1

            print(f"{'APPLY' if APPLY else 'DRY-RUN'} — org {org_id}")
            print("== GROUP entities ==")
            for label, pname, inc, exc in GROUP:
                kids = [
                    c for c in _match(custs, inc, exc) if (c.legal_name or "") != pname
                ]
                if not kids:
                    continue
                parent = by_name.get(pname)
                if parent is None:
                    print(f"  {label}: CREATE parent {pname!r}")
                    if APPLY:
                        # Parent inherits the AR control account from its
                        # children (same family/org -> same control account);
                        # it is NOT NULL on the customer table.
                        ar_acct = next(
                            (
                                k.ar_control_account_id
                                for k in kids
                                if k.ar_control_account_id is not None
                            ),
                            None,
                        )
                        parent = CustomerService.create_customer(
                            db,
                            org_id,
                            CustomerInput(
                                customer_type=CustomerType.COMPANY,
                                customer_name=pname,
                                default_receivable_account_id=ar_acct,
                            ),
                        )
                        by_name[pname] = parent
                    created += 1
                else:
                    print(f"  {label}: parent {pname!r} EXISTS")
                if parent is not None:  # in dry-run with no parent, just count
                    for child in kids:
                        set_parent(child, parent)
                else:
                    for child in kids:
                        if (child.legal_name or "") != pname:
                            print(f"      reassign {child.legal_name!r} -> {pname!r}")
                            reassigned += 1

            print("== BASE entities (canonical record = parent) ==")
            for label, inc in BASE:
                m = _match(custs, inc, None)
                if not m:
                    continue
                parent = sorted(
                    m,
                    key=lambda c: (
                        1 if re.search(r"\d\s*$", c.legal_name or "") else 0,
                        len(c.legal_name or ""),
                    ),
                )[0]
                print(f"  {label}: parent = {parent.legal_name!r}")
                for child in m:
                    set_parent(child, parent)

            if APPLY:
                db.commit()
            print(
                f"== {'COMMITTED' if APPLY else 'DRY-RUN'}: "
                f"parents created={created}, children reassigned={reassigned} =="
            )


if __name__ == "__main__":
    main()
