from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_event(db: Session, *, project_id: str, event_type: str, object_type: str,
                 object_id: str, title: str, detail: str = "", amount_delta_cents: int = 0,
                 actor: str = "项目所有者") -> AuditEvent:
    event = AuditEvent(
        project_id=project_id,
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        title=title,
        detail=detail,
        amount_delta_cents=amount_delta_cents,
        actor=actor,
    )
    db.add(event)
    return event
