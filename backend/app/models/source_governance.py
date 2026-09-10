"""Immutable source-use contracts and provider retrieval records.

The evidence ledger keeps a document's bytes and locator separately from the
permission that allows a particular team to process or show those bytes.  A
contract is therefore attached to one frozen document version; it is never
silently widened in place.  A correction must freeze a successor document and
append a successor contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class SourceContract(Base):
    __tablename__ = "source_contracts"
    __table_args__ = (UniqueConstraint("document_version_id", name="uq_source_contracts_document"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_versions.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    research_source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=lambda context: context.get_current_parameters()["source_type"],
    )
    provider_or_tenant: Mapped[str] = mapped_column(String(256), nullable=False)
    allow_ai_processing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_display: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_export: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_api: Mapped[bool] = mapped_column(Boolean, nullable=False)
    region: Mapped[str] = mapped_column(String(128), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_policy: Mapped[str] = mapped_column(String(128), nullable=False)
    deletion_policy: Mapped[str] = mapped_column(Text, nullable=False)
    downstream_restrictions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    contract_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intake_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    declared_by: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderRecord(Base):
    """Reproducibility record for a provider-delivered document version."""

    __tablename__ = "provider_records"
    __table_args__ = (UniqueConstraint("document_version_id", name="uq_provider_records_document"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_versions.id"), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    request_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retrieval_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contract_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
