"""Selected stored-data bridge: resolve IDs, then reuse B4 verification unchanged.

The phase-d2 candidate/demo traversal is intentionally excluded. Neither fixture
payloads nor test-only approval/snapshot services enter this read-only path.
"""
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.schema import GlobalEvent
from app.services.event_first_query_service import EventFirstNotFound, EventFirstQueryService
from app.services.event_supply_chain_service import EventSupplyChainService


class EventFirstStoredDataProvider:
    mode = "stored_data"

    def __init__(self) -> None:
        self.supply_chain = EventSupplyChainService()

    def analyze(self, db: Session, *, event_id: str, as_of: datetime) -> dict[str, Any]:
        with db.no_autoflush:
            event = db.get(GlobalEvent, int(event_id)) if event_id.isdigit() else None
            if event is None:
                event = db.query(GlobalEvent).filter(GlobalEvent.external_id == event_id).first()
            if event is None:
                raise EventFirstNotFound("event not found")
            return self.supply_chain.analyze(db, event_id=event.id, as_of=as_of)


class EventFirstStoredDataBridge(EventFirstQueryService):
    """Reuse the main query service for stored price/score and B4 chain results."""

    def __init__(self) -> None:
        super().__init__(provider=EventFirstStoredDataProvider())
