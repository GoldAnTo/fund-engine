"""Provider-run audit read model (prototype Provider 运行记录)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import AIRun
from app.schemas.v1.provider_runs import ProviderRunDTO, ProviderRunsResponse


class ProviderRunQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_runs(
        self, *, kind: str | None = None, limit: int = 50
    ) -> ProviderRunsResponse:
        query = select(AIRun).order_by(AIRun.started_at.desc()).limit(limit)
        if kind is not None:
            query = query.where(AIRun.kind == kind)
        runs = [
            ProviderRunDTO(
                id=str(run.id),
                kind=run.kind,
                model_version=run.model_version,
                prompt_version=run.prompt_version,
                status=run.status,
                output_summary=run.output_summary,
                error=run.error,
                input_ref=run.input_ref,
                started_at=run.started_at.isoformat(),
                finished_at=(
                    run.finished_at.isoformat() if run.finished_at else None
                ),
            )
            for run in self._db.scalars(query)
        ]
        return ProviderRunsResponse(runs=runs)
