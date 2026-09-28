from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import (
    Company,
    DailyPrice,
    DisclosureEvent,
    Filing,
    FinancialFact,
    ScoreSnapshot,
    Security,
)
from app.services.fred_service import FredCollectionService

router = APIRouter(prefix="/ui", tags=["Web MVP"])


@router.get("/dashboard")
def dashboard(as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    cutoff = datetime.combine(as_of, time.max)
    latest_score_date = db.query(func.max(ScoreSnapshot.as_of_date)).filter(
        ScoreSnapshot.as_of_date <= as_of,
    ).scalar()
    industries = []
    if latest_score_date:
        industries = db.query(
            Company.industry_id, func.avg(ScoreSnapshot.total), func.count(ScoreSnapshot.id),
        ).join(Security, Security.company_id == Company.id).join(
            ScoreSnapshot, ScoreSnapshot.security_id == Security.id,
        ).filter(
            ScoreSnapshot.as_of_date == latest_score_date,
            Company.industry_id.isnot(None),
        ).group_by(Company.industry_id).order_by(func.avg(ScoreSnapshot.total).desc()).limit(5).all()
    events = db.query(DisclosureEvent, Company.name, Filing.title).join(
        Company, Company.id == DisclosureEvent.company_id,
    ).join(Filing, Filing.rcept_no == DisclosureEvent.filing_id).filter(
        DisclosureEvent.event_date <= cutoff,
    ).order_by(DisclosureEvent.event_date.desc()).limit(8).all()
    latest_price = db.query(func.max(DailyPrice.trade_date)).scalar()
    latest_filing = db.query(func.max(Filing.available_at)).scalar()
    return {
        "as_of": as_of, "generated_at": datetime.utcnow(),
        "regime": FredCollectionService.regime_dataset(db, as_of),
        "strong_industries": [{"industry_id": row[0], "average_score": round(float(row[1]), 2),
                               "company_count": row[2]} for row in industries],
        "new_events": [{"id": event.id, "company_id": event.company_id,
                        "company_name": company_name, "event_type": event.event_type,
                        "sentiment": event.sentiment, "title": title,
                        "event_date": event.event_date} for event, company_name, title in events],
        "freshness": {"prices": latest_price, "filings": latest_filing,
                      "scores": latest_score_date, "macro": cutoff},
    }


@router.get("/companies/{company_id}")
def company_detail(company_id: int, as_of: date = Query(default_factory=date.today),
                   db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    cutoff = datetime.combine(as_of, time.max)
    security = db.query(Security).filter(
        Security.company_id == company_id,
        Security.security_type == "COMMON",
    ).order_by(Security.effective_to.is_(None).desc(), Security.id).first()
    prices = []
    score = None
    if security:
        prices = db.query(DailyPrice).filter(
            DailyPrice.security_id == security.id,
            DailyPrice.trade_date <= as_of,
            DailyPrice.trade_date >= as_of - timedelta(days=180),
        ).order_by(DailyPrice.trade_date).all()
        score = db.query(ScoreSnapshot).filter(
            ScoreSnapshot.security_id == security.id,
            ScoreSnapshot.as_of_date <= as_of,
        ).order_by(ScoreSnapshot.as_of_date.desc()).first()
    filings = db.query(Filing).filter(
        Filing.company_id == company_id, Filing.available_at <= cutoff,
    ).order_by(Filing.available_at.desc()).limit(10).all()
    facts = db.query(FinancialFact).filter(
        FinancialFact.company_id == company_id, FinancialFact.filed_at <= cutoff,
    ).order_by(FinancialFact.filed_at.desc(), FinancialFact.id).limit(12).all()
    return {
        "as_of": as_of, "generated_at": datetime.utcnow(),
        "company": {"id": company.id, "name": company.name, "status": company.status,
                    "industry_id": company.industry_id, "corp_code": company.corp_code},
        "security": {"id": security.id, "ticker": security.ticker, "market": security.market}
                    if security else None,
        "prices": [{"date": row.trade_date, "close": float(row.adjusted_close or row.close)
                    if (row.adjusted_close is not None or row.close is not None) else None,
                    "volume": row.volume} for row in prices],
        "score": {"as_of_date": score.as_of_date, "total": score.total,
                  "confidence": score.confidence, "components": score.component,
                  "explanations": score.explanations} if score else None,
        "financials": [{"account_name": row.account_name, "value": float(row.value)
                        if row.value is not None else None, "unit": row.unit,
                        "period": row.period, "filed_at": row.filed_at} for row in facts],
        "filings": [{"rcept_no": row.rcept_no, "title": row.title,
                     "available_at": row.available_at,
                     "source_url": row.raw_ref} for row in filings],
        "freshness": {"price": prices[-1].trade_date if prices else None,
                      "score": score.as_of_date if score else None,
                      "filing": filings[0].available_at if filings else None},
    }
