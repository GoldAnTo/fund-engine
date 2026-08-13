"""Repository for automatic research runs and tasks."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session
from app.models.operational import ResearchRun, ResearchTask, Job, JobEvent, TaskItem


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
        scope_thesis_ids: list[str] | None = None,
        monitor_version_id: uuid.UUID | None = None,
    ) -> ResearchRun:
        run = ResearchRun(
            research_case_id=research_case_id,
            max_rounds=max_rounds,
            budget=budget,
            scope_thesis_ids=scope_thesis_ids,
            monitor_version_id=monitor_version_id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def enqueue_run_job(self, run: ResearchRun) -> Job:
        """Persist the worker handoff in the operational jobs table."""
        job = Job(
            kind="research_run",
            status="queued",
            target_type="research_run",
            target_id=run.id,
            research_case_id=run.research_case_id,
            created_at=_utcnow(),
        )
        self._session.add(job)
        self._session.flush()
        self._append_job_event(job, status="queued", step="planning", message="research run queued")
        return job

    def job_for_run(self, run_id: uuid.UUID) -> Job | None:
        return self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run_id)
            .order_by(Job.created_at.desc())
            .limit(1)
        )

    def active_run_for_exact_thesis_scope(
        self, *, research_case_id: uuid.UUID, thesis_id: uuid.UUID
    ) -> ResearchRun | None:
        """Return a queued/in-flight run for precisely one thesis.

        A factor-level replenishment is intentionally a one-thesis run.  Do
        the JSON comparison in Python so the rule is identical in SQLite and
        PostgreSQL, then let callers reject a second click before it creates a
        duplicate worker job.
        """
        target_scope = [str(thesis_id)]
        runs = self._session.scalars(
            select(ResearchRun)
            .where(ResearchRun.research_case_id == research_case_id)
            .where(ResearchRun.status.in_(["queued", "running", "waiting_for_review"]))
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        )
        return next(
            (run for run in runs if run.scope_thesis_ids == target_scope),
            None,
        )

    def claim_next_run_job(self) -> Job | None:
        """Claim one queued job atomically; PostgreSQL workers skip each other."""
        stmt = (
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        job = self._session.scalar(stmt.limit(1))
        if job is None:
            return None
        job.status = "running"
        job.step = "extract"
        job.started_at = job.started_at or _utcnow()
        self._append_job_event(job, status="running", step="extract", message="worker claimed run")
        return job

    def recover_stale_run_jobs(self, *, before: datetime) -> int:
        """Requeue jobs abandoned by a dead worker after a conservative timeout."""
        jobs = list(
            self._session.scalars(
                select(Job)
                .where(Job.kind == "research_run")
                .where(Job.status == "running")
                .where(Job.started_at.is_not(None))
                .where(Job.started_at < before)
            )
        )
        for job in jobs:
            job.status = "queued"
            job.step = "recovered"
            job.error = "worker lease expired; requeued"
            self._append_job_event(job, status="queued", step="recovered", message=job.error)
        return len(jobs)

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
        for task in self._session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.status.in_(("queued", "running")))
        ):
            task.status = "cancelled"
            task.stage = "stopped"
            task.updated_at = _utcnow()
        job = self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run.id)
            .order_by(Job.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if job is not None and job.status not in {"succeeded", "failed", "cancelled"}:
            job.cancel_requested = True
            job.status = "cancelled"
            job.finished_at = _utcnow()
            self._append_job_event(job, status="cancelled", step="stopped", message="cancel requested")
        return True

    def record_job_completion(self, job: Job, *, status: str, step: str, error: str | None = None) -> None:
        job.status = status
        job.step = step
        job.error = error
        job.finished_at = _utcnow()
        self._append_job_event(job, status=status, step=step, message=error or "worker finished")

    def resume_after_claim_review(self, run: ResearchRun) -> bool:
        """Requeue the same frozen run after its atomic-claim gate is cleared.

        The previous pause remains in ``research_run_events`` and ``job_events``.
        We only reopen operational work that was explicitly blocked by that gate;
        a budget stop or any other review state must not be silently resumed.
        """
        if not (
            run.status == "waiting_for_review"
            and run.stop_reason == "pending_atomic_claim_review"
        ):
            return False
        job = self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run.id)
            .order_by(Job.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if job is not None and job.status != "waiting_for_review":
            # A concurrent worker/administrator action won the state change;
            # do not overwrite it or create a duplicate job.
            return False
        run.status = "queued"
        run.stage = "resume_after_claim_review"
        run.stop_reason = None
        # Resume the interrupted round so its blocked support/contradict/result
        # tasks are actually eligible for the worker again.
        run.round = max(0, (run.round or 1) - 1)
        run.updated_at = _utcnow()
        for task in self._session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.status == "blocked")
            .where(ResearchTask.stage == "claim_review")
        ):
            task.status = "queued"
            task.stage = "planning"
            task.updated_at = _utcnow()
        if job is None:
            self.enqueue_run_job(run)
        else:
            job.status = "queued"
            job.step = "resume_after_claim_review"
            job.error = None
            job.finished_at = None
            job.attempt += 1
            self._append_job_event(
                job,
                status="queued",
                step="resume_after_claim_review",
                message="atomic claim review completed; run requeued",
            )
        return True

    def _append_job_event(self, job: Job, *, status: str, step: str, message: str) -> None:
        previous = self._session.scalar(
            select(func.max(JobEvent.seq)).where(JobEvent.job_id == job.id)
        )
        self._session.add(JobEvent(
            job_id=job.id,
            seq=int(previous or 0) + 1,
            status=status,
            step=step,
            message=message,
            created_at=_utcnow(),
        ))

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
