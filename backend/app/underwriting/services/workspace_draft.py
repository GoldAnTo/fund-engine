"""Strict, optimistic operations for the mutable research workspace draft."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.persistence.product_repository import ProductRepository

_DRAFT_SCHEMA = "product.workspace-draft.v1"
_REFERENCE_TUPLE_FIELDS = frozenset(
    {
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "security_rights_ids",
    }
)


def _canonical_references(
    value: tuple[UUID, ...] | None,
) -> tuple[UUID, ...] | None:
    if value is None:
        return None
    return tuple(sorted(set(value), key=str))


class WorkspaceDraftPatch(BaseModel):
    """Allowlisted user patch; missing preserves while explicit null clears."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mandate_id: UUID | None = None
    scope_id: UUID | None = None
    agenda_id: UUID | None = None
    historical_basis_id: UUID | None = None
    price_snapshot_ids: tuple[UUID, ...] | None = None
    fx_snapshot_ids: tuple[UUID, ...] | None = None
    capital_structure_snapshot_id: UUID | None = None
    security_rights_ids: tuple[UUID, ...] | None = None
    user_focus: str | None = None

    @field_validator(
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "security_rights_ids",
    )
    @classmethod
    def canonicalize_references(
        cls, value: tuple[UUID, ...] | None
    ) -> tuple[UUID, ...] | None:
        return _canonical_references(value)

    @field_validator("user_focus")
    @classmethod
    def normalize_user_focus(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_focus must not be empty")
        return normalized


class WorkspaceDraftContent(BaseModel):
    """Complete controlled schema persisted in ``WorkspaceDraft.content``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["product.workspace-draft.v1"] = _DRAFT_SCHEMA
    publication_status: Literal["draft"] = "draft"
    mandate_id: UUID | None = None
    scope_id: UUID | None = None
    agenda_id: UUID | None = None
    historical_basis_id: UUID | None = None
    price_snapshot_ids: tuple[UUID, ...] = ()
    fx_snapshot_ids: tuple[UUID, ...] = ()
    capital_structure_snapshot_id: UUID | None = None
    security_rights_ids: tuple[UUID, ...] = ()
    user_focus: str | None = None

    @field_validator(
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "security_rights_ids",
    )
    @classmethod
    def require_canonical_references(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        canonical = _canonical_references(value)
        if value != canonical:
            raise ValueError("draft references must be unique and canonically ordered")
        return value

    @field_validator("user_focus")
    @classmethod
    def require_normalized_user_focus(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip():
            raise ValueError("draft user_focus must be non-empty normalized text")
        return value


@dataclass(frozen=True, slots=True)
class WorkspaceDraftView:
    id: UUID
    project_id: UUID
    base_revision_id: UUID | None
    lock_version: int
    content: WorkspaceDraftContent
    created_at: datetime
    updated_at: datetime


class WorkspaceDraftService:
    """Create, read, and compare-and-swap one mutable draft per project."""

    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._repository = ProductRepository(session)
        self._now = now

    @staticmethod
    def _uuid(value: UUID, field: str) -> UUID:
        if type(value) is not UUID:
            raise ValidationError(f"{field} must be a UUID")
        return value

    @staticmethod
    def _utc(value: datetime, field: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"{field} must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def decode_content(value: object) -> WorkspaceDraftContent:
        """Validate persisted draft JSON at a durable-boundary read."""
        try:
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return WorkspaceDraftContent.model_validate_json(serialized)
        except (PydanticValidationError, TypeError, ValueError) as exc:
            raise ValidationError("workspace draft content is invalid") from exc

    @classmethod
    def _view(cls, row) -> WorkspaceDraftView:
        return WorkspaceDraftView(
            id=row.id,
            project_id=row.project_id,
            base_revision_id=row.base_revision_id,
            lock_version=row.lock_version,
            content=cls.decode_content(row.content),
            created_at=cls._stored_utc(row.created_at),
            updated_at=cls._stored_utc(row.updated_at),
        )

    @staticmethod
    def _patch(
        value: WorkspaceDraftPatch | Mapping[str, object],
    ) -> WorkspaceDraftPatch:
        if type(value) is WorkspaceDraftPatch:
            return value
        if not isinstance(value, Mapping):
            raise ValidationError("workspace draft patch must be an object")
        try:
            return WorkspaceDraftPatch.model_validate(dict(value))
        except PydanticValidationError as exc:
            raise ValidationError("workspace draft patch is invalid") from exc

    def create(
        self,
        project_id: UUID,
        *,
        initial_content: WorkspaceDraftContent | None = None,
    ) -> WorkspaceDraftView:
        project_id = self._uuid(project_id, "project_id")
        if self._repository.project(project_id) is None:
            raise ValidationError("research project does not exist")
        existing = self._repository.workspace_draft(project_id)
        if existing is not None:
            return self._view(existing)
        created_at = self._utc(self._now(), "clock")
        if (
            initial_content is not None
            and type(initial_content) is not WorkspaceDraftContent
        ):
            raise ValidationError("initial_content must be a WorkspaceDraftContent")
        content = initial_content or WorkspaceDraftContent()
        row = self._repository.create_workspace_draft(
            draft_id=uuid4(),
            project_id=project_id,
            content=content.model_dump(mode="json"),
            created_at=created_at,
        )
        return self._view(row)

    def read(self, project_id: UUID) -> WorkspaceDraftView | None:
        project_id = self._uuid(project_id, "project_id")
        row = self._repository.workspace_draft(project_id)
        return self._view(row) if row is not None else None

    def save(
        self,
        project_id: UUID,
        *,
        expected_lock_version: int,
        patch: WorkspaceDraftPatch | Mapping[str, object],
    ) -> WorkspaceDraftView:
        project_id = self._uuid(project_id, "project_id")
        if type(expected_lock_version) is not int or expected_lock_version < 1:
            raise ValidationError("expected_lock_version must be a positive integer")
        validated_patch = self._patch(patch)
        current = self._repository.workspace_draft(project_id)
        if current is None:
            raise ValidationError("workspace draft does not exist for project")
        current_content = self.decode_content(current.content)
        updated_at = self._utc(self._now(), "clock")
        if updated_at < self._stored_utc(
            current.created_at
        ) or updated_at < self._stored_utc(current.updated_at):
            raise ValidationError(
                "workspace draft updated_at cannot be earlier than its persisted times"
            )

        merged = current_content.model_dump(mode="python")
        for field in validated_patch.model_fields_set:
            value = getattr(validated_patch, field)
            if field in _REFERENCE_TUPLE_FIELDS and value is None:
                value = ()
            merged[field] = value
        try:
            content = WorkspaceDraftContent.model_validate(merged)
        except PydanticValidationError as exc:  # Defensive internal boundary.
            raise ValidationError(
                "workspace draft patch produced invalid content"
            ) from exc

        row = self._repository.compare_and_swap_workspace_draft(
            project_id=project_id,
            expected_lock_version=expected_lock_version,
            content=content.model_dump(mode="json"),
            updated_at=updated_at,
        )
        return self._view(row)
