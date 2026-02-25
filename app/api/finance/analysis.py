"""
Analysis (pivot-like) API endpoints.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.services.finance.rpt.analysis_cube import AnalysisCubeService

router = APIRouter(
    prefix="/analysis",
    tags=["analysis"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class AnalysisCubeRead(BaseModel):
    code: str
    name: str
    description: str | None = None
    dimensions: list[dict] = Field(default_factory=list)
    measures: list[dict] = Field(default_factory=list)
    default_rows: list[str] | None = None
    default_columns: list[str] | None = None
    default_measures: list[str] | None = None


class AnalysisQueryRequest(BaseModel):
    row_dimensions: list[str] = Field(min_length=1, max_length=10)
    measures: list[str] = Field(min_length=1, max_length=10)
    filters: list[dict] | None = None
    limit: int = Field(default=1000, ge=1, le=5000)


class AnalysisQueryResponse(BaseModel):
    cube_code: str
    columns: list[str]
    rows: list[dict]


@router.get("/cubes", response_model=list[AnalysisCubeRead])
def list_analysis_cubes(
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    service = AnalysisCubeService(db)
    cubes = service.list_cubes(org_id)
    return [
        AnalysisCubeRead(
            code=cube.code,
            name=cube.name,
            description=cube.description,
            dimensions=cube.dimensions or [],
            measures=cube.measures or [],
            default_rows=cube.default_rows,
            default_columns=cube.default_columns,
            default_measures=cube.default_measures,
        )
        for cube in cubes
    ]


@router.post("/{cube_code}/query", response_model=AnalysisQueryResponse)
def query_analysis_cube(
    cube_code: str,
    payload: AnalysisQueryRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    service = AnalysisCubeService(db)
    try:
        result = service.query_cube(
            org_id,
            cube_code,
            row_dimensions=payload.row_dimensions,
            measures=payload.measures,
            filters=payload.filters,
            limit=payload.limit,
        )
        return AnalysisQueryResponse(
            cube_code=result.cube_code,
            columns=result.columns,
            rows=result.rows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
