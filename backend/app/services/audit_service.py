from typing import Any

from sqlalchemy.orm import Session

from app.logging_config import request_id_context
from app.models.schema import AuditLog


def record_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | int | None,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        before_state=before_state,
        after_state=after_state,
        request_id=request_id_context.get(),
    )
    db.add(row)
