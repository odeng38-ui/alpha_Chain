from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import AuditLog, IdentifierMap, Security
from app.security import require_admin

router = APIRouter(prefix="/admin", tags=["Admin & Manual Mapping"], dependencies=[Depends(require_admin)])


class IdentifierCreateRequest(BaseModel):
    company_id: Optional[int] = None
    security_id: Optional[int] = None
    source: str
    source_id_value: str
    is_primary: bool = True


class SecurityUpdateRequest(BaseModel):
    security_type: Optional[str] = None
    market: Optional[str] = None
    status: Optional[str] = None
    isin: Optional[str] = None


@router.post("/identifiers")
def create_or_update_identifier(
    req: IdentifierCreateRequest,
    db: Session = Depends(get_db)
):
    """
    관리자 수동 식별자 매핑 추가/업데이트 API
    """
    if not req.company_id and not req.security_id:
        raise HTTPException(status_code=400, detail="company_id 또는 security_id 중 하나 이상 필수입니다.")

    id_map = db.query(IdentifierMap).filter(
        IdentifierMap.source == req.source,
        IdentifierMap.source_id_value == req.source_id_value
    ).first()

    if not id_map:
        id_map = IdentifierMap(
            company_id=req.company_id,
            security_id=req.security_id,
            source=req.source,
            source_id_value=req.source_id_value,
            is_primary=req.is_primary
        )
        db.add(id_map)
    else:
        if req.company_id:
            id_map.company_id = req.company_id
        if req.security_id:
            id_map.security_id = req.security_id
        id_map.is_primary = req.is_primary

    db.commit()
    db.refresh(id_map)
    return {
        "message": "식별자 매핑이 정상 저장되었습니다.",
        "identifier_map": {
            "id": id_map.id,
            "company_id": id_map.company_id,
            "security_id": id_map.security_id,
            "source": id_map.source,
            "source_id_value": id_map.source_id_value,
            "is_primary": id_map.is_primary
        }
    }


@router.delete("/identifiers/{identifier_id}")
def delete_identifier(
    identifier_id: int,
    db: Session = Depends(get_db)
):
    """
    관리자 식별자 매핑 삭제 API
    """
    id_map = db.query(IdentifierMap).filter(IdentifierMap.id == identifier_id).first()
    if not id_map:
        raise HTTPException(status_code=404, detail="해당 식별자 매핑을 찾을 수 없습니다.")

    db.delete(id_map)
    db.commit()
    return {"message": "식별자 매핑이 성공적으로 삭제되었습니다."}


@router.put("/securities/{security_id}")
def update_security_status(
    security_id: int,
    req: SecurityUpdateRequest,
    db: Session = Depends(get_db)
):
    """
    증권 분류 및 상태 수동 수정 API
    """
    security = db.query(Security).filter(Security.id == security_id).first()
    if not security:
        raise HTTPException(status_code=404, detail="해당 증권을 찾을 수 없습니다.")

    if req.security_type:
        security.security_type = req.security_type
    if req.market:
        security.market = req.market
    if req.isin:
        security.isin = req.isin
    if req.status and security.company:
        security.company.status = req.status

    db.commit()
    db.refresh(security)

    return {
        "message": "증권 정보가 정상 수정되었습니다.",
        "security": {
            "id": security.id,
            "ticker": security.ticker,
            "security_type": security.security_type,
            "market": security.market,

            "isin": security.isin,
            "company_status": security.company.status if security.company else None
        }
    }


@router.get("/audit-logs")
def list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    resource_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    rows = query.order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc()
    ).offset(offset).limit(limit).all()
    return [{
        "id": row.id,
        "actor": row.actor,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "before_state": row.before_state,
        "after_state": row.after_state,
        "request_id": row.request_id,
        "created_at": row.created_at,
    } for row in rows]
