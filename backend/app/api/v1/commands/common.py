"""Shared helpers for command routers: unit-of-work + error translation."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import Request
from sqlalchemy.orm import Session

from app.errors import ConflictError, ValidationFailedError
from app.models.ledger import ConflictError as DomainConflictError
from app.models.ledger import ValidationError
from app.repositories.audit import AuditLogRepository


def commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def translate_validation(fn, *args, **kwargs):
    """Run a service call, mapping domain errors to their HTTP envelopes.

    Two domain errors share the same mapping rule (re-raise with the same
    message as the corresponding HTTP-layer error class):

    * ``app.models.ledger.ValidationError`` → ``ValidationFailedError`` (422)
    * ``app.models.ledger.ConflictError``   → ``ConflictError`` (409)

    Combining them keeps command routes symmetric: every service call
    goes through one decorator-style helper, and the choice of 422 vs 409
    is made at the domain layer (where the service knows whether the
    input is malformed or merely colliding with prior state).
    """
    try:
        return fn(*args, **kwargs)
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc)) from exc


# Backwards-compat alias: routes that want to be explicit about expecting
# conflict-class errors can call this in addition to ``translate_validation``
# for documentation purposes. Behaviour is identical to ``translate_validation``
# because both errors are now handled there.
translate_conflict = translate_validation


def resolve_actor(request: Request) -> str:
    """Extract a best-effort actor identifier from request headers.

    Production deployments will plug authentication in here; for now the
    only signal is an optional ``X-Actor`` header (``"human:alice"`` or
    ``"ai:openai/gpt-4"`` etc.). The fallback ``"human:anonymous"`` is
    deliberately distinct from any well-known identity so it's easy to
    spot un-attributed writes in the audit log.
    """
    actor = request.headers.get("X-Actor", "").strip()
    if not actor:
        return "human:anonymous"
    return actor[:128]


def _record_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    payload: dict[str, Any],
    request_id: str,
    result: str,
    entity_id: uuid.UUID | None = None,
    error_message: str | None = None,
) -> None:
    """Append a single audit row, then commit. Best-effort: never raises."""
    try:
        AuditLogRepository(db).record(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            result=result,
            error_message=error_message,
            request_id=request_id,
        )
        db.commit()
    except Exception:
        # Audit failure must not mask the original outcome. Roll back the
        # half-committed audit row, swallow the error, and let the caller
        # continue. (Monitoring can detect audit gaps by counting
        # successful HTTP responses without a corresponding audit row.)
        try:
            db.rollback()
        except Exception:
            pass


def audit_command(
    db: Session,
    request: Request,
    *,
    action: str,
    entity_type: str,
    payload: dict[str, Any],
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Run a service call, appending an audit row for the outcome.

    The audit row carries the ``actor`` (from ``X-Actor`` header) and the
    request id (from middleware-injected ``request.state.request_id``).
    On success, ``entity_id`` is taken from the returned object if it has
    an ``.id`` attribute. On any exception, a ``result='failed'`` row is
    appended before the exception is re-raised so the original HTTP error
    envelope is unaffected.
    """
    actor = resolve_actor(request)
    request_id = getattr(request.state, "request_id", "") or ""
    entity_id: uuid.UUID | None = None
    bound_kwargs = kwargs or {}
    try:
        value = fn(*args, **bound_kwargs)
        if hasattr(value, "id"):
            entity_id = value.id
        _record_audit(
            db,
            actor=actor,
            action=action,
            entity_type=entity_type,
            payload=payload,
            request_id=request_id,
            result="success",
            entity_id=entity_id,
        )
        return value
    except Exception as exc:
        _record_audit(
            db,
            actor=actor,
            action=action,
            entity_type=entity_type,
            payload=payload,
            request_id=request_id,
            result="failed",
            entity_id=entity_id,
            error_message=str(exc)[:500] or exc.__class__.__name__,
        )
        raise
