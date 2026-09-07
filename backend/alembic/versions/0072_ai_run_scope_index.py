"""Index explicit Case/run audit attribution for usage reads."""
from alembic import op
import sqlalchemy as sa

revision = '0072'
down_revision = '0071'
branch_labels = None
depends_on = None


def _index(dialect):
    table = sa.Table('ai_runs', sa.MetaData(), sa.Column('input_ref', sa.JSON()))
    # Freeze the expressions here; historical migrations must not depend on
    # future application query helper changes.
    if dialect == 'sqlite':
        paths = [sa.func.json_extract(table.c.input_ref, '$."' + key + '"')
                 for key in ('research_case_id', 'research_run_id')]
    else:
        paths = [table.c.input_ref[key].as_string()
                 for key in ('research_case_id', 'research_run_id')]
    return sa.Index('ix_ai_runs_research_scope', *paths)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        # SQLAlchemy intentionally skips reflection of SQLite expression indexes.
        if any(row[1] == 'ix_ai_runs_research_scope' for row in bind.exec_driver_sql("PRAGMA index_list('ai_runs')")):
            return
        _index(bind.dialect.name).create(bind)
    else:
        _index(bind.dialect.name).create(bind, checkfirst=True)


def downgrade():
    op.drop_index('ix_ai_runs_research_scope', table_name='ai_runs')
