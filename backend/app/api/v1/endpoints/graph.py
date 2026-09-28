from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.graph_service import GraphSearchService

router = APIRouter(prefix="/companies", tags=["Company Graph"])
service = GraphSearchService()


@router.get("/{company_id}/graph")
def company_graph(
    company_id: int,
    direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
    hops: int = Query(3, ge=1, le=3),
    relationship_types: Optional[str] = Query(None, description="Comma-separated relationship types"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    as_of: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    types = {item.strip() for item in relationship_types.split(",") if item.strip()} if relationship_types else None
    try:
        return service.search(
            db, company_id, direction, hops, types, min_confidence,
            as_of, limit, offset,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
