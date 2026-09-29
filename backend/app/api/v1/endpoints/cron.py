import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services.price_service import incremental_due_batch_update

router = APIRouter(prefix="/cron", tags=["Cron"])


@router.get("/prices")
def collect_due_prices(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Run one bounded daily-price batch from Vercel Cron."""
    if not settings.CRON_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRON_SECRET is not configured",
        )

    expected = f"Bearer {settings.CRON_SECRET}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron authorization",
        )

    return incremental_due_batch_update(
        db,
        batch_size=max(1, min(settings.CRON_BATCH_SIZE, 100)),
    )
