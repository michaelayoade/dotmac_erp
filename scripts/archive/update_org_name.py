#!/usr/bin/env python3
"""ARCHIVED — one-off rename, already applied.

Renamed every organization to the hardcoded string "Dotmac Technologies".
That was correct exactly once, on a single-tenant database. It is kept for
provenance only: re-running it today would rename EVERY organization in the
fleet, which is why it is here and not in scripts/.

A real rename tool would take --org-id and --name and scope its session.
Write that instead of reviving this.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization


def main():
    db = SessionLocal()
    try:
        orgs = db.query(Organization).all()
        if not orgs:
            print("No organizations found")
            return

        for org in orgs:
            old_name = org.legal_name
            org.legal_name = "Dotmac Technologies"
            print(f"Updated: {old_name} -> Dotmac Technologies")

        db.commit()
        print("\nDone!")
    finally:
        db.close()


if __name__ == "__main__":
    main()
