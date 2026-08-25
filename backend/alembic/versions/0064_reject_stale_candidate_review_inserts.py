"""Reject reviews inserted after a candidate dossier is superseded.

Revision ID: 0064
Revises: 0063
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TRIGGER = "trg_uw_candidate_review_reject_superseded"
_FUNCTION = "reject_superseded_candidate_review_insert"
_STALE_MESSAGE = "candidate dossier is no longer current"


def _stale_review_exists_sql(*, new_id: str) -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM uw_evidence_candidate_dossier_versions AS reviewed
            JOIN uw_evidence_candidate_dossier_versions AS successor
              ON successor.supersedes_id = reviewed.id
             AND successor.object_id = reviewed.object_id
             AND successor.basis_id = reviewed.basis_id
             AND successor.dossier_key = reviewed.dossier_key
            WHERE reviewed.id = {new_id}
        )
    """


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER {_TRIGGER}
            BEFORE INSERT ON uw_evidence_candidate_review_versions
            FOR EACH ROW
            WHEN {_stale_review_exists_sql(new_id='NEW.dossier_id')}
            BEGIN
                SELECT RAISE(ABORT, '{_STALE_MESSAGE}');
            END;
        """)
        return
    op.execute(f"""
        CREATE FUNCTION {_FUNCTION}()
        RETURNS trigger AS $$
        BEGIN
            IF {_stale_review_exists_sql(new_id='NEW.dossier_id')} THEN
                RAISE EXCEPTION '{_STALE_MESSAGE}';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE INSERT ON uw_evidence_candidate_review_versions
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER};")
        return
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON uw_evidence_candidate_review_versions;")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}();")
