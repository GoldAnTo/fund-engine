"""Fail closed when any stored independent-research revision cannot replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.underwriting.persistence.models import UnderwritingResearchVersion
from app.underwriting.services.research_revision_diff import (
    PRODUCT_MANIFEST_SCHEMA,
    PRODUCT_REVISION_KIND,
    ProductResearchRevisionSummary,
    ResearchRevisionDiffService,
)


_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "object_id",
        "basis_id",
        "version_kind",
        "sequence",
        "content_hash",
        "parent_ids",
        "supersedes_id",
        "project_id",
        "boundary_id",
        "manifest_id",
        "manifest_schema",
        "publication_status",
        "created_at",
    }
)
_REQUIRED_PRODUCT_TABLES = frozenset(
    {
        "uw_research_objects",
        "uw_historical_bases",
        "uw_mandate_versions",
        "uw_research_projects",
        "uw_research_project_securities",
        "uw_research_scope_versions",
        "uw_research_agenda_versions",
        "uw_price_snapshots",
        "uw_fx_snapshots",
        "uw_capital_structure_snapshots",
        "uw_security_rights_versions",
        "uw_revision_boundaries",
        "uw_revision_manifests",
        "uw_research_assessment_versions",
    }
)


class RevisionManifestVerificationError(RuntimeError):
    """The persisted schema or first product revision failed strict replay."""


@dataclass(frozen=True, slots=True)
class RevisionManifestVerification:
    count: int
    revision_hashes: tuple[tuple[UUID, str], ...]


def _verify_schema(session: Session) -> None:
    schema = inspect(session.connection())
    tables = set(schema.get_table_names())
    missing_tables = _REQUIRED_PRODUCT_TABLES - tables
    if "uw_research_versions" not in tables or missing_tables:
        missing = sorted({"uw_research_versions"} - tables | missing_tables)
        raise RevisionManifestVerificationError(
            f"independent revision schema is incomplete; missing tables: {missing}"
        )
    columns = {column["name"] for column in schema.get_columns("uw_research_versions")}
    missing_columns = _REQUIRED_COLUMNS - columns
    if missing_columns:
        raise RevisionManifestVerificationError(
            "independent revision schema is incomplete; missing columns: "
            f"{sorted(missing_columns)}"
        )


def verify_revision_manifests(
    session: Session,
    *,
    reader_factory: Callable[[Session], ResearchRevisionDiffService] = (
        ResearchRevisionDiffService
    ),
) -> RevisionManifestVerification:
    """Verify every product revision in deterministic order, stopping on first error."""

    _verify_schema(session)
    revision_ids = tuple(
        session.scalars(
            select(UnderwritingResearchVersion.id)
            .where(UnderwritingResearchVersion.version_kind == PRODUCT_REVISION_KIND)
            .order_by(
                UnderwritingResearchVersion.sequence,
                UnderwritingResearchVersion.id,
            )
        )
    )
    reader = reader_factory(session)
    verified: list[tuple[UUID, str]] = []
    for revision_id in revision_ids:
        try:
            summary = reader.revision_summary(revision_id)
            if not isinstance(summary, ProductResearchRevisionSummary):
                raise ValueError("reader returned a non-product revision")
            if summary.id != revision_id:
                raise ValueError("reader returned a different revision")
            if summary.version_kind != PRODUCT_REVISION_KIND:
                raise ValueError("reader returned a different revision kind")
            row = session.get(UnderwritingResearchVersion, revision_id)
            if row is None:
                raise ValueError("revision disappeared during verification")
            if row.manifest_schema != PRODUCT_MANIFEST_SCHEMA:
                raise ValueError("stored manifest schema is unsupported")
            if summary.content_hash != row.content_hash:
                raise ValueError("replayed content hash differs from stored hash")
        except Exception as exc:
            raise RevisionManifestVerificationError(
                f"independent revision {revision_id} failed replay: {exc}"
            ) from exc
        verified.append((revision_id, summary.manifest_hash))
    return RevisionManifestVerification(
        count=len(verified), revision_hashes=tuple(verified)
    )


def main() -> int:
    with SessionLocal() as session:
        result = verify_revision_manifests(session)
    print(f"Verified {result.count} independent research revision manifest(s).")
    for revision_id, manifest_hash in result.revision_hashes:
        print(f"{revision_id} {manifest_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
