#!/usr/bin/env python3
"""Update organization name to Dotmac Technologies."""

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
