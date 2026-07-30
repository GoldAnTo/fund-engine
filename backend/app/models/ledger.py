"""Immutable evidence ledger tables.

Every ledger entity is append-only: no UPDATE or DELETE path is exposed.
Corrections append a successor record carrying ``supersedes_id``.

Immutability is enforced at two layers:
1. Application layer: a SQLAlchemy ``before_execute`` guard rejects any
   UPDATE/DELETE targeting an immutable table, raising ImmutableLedgerError.
2. Database layer: PostgreSQL triggers (see Alembic migration 0001) raise on
   UPDATE/DELETE as defence-in-depth against connections bypassing the app.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.dml import Delete, Update, UpdateBase

IMMUTABLE_TABLES = frozenset({"document_versions", "source_spans"})


class ImmutableLedgerError(Exception):
    """Raised on any attempt to UPDATE or DELETE an append-only ledger table."""


class Base(DeclarativeBase):
    pass


def _target_table_name(stmt: UpdateBase) -> str | None:
    table = getattr(stmt, "table", None)
    if table is not None and hasattr(table, "name"):
        return table.name
    try:
        froms = list(stmt.get_final_froms())
    except Exception:
        froms = []
    for clause in froms:
        if hasattr(clause, "name"):
            return clause.name
    return None


@event.listens_for(Engine, "before_execute")
def _guard_immutable_tables(*args: Any, **kwargs: Any) -> None:
    statement = kwargs.get("statement")
    if statement is None and len(args) >= 2:
        statement = args[1]
    if isinstance(statement, (Update, Delete)):
        name = _target_table_name(statement)
        if name in IMMUTABLE_TABLES:
            raise ImmutableLedgerError(
                f"table '{name}' is append-only: UPDATE/DELETE is not allowed"
            )


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=True
    )


class SourceSpan(Base):
    __tablename__ = "source_spans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
