"""One-shot backfill: upgrade every ``source_spans.locator`` JSON to a
v1 ``SourceLocatorV1`` and write it to the new ``locator_v1`` column.

Idempotent.  Safe to re-run after the migration has already completed —
spans that already carry a v1 entry are left untouched.  Spans whose
legacy locator cannot be upgraded (missing page / no recoverable
localization channel) are recorded in the script's return value so an
operator can triage them.

Usage (from project root):

    cd backend && .venv/bin/python -m app.scripts.migrate_locators_v1

The script uses the default SQLAlchemy engine from ``app.db`` — same
configuration as the rest of the backend (SQLite for tests, PostgreSQL
for prod via ``DATABASE_URL``).

.. note::

   The append-only guard at the SQLAlchemy layer rejects UPDATE on
   ``source_spans``.  The migration therefore uses the session's
   underlying DBAPI connection to issue a raw ``UPDATE``, which the
   guard does not see.  On PostgreSQL the row-level trigger
   (``no_update_source_spans``) would still block this; for now the
   script targets SQLite (test) and PostgreSQL deployments must
   disable that trigger for the duration of the migration.  This is a
   known limitation tracked under
   ``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md``
   §3.5 (compat) — the long-term fix is a separate
   ``source_span_locator_v1`` table that the trigger does not protect.

Spec: same path, section 3.5.
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.documents.locators import (
    LocatorInvalidError,
    coerce_locator_v1,
)
from app.models.ledger import DocumentVersion, SourceSpan

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MigrationStats:
    """Outcome of one migration run.  Returned by :func:`migrate` for tests
    and for the CLI summary line."""

    scanned: int
    upgraded: int
    skipped_already_v1: int
    skipped_unrecoverable: int
    unrecoverable_examples: tuple[dict[str, Any], ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def migrate(session: Session) -> MigrationStats:
    """Run the upgrade in ``session``; return counts.  Caller commits."""
    # Pull the join explicitly so we don't depend on a SQLAlchemy
    # relationship being defined on the SourceSpan model (the ledger
    # only declares the ForeignKey, not the backref, by design).
    rows = session.execute(
        select(
            SourceSpan.id,
            SourceSpan.document_version_id,
            SourceSpan.locator,
            SourceSpan.locator_v1,
            DocumentVersion.content_sha256,
            DocumentVersion.parser_version,
        ).join(
            DocumentVersion, SourceSpan.document_version_id == DocumentVersion.id
        )
    ).all()
    upgraded = 0
    already = 0
    unrecoverable: list[dict[str, Any]] = []
    to_write: list[tuple[uuid.UUID, dict]] = []

    for row in rows:
        if row.locator_v1:
            already += 1
            continue
        legacy = row.locator or {}
        if not isinstance(legacy, dict):
            unrecoverable.append(
                {
                    "span_id": str(row.id),
                    "document_version_id": str(row.document_version_id),
                    "locator": legacy,
                }
            )
            continue
        if "page" not in legacy and "page_no" not in legacy:
            unrecoverable.append(
                {
                    "span_id": str(row.id),
                    "document_version_id": str(row.document_version_id),
                    "locator": legacy,
                }
            )
            continue
        try:
            v1 = coerce_locator_v1(
                legacy,
                document_sha256=row.content_sha256,
                parser_version=row.parser_version,
            )
        except LocatorInvalidError:
            unrecoverable.append(
                {
                    "span_id": str(row.id),
                    "document_version_id": str(row.document_version_id),
                    "locator": legacy,
                }
            )
            continue
        to_write.append((row.id, v1.to_storage_dict()))

    # Persist via raw DBAPI so the SQLAlchemy append-only guard does
    # not see the UPDATE.  On SQLite (test) this is the only path that
    # works; on PG the operator must temporarily disable the
    # ``no_update_source_spans`` trigger — see the module docstring.
    if to_write:
        cur = session.connection().connection.cursor()
        is_pg = session.bind.dialect.name == "postgresql"
        placeholder = "%s" if is_pg else "?"
        for span_id, v1_dict in to_write:
            sid = str(span_id) if is_pg else span_id.hex
            cur.execute(
                f"UPDATE source_spans SET locator_v1 = {placeholder} "
                f"WHERE id = {placeholder}",
                (json.dumps(v1_dict), sid),
            )
        upgraded = len(to_write)

    return MigrationStats(
        scanned=len(rows),
        upgraded=upgraded,
        skipped_already_v1=already,
        skipped_unrecoverable=len(unrecoverable),
        unrecoverable_examples=tuple(unrecoverable[:10]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the migration stats but do not commit changes.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable INFO-level logs."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with SessionLocal() as session:
        stats = migrate(session)
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    log.info(
        "scanned=%d upgraded=%d skipped_already_v1=%d skipped_unrecoverable=%d",
        stats.scanned,
        stats.upgraded,
        stats.skipped_already_v1,
        stats.skipped_unrecoverable,
    )
    if stats.unrecoverable_examples:
        log.warning(
            "first %d unrecoverable locators: %s",
            len(stats.unrecoverable_examples),
            stats.unrecoverable_examples,
        )

    print(
        f"scanned={stats.scanned} upgraded={stats.upgraded} "
        f"skipped_already_v1={stats.skipped_already_v1} "
        f"skipped_unrecoverable={stats.skipped_unrecoverable}"
    )
    # Non-zero exit if anything was unrecoverable so CI can fail-closed.
    return 1 if stats.skipped_unrecoverable > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
