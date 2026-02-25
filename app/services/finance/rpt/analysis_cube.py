"""
Analysis cube querying service.
"""
# ruff: noqa: S608

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.finance.rpt.analysis_cube import AnalysisCube

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_VIEW_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
_AGG_RE = re.compile(r"^(sum|count|avg|min|max)$", re.IGNORECASE)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CubeQueryResult:
    cube_code: str
    columns: list[str]
    rows: list[dict]


class AnalysisCubeService:
    """Validate and execute ad-hoc cube queries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_cubes(self, organization_id: UUID) -> list[AnalysisCube]:
        from sqlalchemy import or_, select

        stmt = (
            select(AnalysisCube)
            .where(
                AnalysisCube.is_active.is_(True),
                or_(
                    AnalysisCube.organization_id.is_(None),
                    AnalysisCube.organization_id == organization_id,
                ),
            )
            .order_by(AnalysisCube.code.asc())
        )
        return list(self.db.scalars(stmt).all())

    def query_cube(
        self,
        organization_id: UUID,
        cube_code: str,
        *,
        row_dimensions: list[str],
        measures: list[str],
        filters: list[dict] | None = None,
        limit: int = 1000,
    ) -> CubeQueryResult:
        cube = self._get_cube(organization_id, cube_code)
        if not row_dimensions:
            raise ValueError("At least one row dimension is required.")
        if not measures:
            raise ValueError("At least one measure is required.")
        if limit < 1 or limit > 5000:
            raise ValueError("Limit must be between 1 and 5000.")

        dim_map = {d["field"]: d for d in (cube.dimensions or []) if d.get("field")}
        measure_map = {
            m["field"]: m for m in (cube.measures or []) if m.get("field")
        }

        select_parts: list[str] = []
        group_parts: list[str] = []
        params: dict[str, object] = {"org_id": str(organization_id), "limit": limit}

        for dim in row_dimensions:
            if dim not in dim_map:
                raise ValueError(f"Unknown dimension: {dim}")
            field = self._safe_ident(str(dim_map[dim]["field"]))
            select_parts.append(f"{field} AS {field}")
            group_parts.append(field)

        for measure in measures:
            if measure not in measure_map:
                raise ValueError(f"Unknown measure: {measure}")
            mdef = measure_map[measure]
            field = self._safe_ident(str(mdef["field"]))
            agg = str(mdef.get("agg", "sum")).lower()
            if not _AGG_RE.match(agg):
                raise ValueError(f"Unsupported aggregation: {agg}")
            alias = self._safe_ident(measure)
            select_parts.append(f"{agg}({field}) AS {alias}")

        where_parts = ["organization_id = :org_id::uuid"]
        filter_items = filters or []
        for idx, filter_item in enumerate(filter_items):
            field_name = str(filter_item.get("field") or "")
            value = filter_item.get("value")
            if field_name not in dim_map and field_name not in measure_map:
                raise ValueError(f"Unknown filter field: {field_name}")
            field = self._safe_ident(field_name)
            param_key = f"f_{idx}"
            where_parts.append(f"{field} = :{param_key}")
            params[param_key] = value

        from_clause = self._safe_view(cube.source_view)
        select_clause = ", ".join(select_parts)
        where_clause = " AND ".join(where_parts)
        group_clause = ", ".join(group_parts)

        sql = (
            f"SELECT {select_clause} "
            f"FROM {from_clause} "
            f"WHERE {where_clause} "
            f"GROUP BY {group_clause} "
            f"ORDER BY {group_clause} "
            f"LIMIT :limit"
        )
        rows = [
            dict(row) for row in self.db.execute(text(sql), params).mappings().all()
        ]
        columns = [*row_dimensions, *measures]
        return CubeQueryResult(cube_code=cube.code, columns=columns, rows=rows)

    def refresh_due_cubes(self, *, now: datetime | None = None) -> dict[str, int]:
        """Refresh due cube materialized views and update refresh timestamps."""
        from sqlalchemy import select

        current = now or datetime.now(UTC)
        cubes = list(
            self.db.scalars(
                select(AnalysisCube).where(AnalysisCube.is_active.is_(True))
            ).all()
        )
        results = {"checked": len(cubes), "refreshed": 0, "errors": 0}

        for cube in cubes:
            if not self._is_refresh_due(cube, current):
                continue
            try:
                self.refresh_cube(cube, now=current)
                results["refreshed"] += 1
            except Exception:
                logger.exception("Failed refreshing cube %s", cube.code)
                results["errors"] += 1

        return results

    def refresh_cube(self, cube: AnalysisCube, *, now: datetime | None = None) -> None:
        """Refresh one materialized view with concurrent fallback."""
        view_name = self._safe_view(cube.source_view)
        try:
            self.db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}"))
        except Exception:
            self.db.execute(text(f"REFRESH MATERIALIZED VIEW {view_name}"))
        cube.last_refreshed_at = now or datetime.now(UTC)

    def _get_cube(self, organization_id: UUID, cube_code: str) -> AnalysisCube:
        from sqlalchemy import or_, select

        stmt = select(AnalysisCube).where(
            AnalysisCube.code == cube_code,
            AnalysisCube.is_active.is_(True),
            or_(
                AnalysisCube.organization_id.is_(None),
                AnalysisCube.organization_id == organization_id,
            ),
        )
        cube = self.db.scalar(stmt)
        if cube is None:
            raise ValueError(f"Analysis cube not found: {cube_code}")
        return cube

    @staticmethod
    def _is_refresh_due(cube: AnalysisCube, current: datetime) -> bool:
        interval = max(int(cube.refresh_interval_minutes or 60), 1)
        if cube.last_refreshed_at is None:
            return True
        return cube.last_refreshed_at <= current - timedelta(minutes=interval)

    @staticmethod
    def _safe_ident(value: str) -> str:
        if not _IDENT_RE.match(value):
            raise ValueError(f"Invalid identifier: {value}")
        return value

    @staticmethod
    def _safe_view(value: str) -> str:
        if not _VIEW_RE.match(value):
            raise ValueError(f"Invalid source view: {value}")
        return value
