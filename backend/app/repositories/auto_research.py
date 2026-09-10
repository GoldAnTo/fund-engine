"""Repository for automatic research runs and tasks."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session
from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun, ResearchTask, Job, JobEvent
from app.services.case_monitor import ResearchRunEventRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ResearchRunJobClaim:
    """Immutable server-issued identity for one worker attempt."""

    job_id: uuid.UUID
    research_run_id: uuid.UUID
    research_case_id: uuid.UUID
    claimed_attempt: int


@dataclass(frozen=True, slots=True)
class _ResearchRunJobCandidate:
    job_id: uuid.UUID
    research_run_id: uuid.UUID
    research_case_id: uuid.UUID
    attempt: int


class LostResearchRunLeaseError(RuntimeError):
    """Raised when a worker tries to persist after its attempt was replaced."""


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
        status: str = "queued",
        stage: str = "planning",
    ) -> ResearchRun:
        run = ResearchRun(
            research_case_id=research_case_id,
            status=status,
            stage=stage,
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

    def get_run_for_update(self, run_id: uuid.UUID) -> ResearchRun | None:
        case_id = self._session.scalar(
            select(ResearchRun.research_case_id).where(ResearchRun.id == run_id)
        )
        if case_id is None:
            return None
        self._session.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == case_id)
            .with_for_update()
        )
        return self._session.scalar(
            select(ResearchRun)
            .where(
                ResearchRun.id == run_id,
                ResearchRun.research_case_id == case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def enqueue_run_job(self, run: ResearchRun) -> Job:
        """Persist one idempotent handoff under Case -> Run -> Job locks."""
        locked_run = self.get_run_for_update(run.id)
        if locked_run is None:
            raise ValueError(f"research run {run.id} not found")
        existing = self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == locked_run.id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_run.status == "prepared":
            locked_run.status = "queued"
            locked_run.stage = "planning"
            locked_run.updated_at = _utcnow()
        if existing is not None:
            return existing
        job = Job(
            kind="research_run",
            status="queued",
            target_type="research_run",
            target_id=locked_run.id,
            research_case_id=locked_run.research_case_id,
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

    def claim_next_run_job(self) -> ResearchRunJobClaim | None:
        """Claim one queued job in stable Case -> Run -> Job lock order."""
        rows = self._session.execute(
            select(
                Job.id,
                Job.target_id,
                Job.research_case_id,
                Job.attempt,
            )
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
        )
        for job_id, run_id, case_id, attempt in rows:
            if case_id is None or run_id is None:
                raise ValueError("research run job has incomplete ownership")
            claim = self._claim_job(
                _ResearchRunJobCandidate(
                    job_id=job_id,
                    research_run_id=run_id,
                    research_case_id=case_id,
                    attempt=attempt,
                ),
                skip_locked_case=True,
            )
            if claim is not None:
                return claim
        return None

    def _claim_job(
        self,
        candidate: _ResearchRunJobCandidate,
        *,
        skip_locked_case: bool,
    ) -> ResearchRunJobClaim | None:
        case_statement = select(ResearchCase).where(
            ResearchCase.id == candidate.research_case_id
        )
        if (
            skip_locked_case
            and self._session.bind is not None
            and self._session.bind.dialect.name == "postgresql"
        ):
            case_statement = case_statement.with_for_update(skip_locked=True)
        else:
            case_statement = case_statement.with_for_update()
        case = self._session.scalar(case_statement)
        if case is None:
            return None
        run = self._session.scalar(
            select(ResearchRun)
            .where(
                ResearchRun.id == candidate.research_run_id,
                ResearchRun.research_case_id == candidate.research_case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = self._session.scalar(
            select(Job)
            .where(
                Job.id == candidate.job_id,
                Job.kind == "research_run",
                Job.target_type == "research_run",
                Job.target_id == candidate.research_run_id,
                Job.research_case_id == candidate.research_case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            return None
        if job.status != "queued" or job.attempt != candidate.attempt:
            return None
        if job.research_case_id is None or job.target_id is None:
            raise ValueError("research run job has incomplete ownership")
        if run is None or run.status != "queued":
            actual_status = run.status if run is not None else "missing"
            rejected = self._session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.kind == "research_run",
                    Job.status == "queued",
                    Job.attempt == candidate.attempt,
                )
                .values(
                    status="failed",
                    step="state_mismatch",
                    error=(
                        "research run is not claimable "
                        f"(status={actual_status})"
                    ),
                    finished_at=_utcnow(),
                )
                .execution_options(synchronize_session="fetch")
            )
            if rejected.rowcount == 1:
                self._append_job_event(
                    job,
                    status="failed",
                    step="state_mismatch",
                    message=job.error,
                )
            return None
        claimed_at = _utcnow()
        with self._session.no_autoflush:
            claimed = self._session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.kind == "research_run",
                    Job.target_type == "research_run",
                    Job.target_id == job.target_id,
                    Job.research_case_id == job.research_case_id,
                    Job.status == "queued",
                    Job.attempt == candidate.attempt,
                )
                .values(
                    status="running",
                    step="extract",
                    error=None,
                    started_at=claimed_at,
                    finished_at=None,
                )
                .execution_options(synchronize_session="fetch")
            )
        if claimed.rowcount != 1:
            return None
        self._append_job_event(
            job,
            status="running",
            step="extract",
            message="worker claimed run",
        )
        return ResearchRunJobClaim(
            job_id=job.id,
            research_run_id=job.target_id,
            research_case_id=job.research_case_id,
            claimed_attempt=candidate.attempt,
        )

    def claim_run_job(self, run_id: uuid.UUID) -> ResearchRunJobClaim | None:
        """Server-side claim used by synchronous service callers and tests."""
        row = self._session.execute(
            select(
                Job.id,
                Job.target_id,
                Job.research_case_id,
                Job.attempt,
            )
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run_id)
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
            .limit(1)
        ).first()
        if row is None:
            return None
        job_id, target_id, case_id, attempt = row
        if target_id is None or case_id is None:
            raise ValueError("research run job has incomplete ownership")
        return self._claim_job(
            _ResearchRunJobCandidate(
                job_id=job_id,
                research_run_id=target_id,
                research_case_id=case_id,
                attempt=attempt,
            ),
            skip_locked_case=False,
        )

    def lock_active_claim(
        self,
        claim: ResearchRunJobClaim,
        *,
        task_id: uuid.UUID | None = None,
        touch_heartbeat: bool = False,
    ) -> tuple[ResearchRun, Job, ResearchTask | None] | None:
        """Lock and validate one attempt before any worker-owned write.

        The stable Case -> Run -> Job -> Task order is shared with stale
        recovery and terminalization.  Returning ``None`` is a typed lease
        loss at the service boundary; callers must roll back their transaction.
        """
        with self._session.no_autoflush:
            case = self._session.scalar(
                select(ResearchCase)
                .where(ResearchCase.id == claim.research_case_id)
                .with_for_update()
            )
            run = self._session.scalar(
                select(ResearchRun)
                .where(
                    ResearchRun.id == claim.research_run_id,
                    ResearchRun.research_case_id == claim.research_case_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            job = self._session.scalar(
                select(Job)
                .where(
                    Job.id == claim.job_id,
                    Job.kind == "research_run",
                    Job.target_type == "research_run",
                    Job.target_id == claim.research_run_id,
                    Job.research_case_id == claim.research_case_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if (
            case is None
            or run is None
            or job is None
        ):
            return None
        heartbeat_at = _utcnow()
        heartbeat_value = heartbeat_at if touch_heartbeat else Job.started_at
        with self._session.no_autoflush:
            guarded = self._session.execute(
                update(Job)
                .where(
                    Job.id == claim.job_id,
                    Job.kind == "research_run",
                    Job.target_type == "research_run",
                    Job.target_id == claim.research_run_id,
                    Job.research_case_id == claim.research_case_id,
                    Job.status == "running",
                    Job.cancel_requested.is_(False),
                    Job.finished_at.is_(None),
                    Job.attempt == claim.claimed_attempt,
                )
                .values(started_at=heartbeat_value)
                .execution_options(synchronize_session="fetch")
            )
            if guarded.rowcount != 1:
                return None
            task = None
            if task_id is not None:
                task = self._session.scalar(
                    select(ResearchTask)
                    .where(
                        ResearchTask.id == task_id,
                        ResearchTask.run_id == claim.research_run_id,
                        ResearchTask.research_case_id == claim.research_case_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
        if task_id is not None and task is None:
            return None
        return run, job, task

    def recover_stale_run_jobs(self, *, before: datetime) -> int:
        """Atomically requeue one dead worker's Job, Run, and running tasks."""
        candidates = list(
            self._session.execute(
                select(Job.id, Job.research_case_id, Job.target_id)
                .where(Job.kind == "research_run")
                .where(Job.target_type == "research_run")
                .where(Job.status == "running")
                .where(Job.cancel_requested.is_(False))
                .where(Job.started_at.is_not(None))
                .where(Job.started_at < before)
                .where(Job.finished_at.is_(None))
                .order_by(Job.created_at, Job.id)
            )
        )
        recovered = 0
        for job_id, case_id, run_id in candidates:
            if case_id is None or run_id is None:
                job = self._lock_stale_job(job_id, before=before)
                if job is not None:
                    self._reject_stale_recovery(job)
                continue

            run_case_id = self._session.scalar(
                select(ResearchRun.research_case_id).where(ResearchRun.id == run_id)
            )
            if run_case_id != case_id:
                job = self._lock_stale_job(job_id, before=before)
                if job is not None:
                    self._reject_stale_recovery(job)
                continue

            self._session.scalar(
                select(ResearchCase)
                .where(ResearchCase.id == case_id)
                .with_for_update()
            )
            run = self._session.scalar(
                select(ResearchRun)
                .where(
                    ResearchRun.id == run_id,
                    ResearchRun.research_case_id == case_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            job = self._lock_stale_job(job_id, before=before)
            if run is None or job is None:
                continue
            tasks = list(
                self._session.scalars(
                    select(ResearchTask)
                    .where(ResearchTask.run_id == run.id)
                    .order_by(ResearchTask.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if any(task.research_case_id != case_id for task in tasks):
                self._reject_stale_recovery(job)
                continue
            if run.status not in {"queued", "running"}:
                continue

            recovered_error = "worker lease expired; run and running tasks requeued"
            next_attempt = job.attempt + 1
            with self._session.no_autoflush:
                recovery_won = self._session.execute(
                    update(Job)
                    .where(
                        Job.id == job.id,
                        Job.kind == "research_run",
                        Job.target_type == "research_run",
                        Job.target_id == run.id,
                        Job.research_case_id == case_id,
                        Job.status == "running",
                        Job.cancel_requested.is_(False),
                        Job.started_at.is_not(None),
                        Job.started_at < before,
                        Job.finished_at.is_(None),
                        Job.attempt == job.attempt,
                    )
                    .values(
                        status="queued",
                        step="recovered",
                        error=recovered_error,
                        attempt=next_attempt,
                        started_at=None,
                        finished_at=None,
                    )
                    .execution_options(synchronize_session="fetch")
                )
            if recovery_won.rowcount != 1:
                continue

            running_tasks = [task for task in tasks if task.status == "running"]
            interrupted_round = min(
                (task.round for task in running_tasks),
                default=max(1, run.round),
            )
            run.status = "queued"
            run.stage = "planning"
            run.round = min(run.round, max(0, interrupted_round - 1))
            run.stop_reason = None
            run.updated_at = _utcnow()
            for task in running_tasks:
                task.status = "queued"
                task.stage = "planning"
                task.updated_at = _utcnow()
            self._append_job_event(job, status="queued", step="recovered", message=job.error)
            recovered += 1
        return recovered

    def _lock_stale_job(self, job_id: uuid.UUID, *, before: datetime) -> Job | None:
        return self._session.scalar(
            select(Job)
            .where(
                Job.id == job_id,
                Job.kind == "research_run",
                Job.target_type == "research_run",
                Job.status == "running",
                Job.cancel_requested.is_(False),
                Job.started_at.is_not(None),
                Job.started_at < before,
                Job.finished_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _reject_stale_recovery(self, job: Job) -> None:
        job.status = "failed"
        job.step = "recovery_rejected"
        job.error = "research run ownership mismatch; recovery refused"
        job.finished_at = _utcnow()
        self._append_job_event(
            job,
            status="failed",
            step="recovery_rejected",
            message=job.error,
        )

    def requeue_failed_run(self, run_id: uuid.UUID) -> tuple[ResearchRun, Job]:
        """Legacy seam: schedule/requeue through the bounded default policy."""
        _state, run, job = self.reconcile_failed_run_retry(
            run_id,
            now=_utcnow(),
            policy_version="research-synthesis-retry-v1",
            max_failures=3,
            base_delay_seconds=60,
            max_delay_seconds=900,
        )
        return run, job

    def reconcile_failed_run_retry(
        self,
        run_id: uuid.UUID,
        *,
        now: datetime,
        policy_version: str,
        max_failures: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
    ) -> tuple[str, ResearchRun, Job]:
        """Persist a bounded retry schedule or atomically requeue when due."""
        run = self.get_run_for_update(run_id)
        if run is None:
            raise ValueError("research run not found")
        job = self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run.id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
            .with_for_update()
        )
        if job is None:
            raise ValueError("research run job not found")
        if run.status == "queued" and job.status == "queued":
            return "queued", run, job
        if run.status != "failed" or job.status != "failed":
            raise ValueError("only a failed research run can be reconciled")
        job.retry_policy_version = policy_version
        if job.failure_count >= max_failures:
            job.next_retry_at = None
            job.step = "retry_exhausted"
            self._session.flush()
            return "exhausted", run, job

        current_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        persisted_retry_at = job.next_retry_at
        if persisted_retry_at is None:
            exponent = max(0, job.failure_count - 1)
            delay_seconds = min(
                max_delay_seconds,
                base_delay_seconds * (2**exponent),
            )
            job.next_retry_at = current_now + timedelta(seconds=delay_seconds)
            job.step = "retry_scheduled"
            self._append_job_event(
                job,
                status="failed",
                step="retry_scheduled",
                message="failed synthesis retry scheduled",
            )
            self._session.flush()
            return "scheduled", run, job
        comparable_retry_at = (
            persisted_retry_at
            if persisted_retry_at.tzinfo is not None
            else persisted_retry_at.replace(tzinfo=timezone.utc)
        )
        if current_now < comparable_retry_at:
            return "scheduled", run, job

        run.status = "queued"
        run.stage = "planning"
        run.stop_reason = None
        run.budget_used = 0
        run.updated_at = _utcnow()
        for task in self._session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.status == "failed")
        ):
            task.status = "queued"
            task.stage = "planning"
            task.gap_reason = None
            task.updated_at = _utcnow()
        job.status = "queued"
        job.step = "retry"
        job.error = None
        job.attempt += 1
        job.next_retry_at = None
        job.started_at = None
        job.finished_at = None
        self._append_job_event(
            job,
            status="queued",
            step="retry",
            message="failed synthesis run requeued",
        )
        self._session.flush()
        return "queued", run, job

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
        locked_run = self.get_run_for_update(run.id)
        if locked_run is None:
            return False
        run = locked_run
        if run.status not in {
            "prepared",
            "running",
            "queued",
            "waiting_for_review",
        }:
            return False
        job = self._session.scalar(
            select(Job)
            .where(Job.kind == "research_run")
            .where(Job.target_type == "research_run")
            .where(Job.target_id == run.id)
            .order_by(Job.created_at.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
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
        if job is not None and job.status not in {"succeeded", "failed", "cancelled"}:
            job.cancel_requested = True
            job.status = "cancelled"
            job.finished_at = _utcnow()
            self._append_job_event(job, status="cancelled", step="stopped", message="cancel requested")
        return True

    def record_job_completion(
        self,
        claim: ResearchRunJobClaim,
        *,
        status: str,
        step: str,
        error: str | None = None,
        run: ResearchRun | None = None,
    ) -> bool:
        """Serialize terminal writes with Case -> Run -> Job cancellation."""
        desired_run = None
        if run is not None:
            desired_run = {
                "status": run.status,
                "stage": run.stage,
                "round": run.round,
                "budget_used": run.budget_used,
                "stop_reason": run.stop_reason,
            }
        current_run, current_job = self._lock_terminal_rows(
            claim=claim,
        )
        if current_job is None:
            self._session.rollback()
            return False
        if (
            current_job.attempt != claim.claimed_attempt
            or current_job.target_id != claim.research_run_id
            or current_job.research_case_id != claim.research_case_id
        ):
            self._session.rollback()
            return False
        if current_job.status in {"succeeded", "failed"}:
            # A different worker already won terminal ownership.  Nothing
            # from this worker's losing Run/event/handoff transaction may be
            # published by run_once's outer commit.
            self._session.rollback()
            return False
        if not self._acquire_terminal_claim_guard(claim):
            self._session.rollback()
            return False
        with self._session.no_autoflush:
            self._session.refresh(current_job)

        cancel_won = bool(
            current_job.cancel_requested or current_job.status == "cancelled"
        )
        if cancel_won and current_run is not None:
            # execute() leaves its final Run/event/lifecycle work uncommitted.
            # Discard that losing terminal branch, then reacquire the stable
            # lock order in a clean transaction before publishing cancellation.
            self._session.rollback()
            current_run, current_job = self._lock_terminal_rows(
                claim=claim,
            )
            if (
                current_job is None
                or current_job.attempt != claim.claimed_attempt
                or current_job.status in {"succeeded", "failed"}
            ):
                return False
            if not self._acquire_terminal_claim_guard(claim):
                self._session.rollback()
                return False
            with self._session.no_autoflush:
                self._session.refresh(current_job)
            if not (
                current_job.cancel_requested or current_job.status == "cancelled"
            ):
                return False
            self._cancel_locked_run(current_run)
            self._set_job_completion(
                current_job,
                status="cancelled",
                step="stopped",
                error=None,
            )
            return True

        if current_job.status == "cancelled" or current_job.cancel_requested:
            self._set_job_completion(
                current_job,
                status="cancelled",
                step="stopped",
                error=None,
            )
            return True

        if current_job.status != "running":
            self._session.rollback()
            return False

        if current_run is not None and desired_run is not None:
            current_run.status = str(desired_run["status"])
            current_run.stage = str(desired_run["stage"])
            current_run.round = int(desired_run["round"])
            current_run.budget_used = int(desired_run["budget_used"])
            current_run.stop_reason = desired_run["stop_reason"]
            current_run.updated_at = _utcnow()
        self._set_job_completion(
            current_job,
            status=status,
            step=step,
            error=error,
        )
        return True

    def _acquire_terminal_claim_guard(
        self,
        claim: ResearchRunJobClaim,
    ) -> bool:
        """Reserve the current attempt before publishing terminal state.

        PostgreSQL already holds the Job row lock.  The conditional no-op
        update provides the equivalent compare-and-swap guard for SQLite,
        where ``FOR UPDATE`` is ignored.
        """
        guarded = self._session.execute(
            update(Job)
            .where(
                Job.id == claim.job_id,
                Job.kind == "research_run",
                Job.target_type == "research_run",
                Job.target_id == claim.research_run_id,
                Job.research_case_id == claim.research_case_id,
                Job.status.in_(("running", "cancelled")),
                Job.attempt == claim.claimed_attempt,
            )
            .values(started_at=Job.started_at)
            .execution_options(synchronize_session="fetch")
        )
        return guarded.rowcount == 1

    def _lock_terminal_rows(
        self,
        *,
        claim: ResearchRunJobClaim,
    ) -> tuple[ResearchRun | None, Job | None]:
        with self._session.no_autoflush:
            self._session.scalar(
                select(ResearchCase)
                .where(ResearchCase.id == claim.research_case_id)
                .with_for_update()
            )
            current_run = self._session.scalar(
                select(ResearchRun)
                .where(
                    ResearchRun.id == claim.research_run_id,
                    ResearchRun.research_case_id == claim.research_case_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_job = self._session.scalar(
                select(Job)
                .where(
                    Job.id == claim.job_id,
                    Job.kind == "research_run",
                    Job.target_type == "research_run",
                    Job.target_id == claim.research_run_id,
                    Job.research_case_id == claim.research_case_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        return current_run, current_job

    def _cancel_locked_run(self, run: ResearchRun | None) -> None:
        if run is None:
            return
        run.status = "cancelled"
        run.stage = "stopped"
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
        ResearchRunEventRepository(self._session).append(
            run.id,
            stage="stopped",
            status="cancelled",
            message="研究任务在最终提交前收到取消请求。",
            payload_json={"stop_reason": "cancelled"},
        )

    def _set_job_completion(
        self,
        job: Job,
        *,
        status: str,
        step: str,
        error: str | None,
    ) -> None:
        if status == "failed":
            job.failure_count += 1
            job.next_retry_at = None
        job.status = status
        job.step = step
        job.error = error
        job.finished_at = _utcnow()
        self._append_job_event(
            job,
            status=status,
            step=step,
            message=error or "worker finished",
        )

    def resume_after_claim_review(self, run: ResearchRun) -> bool:
        """Requeue the same frozen run after its atomic-claim gate is cleared.

        The previous pause remains in ``research_run_events`` and ``job_events``.
        We only reopen operational work that was explicitly blocked by that gate;
        a budget stop or any other review state must not be silently resumed.
        """
        locked_run = self.get_run_for_update(run.id)
        if locked_run is None:
            return False
        run = locked_run
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
