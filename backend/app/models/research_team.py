"""Professional task DAG; separate from the Gateway's execution-stage projection."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base

PROFESSIONAL_ROLES = ('industry', 'finance', 'strategy', 'quality')
TASK_STATUSES = ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')


def utcnow() -> datetime:
    return datetime.now(UTC)


class ResearchTeam(Base):
    __tablename__ = 'research_teams'
    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'cancelled')", name='ck_research_team_status'),
        CheckConstraint('revision >= 1 AND event_sequence >= 0', name='ck_research_team_counters'),
    )
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('research_run_specs.id'), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='active')
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalTask(Base):
    __tablename__ = 'professional_tasks'
    __table_args__ = (
        UniqueConstraint('run_spec_id', 'revision', 'role', name='uq_professional_task_role_revision'),
        UniqueConstraint('id', 'run_spec_id', name='uq_professional_task_scope'),
        CheckConstraint("role IN ('industry', 'finance', 'strategy', 'quality')", name='ck_professional_task_role'),
        CheckConstraint("status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')", name='ck_professional_task_status'),
        CheckConstraint('revision >= 1 AND attempt >= 0', name='ck_professional_task_counters'),
        Index('ix_professional_task_claim', 'status', 'lease_expires_at', 'created_at'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('research_teams.run_spec_id'), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default='')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalDependency(Base):
    __tablename__ = 'professional_dependencies'
    __table_args__ = (
        ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_dependency_child'),
        ForeignKeyConstraint(['parent_task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_dependency_parent'),
        CheckConstraint('task_id != parent_task_id', name='ck_professional_dependency_not_self'),
    )
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    parent_task_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class ProfessionalOutput(Base):
    __tablename__ = 'professional_outputs'
    __table_args__ = (
        UniqueConstraint('task_id', name='uq_professional_output_task'),
        ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_output_scope'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_manifest: Mapped[list] = mapped_column(JSON, nullable=False)
    dependency_output_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalAttempt(Base):
    __tablename__ = 'professional_attempts'
    __table_args__ = (
        UniqueConstraint('task_id', 'call_id', 'attempt', name='uq_professional_attempt_call'),
        CheckConstraint('attempt >= 1 AND task_attempt >= 1', name='ck_professional_attempt_count'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('professional_tasks.id'), nullable=False, index=True)
    task_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalEvent(Base):
    __tablename__ = 'professional_events'
    __table_args__ = (
        UniqueConstraint('run_spec_id', 'sequence', name='uq_professional_event_sequence'),
        ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_event_task_scope'),
        CheckConstraint('sequence >= 1', name='ck_professional_event_sequence'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('research_teams.run_spec_id'), nullable=False, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalRequest(Base):
    __tablename__ = 'professional_requests'
    __table_args__ = (UniqueConstraint('run_spec_id', 'key_sha256', name='uq_professional_request_idempotency'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('research_teams.run_spec_id'), nullable=False, index=True)
    key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProfessionalReview(Base):
    __tablename__ = 'professional_reviews'
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'changes_requested')", name='ck_professional_review_decision'),
        CheckConstraint('revision >= 1', name='ck_professional_review_revision'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('research_teams.run_spec_id'), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    output_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


# Metadata-created test databases enforce the same persistence boundary.
from sqlalchemy import DDL, event

def _team_guard_sql(dialect):
    """All identifiers are release-owned literals; no user input enters DDL."""
    tables = ('research_teams', 'professional_tasks', 'professional_dependencies', 'professional_outputs', 'professional_attempts', 'professional_events', 'professional_requests', 'professional_reviews')
    immutable = tables[2:]
    definitions = {
        'research_teams': ('run_spec_id', 'created_at'),
        'professional_tasks': ('id', 'run_spec_id', 'revision', 'role', 'instruction', 'created_at'),
    }
    unique_sets = {
        'research_teams': [('run_spec_id',)],
        'professional_tasks': [('id',), ('run_spec_id', 'revision', 'role')],
        'professional_dependencies': [('task_id', 'parent_task_id')],
        'professional_outputs': [('id',), ('task_id',)],
        'professional_attempts': [('id',), ('task_id', 'call_id', 'attempt')],
        'professional_events': [('id',), ('run_spec_id', 'sequence')],
        'professional_requests': [('id',), ('run_spec_id', 'key_sha256')],
        'professional_reviews': [('id',)],
    }
    result = {table: [] for table in tables}
    for table in tables:
        operations = ('UPDATE', 'DELETE') if table in immutable else ('DELETE',)
        for operation in operations:
            name = f'team_no_{operation.lower()}_{table}'
            if dialect == 'sqlite':
                result[table].append(f"CREATE TRIGGER {name} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'professional history is immutable'); END")
            else:
                result[table].append(f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'professional history is immutable'; END; $$")
                result[table].append(f'CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        if table in definitions:
            condition = ' OR '.join(f'OLD.{col} IS NOT NEW.{col}' if dialect == 'sqlite' else f'OLD.{col} IS DISTINCT FROM NEW.{col}' for col in definitions[table])
            name = f'team_frozen_{table}'
            if dialect == 'sqlite':
                result[table].append(f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'professional definition is immutable'); END")
            else:
                result[table].append(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'professional definition is immutable'; END IF; RETURN NEW; END; $$")
                result[table].append(f'CREATE TRIGGER {name} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        if dialect == 'sqlite':
            conflict = ' OR '.join('(' + ' AND '.join(f'{col}=NEW.{col}' for col in cols) + ')' for cols in unique_sets[table])
            result[table].append(f"CREATE TRIGGER team_no_replace_{table} BEFORE INSERT ON {table} WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflict}) BEGIN SELECT RAISE(ABORT, 'professional history cannot be replaced'); END")
    for table, column in [('professional_outputs', 'dependency_output_ids'), ('professional_reviews', 'output_ids')]:
        name = f'team_valid_refs_{table}'
        if dialect == 'sqlite':
            result[table].append(f"""CREATE TRIGGER {name} BEFORE INSERT ON {table} BEGIN
              SELECT CASE WHEN json_valid(NEW.{column}) != 1 OR instr(NEW.{column}, char(0)) != 0 THEN RAISE(ABORT, 'invalid professional output references') END;
              SELECT CASE WHEN json_type(NEW.{column}) != 'array' THEN RAISE(ABORT, 'invalid professional output references') END;
              SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.{column}) ref WHERE ref.type != 'text' OR NOT EXISTS (SELECT 1 FROM professional_outputs output WHERE output.id=replace(ref.value, '-', '') AND output.run_spec_id=NEW.run_spec_id)) THEN RAISE(ABORT, 'foreign professional output reference') END;
            END""")
        else:
            result[table].append(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
              IF json_typeof(NEW.{column}) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'invalid professional output references'; END IF;
              IF EXISTS (SELECT 1 FROM json_array_elements(NEW.{column}) ref WHERE json_typeof(ref) IS DISTINCT FROM 'string' OR NOT EXISTS (SELECT 1 FROM professional_outputs output WHERE output.id::text=ref#>>'{{}}' AND output.run_spec_id=NEW.run_spec_id)) THEN RAISE EXCEPTION 'foreign professional output reference'; END IF;
              RETURN NEW; END; $$""")
            result[table].append(f'CREATE TRIGGER {name} BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()')
    return result


for _dialect in ('sqlite', 'postgresql'):
    for _table_name, _statements in _team_guard_sql(_dialect).items():
        for _statement in _statements:
            event.listen(Base.metadata.tables[_table_name], 'after_create', DDL(_statement).execute_if(dialect=_dialect))
