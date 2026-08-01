"""Case-level frozen-snapshot compare (prototype 版本比较).

Diffs two point-in-time views of one research case: for every thesis, the
latest snapshot/assessment at each cutoff is resolved by ledger write time
(``created_at``), then link sets, conclusions, and gaps are diffed.  Document
versions that became available inside the window are listed as inputs —
the prototype's 「为什么改变 · 输入变化」 block.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    SourceStatement,
)
from app.repositories.research import ResearchRepository
from app.schemas.v1.compare import (
    CaseCompareResponse,
    CompareLinkDTO,
    DocumentVersionAddedDTO,
    ThesisCompareDTO,
)


class CaseCompareQueries:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ResearchRepository(db)

    def compare(
        self,
        *,
        case_id: uuid.UUID,
        base_cutoff: datetime,
        compare_cutoff: datetime,
    ) -> CaseCompareResponse:
        if base_cutoff >= compare_cutoff:
            raise ValidationFailedError(
                "base_cutoff must be earlier than compare_cutoff"
            )
        case = self._repo.get_case(case_id, cutoff=compare_cutoff)
        if case is None:
            raise NotFoundError(f"research case {case_id} not found")

        theses = self._repo.theses_for_case(case_id, cutoff=compare_cutoff)
        thesis_dtos: list[ThesisCompareDTO] = []
        for thesis in theses:
            snap_before = self._repo.latest_snapshot_for_thesis(
                thesis.id, cutoff=base_cutoff
            )
            snap_after = self._repo.latest_snapshot_for_thesis(
                thesis.id, cutoff=compare_cutoff
            )
            assess_before = self._repo.latest_assessment_for_thesis(
                thesis.id, cutoff=base_cutoff
            )
            assess_after = self._repo.latest_assessment_for_thesis(
                thesis.id, cutoff=compare_cutoff
            )

            ids_before = (
                set(snap_before.evidence_link_ids) if snap_before else set()
            )
            ids_after = (
                set(snap_after.evidence_link_ids) if snap_after else set()
            )

            conclusion_before = assess_before.conclusion if assess_before else None
            conclusion_after = assess_after.conclusion if assess_after else None
            thesis_dtos.append(
                ThesisCompareDTO(
                    thesis_id=str(thesis.id),
                    statement=thesis.statement,
                    snapshot_before_id=(
                        str(snap_before.id) if snap_before else None
                    ),
                    snapshot_after_id=str(snap_after.id) if snap_after else None,
                    conclusion_before=conclusion_before,
                    conclusion_after=conclusion_after,
                    conclusion_changed=conclusion_before != conclusion_after,
                    added_links=self._link_dtos(ids_after - ids_before),
                    removed_links=self._link_dtos(ids_before - ids_after),
                    gaps_before=list(assess_before.gaps) if assess_before else [],
                    gaps_after=list(assess_after.gaps) if assess_after else [],
                )
            )

        documents_added = [
            DocumentVersionAddedDTO(
                document_version_id=str(v.id),
                source_url=v.source_url,
                published_at=v.published_at.isoformat() if v.published_at else None,
                available_at=v.available_at.isoformat(),
            )
            for v in self._db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.available_at > base_cutoff)
                .where(DocumentVersion.available_at <= compare_cutoff)
                .order_by(DocumentVersion.available_at)
            )
        ]

        return CaseCompareResponse(
            case_id=str(case_id),
            base_cutoff=base_cutoff.isoformat(),
            compare_cutoff=compare_cutoff.isoformat(),
            documents_added=documents_added,
            theses=thesis_dtos,
        )

    def _link_dtos(self, link_ids: set[str]) -> list[CompareLinkDTO]:
        dtos: list[CompareLinkDTO] = []
        for link_id in sorted(link_ids):
            link = self._repo.get_evidence_link(uuid.UUID(link_id))
            if link is None:
                continue
            statement = self._db.scalar(
                select(SourceStatement).where(
                    SourceStatement.id == link.source_statement_id
                )
            )
            dtos.append(
                CompareLinkDTO(
                    link_id=str(link.id),
                    role=link.role,
                    reason=link.reason,
                    statement_text=(
                        statement.normalized_text if statement else None
                    ),
                    review_state=link.review_state,
                )
            )
        return dtos
