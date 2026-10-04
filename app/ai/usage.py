"""Record token usage of every model call, per user."""
import logging
import time
from contextlib import contextmanager

from ..extensions import db
from ..models import AIUsage
from ..services.tracking import current_user_id

log = logging.getLogger(__name__)


@contextmanager
def track(provider, kind: str):
    """Wrap one logical AI call; records tokens even when the call fails part-way."""
    started = time.time()
    ok = False
    try:
        yield
        ok = True
    finally:
        u = provider.take_usage()
        try:
            db.session.add(AIUsage(user_id=current_user_id(), kind=kind, provider=provider.name,
                                   model=provider.model[:120], input_tokens=u["input"],
                                   output_tokens=u["output"], ok=ok,
                                   duration_ms=int((time.time() - started) * 1000)))
            db.session.commit()
        except Exception:  # noqa: BLE001 - accounting must never break the feature
            log.exception("could not record AI usage")
            db.session.rollback()
