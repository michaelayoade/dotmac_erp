#!/usr/bin/env python3
"""Merge duplicate active HR departments by organization and name.

Default mode is a dry run. Pass ``--apply`` to repoint foreign-key references and
delete duplicate department rows.
"""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class DepartmentRow:
    department_id: UUID
    organization_id: UUID
    department_code: str
    department_name: str
    created_at: datetime | None


@dataclass(frozen=True)
class ForeignKeyRef:
    schema: str
    table: str
    column: str


def normalize_name(value: str) -> str:
    text_value = str(value or "").strip().casefold()
    text_value = text_value.replace("&", " and ")
    text_value = re.sub(r"[,\-\.]+", " ", text_value)
    text_value = re.sub(r"\band\b", " and ", text_value)
    return " ".join(text_value.split())


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def fq_table(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    # Normalize a plain postgresql:// DSN onto the psycopg3 driver.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def load_department_refs(conn: Connection) -> list[ForeignKeyRef]:
    rows = conn.execute(
        text(
            """
            select tc.table_schema, tc.table_name, kcu.column_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_name = kcu.constraint_name
             and tc.table_schema = kcu.table_schema
            join information_schema.constraint_column_usage ccu
              on ccu.constraint_name = tc.constraint_name
             and ccu.table_schema = tc.table_schema
            where tc.constraint_type = 'FOREIGN KEY'
              and ccu.table_schema = 'hr'
              and ccu.table_name = 'department'
              and ccu.column_name = 'department_id'
            order by tc.table_schema, tc.table_name, kcu.column_name
            """
        )
    ).all()
    return [
        ForeignKeyRef(row.table_schema, row.table_name, row.column_name) for row in rows
    ]


def load_active_departments(
    conn: Connection, organization_id: str | None
) -> list[DepartmentRow]:
    where = "where is_active = true"
    params: dict[str, str] = {}
    if organization_id:
        where += " and organization_id = :organization_id"
        params["organization_id"] = organization_id

    rows = conn.execute(
        text(
            f"""
            select department_id, organization_id, department_code, department_name, created_at
            from hr.department
            {where}
            order by organization_id, department_name, department_code, created_at, department_id
            """
        ),
        params,
    ).all()
    return [
        DepartmentRow(
            department_id=row.department_id,
            organization_id=row.organization_id,
            department_code=row.department_code,
            department_name=row.department_name,
            created_at=row.created_at,
        )
        for row in rows
    ]


def reference_counts(
    conn: Connection, refs: list[ForeignKeyRef], department_ids: list[UUID]
) -> dict[UUID, int]:
    counts = {department_id: 0 for department_id in department_ids}
    if not department_ids:
        return counts

    for ref in refs:
        query = text(
            f"""
                select {quote_ident(ref.column)} as department_id, count(*) as count
                from {fq_table(ref.schema, ref.table)}
                where {quote_ident(ref.column)} in :department_ids
                group by {quote_ident(ref.column)}
                """
        ).bindparams(bindparam("department_ids", expanding=True))
        rows = conn.execute(
            query,
            {"department_ids": department_ids},
        ).all()
        for row in rows:
            counts[row.department_id] += int(row.count)
    return counts


def choose_keeper(
    departments: list[DepartmentRow], counts: dict[UUID, int]
) -> DepartmentRow:
    return sorted(
        departments,
        key=lambda department: (
            -counts[department.department_id],
            department.created_at or datetime.max,
            department.department_code,
            str(department.department_id),
        ),
    )[0]


def update_references(
    conn: Connection,
    refs: list[ForeignKeyRef],
    duplicate_id: UUID,
    keeper_id: UUID,
) -> dict[str, int]:
    updates: dict[str, int] = {}
    for ref in refs:
        if (
            ref.schema == "hr"
            and ref.table == "department"
            and ref.column == "parent_department_id"
        ):
            result = conn.execute(
                text(
                    """
                    update hr.department
                       set parent_department_id = null
                     where department_id = :keeper_id
                       and parent_department_id = :duplicate_id
                    """
                ),
                {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
            )
            cleared = int(result.rowcount or 0)
            if cleared:
                updates["hr.department.parent_department_id:self"] = cleared

            result = conn.execute(
                text(
                    """
                    update hr.department
                       set parent_department_id = :keeper_id
                     where parent_department_id = :duplicate_id
                       and department_id != :keeper_id
                    """
                ),
                {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
            )
            updated = int(result.rowcount or 0)
            if updated:
                updates[f"{ref.schema}.{ref.table}.{ref.column}"] = updated
            continue

        result = conn.execute(
            text(
                f"""
                update {fq_table(ref.schema, ref.table)}
                   set {quote_ident(ref.column)} = :keeper_id
                 where {quote_ident(ref.column)} = :duplicate_id
                """
            ),
            {"keeper_id": keeper_id, "duplicate_id": duplicate_id},
        )
        updated = int(result.rowcount or 0)
        if updated:
            updates[f"{ref.schema}.{ref.table}.{ref.column}"] = updated
    return updates


def merge_duplicates(
    conn: Connection,
    *,
    apply: bool,
    organization_id: str | None,
    only_names: set[str] | None,
) -> None:
    refs = load_department_refs(conn)
    departments = load_active_departments(conn, organization_id)

    groups: dict[tuple[UUID, str], list[DepartmentRow]] = defaultdict(list)
    for department in departments:
        normalized_name = normalize_name(department.department_name)
        if only_names and normalized_name not in only_names:
            continue
        groups[(department.organization_id, normalized_name)].append(department)

    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    if not duplicate_groups:
        print("No duplicate active departments found.")
        return

    print(f"Found {len(duplicate_groups)} duplicate department group(s).")
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")

    for group in duplicate_groups:
        ids = [department.department_id for department in group]
        counts = reference_counts(conn, refs, ids)
        keeper = choose_keeper(group, counts)
        print()
        print(
            f"{keeper.organization_id} / {keeper.department_name}: "
            f"keeping {keeper.department_code} {keeper.department_id} "
            f"({counts[keeper.department_id]} reference(s))"
        )

        for duplicate in group:
            if duplicate.department_id == keeper.department_id:
                continue
            print(
                f"  merge {duplicate.department_code} {duplicate.department_id} "
                f"({counts[duplicate.department_id]} reference(s))"
            )
            if not apply:
                continue

            updates = update_references(
                conn,
                refs,
                duplicate.department_id,
                keeper.department_id,
            )
            for target, count in sorted(updates.items()):
                print(f"    updated {target}: {count}")

            result = conn.execute(
                text(
                    """
                    delete from hr.department
                     where department_id = :department_id
                    """
                ),
                {"department_id": duplicate.department_id},
            )
            print(f"    deleted department row: {int(result.rowcount or 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge duplicate active HR departments by normalized name."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates and deletes. Without this flag, only prints the plan.",
    )
    parser.add_argument(
        "--organization-id",
        help="Limit merge to one organization UUID.",
    )
    parser.add_argument(
        "--names",
        help="Comma-separated department names to merge. Defaults to all duplicates.",
    )
    args = parser.parse_args()

    only_names = None
    if args.names:
        only_names = {normalize_name(name) for name in args.names.split(",") if name}

    engine = create_engine(database_url())
    with engine.begin() as conn:
        merge_duplicates(
            conn,
            apply=args.apply,
            organization_id=args.organization_id,
            only_names=only_names,
        )


if __name__ == "__main__":
    main()
