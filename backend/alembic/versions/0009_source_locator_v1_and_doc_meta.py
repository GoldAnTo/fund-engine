"""S4 of the Docling + SourceLocatorV1 upgrade spec:
add SourceLocatorV1 column + provenance hashes on ``source_spans`` and
display / parser-state metadata on ``document_versions``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-02

The new columns are all nullable or have a non-breaking default, so the
migration is a pure schema extension: existing rows keep their old values
untouched, and a one-shot ``migrate_locators_v1.py`` script fills in the
new ``locator_v1`` column where it can.

Spec: ``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md``
sections 3.5 (compat) and 4 (module / file map).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # document_versions: display + parser-state metadata.
    op.add_column(
        "document_versions",
        sa.Column("title", sa.String(512), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("byte_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("language", sa.String(16), nullable=True),
    )
    # ``parse_state`` has a non-null default so existing rows back-fill
    # with ``success`` (the historical behaviour — every span was parsed).
    op.add_column(
        "document_versions",
        sa.Column(
            "parse_state",
            sa.String(16),
            nullable=False,
            server_default="success",
        ),
    )

    # source_spans: round-trip + context hashes + v1 locator column.
    op.add_column(
        "source_spans",
        sa.Column("text_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "source_spans",
        sa.Column("context_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "source_spans",
        sa.Column("locator_v1", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_spans", "locator_v1")
    op.drop_column("source_spans", "context_hash")
    op.drop_column("source_spans", "text_sha256")
    op.drop_column("document_versions", "parse_state")
    op.drop_column("document_versions", "language")
    op.drop_column("document_versions", "byte_size")
    op.drop_column("document_versions", "title")
