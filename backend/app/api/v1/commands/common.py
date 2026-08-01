"""Shared helpers for command routers: unit-of-work + error translation."""

from sqlalchemy.orm import Session

from app.errors import ValidationFailedError
from app.models.ledger import ValidationError


def commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def translate_validation(fn, *args, **kwargs):
    """Run a service call, mapping domain ValidationError → 422 envelope."""
    try:
        return fn(*args, **kwargs)
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
