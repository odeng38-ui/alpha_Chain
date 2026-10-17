from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import ScoreSnapshot, Security

TargetPolicy = Literal["active_common", "all_common"]


@dataclass(frozen=True)
class ScoreBatchVerification:
    as_of_date: str
    horizon: str
    version: str
    target_policy: TargetPolicy
    expected_count: int
    scored_count: int
    missing_count: int
    duplicate_group_count: int
    missing_security_ids: list[int]
    missing_offset: int
    missing_limit: int | None
    has_more_missing: bool
    status: Literal["COMPLETE", "PARTIAL", "EMPTY"]

    def to_dict(self) -> dict:
        return asdict(self)


def _eligible_query(db: Session, target_policy: TargetPolicy):
    query = db.query(Security.id).filter(Security.security_type == "COMMON")
    if target_policy == "active_common":
        query = query.filter(Security.effective_to.is_(None))
    elif target_policy != "all_common":
        raise ValueError(f"unsupported target policy: {target_policy}")
    return query


def verify_score_batch(
    db: Session,
    *,
    as_of_date: date,
    horizon: str,
    version: str,
    target_policy: TargetPolicy = "active_common",
    missing_offset: int = 0,
    missing_limit: int | None = None,
) -> ScoreBatchVerification:
    """Read stored score coverage without creating or recalculating scores."""
    if missing_offset < 0:
        raise ValueError("missing_offset must be at least 0")
    if missing_limit is not None and missing_limit < 1:
        raise ValueError("missing_limit must be at least 1")

    eligible = _eligible_query(db, target_policy).subquery()
    scored = db.query(ScoreSnapshot.security_id).filter(
        ScoreSnapshot.as_of_date == as_of_date,
        ScoreSnapshot.horizon == horizon,
        ScoreSnapshot.version == version,
    ).distinct().subquery()

    expected_count = db.query(func.count()).select_from(eligible).scalar() or 0
    scored_count = db.query(func.count()).select_from(eligible).join(
        scored, scored.c.security_id == eligible.c.id,
    ).scalar() or 0
    missing_query = db.query(eligible.c.id).outerjoin(
        scored, scored.c.security_id == eligible.c.id,
    ).filter(scored.c.security_id.is_(None))
    missing_count = missing_query.count()
    missing_page = missing_query.order_by(eligible.c.id).offset(missing_offset)
    if missing_limit is not None:
        missing_page = missing_page.limit(missing_limit)
    missing_security_ids = [row[0] for row in missing_page.all()]
    duplicate_group_count = db.query(func.count()).select_from(
        db.query(ScoreSnapshot.security_id).filter(
            ScoreSnapshot.as_of_date == as_of_date,
            ScoreSnapshot.horizon == horizon,
            ScoreSnapshot.version == version,
        ).group_by(ScoreSnapshot.security_id).having(func.count(ScoreSnapshot.id) > 1).subquery()
    ).scalar() or 0

    if scored_count == 0:
        status = "EMPTY"
    elif scored_count == expected_count and duplicate_group_count == 0:
        status = "COMPLETE"
    else:
        status = "PARTIAL"

    return ScoreBatchVerification(
        as_of_date=as_of_date.isoformat(),
        horizon=horizon,
        version=version,
        target_policy=target_policy,
        expected_count=expected_count,
        scored_count=scored_count,
        missing_count=missing_count,
        duplicate_group_count=duplicate_group_count,
        missing_security_ids=missing_security_ids,
        missing_offset=missing_offset,
        missing_limit=missing_limit,
        has_more_missing=missing_offset + len(missing_security_ids) < missing_count,
        status=status,
    )
