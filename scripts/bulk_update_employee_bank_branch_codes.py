#!/usr/bin/env python3
"""
Bulk update hr.employee.bank_branch_code from a CSV mapping.

CSV headers (case-insensitive):
  - Bank Name
  - Bank Sort Code (or Sort Code / Branch Code)
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable

from sqlalchemy import create_engine, text

from app.config import settings


def _normalize_bank_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _load_mapping(csv_path: str) -> dict[str, str]:
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("CSV has no headers.")

        header_map = {h.strip().lower(): h for h in reader.fieldnames}
        name_key = header_map.get("bank name")
        code_key = (
            header_map.get("bank sort code")
            or header_map.get("sort code")
            or header_map.get("branch code")
        )
        if not name_key or not code_key:
            raise SystemExit(
                "CSV must include headers: 'Bank Name' and 'Bank Sort Code' (or 'Sort Code'/'Branch Code')."
            )

        mapping: dict[str, str] = {}
        for row in reader:
            raw_name = (row.get(name_key) or "").strip()
            raw_code = (row.get(code_key) or "").strip()
            if not raw_name or not raw_code:
                continue
            norm = _normalize_bank_name(raw_name)
            existing = mapping.get(norm)
            if existing and existing != raw_code:
                raise SystemExit(
                    f"Conflicting codes for bank '{raw_name}': '{existing}' vs '{raw_code}'."
                )
            mapping[norm] = raw_code
        if not mapping:
            raise SystemExit("No valid bank mappings found in CSV.")

        # Alias normalization for common bank name variants.
        aliases = {
            "ACCESS BANK NIGERIA PLC": "Access Bank",
            "Access Diamond": "Access Bank (Diamond)",
            "Access Diamond Bank": "Access Bank (Diamond)",
            "Ecobank": "Ecobank Nigeria",
            "FCMB": "First City Monument Bank",
            "Fcmb": "First City Monument Bank",
            "Fidelity": "Fidelity Bank",
            "FIDELITY BANK PLC": "Fidelity Bank",
            "First bank": "First Bank of Nigeria",
            "First Bank": "First Bank of Nigeria",
            "FIRST BANK OF NIGERIA PLC": "First Bank of Nigeria",
            "GT": "Guaranty Trust Bank",
            "GTB": "Guaranty Trust Bank",
            "GT Bank": "Guaranty Trust Bank",
            "GTBank": "Guaranty Trust Bank",
            "GTBANK": "Guaranty Trust Bank",
            "GTB BANK": "Guaranty Trust Bank",
            "Guarantee Trust bank": "Guaranty Trust Bank",
            "Guarantee Trust Bank": "Guaranty Trust Bank",
            "Guarant Trust Bank": "Guaranty Trust Bank",
            "Guranty Trust Bank": "Guaranty Trust Bank",
            "Jaiz": "Jaiz Bank",
            "Kaystone Bank": "Keystone Bank",
            "Kuda": "Kuda Bank",
            "Polaris": "Polaris Bank",
            "Stanbic IBTC": "Stanbic IBTC Bank",
            "Stanbic-IBTC": "Stanbic IBTC Bank",
            "Standard Chartered": "Standard Chartered Bank",
            "UBA": "United Bank For Africa",
            "UBA Bank": "United Bank For Africa",
            "Union": "Union Bank of Nigeria",
            "Union Bank": "Union Bank of Nigeria",
            "UNION BANK": "Union Bank of Nigeria",
            "United Bank of Africa": "United Bank For Africa",
            "United Bank of Africa (UBA)": "United Bank For Africa",
            "Zenith Bank Plc": "Zenith Bank",
        }

        for alias, canonical in aliases.items():
            alias_norm = _normalize_bank_name(alias)
            canonical_norm = _normalize_bank_name(canonical)
            canonical_code = mapping.get(canonical_norm)
            if canonical_code:
                mapping[alias_norm] = canonical_code

        return mapping


def _insert_mapping(conn, rows: Iterable[tuple[str, str]]) -> None:
    conn.execute(
        text("CREATE TEMP TABLE tmp_bank_codes (bank_name text, sort_code text)")
    )
    conn.execute(
        text(
            "INSERT INTO tmp_bank_codes (bank_name, sort_code) VALUES (:bank_name, :sort_code)"
        ),
        [{"bank_name": name, "sort_code": code} for name, code in rows],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk update employee bank branch codes from CSV."
    )
    parser.add_argument("--csv", required=True, help="Path to CSV mapping file")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing bank branch codes when they differ from CSV",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without updating data",
    )
    args = parser.parse_args()

    mapping = _load_mapping(args.csv)
    rows = sorted(mapping.items())

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        _insert_mapping(conn, rows)

        candidates = conn.execute(
            text(
                """
                SELECT count(*)
                FROM hr.employee e
                WHERE e.bank_name IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM tmp_bank_codes t
                      WHERE lower(trim(e.bank_name)) = lower(trim(t.bank_name))
                        AND (
                            e.bank_branch_code IS NULL
                            OR e.bank_branch_code <> t.sort_code
                        )
                  )
                """
            )
        ).scalar_one()

        if args.dry_run:
            print(f"Would update {candidates} employee record(s).")
            return

        result = conn.execute(
            text(
                """
                UPDATE hr.employee e
                SET bank_branch_code = t.sort_code
                FROM tmp_bank_codes t
                WHERE e.bank_name IS NOT NULL
                  AND lower(trim(e.bank_name)) = lower(trim(t.bank_name))
                  AND (
                      e.bank_branch_code IS NULL
                      OR (e.bank_branch_code <> t.sort_code AND :overwrite)
                  )
                """
            ),
            {"overwrite": args.overwrite},
        )

        unmatched = (
            conn.execute(
                text(
                    """
                SELECT DISTINCT e.bank_name
                FROM hr.employee e
                WHERE e.bank_branch_code IS NULL
                  AND e.bank_name IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM tmp_bank_codes t
                      WHERE lower(trim(e.bank_name)) = lower(trim(t.bank_name))
                  )
                ORDER BY e.bank_name
                """
                )
            )
            .scalars()
            .all()
        )

        print(f"Updated {result.rowcount} employee record(s).")
        if unmatched:
            print("\nUnmatched bank names (still missing branch code):")
            for name in unmatched:
                print(f"- {name}")


if __name__ == "__main__":
    main()
