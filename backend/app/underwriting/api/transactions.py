"""Shared transaction and HTTP error translation for underwriting writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError as HttpConflictError
from app.errors import ValidationFailedError
from app.models.ledger import ConflictError as DomainConflictError
from app.models.ledger import ValidationError
from app.underwriting.persistence.repository import StaleParentError


T = TypeVar("T")


def commit_write(db: Session, operation: Callable[[], T]) -> T:
    """Commit only a successful service operation and normalize kernel errors."""
    try:
        value = operation()
        db.commit()
        return value
    except ValidationError as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HttpConflictError("underwriting write conflicts") from exc
    except (DomainConflictError, StaleParentError) as exc:
        db.rollback()
        raise HttpConflictError(str(exc)) from exc
    except Exception:
        db.rollback()
        raise
