from ..extensions import db
from ..models import AuditLog
from .tracking import current_user_id


def audit(action: str, entity: str, entity_id: int | None = None, detail: str | None = None,
          commit: bool = True):
    db.session.add(AuditLog(user_id=current_user_id(), action=action, entity=entity,
                            entity_id=entity_id, detail=(detail or None) and detail[:2000]))
    if commit:
        db.session.commit()
