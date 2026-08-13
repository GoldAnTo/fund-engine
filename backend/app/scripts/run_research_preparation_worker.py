"""Durable worker for review-gated research preparation jobs.

This worker is intentionally separate from ``run_research_worker``: it only
consumes ``prepare_research`` jobs and never creates or advances ResearchRuns.
"""
from __future__ import annotations

import argparse
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.env import load_local_env

load_local_env()

from app.ai.research_preparation import (
    PreparationInputUnavailableError,
    PREPARATION_INPUT_UNAVAILABLE_MESSAGE,
    PREPARATION_PROVIDER_ERROR_MESSAGE,
    ResearchPreparationGenerator,
    ResearchPreparationProviderError,
    load_preparation_input,
)
from app.db import SessionLocal
from app.errors import ConflictError, NotFoundError
from app.models.operational import Job
from app.models.research_preparation import ResearchPreparation
from app.repositories.research_preparation import ResearchPreparationRepository
from app.services.research_preparation import ResearchPreparationService
from app.services.research_worker_heartbeat import WorkerHeartbeatService


MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 600)
_SAFE_INTERNAL_ERROR = "preparation worker internal error"
_SAFE_STALE_MESSAGE = "stale preparation output discarded"
_STEPS = frozenset({"parse_claims", "draft_protocol", "draft_evidence_plan"})


@dataclass(frozen=True, slots=True)
class _JobInput:
    case_id: uuid.UUID
    preparation_id: uuid.UUID
    version: int
    step: str
    fingerprint: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    return os.getenv(
        "RESEARCH_PREPARATION_WORKER_ID", f"{socket.gethostname()}-preparation"
    )[:128]


def _touch(*, mode: str, state: str, session_factory=None) -> None:
    session_factory = session_factory or SessionLocal
    with session_factory() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=_worker_id(), mode=mode, state=state,
            worker_kind="research_preparation",
        )
        session.commit()


def _correlation_job_input(job: Job, preparation: ResearchPreparation) -> _JobInput | None:
    """Accept only a canonical worker correlation id (or a known step)."""
    if job.target_type != "research_preparation" or job.target_id != preparation.id:
        return None
    if job.research_case_id != preparation.research_case_id:
        return None
    if job.correlation_id:
        raw = job.correlation_id.split(":")
        if len(raw) != 3:
            return None
        try:
            preparation_id, version = uuid.UUID(raw[0]), int(raw[1])
        except (ValueError, TypeError):
            return None
        step = raw[2]
        if preparation_id != preparation.id or version < 1 or step not in _STEPS:
            return None
    elif job.step in _STEPS:
        version, step = preparation.version, job.step
    else:
        return None
    return _JobInput(
        case_id=preparation.research_case_id,
        preparation_id=preparation.id,
        version=version,
        step=step,
        fingerprint=preparation.input_fingerprint,
    )


def _locked_job(session: Session, job_id: uuid.UUID) -> Job | None:
    return session.scalar(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True)
    )


def _cancel(repo: ResearchPreparationRepository, job: Job, *, step: str | None) -> None:
    repo.set_preparation_job_terminal(
        job, status="cancelled", step=step, error=_SAFE_STALE_MESSAGE
    )


def _discard_output(
    service: ResearchPreparationService,
    repo: ResearchPreparationRepository,
    job: Job,
    input: _JobInput,
    *,
    reason: Literal["version_changed", "input_changed", "candidate_context_changed", "cancelled", "step_no_longer_eligible"],
) -> None:
    service.record_worker_output_discarded(
        input.case_id,
        input.step,  # type: ignore[arg-type]
        job_id=job.id,
        reason=reason,
    )
    _cancel(repo, job, step=input.step)


def _fresh_job_reason(
    job: Job, preparation: ResearchPreparation, input: _JobInput
) -> Literal["version_changed", "input_changed", "cancelled"] | None:
    if job.cancel_requested:
        return "cancelled"
    if preparation.version != input.version or _correlation_job_input(job, preparation) is None:
        return "version_changed"
    if preparation.input_fingerprint != input.fingerprint:
        return "input_changed"
    return None


def _provider_failure(
    session_factory, *, job_id: uuid.UUID, input: _JobInput
) -> None:
    """Persist retry state in a clean UoW after a provider boundary failure."""
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        job = _locked_job(session, job_id)
        if job is None or job.status != "running":
            session.commit()
            return
        preparation = repo.lock_for_case(input.case_id)
        if preparation is None:
            _cancel(repo, job, step=input.step)
            session.commit()
            return
        reason = _fresh_job_reason(job, preparation, input)
        if reason is not None:
            _discard_output(ResearchPreparationService(session), repo, job, input, reason=reason)
            session.commit()
            return
        service = ResearchPreparationService(session)
        if job.attempt >= MAX_ATTEMPTS:
            service.mark_step_failed(
                input.case_id, input.step, error_code="retry_exhausted", retry_at=None
            )
            repo.set_preparation_job_terminal(
                job, status="failed", step=input.step, error=PREPARATION_PROVIDER_ERROR_MESSAGE
            )
        else:
            retry_at = _utcnow() + timedelta(seconds=BACKOFF_SECONDS[job.attempt - 1])
            service.mark_step_failed(
                input.case_id, input.step, error_code="provider_unavailable", retry_at=retry_at
            )
            repo.requeue_preparation_job(
                job, step=input.step, error=PREPARATION_PROVIDER_ERROR_MESSAGE
            )
        session.commit()


def _input_unavailable_failure(
    session_factory, *, job_id: uuid.UUID, input: _JobInput
) -> None:
    """Terminate a source-governance input race without leaving a running Job."""
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        job = _locked_job(session, job_id)
        if job is None or job.status != "running":
            session.commit()
            return
        preparation = repo.lock_for_case(input.case_id)
        if preparation is None:
            _cancel(repo, job, step=input.step)
        else:
            reason = _fresh_job_reason(job, preparation, input)
            if reason is not None:
                _discard_output(ResearchPreparationService(session), repo, job, input, reason=reason)
            else:
                ResearchPreparationService(session).mark_step_failed(
                    input.case_id,
                    input.step,  # type: ignore[arg-type]
                    error_code="provider_unavailable",
                    retry_at=None,
                )
                repo.set_preparation_job_terminal(
                    job,
                    status="failed",
                    step=input.step,
                    error=PREPARATION_INPUT_UNAVAILABLE_MESSAGE,
                )
        session.commit()


def _internal_failure(session_factory, *, job_id: uuid.UUID, step: str | None) -> None:
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        job = _locked_job(session, job_id)
        if job is not None and job.status in {"queued", "running"}:
            repo.set_preparation_job_terminal(job, status="failed", step=step, error=_SAFE_INTERNAL_ERROR)
        session.commit()


def _begin(session: Session, job: Job) -> _JobInput | None:
    repo = ResearchPreparationRepository(session)
    if job.target_type != "research_preparation" or job.target_id is None or job.research_case_id is None:
        repo.set_preparation_job_terminal(job, status="failed", step=None, error=_SAFE_INTERNAL_ERROR)
        return None
    try:
        preparation = repo.lock_for_case(job.research_case_id)
    except NotFoundError:
        repo.set_preparation_job_terminal(job, status="failed", step=None, error=_SAFE_INTERNAL_ERROR)
        return None
    if preparation is None:
        repo.set_preparation_job_terminal(job, status="failed", step=None, error=_SAFE_INTERNAL_ERROR)
        return None
    input = _correlation_job_input(job, preparation)
    if input is None:
        repo.set_preparation_job_terminal(job, status="failed", step=None, error=_SAFE_INTERNAL_ERROR)
        return None
    if job.cancel_requested:
        _discard_output(ResearchPreparationService(session), repo, job, input, reason="cancelled")
        return None
    try:
        ResearchPreparationService(session).start_system_step(
            input.case_id,
            input.step,  # type: ignore[arg-type]
            expected_version=input.version,
            expected_fingerprint=input.fingerprint,
        )
    except ConflictError:
        if preparation.version != input.version:
            reason = "version_changed"
        elif preparation.input_fingerprint != input.fingerprint:
            reason = "input_changed"
        else:
            reason = "step_no_longer_eligible"
        _discard_output(ResearchPreparationService(session), repo, job, input, reason=reason)
        return None
    job.step = input.step
    session.flush()
    return input


def _ready_for_provider(session_factory, *, job_id: uuid.UUID, input: _JobInput) -> bool:
    """Give a just-cancelled Job one last lock-protected off-ramp."""
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        job = _locked_job(session, job_id)
        if job is None or job.status != "running":
            session.commit()
            return False
        try:
            preparation = repo.lock_for_case(input.case_id)
        except NotFoundError:
            repo.set_preparation_job_terminal(job, status="failed", step=input.step, error=_SAFE_INTERNAL_ERROR)
            session.commit()
            return False
        if preparation is None:
            repo.set_preparation_job_terminal(job, status="failed", step=input.step, error=_SAFE_INTERNAL_ERROR)
            session.commit()
            return False
        reason = _fresh_job_reason(job, preparation, input)
        if reason is not None:
            _discard_output(ResearchPreparationService(session), repo, job, input, reason=reason)
            session.commit()
            return False
        session.commit()
        return True


def _complete(
    session_factory,
    *,
    job_id: uuid.UUID,
    input: _JobInput,
    generator: object,
    loaded_input: object,
    output: object,
) -> None:
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        job = _locked_job(session, job_id)
        if job is None or job.status != "running":
            session.commit()
            return
        preparation = repo.lock_for_case(input.case_id)
        if preparation is None:
            _cancel(repo, job, step=input.step)
            session.commit()
            return
        reason = _fresh_job_reason(job, preparation, input)
        if reason is not None:
            _discard_output(ResearchPreparationService(session), repo, job, input, reason=reason)
            session.commit()
            return
        service = ResearchPreparationService(session)
        if input.step != "parse_claims":
            if getattr(loaded_input, "candidate_context_fingerprint") != service.current_candidate_context_fingerprint(input.case_id):
                _discard_output(service, repo, job, input, reason="candidate_context_changed")
                session.commit()
                return
            payload = output
        else:
            def guard() -> bool:
                return (
                    job.status == "running"
                    and not job.cancel_requested
                    and preparation.version == input.version
                    and preparation.input_fingerprint == input.fingerprint
                    and preparation.parse_claims_state == "running"
                )

            # Persistence is deliberately only available after this locked,
            # post-provider guard.  Use the real persistence adapter even
            # when a test injects a draft-only generator.
            persister = generator if hasattr(generator, "persist_claim_drafts") else ResearchPreparationGenerator()
            payload = persister.persist_claim_drafts(loaded_input, output, session, before_persist=guard)
            if payload is None:
                _discard_output(service, repo, job, input, reason="cancelled")
                session.commit()
                return
        completed = service.complete_system_step(
            input.case_id,
            input.step,  # type: ignore[arg-type]
            payload,
            expected_version=input.version,
            expected_fingerprint=input.fingerprint,
            expected_context_fingerprint=(
                None if input.step == "parse_claims" else getattr(loaded_input, "candidate_context_fingerprint")
            ),
        )
        if completed.version != input.version or getattr(completed, f"{input.step}_state") != "succeeded":
            _discard_output(service, repo, job, input, reason="version_changed")
        else:
            repo.set_preparation_job_terminal(job, status="succeeded", step=input.step)
        session.commit()


def run_once(
    *,
    recover_after_minutes: int = 30,
    session_factory=SessionLocal,
    generator_factory: Callable[[], object] = ResearchPreparationGenerator,
) -> bool:
    """Claim and execute one preparation job; return whether one was found."""
    with session_factory() as session:
        repo = ResearchPreparationRepository(session)
        repo.recover_stale_preparation_jobs(
            before=_utcnow() - timedelta(minutes=recover_after_minutes)
        )
        cancelled = repo.cancel_queued_preparation_jobs()
        for cancelled_job in cancelled:
            if (
                cancelled_job.target_type != "research_preparation"
                or cancelled_job.target_id is None
                or cancelled_job.research_case_id is None
            ):
                continue
            try:
                preparation = repo.lock_for_case(cancelled_job.research_case_id)
            except NotFoundError:
                continue
            if preparation is None:
                continue
            cancelled_input = _correlation_job_input(cancelled_job, preparation)
            if cancelled_input is not None:
                ResearchPreparationService(session).record_worker_output_discarded(
                    cancelled_input.case_id,
                    cancelled_input.step,  # type: ignore[arg-type]
                    job_id=cancelled_job.id,
                    reason="cancelled",
                )
        job = repo.claim_next_preparation_job()
        if job is None:
            session.commit()
            return bool(cancelled)
        job_id = job.id
        input = _begin(session, job)
        session.commit()  # publish claim/state before any provider wait
    if input is None:
        return True

    generator = generator_factory()
    try:
        with session_factory() as session:
            loaded_input = load_preparation_input(session, input.case_id)
        if not _ready_for_provider(session_factory, job_id=job_id, input=input):
            return True
        with session_factory() as session:
            if input.step == "parse_claims":
                output = generator.validate_claim_drafts(loaded_input)
            elif input.step == "draft_protocol":
                output = generator.draft_protocol(loaded_input)
            else:
                output = generator.draft_evidence_plan(loaded_input)
    except ResearchPreparationProviderError:
        _provider_failure(session_factory, job_id=job_id, input=input)
        return True
    except PreparationInputUnavailableError:
        _input_unavailable_failure(session_factory, job_id=job_id, input=input)
        return True
    except Exception:
        _internal_failure(session_factory, job_id=job_id, step=input.step)
        raise

    try:
        _complete(
            session_factory,
            job_id=job_id,
            input=input,
            generator=generator,
            loaded_input=loaded_input,
            output=output,
        )
    except Exception:
        # The output UoW is rolled back by its context manager.  Record only
        # a fixed operational error in a fresh transaction, then retain the
        # formal worker convention of surfacing programmer failures.
        _internal_failure(session_factory, job_id=job_id, step=input.step)
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute persisted research-preparation jobs")
    parser.add_argument("--once", action="store_true", help="claim at most one preparation job and exit")
    parser.add_argument("--loop", action="store_true", help="poll preparation jobs until interrupted")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if not args.once and not args.loop:
        parser.error("choose --once or --loop")
    if args.once:
        _touch(mode="once", state="executing")
        run_once()
        _touch(mode="once", state="idle")
        return
    while True:
        _touch(mode="loop", state="polling")
        found = run_once()
        _touch(mode="loop", state="polling")
        if not found:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
