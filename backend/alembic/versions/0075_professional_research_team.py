"""Create professional team history and fenced mutable task state.

Revision ID: 0075
Revises: 0074
Table definitions and guard SQL are frozen for this release.
"""
import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

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


def upgrade():
    op.create_table('research_teams',
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('event_sequence', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('run_spec_id',)),
        sa.ForeignKeyConstraint(['run_spec_id'], ['research_run_specs.id'], name=None),
        sa.CheckConstraint('revision >= 1 AND event_sequence >= 0', name='ck_research_team_counters'),
        sa.CheckConstraint("status IN ('active', 'paused', 'cancelled')", name='ck_research_team_status'),
    )
    op.create_table('professional_tasks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('instruction', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('reason_code', sa.String(length=64), nullable=True),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('lease_token', sa.Uuid(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['run_spec_id'], ['research_teams.run_spec_id'], name=None),
        sa.CheckConstraint('revision >= 1 AND attempt >= 0', name='ck_professional_task_counters'),
        sa.CheckConstraint("role IN ('industry', 'finance', 'strategy', 'quality')", name='ck_professional_task_role'),
        sa.CheckConstraint("status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')", name='ck_professional_task_status'),
        sa.UniqueConstraint(*('run_spec_id', 'revision', 'role'), name='uq_professional_task_role_revision'),
        sa.UniqueConstraint(*('id', 'run_spec_id'), name='uq_professional_task_scope'),
    )
    op.create_index('ix_professional_task_claim', 'professional_tasks', ['status', 'lease_expires_at', 'created_at'], unique=False)
    op.create_index('ix_professional_tasks_run_spec_id', 'professional_tasks', ['run_spec_id'], unique=False)
    op.create_table('professional_dependencies',
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('parent_task_id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint(*('task_id', 'parent_task_id')),
        sa.CheckConstraint('task_id != parent_task_id', name='ck_professional_dependency_not_self'),
        sa.ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_dependency_child'),
        sa.ForeignKeyConstraint(['parent_task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_dependency_parent'),
    )
    op.create_index('ix_professional_dependencies_run_spec_id', 'professional_dependencies', ['run_spec_id'], unique=False)
    op.create_table('professional_outputs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('evidence_manifest', sa.JSON(), nullable=False),
        sa.Column('dependency_output_ids', sa.JSON(), nullable=False),
        sa.Column('input_sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_output_scope'),
        sa.UniqueConstraint(*('task_id',), name='uq_professional_output_task'),
    )
    op.create_index('ix_professional_outputs_run_spec_id', 'professional_outputs', ['run_spec_id'], unique=False)
    op.create_table('professional_attempts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('task_attempt', sa.Integer(), nullable=False),
        sa.Column('call_id', sa.String(length=64), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['task_id'], ['professional_tasks.id'], name=None),
        sa.CheckConstraint('attempt >= 1 AND task_attempt >= 1', name='ck_professional_attempt_count'),
        sa.UniqueConstraint(*('task_id', 'call_id', 'attempt'), name='uq_professional_attempt_call'),
    )
    op.create_index('ix_professional_attempts_task_id', 'professional_attempts', ['task_id'], unique=False)
    op.create_table('professional_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('reason_code', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['run_spec_id'], ['research_teams.run_spec_id'], name=None),
        sa.CheckConstraint('sequence >= 1', name='ck_professional_event_sequence'),
        sa.ForeignKeyConstraint(['task_id', 'run_spec_id'], ['professional_tasks.id', 'professional_tasks.run_spec_id'], name='fk_professional_event_task_scope'),
        sa.UniqueConstraint(*('run_spec_id', 'sequence'), name='uq_professional_event_sequence'),
    )
    op.create_index('ix_professional_events_run_spec_id', 'professional_events', ['run_spec_id'], unique=False)
    op.create_table('professional_requests',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('key_sha256', sa.String(length=64), nullable=False),
        sa.Column('payload_sha256', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('receipt', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['run_spec_id'], ['research_teams.run_spec_id'], name=None),
        sa.UniqueConstraint(*('run_spec_id', 'key_sha256'), name='uq_professional_request_idempotency'),
    )
    op.create_index('ix_professional_requests_run_spec_id', 'professional_requests', ['run_spec_id'], unique=False)
    op.create_table('professional_reviews',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_spec_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('decision', sa.String(length=32), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('reviewed_by', sa.String(length=128), nullable=False),
        sa.Column('output_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*('id',)),
        sa.ForeignKeyConstraint(['run_spec_id'], ['research_teams.run_spec_id'], name=None),
        sa.CheckConstraint("decision IN ('approved', 'changes_requested')", name='ck_professional_review_decision'),
        sa.CheckConstraint('revision >= 1', name='ck_professional_review_revision'),
    )
    op.create_index('ix_professional_reviews_run_spec_id', 'professional_reviews', ['run_spec_id'], unique=False)
    for statements in _team_guard_sql(op.get_bind().dialect.name).values():
        for statement in statements:
            op.execute(statement)


def downgrade():
    dialect = op.get_bind().dialect.name
    guards = _team_guard_sql(dialect)
    for table in reversed(tuple(guards)):
        op.drop_table(table)
    if dialect == "postgresql":
        for statements in guards.values():
            for statement in statements:
                if statement.startswith("CREATE FUNCTION") or statement.startswith("CREATE OR REPLACE FUNCTION"):
                    name = statement.split("FUNCTION ", 1)[1].split("(", 1)[0]
                    op.execute(f"DROP FUNCTION {name}()")
