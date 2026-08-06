"""Repository for automatic research runs and tasks."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.operational import ResearchRun, ResearchTask, Job, TaskItem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutoResearchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_run(
        self,
        *,
        research_case_id: uuid.UUID,
        max_rounds: int = 3,
        budget: int = 100,
    ) -> ResearchRun:
        run = ResearchRun(
            research_case_id=research_case_id,
            max_rounds=max_rounds,
            budget=budget,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get_run(self, run_id: uuid.UUID) -> ResearchRun | None:
        return self._session.get(ResearchRun, run_id)

    def list_runs_for_case(
        self,
        case_id: uuid.UUID,
        *,
        limit: int = 20,
        after_created_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
    ) -> list[ResearchRun]:
        stmt = select(ResearchRun).where(ResearchRun.research_case_id == case_id)
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                (ResearchRun.created_at < after_created_at)
                | ((ResearchRun.created_at == after_created_at) & (ResearchRun.id < after_id))
            )
        return list(
            self._session.scalars(
                stmt.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc()).limit(limit)
            )
        )

    def cancel_run(self, run: ResearchRun) -> bool:
        if run.status not in {"running", "queued", "waiting_for_review"}:
            return False
        run.status = "cancelled"
        run.stage = "stopped"
        if run.stop_reason is None:
            run.stop_reason = "cancelled"
        run.updated_at = _utcnow()
        return True

    def update_run(
        self,
        run: ResearchRun,
        *,
        status: str | None = None,
        stage: str | None = None,
        round: int | None = None,
        budget_used: int | None = None,
        stop_reason: str | None = None,
    ) -> None:
        if status is not None:
            run.status = status
        if stage is not None:
            run.stage = stage
        if round is not None:
            run.round = round
        if budget_used is not None:
            run.budget_used = budget_used
        if stop_reason is not None:
            run.stop_reason = stop_reason
        run.updated_at = _utcnow()

    def create_task(
        self,
        *,
        run_id: uuid.UUID,
        research_case_id: uuid.UUID,
        thesis_id: uuid.UUID | None = None,
        task_type: str,
        query: str,
        round: int = 1,
    ) -> ResearchTask:
        task = ResearchTask(
            run_id=run_id,
            research_case_id=research_case_id,
            thesis_id=thesis_id,
            task_type=task_type,
            query=query,
            round=round,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get_task(self, task_id: uuid.UUID) -> ResearchTask | None:
        return self._session.get(ResearchTask, task_id)

    def update_task(
        self,
        task: ResearchTask,
        *,
        status: str | None = None,
        stage: str | None = None,
        evidence_count: int | None = None,
        gap_reason: str | None = None,
        result: dict | None = None,
    ) -> None:
        if status is not None:
            task.status = status
        if stage is not None:
            task.stage = stage
        if evidence_count is not None:
            task.evidence_count = evidence_count
        if gap_reason is not None:
            task.gap_reason = gap_reason
        if result is not None:
            task.result = result
        task.updated_at = _utcnow()

    def tasks_for_run(self, run_id: uuid.UUID) -> list[ResearchTask]:
        return list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run_id)
                .order_by(ResearchTask.created_at)
            )
        )

    def count_by_type(self, run_id: uuid.UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(ResearchTask.task_type, func.count())
            .where(ResearchTask.run_id == run_id)
            .group_by(ResearchTask.task_type)
        ).all()
        return {row[0]: row[1] for row in rows}

    def evidence_link_counts_by_thesis(self, research_case_id: uuid.UUID) -> dict[str, dict[str, int]]:
        """Count formal evidence links (not proposals) per thesis by role, deduped by link id.

        Returns a mapping thesis_id -> {'support': int, 'contradict': int, 'context': int}.
        """
        from app.models.ledger import EvidenceLink
        from app.models.proposals import Proposal

        # Only count formal EvidenceLinks; proposals are not counted.
        from app.models.ledger import Thesis

        rows = self._session.execute(
            select(EvidenceLink.thesis_id, EvidenceLink.role, func.count(func.distinct(EvidenceLink.id)))
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(Thesis.research_case_id == research_case_id)
            .group_by(EvidenceLink.thesis_id, EvidenceLink.role)
        ).all()
        result: dict[str, dict[str, int]] = {}
        for thesis_id, role, count in rows:
            tid = str(thesis_id)
            if tid not in result:
                result[tid] = {"support": 0, "contradict": 0, "context": 0}
            role_key = {
                "supports": "support",
                "support": "support",
                "contradicts": "contradict",
                "contradict": "contradict",
                "contextualizes": "context",
                "context": "context",
            }.get(role)
            if role_key is not None:
                result[tid][role_key] = count
        return result

    def pending_proposal_hashes_for_thesis(self, thesis_id: uuid.UUID) -> set[str]:
        """Return content_hash values of pending evidence_link proposals for a thesis.

        Used for dedup before proposing during auto research.
        """
        from app.models.proposals import Proposal

        rows = self._session.execute(
            select(Proposal.content_hash)
            .where(Proposal.target_context["thesis_id"].as_string() == str(thesis_id))
            .where(Proposal.kind == "evidence_link")
            .where(Proposal.status == "pending")
            .where(Proposal.content_hash.isnot(None))
        ).all()
        return {row[0] for row in rows if row[0]}

    def queued_tasks_for_run(self, run_id: uuid.UUID, round: int) -> list[ResearchTask]:
        """Return queued tasks for a given run and round, ordered by creation."""
        return list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run_id)
                .where(ResearchTask.round == round)
                .where(ResearchTask.status == "queued")
                .order_by(ResearchTask.created_at)
            )
        )