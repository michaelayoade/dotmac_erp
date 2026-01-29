#!/usr/bin/env python3
"""
Bulk update hr.employee.assigned_location_id from a CSV mapping.

CSV headers (case-insensitive):
  - Full Name
  - Branch

Branch is matched to core_org.location by location_name or location_code
(case-insensitive, whitespace-normalized) within the employee's organization.
"""

from __future__ import annotations

import argparse
import csv
from typing import Dict, Iterable, Tuple

from sqlalchemy import create_engine, text

from app.config import settings


def _normalize(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in value)
    return " ".join(cleaned.strip().lower().split())


def _load_mapping(csv_path: str) -> Dict[str, str]:
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("CSV has no headers.")

        header_map = {h.strip().lower(): h for h in reader.fieldnames}
        name_key = header_map.get("full name")
        branch_key = header_map.get("branch")
        if not name_key or not branch_key:
            raise SystemExit("CSV must include headers: 'Full Name' and 'Branch'.")

        mapping: Dict[str, str] = {}
        for row in reader:
            raw_name = (row.get(name_key) or "").strip()
            raw_branch = (row.get(branch_key) or "").strip()
            if not raw_name or not raw_branch:
                continue
            norm_name = _normalize(raw_name)
            norm_branch = _normalize(raw_branch)
            existing = mapping.get(norm_name)
            if existing and existing != norm_branch:
                raise SystemExit(
                    f"Conflicting branches for '{raw_name}': '{existing}' vs '{raw_branch}'."
                )
            mapping[norm_name] = norm_branch

        if not mapping:
            raise SystemExit("No valid employee/branch mappings found in CSV.")

        return mapping


def _insert_mapping(conn, rows: Iterable[Tuple[str, str]]) -> None:
    conn.execute(
        text("CREATE TEMP TABLE tmp_employee_branches (full_name text, branch text)")
    )
    conn.execute(
        text(
            "INSERT INTO tmp_employee_branches (full_name, branch) VALUES (:full_name, :branch)"
        ),
        [{"full_name": name, "branch": branch} for name, branch in rows],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk update employee assigned branch from CSV."
    )
    parser.add_argument("--csv", required=True, help="Path to CSV mapping file")
    parser.add_argument(
        "--fuzzy",
        action="store_true",
        help="Allow fuzzy name matching (>=2 shared name tokens)",
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

        match_cte = r"""
            WITH tmp AS (
                SELECT
                    t.full_name,
                    t.branch,
                    regexp_replace(regexp_replace(lower(trim(t.full_name)), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g') AS full_name_norm,
                    regexp_replace(regexp_replace(lower(trim(t.branch)), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g') AS branch_norm
                FROM tmp_employee_branches t
            ),
            person_names AS (
                SELECT
                    p.id AS person_id,
                    p.organization_id,
                    regexp_replace(
                        regexp_replace(lower(trim(COALESCE(NULLIF(p.display_name, ''), trim(p.first_name || ' ' || p.last_name)))), '[^a-z0-9]+', ' ', 'g'),
                        '\s+',
                        ' ',
                        'g'
                    ) AS full_name_norm
                FROM people p
            ),
            name_scores AS (
                SELECT
                    t.full_name,
                    t.branch,
                    t.branch_norm,
                    p.person_id,
                    p.organization_id,
                    CASE
                        WHEN t.full_name_norm = p.full_name_norm THEN 100
                        ELSE count(DISTINCT t_tok.tok)
                    END AS score,
                    (t.full_name_norm = p.full_name_norm) AS exact_match
                FROM tmp t
                JOIN person_names p ON true
                JOIN LATERAL unnest(regexp_split_to_array(t.full_name_norm, ' ')) AS t_tok(tok) ON true
                JOIN LATERAL unnest(regexp_split_to_array(p.full_name_norm, ' ')) AS p_tok(tok)
                  ON t_tok.tok = p_tok.tok
                GROUP BY
                    t.full_name,
                    t.branch,
                    t.branch_norm,
                    p.person_id,
                    p.organization_id,
                    t.full_name_norm,
                    p.full_name_norm
                HAVING
                    t.full_name_norm = p.full_name_norm
                    OR (:fuzzy AND count(DISTINCT t_tok.tok) >= 2)
            ),
            best AS (
                SELECT
                    ns.*,
                    max(score) OVER (PARTITION BY full_name) AS max_score,
                    count(*) OVER (PARTITION BY full_name, score) AS score_ties
                FROM name_scores ns
            ),
            selected AS (
                SELECT *
                FROM best
                WHERE score = max_score
                  AND score_ties = 1
            ),
            locations AS (
                SELECT
                    l.location_id,
                    l.organization_id,
                    regexp_replace(regexp_replace(lower(trim(l.location_name)), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g') AS loc_name_norm,
                    regexp_replace(regexp_replace(lower(trim(l.location_code)), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g') AS loc_code_norm
                FROM core_org.location l
            )
        """

        candidates = conn.execute(
            text(
                match_cte
                + r"""
                SELECT count(*)
                FROM hr.employee e
                JOIN selected s
                  ON s.person_id = e.person_id
                 AND s.organization_id = e.organization_id
                JOIN locations l
                  ON l.organization_id = s.organization_id
                 AND (l.loc_name_norm = s.branch_norm OR l.loc_code_norm = s.branch_norm)
                WHERE e.assigned_location_id IS DISTINCT FROM l.location_id
                """
            ),
            {"fuzzy": args.fuzzy},
        ).scalar_one()

        if args.dry_run:
            print(f"Would update {candidates} employee record(s).")
        else:
            result = conn.execute(
                text(
                    match_cte
                    + r"""
                    UPDATE hr.employee e
                    SET assigned_location_id = l.location_id
                    FROM selected s
                    JOIN locations l
                      ON l.organization_id = s.organization_id
                     AND (l.loc_name_norm = s.branch_norm OR l.loc_code_norm = s.branch_norm)
                    WHERE e.person_id = s.person_id
                      AND e.organization_id = s.organization_id
                      AND e.assigned_location_id IS DISTINCT FROM l.location_id
                    """
                ),
                {"fuzzy": args.fuzzy},
            )
            print(f"Updated {result.rowcount} employee record(s).")

        unmatched_names = conn.execute(
            text(
                match_cte
                + r"""
                SELECT DISTINCT t.full_name
                FROM tmp t
                LEFT JOIN name_scores ns ON ns.full_name = t.full_name
                WHERE ns.full_name IS NULL
                ORDER BY t.full_name
                """
            ),
            {"fuzzy": args.fuzzy},
        ).scalars().all()

        ambiguous_names = conn.execute(
            text(
                match_cte
                + r"""
                SELECT DISTINCT b.full_name
                FROM best b
                WHERE b.score = b.max_score
                  AND b.score_ties > 1
                ORDER BY b.full_name
                """
            ),
            {"fuzzy": args.fuzzy},
        ).scalars().all()

        unmatched_branches = conn.execute(
            text(
                match_cte
                + r"""
                SELECT DISTINCT s.branch
                FROM selected s
                LEFT JOIN locations l
                  ON l.organization_id = s.organization_id
                 AND (l.loc_name_norm = s.branch_norm OR l.loc_code_norm = s.branch_norm)
                WHERE l.location_id IS NULL
                ORDER BY s.branch
                """
            ),
            {"fuzzy": args.fuzzy},
        ).scalars().all()

        if unmatched_names:
            print("\nUnmatched employee names:")
            for name in unmatched_names:
                print(f"- {name}")
        if ambiguous_names:
            print("\nAmbiguous employee names (multiple matches):")
            for name in ambiguous_names:
                print(f"- {name}")
        if unmatched_branches:
            print("\nUnmatched branches (no location match in org):")
            for branch in unmatched_branches:
                print(f"- {branch}")


if __name__ == "__main__":
    main()
