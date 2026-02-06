"""
Search API endpoints for auto-suggestions.

Provides unified search endpoint for auto-complete functionality across entity types.
"""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import get_db_session
from app.services.finance.platform.search_suggestions import search_suggestions_service


router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(require_tenant_auth)],
)


@router.get("/suggestions")
def get_suggestions(
    entity_type: str = Query(
        ...,
        description="Entity type to search (customers, suppliers, accounts, items, tax_codes, bank_accounts)",
    ),
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=10, ge=1, le=20, description="Maximum results"),
    account_type: Optional[str] = Query(
        default=None, description="Filter accounts by type"
    ),
    category_id: Optional[str] = Query(
        default=None, description="Filter items by category"
    ),
    tax_type: Optional[str] = Query(
        default=None, description="Filter tax codes by type"
    ),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Get search suggestions for auto-complete.

    Supported entity types:
    - customers: Search by customer name, code, or email
    - suppliers: Search by supplier name, code, or email
    - accounts: Search by account code or name
    - items: Search by item code or name
    - tax_codes: Search by tax code or name
    - bank_accounts: Search by account name, number, or bank name

    Returns suggestions with id, label, subtitle, and category.
    """
    # Build filters from query params
    filters = {}
    if account_type:
        filters["account_type"] = account_type
    if category_id:
        filters["category_id"] = category_id
    if tax_type:
        filters["tax_type"] = tax_type

    result = search_suggestions_service.search(
        db=db,
        org_id=org_id,
        entity_type=entity_type,
        query=q,
        limit=limit,
        filters=filters,
    )

    return result.to_dict()


@router.get("/global")
def global_search(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(
        default=5, ge=1, le=10, description="Max results per entity type"
    ),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Global search across multiple entity types.

    Searches customers, suppliers, accounts, and items simultaneously.
    Returns grouped results by entity type.
    """
    entity_types = ["customers", "suppliers", "accounts", "items"]
    results = {}

    for entity_type in entity_types:
        result = search_suggestions_service.search(
            db=db,
            org_id=org_id,
            entity_type=entity_type,
            query=q,
            limit=limit,
        )
        if result.suggestions:
            results[entity_type] = {
                "suggestions": [s.to_dict() for s in result.suggestions],
                "has_more": result.has_more,
            }

    total_count = 0
    for entity_result in results.values():
        suggestions = entity_result.get("suggestions")
        if isinstance(suggestions, list):
            total_count += len(suggestions)

    return {
        "query": q,
        "results": results,
        "total_count": total_count,
    }
