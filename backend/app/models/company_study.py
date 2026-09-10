"""Private user-authored dossiers; these rows confer no verified company identity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DDL,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base


def utcnow():
    return datetime.now(UTC)


class CompanyStudy(Base):
    __tablename__ = "company_studies"
    __table_args__ = (
        CheckConstraint(
            "market IN ('CN', 'HK', 'US', 'other')", name="ck_company_study_market"
        ),
        CheckConstraint(
            "revision >= 0 AND monitor_version >= 0", name="ck_company_study_versions"
        ),
        Index("ix_company_study_owner", "tenant_id", "subject_id", "updated_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    focus: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monitor_next_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CompanyStudyActivity(Base):
    __tablename__ = "company_study_activities"
    __table_args__ = (
        UniqueConstraint("id", "study_id", name="uq_company_activity_scope"),
        UniqueConstraint("study_id", "schedule_key", name="uq_company_activity_window"),
        CheckConstraint(
            "kind IN ('baseline', 'event', 'material', 'refresh', 'linked')",
            name="ck_company_activity_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'starting', 'running', 'completed', 'failed', 'blocked')",
            name="ck_company_activity_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_company_activity_attempt"),
        Index("ix_company_activity_claim", "status", "lease_expires_at", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_studies.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=True
    )
    run_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_run_specs.id"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CompanyStudyRevision(Base):
    __tablename__ = "company_study_revisions"
    __table_args__ = (
        UniqueConstraint("study_id", "version", name="uq_company_study_revision"),
        ForeignKeyConstraint(
            ["activity_id", "study_id"],
            ["company_study_activities.id", "company_study_activities.study_id"],
            name="fk_company_revision_activity",
        ),
        CheckConstraint(
            "version >= 1 AND team_revision >= 1", name="ck_company_revision_versions"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_studies.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False
    )
    run_spec_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_run_specs.id"), nullable=False
    )
    team_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    output_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CompanyStudyMonitor(Base):
    __tablename__ = "company_study_monitors"
    __table_args__ = (
        UniqueConstraint("study_id", "version", name="uq_company_monitor_version"),
        CheckConstraint("version >= 1", name="ck_company_monitor_version"),
        CheckConstraint(
            "status IN ('active', 'paused')", name="ck_company_monitor_status"
        ),
        CheckConstraint(
            "frequency IN ('daily', 'weekly')", name="ck_company_monitor_frequency"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_studies.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    focus: Mapped[str] = mapped_column(Text, nullable=False)
    next_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CompanyStudyRequest(Base):
    __tablename__ = "company_study_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_id", "scope", "key_sha256", name="uq_company_request"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


def company_guard_sql(dialect):
    tables = (
        "company_studies",
        "company_study_activities",
        "company_study_revisions",
        "company_study_monitors",
        "company_study_requests",
    )
    frozen = {
        "company_studies": (
            "id",
            "tenant_id",
            "subject_id",
            "name",
            "symbol",
            "market",
            "focus",
            "created_at",
        ),
        "company_study_activities": (
            "id",
            "study_id",
            "kind",
            "title",
            "text",
            "context_revision",
            "schedule_key",
            "created_at",
        ),
    }
    keys = {
        "company_studies": [("id",)],
        "company_study_activities": [("id",), ("study_id", "schedule_key")],
        "company_study_revisions": [("id",), ("study_id", "version")],
        "company_study_monitors": [("id",), ("study_id", "version")],
        "company_study_requests": [
            ("id",),
            ("tenant_id", "subject_id", "scope", "key_sha256"),
        ],
    }
    result = {table: [] for table in tables}
    for table in tables:
        for operation in ("DELETE",) if table in frozen else ("UPDATE", "DELETE"):
            name = f"cs_no_{operation.lower()}_{table}"
            if dialect == "sqlite":
                result[table].append(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'company study history is immutable'); END"
                )
            else:
                result[table] += [
                    f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'company study history is immutable'; END; $$",
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()",
                ]
        if table in frozen:
            condition = " OR ".join(
                f"OLD.{col} IS NOT NEW.{col}"
                if dialect == "sqlite"
                else f"OLD.{col} IS DISTINCT FROM NEW.{col}"
                for col in frozen[table]
            )
            if table == "company_study_activities":
                comparison = "IS NOT" if dialect == "sqlite" else "IS DISTINCT FROM"
                condition += f" OR (OLD.prompt IS NOT NULL AND OLD.prompt {comparison} NEW.prompt) OR (OLD.run_spec_id IS NOT NULL AND OLD.run_spec_id {comparison} NEW.run_spec_id) OR (OLD.conversation_id IS NOT NULL AND OLD.conversation_id {comparison} NEW.conversation_id)"
            name = f"cs_frozen_{table}"
            if dialect == "sqlite":
                result[table].append(
                    f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'company study definition is immutable'); END"
                )
            else:
                result[table] += [
                    f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'company study definition is immutable'; END IF; RETURN NEW; END; $$",
                    f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()",
                ]
        if dialect == "sqlite":
            conflicts = " OR ".join(
                "(" + " AND ".join(f"{col}=NEW.{col}" for col in columns) + ")"
                for columns in keys[table]
            )
            result[table].append(
                f"CREATE TRIGGER cs_no_replace_{table} BEFORE INSERT ON {table} WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflicts}) BEGIN SELECT RAISE(ABORT, 'company study history is immutable'); END"
            )
    return result


for _dialect in ("sqlite", "postgresql"):
    for _table, _statements in company_guard_sql(_dialect).items():
        for _statement in _statements:
            event.listen(
                Base.metadata.tables[_table],
                "after_create",
                DDL(_statement).execute_if(dialect=_dialect),
            )
