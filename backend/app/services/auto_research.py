"""Persistent automatic research orchestration executed by a worker."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.ai.error_safety import (
    AI_COMPLIANCE_ERROR_MESSAGE,
    AI_COMPLIANCE_ERROR_TYPE,
    AI_OPERATION_ERROR_MESSAGE,
    AI_OPERATION_ERROR_TYPE,
)
from app.ai.extraction import StatementExtractor
from app.ai.proposal import EvidenceProposer
from app.errors import ValidationFailedError
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseDocumentVersion,
    AIAssessment,
    AtomicClaimReview,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.proposals import Proposal
from app.models.operational import Job, ResearchRun, ResearchTask, TaskItem
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent
from app.models.research_expression import KeyFactor
from app.models.event_research import EventResearchConclusion
from app.models.source_governance import SourceContract
from app.repositories.operational import TaskRepository
from app.repositories.auto_research import AutoResearchRepository
from app.repositories.event_research import EventResearchLifecycleRepository
from app.scripts.run_ai_engine import _pending_versions
from app.services.compliance import ComplianceRefusedError
from app.services.event_review_queue import EventReviewQueueService
from app.services.research_protocol import ResearchProtocolService
from app.services.case_monitor import ResearchRunEventRepository
from app.services.source_admission import source_contract_is_active
from app.services.event_research_scope_evidence import (
    lock_event_scope_case,
    lock_event_research_lifecycle,
)


class AutoResearchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AutoResearchRepository(session)
        self.task_repo = TaskRepository(session)
        self._client: LLMClient | None = None

    @property
    def client(self) -> LLMClient:
        """Create the model client only in the worker execution path.

        Queueing a run must remain durable and inspectable even when the
        worker's model endpoint or proxy is temporarily unavailable.  The
        worker records that execution-time failure on the run/job instead of
        making the researcher-facing command endpoint look like a no-op.
        """
        if self._client is None:
            self._client = LLMClient.from_env()
        return self._client

    def start(
        self,
        case_id: uuid.UUID,
        *,
        max_rounds: int = 3,
        budget: int = 100,
        auto_execute: bool = False,
        commit: bool = True,
        thesis_ids: list[uuid.UUID] | None = None,
        monitor_version_id: uuid.UUID | None = None,
        trigger: str = "manual",
        allowed_source_types: list[str] | None = None,
        scope_context: dict[str, object] | None = None,
    ):
        case = self.session.get(ResearchCase, case_id)
        if case is None:
            raise ValueError(f"research case {case_id} not found")
        monitor = None
        if monitor_version_id is not None:
            monitor = self.session.get(CaseMonitorVersion, monitor_version_id)
            if monitor is None or monitor.research_case_id != case_id:
                raise ValueError("case monitor version not found")
        elif thesis_ids is None:
            monitor = self.session.scalar(
                select(CaseMonitorVersion)
                .where(CaseMonitorVersion.research_case_id == case_id)
                .order_by(CaseMonitorVersion.version.desc())
                .limit(1)
            )
        if monitor is not None and thesis_ids is None:
            if trigger == "schedule" and monitor.status != "active":
                raise ValueError("scheduled research is paused for this case")
            thesis_ids = [uuid.UUID(value) for value in monitor.factor_ids]
        if allowed_source_types is not None:
            if monitor is None or not set(allowed_source_types).issubset(set(monitor.allowed_source_types)):
                raise ValueError("run sources must be a subset of the saved case monitor")
        thesis_stmt = select(Thesis).where(Thesis.research_case_id == case_id)
        if thesis_ids is not None:
            thesis_stmt = thesis_stmt.where(Thesis.id.in_(thesis_ids))
        theses = list(self.session.scalars(thesis_stmt))
        # A protocol-required thesis may not create a run merely because a
        # caller reached the run endpoint. The same immutable protocol gate
        # used by assessment generation is enforced at orchestration time.
        protocol = ResearchProtocolService(self.session)
        blocked_reasons: list[str] = []
        for thesis in theses:
            if not thesis.research_protocol_required:
                continue
            result = protocol.check_researchability(thesis.id)
            if result.status == "blocked":
                blocked_reasons.extend(result.reason_codes)
        if blocked_reasons:
            raise ValidationFailedError(
                "researchability gate blocked: " + ", ".join(sorted(set(blocked_reasons)))
            )
        run = self.repo.create_run(
            research_case_id=case_id,
            max_rounds=max(1, min(max_rounds, 3)),
            budget=max(1, budget),
            scope_thesis_ids=[str(thesis.id) for thesis in theses],
            monitor_version_id=monitor.id if monitor is not None else None,
        )
        scope_payload: dict[str, object] = {
            "trigger": trigger,
            "monitor_version_id": str(monitor.id) if monitor is not None else None,
            "factor_ids": [str(thesis.id) for thesis in theses],
            "factor_statements": [thesis.statement for thesis in theses],
            "allowed_source_types": allowed_source_types if allowed_source_types is not None else (monitor.allowed_source_types if monitor is not None else []),
            "budget": run.budget,
            "frequency": monitor.frequency if monitor is not None else None,
            "next_verification_event": monitor.next_verification_event if monitor is not None else None,
            "configured_by": monitor.changed_by if monitor is not None else None,
            "configuration_change_reason": monitor.change_reason if monitor is not None else None,
        }
        for key, value in (scope_context or {}).items():
            if key not in scope_payload:
                scope_payload[key] = value
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="scope",
            status="completed",
            message="已冻结本次运行范围",
            payload_json=scope_payload,
        )
        for thesis in theses:
            for task_type, label in (
                ("support", "寻找支持证据"),
                ("contradict", "寻找反方证据"),
                ("result", "形成研究结论"),
                ("alternative", "寻找替代解释"),
            ):
                self.repo.create_task(
                    run_id=run.id,
                    research_case_id=case_id,
                    thesis_id=thesis.id,
                    task_type=task_type,
                    query=f"{label}: {thesis.statement}",
                )
        self.repo.enqueue_run_job(run)
        # HTTP commands only persist a run + job.  A separately supervised
        # worker claims the job, so a provider timeout cannot hold an API
        # request open or lose the work on process restart.
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        # Kept as a wire-compatible argument while callers migrate.  Inline
        # execution is intentionally disabled even when an old client sends
        # auto_execute=true.
        del auto_execute
        return run

    def start_from_monitor(
        self,
        case_id: uuid.UUID,
        *,
        trigger: str = "manual",
        commit: bool = True,
        scope_context: dict[str, object] | None = None,
    ):
        """Create a run from one immutable CaseMonitor version.

        The interactive monitor entry point deliberately accepts no caller
        budget, factor, or source overrides.  Those values must be read from
        the saved version so the first scope event can be replayed exactly.
        A paused monitor still permits an explicit human run; only the
        scheduler is prevented from dispatching it.
        """
        monitor = self.session.scalar(
            select(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case_id)
            .order_by(CaseMonitorVersion.version.desc())
            .limit(1)
        )
        if monitor is None:
            raise ValueError("a saved case monitor is required before starting a monitor run")
        return self.start(
            case_id,
            max_rounds=3,
            budget=monitor.budget,
            monitor_version_id=monitor.id,
            trigger=trigger,
            commit=commit,
            scope_context=scope_context,
        )

    def start_from_key_factor(self, case_id: uuid.UUID, *, key_factor_id: uuid.UUID):
        factor = self.session.get(KeyFactor, key_factor_id)
        if factor is None or factor.research_case_id != case_id or factor.review_state != "reviewed" or factor.thesis_id is None:
            raise ValueError("reviewed key factor is not explicitly linked to this Case research scope")
        if (
            factor.verification_window_start is None
            or factor.verification_window_end is None
        ):
            raise ValueError(
                "key factor must have a frozen verification window before starting replenishment"
            )
        monitor = self.session.scalar(select(CaseMonitorVersion).where(CaseMonitorVersion.research_case_id == case_id).order_by(CaseMonitorVersion.version.desc()).limit(1))
        if monitor is None or str(factor.thesis_id) not in monitor.factor_ids:
            raise ValueError("linked key factor is not in the current CaseMonitor scope")
        source_types = [source for source in monitor.allowed_source_types if source in factor.allowed_source_types]
        if not source_types:
            raise ValueError("key factor has no allowed source shared with the current CaseMonitor")
        if self.repo.active_run_for_exact_thesis_scope(
            research_case_id=case_id, thesis_id=factor.thesis_id
        ) is not None:
            raise ValueError(
                "this key factor already has an active replenishment run; inspect or stop it before starting another"
            )
        return self.start(
            case_id,
            max_rounds=3,
            budget=monitor.budget,
            thesis_ids=[factor.thesis_id],
            monitor_version_id=monitor.id,
            trigger="factor_manual",
            allowed_source_types=source_types,
            scope_context={"requested_key_factor_id": str(factor.id)},
        )

    def resume_after_atomic_claim_review(
        self,
        candidate_id: uuid.UUID,
        *,
        reviewer: str,
    ) -> list[uuid.UUID]:
        """Resume only runs whose recorded atomic-claim gate is now clear."""
        document_id = self.session.scalar(
            select(SourceSpan.document_version_id)
            .where(SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .where(AtomicClaimCandidate.id == candidate_id)
        )
        if document_id is None:
            return []
        case_ids = list(
            self.session.scalars(
                select(CaseDocumentVersion.research_case_id)
                .where(CaseDocumentVersion.document_version_id == document_id)
                .distinct()
            )
        )
        resumed: list[uuid.UUID] = []
        run_ids: set[uuid.UUID] = set()
        for case_id in case_ids:
            # JSON candidate IDs are historical payload, so filter by the
            # indexed run state in SQL and parse only the projected event data.
            # A normalized run-output mapping can make this containment lookup
            # indexable in a later schema phase without changing semantics.
            events = self.session.execute(
                select(ResearchRunEvent.run_id, ResearchRunEvent.payload_json)
                .join(ResearchRun, ResearchRun.id == ResearchRunEvent.run_id)
                .where(ResearchRun.research_case_id == case_id)
                .where(ResearchRun.status == "waiting_for_review")
                .where(ResearchRun.stop_reason == "pending_atomic_claim_review")
                .where(ResearchRunEvent.stage == "claim_review")
            )
            for run_id, payload in events:
                if candidate_id in self._claim_candidate_ids(payload):
                    run_ids.add(run_id)
        for run_id in run_ids:
            run = self._lock_run_for_transition(run_id)
            if run is None or self._pending_atomic_claims_for_run(run):
                continue
            if not self.repo.resume_after_claim_review(run):
                continue
            ResearchRunEventRepository(self.session).append(
                run.id,
                stage="claim_review",
                status="completed",
                message="原子陈述审核已完成；原冻结范围已重新入队继续执行",
                payload_json={
                    "candidate_id": str(candidate_id),
                    "reviewer": reviewer.strip(),
                    "resume_from_round": run.round + 1,
                },
            )
            resumed.append(run.id)
        return resumed

    def continue_published_event(
        self,
        case_id: uuid.UUID,
        *,
        document_version_id: uuid.UUID,
        reason: str,
        triggered_by: str,
    ):
        """Start a successor run from a new, case-owned frozen document.

        The earlier published conclusion remains immutable.  The explicit
        document and reason become part of the successor run's frozen scope,
        which makes the reopening decision inspectable rather than a hidden
        status flip.
        """
        lifecycle = lock_event_research_lifecycle(self.session, case_id)
        if lifecycle is None or lifecycle.status != "published":
            raise ValidationFailedError("only a published event research case can start a material continuation")
        if not reason.strip() or not triggered_by.strip():
            raise ValidationFailedError("continuation reason and actor are required")
        source = self.session.scalar(
            select(CaseDocumentVersion)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(CaseDocumentVersion.document_version_id == document_version_id)
            .limit(1)
        )
        if source is None:
            raise ValidationFailedError("continuation document is not frozen in this case")
        contract = self.session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == document_version_id
            )
        )
        if (
            contract is None
            or not contract.allow_display
            or not contract.allow_ai_processing
            or not source_contract_is_active(contract)
        ):
            raise ValidationFailedError(
                "continuation document source contract does not permit research"
            )
        previous = self.session.scalar(
            select(EventResearchConclusion)
            .where(EventResearchConclusion.research_case_id == case_id)
            .where(EventResearchConclusion.state == "published")
            .order_by(EventResearchConclusion.created_at.desc())
            .limit(1)
        )
        if previous is None:
            raise ValidationFailedError("published lifecycle has no immutable published conclusion")
        run = self.start_from_monitor(
            case_id,
            trigger="material_continuation",
            commit=False,
            scope_context={
                "continuation_reason": reason.strip(),
                "triggered_by": triggered_by.strip(),
                "source_document_version_id": str(document_version_id),
                "previous_conclusion_id": str(previous.id),
            },
        )
        EventResearchLifecycleRepository(self.session).update(
            lifecycle,
            status="researching",
            active_run_id=run.id,
            current_round=0,
            summary="已记录新材料触发原因，开始新的受控补证周期",
            current_gap="新材料尚未经过原文与证据审核；此前发布结论保持不变",
            next_human_action=None,
        )
        return run

    def execute(self, run):
        self.repo.update_run(run, status="running", stage="extract")
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="retrieve",
            status="started",
            message="研究工作器已开始执行",
            payload_json={"round": run.round + 1, "budget": run.budget},
        )
        self.session.commit()
        used = run.budget_used or 0
        previous = self._run_evidence_count(run)
        failed = False
        extraction_failed = False
        extraction_cancelled = False
        allowed_source_types = self._run_allowed_source_types(run)
        for current_round in range(max(1, run.round + 1), run.max_rounds + 1):
            if self._is_cancelled(run):
                break
            run.round = current_round
            if used >= run.budget:
                self.repo.update_run(
                    run,
                    status=(
                        "failed"
                        if failed
                        else self._successful_terminal_status(run)
                    ),
                    stage="failed" if failed else "stopped",
                    budget_used=used,
                    stop_reason="task_failed" if failed else "budget_exhausted",
                )
                break
            for version in self._pending_versions_in_run_scope(
                run,
                allowed_source_types=allowed_source_types,
            ):
                if self._is_cancelled(run):
                    break
                if used >= run.budget:
                    break
                try:
                    extracted = StatementExtractor(self.client).extract(
                        version.id,
                        self.session,
                        before_persist=lambda: self._claim_extraction_output_slot(run),
                    )
                except ComplianceRefusedError:
                    used += 1
                    if not self._claim_extraction_output_slot(run):
                        self.session.rollback()
                        extraction_cancelled = True
                        self._is_cancelled(run)
                        break
                    extraction_failed = True
                    failed = True
                except Exception:
                    used += 1
                    if not self._claim_extraction_output_slot(run):
                        self.session.rollback()
                        extraction_cancelled = True
                        self._is_cancelled(run)
                        break
                    extraction_failed = True
                    failed = True
                else:
                    if extracted is None:
                        self.session.rollback()
                        extraction_cancelled = True
                        self._is_cancelled(run)
                        break
                    used += 1
                if extraction_failed:
                    self.repo.update_run(
                        run,
                        status="failed",
                        stage="failed",
                        budget_used=used,
                        stop_reason="task_failed",
                    )
                    break
                self.session.commit()
            if extraction_failed or extraction_cancelled or run.status == "cancelled":
                break
            pending_claims = self._pending_atomic_claims(
                run.research_case_id,
                allowed_source_types=allowed_source_types,
            )
            if pending_claims:
                self._pause_for_atomic_claim_review(run, pending_claims, used)
                break
            proposer, generator = EvidenceProposer(self.client), AssessmentGenerator(self.client)
            for task in self.repo.queued_tasks_for_run(run.id, current_round):
                if self._is_cancelled(run):
                    break
                if used >= run.budget:
                    break
                task.status, task.stage = "running", "research"
                # A provider call can take seconds.  Persist and end this
                # short task-state transaction before it starts, otherwise
                # PostgreSQL holds the ResearchTask row lock and prevents a
                # concurrent scope replacement from cancelling the run.
                self.session.commit()
                cancelled_during_task = False
                try:
                    if task.task_type in {"support", "contradict", "alternative"}:
                        proposed_ids = self._propose_for_task(
                            proposer,
                            task,
                            run,
                            allowed_source_types=allowed_source_types,
                        )
                    else:
                        assessment = generator.generate(
                            task.thesis_id,
                            datetime.now(timezone.utc),
                            self.session,
                            before_persist=lambda: self._claim_task_output_slot(run, task),
                        )
                    # Providers may return after their run was superseded.
                    # Do not flush their proposals/assessments or overwrite a
                    # task that the scope update has already cancelled.
                    if self._is_cancelled(run, task):
                        cancelled_during_task = True
                    elif task.task_type == "result" and assessment is None:
                        cancelled_during_task = True
                    elif task.task_type in {"support", "contradict", "alternative"}:
                        task.result = {
                            "task_type": task.task_type,
                            "proposed_proposal_ids": [str(item) for item in proposed_ids],
                        }
                        task.evidence_count = self._evidence_count(task.thesis_id)
                    else:
                        task.result = {
                            "task_type": task.task_type,
                            "assessment_id": str(assessment.id),
                            "conclusion": assessment.conclusion,
                            "gaps": assessment.gaps,
                        }
                        for gap in assessment.gaps:
                            self._create_gap_task(
                                run,
                                task.thesis_id,
                                "alternative",
                                str(gap),
                                current_round + 1,
                                "assessment_gap",
                            )
                    if not cancelled_during_task:
                        task.status, task.stage = "done", "completed"
                except ComplianceRefusedError:
                    if self._is_cancelled(run, task):
                        cancelled_during_task = True
                    else:
                        task.status, task.stage = "failed", "failed"
                        task.result = {
                            "task_type": task.task_type,
                            "error": AI_COMPLIANCE_ERROR_MESSAGE,
                            "error_type": AI_COMPLIANCE_ERROR_TYPE,
                        }
                        failed = True
                except Exception:
                    if self._is_cancelled(run, task):
                        cancelled_during_task = True
                    else:
                        task.status, task.stage = "failed", "failed"
                        task.result = {
                            "task_type": task.task_type,
                            "error": AI_OPERATION_ERROR_MESSAGE,
                            "error_type": AI_OPERATION_ERROR_TYPE,
                        }
                        failed = True
                finally:
                    # Re-read both the run and task at the final write
                    # boundary.  A scope replacement can land after the
                    # provider returned but before this worker commits.
                    if cancelled_during_task or self._is_cancelled(run, task):
                        # Roll back any provider side effects from this worker
                        # transaction.  The replacement command committed the
                        # durable cancelled run/task/job audit state.
                        self.session.rollback()
                        break
                    task.updated_at = datetime.now(timezone.utc)
                    used += 1
                    if failed:
                        # Keep the failed task and its failed AIRun in this
                        # transaction.  The worker terminalizer commits them
                        # atomically with the failed ResearchRun and Job.
                        break
                    self.session.commit()

            if run.status == "cancelled":
                break
            if failed:
                self.repo.update_run(
                    run,
                    status="failed",
                    stage="failed",
                    budget_used=used,
                    stop_reason="task_failed",
                )
                break

            self._create_balance_gaps(run, current_round)
            now = self._run_evidence_count(run)
            self.repo.update_run(run, budget_used=used, round=current_round)
            if used >= run.budget:
                self.repo.update_run(
                    run,
                    status=(
                        "failed"
                        if failed
                        else self._successful_terminal_status(run)
                    ),
                    stage="failed" if failed else "stopped",
                    stop_reason="task_failed" if failed else "budget_exhausted",
                )
                break
            if current_round >= run.max_rounds:
                self.repo.update_run(run, status="failed" if failed else self._successful_terminal_status(run), stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "max_rounds_reached")
                break
            next_tasks = self.repo.queued_tasks_for_run(run.id, current_round + 1)
            if now <= previous and not next_tasks:
                self.repo.update_run(run, status="failed" if failed else self._successful_terminal_status(run), stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "no_new_evidence")
                break
            previous = now
        else:
            self.repo.update_run(run, status="failed" if failed else self._successful_terminal_status(run), stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "max_rounds_reached")
        # Start the final transaction in the same Case -> ResearchRun -> Job
        # order used by scope replacement and worker terminalization.  The
        # provider calls and intermediate task commits have already finished;
        # this lock is held only through the short terminal write.
        with self.session.no_autoflush:
            lock_event_scope_case(self.session, run.research_case_id)
        self.session.flush()
        if run.status == "waiting_for_review":
            self._handoff_for_review(run)
        self.refresh_event_lifecycle(run)
        completion_message = (
            "运行失败，需检查失败项"
            if run.status == "failed"
            else "运行已结束，未产生新增待审材料"
            if run.status == "succeeded"
            else "运行已结束，等待后续人工动作"
        )
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="complete" if run.status != "failed" else "failed",
            status="completed" if run.status != "failed" else "failed",
            message=completion_message,
            payload_json={
                "status": run.status,
                "stop_reason": run.stop_reason,
                "budget_used": run.budget_used,
            },
        )
        self.session.flush()

    def refresh_event_lifecycle(self, run) -> None:
        """Project one terminal event-run into its next user-facing state.

        This intentionally does nothing for legacy research cases.  Event
        cases continue automatically when no key evidence is awaiting a human
        decision; the cycle cap prevents an empty source universe from
        producing an unbounded queue.
        """
        lifecycle_repo = EventResearchLifecycleRepository(self.session)
        lifecycle = lock_event_research_lifecycle(self.session, run.research_case_id)
        if (
            lifecycle is None
            or lifecycle.status == "published"
            or lifecycle.active_run_id != run.id
        ):
            return

        # This is the production handoff immediately after review tasks are
        # created.  It closes only invalid-source operational tasks and writes
        # its audit event in this lifecycle transaction; proposals stay intact.
        EventReviewQueueService(self.session).reconcile_event_review_queue(
            run.research_case_id
        )
        review_count = lifecycle_repo.pending_key_review_count(run.research_case_id)
        pending_claim_count = len(
            self._pending_atomic_claims(
                run.research_case_id,
                allowed_source_types=self._run_allowed_source_types(run),
            )
        )
        if pending_claim_count:
            lifecycle_repo.update(
                lifecycle,
                status="awaiting_key_review",
                active_run_id=run.id,
                summary=f"已抽取 {pending_claim_count} 条原子陈述，等待人工核对原文后再继续研究",
                current_gap="待审核抽取候选尚未形成正式 SourceStatement",
                next_human_action=f"审核 {pending_claim_count} 条原子陈述",
            )
            return
        if review_count:
            lifecycle_repo.update(
                lifecycle,
                status="awaiting_key_review",
                active_run_id=run.id,
                summary=f"已筛出 {review_count} 条关键证据，等待审核",
                current_gap=None,
                next_human_action=f"审核 {review_count} 条关键证据",
            )
            return

        if run.stop_reason == "budget_exhausted":
            lifecycle_repo.update(
                lifecycle,
                status="exhausted",
                active_run_id=run.id,
                summary="已覆盖本轮允许的检索预算，尚未找到足以形成结论的关键材料",
                current_gap="需要补充更直接的原始来源或调整研究范围",
                next_human_action="补充来源或调整研究范围",
            )
            return

        if run.stop_reason == "task_failed":
            lifecycle_repo.update(
                lifecycle,
                status="awaiting_scope",
                active_run_id=run.id,
                summary="自动研究遇到无法完成的检索任务，已暂停自动扩展",
                current_gap="需要检查失败来源或补充可访问材料",
                next_human_action="补充来源或调整研究范围",
            )
            return

        if run.stop_reason in {"max_rounds_reached", "no_new_evidence"}:
            if lifecycle.current_round < 3:
                next_round = lifecycle.current_round + 1
                successor = self.start(
                    run.research_case_id,
                    max_rounds=run.max_rounds,
                    budget=run.budget,
                    commit=False,
                    thesis_ids=self._run_thesis_ids(run),
                    scope_context={"predecessor_run_id": str(run.id)},
                )
                lifecycle_repo.update(
                    lifecycle,
                    status="continuing",
                    active_run_id=successor.id,
                    current_round=next_round,
                    summary=f"未找到关键证据，正在自动扩展检索 · 第 {next_round} 轮",
                    current_gap="尚缺少能直接区分主要因素的高质量证据",
                    next_human_action=None,
                )
                return
            lifecycle_repo.update(
                lifecycle,
                status="exhausted",
                active_run_id=run.id,
                summary="已完成 3 轮自动扩展，仍缺少足以形成结论的关键材料",
                current_gap="尚缺少能直接区分主要因素的高质量证据",
                next_human_action="补充来源或调整研究范围",
            )
            return

    def continue_after_key_review(self, case_id: uuid.UUID) -> None:
        """Resume a bounded automatic cycle once all key evidence is decided."""
        lifecycle_repo = EventResearchLifecycleRepository(self.session)
        lifecycle = lock_event_research_lifecycle(self.session, case_id)
        if lifecycle is None or lifecycle.status != "awaiting_key_review":
            return
        review_count = lifecycle_repo.pending_key_review_count(case_id)
        if review_count:
            lifecycle_repo.update(
                lifecycle,
                status="awaiting_key_review",
                active_run_id=lifecycle.active_run_id,
                summary=f"仍有 {review_count} 条关键证据等待审核",
                current_gap=None,
                next_human_action=f"审核 {review_count} 条关键证据",
            )
            return
        active_run = self.repo.get_run(lifecycle.active_run_id) if lifecycle.active_run_id else None
        if lifecycle.current_round >= 3:
            from app.services.event_conclusion import EventConclusionService

            try:
                EventConclusionService(self.session).create_draft(case_id)
            except ValidationFailedError:
                lifecycle_repo.update(
                    lifecycle,
                    status="exhausted",
                    active_run_id=lifecycle.active_run_id,
                    summary="已审核证据尚未覆盖所有当前因素，不能生成结论草案",
                    current_gap="每个当前因素均需至少一条已审核映射证据",
                    next_human_action="补充来源或调整研究范围",
                )
                return
            lifecycle_repo.update(
                lifecycle,
                status="draft_ready",
                active_run_id=lifecycle.active_run_id,
                summary="关键证据已审核，AI 正在基于已确认材料整理结论草案",
                current_gap=None,
                next_human_action="审核结论草案",
            )
            return
        successor = self.start(
            case_id,
            max_rounds=active_run.max_rounds if active_run else 1,
            budget=active_run.budget if active_run else 100,
            commit=False,
            thesis_ids=self._run_thesis_ids(active_run) if active_run else None,
            scope_context={"predecessor_run_id": str(active_run.id)} if active_run else None,
        )
        next_round = lifecycle.current_round + 1
        lifecycle_repo.update(
            lifecycle,
            status="continuing",
            active_run_id=successor.id,
            current_round=next_round,
            summary=f"关键证据已审核，系统继续核验其他解释 · 第 {next_round} 轮",
            current_gap=None,
            next_human_action=None,
        )

    def _is_cancelled(self, run, task=None) -> bool:
        """Observe a cancel request at task boundaries without interrupting a call."""
        # Avoid autoflushing provider output before we can observe that a
        # concurrent scope update cancelled this run; cancellation then rolls
        # that uncommitted output back instead of publishing it.
        task_status = None
        with self.session.no_autoflush:
            self.session.refresh(run)
            if task is not None:
                # Do not refresh the identity-mapped task: it may contain the
                # worker's uncommitted provider result.  Query the committed
                # row instead, so a concurrent replacement is observable
                # without discarding output before the caller can decide to
                # commit or roll it back.
                task_status = self.session.scalar(
                    select(ResearchTask.status).where(ResearchTask.id == task.id)
                )
        if run.status == "cancelled":
            return True
        if task_status == "cancelled":
            return True
        job = self.repo.job_for_run(run.id)
        if job is not None and job.cancel_requested:
            self.repo.cancel_run(run)
            self.session.commit()
            return True
        return False

    def _claim_task_output_slot(self, run, task) -> bool:
        """Serialize post-provider persistence with a scope replacement.

        The provider itself runs without any write transaction or row lock.
        Only once it has returned do we take the stable case lock (the same
        one used by scope replacement), then lock the run/task rows and
        confirm that this task still owns an active run.  The caller keeps
        this short transaction through proposal/result persistence.
        """
        lock_event_scope_case(self.session, run.research_case_id)
        with self.session.no_autoflush:
            current_run = self.session.scalar(
                select(ResearchRun)
                .where(ResearchRun.id == run.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_task = self.session.scalar(
                select(ResearchTask)
                .where(ResearchTask.id == task.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            job = self.session.scalar(
                select(Job)
                .where(Job.kind == "research_run")
                .where(Job.target_type == "research_run")
                .where(Job.target_id == run.id)
                .order_by(Job.created_at.desc())
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        return bool(
            current_run is not None
            and current_run.status != "cancelled"
            and current_task is not None
            and current_task.status != "cancelled"
            and (job is None or not job.cancel_requested)
        )

    def _claim_extraction_output_slot(self, run) -> bool:
        """Serialize extraction persistence with run cancellation/replacement."""
        lock_event_scope_case(self.session, run.research_case_id)
        with self.session.no_autoflush:
            current_run = self.session.scalar(
                select(ResearchRun)
                .where(ResearchRun.id == run.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            job = self.session.scalar(
                select(Job)
                .where(Job.kind == "research_run")
                .where(Job.target_type == "research_run")
                .where(Job.target_id == run.id)
                .order_by(Job.created_at.desc())
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        return bool(
            current_run is not None
            and current_run.status != "cancelled"
            and (job is None or not job.cancel_requested)
        )

    def _handoff_for_review(self, run) -> None:
        """Create idempotent home-page tasks for this run's reviewable outputs."""
        if run.status != "waiting_for_review":
            return
        research_tasks = self.repo.tasks_for_run(run.id)
        proposal_ids: set[uuid.UUID] = set()
        assessment_ids: set[uuid.UUID] = set()
        for research_task in research_tasks:
            proposal_ids.update(
                self._result_output_ids(
                    research_task.result, "proposed_proposal_ids"
                )
            )
            assessment_ids.update(
                self._result_output_ids(research_task.result, "assessment_id")
            )
        for proposal_id in proposal_ids:
            proposal = self.session.get(Proposal, proposal_id)
            if proposal is None or proposal.kind != "evidence_link" or proposal.status != "pending":
                continue
            if self.task_repo.find_by_ref(task_type="review_proposal", ref_type="proposal", ref_id=proposal.id):
                continue
            thesis_id = (proposal.target_context or {}).get("thesis_id")
            self.task_repo.add_task(
                title="审核自动研究提出的证据",
                description="自动研究产生的待审核证据，请人工确认后再发布。",
                task_type="review_proposal",
                ref_type="proposal",
                ref_id=proposal.id,
                research_case_id=run.research_case_id,
            )
        for assessment_id in assessment_ids:
            if self.task_repo.find_by_ref(task_type="review_assessment", ref_type="ai_assessment", ref_id=assessment_id):
                continue
            self.task_repo.add_task(
                title="确认临时 AI 评估",
                description="自动研究产生的 provisional AI assessment，待人工确认。",
                task_type="review_assessment",
                ref_type="ai_assessment",
                ref_id=assessment_id,
                research_case_id=run.research_case_id,
            )
        for candidate in self._pending_atomic_claims(
            run.research_case_id,
            allowed_source_types=self._run_allowed_source_types(run),
        ):
            if self.task_repo.find_by_ref(task_type="review_atomic_claim", ref_type="atomic_claim_candidate", ref_id=candidate.id):
                continue
            self.task_repo.add_task(
                title="审核自动抽取的原子陈述",
                description="核对原文、字符定位、来源权限与规范表述；未审核前不会进入证据提议或结论。",
                task_type="review_atomic_claim",
                ref_type="atomic_claim_candidate",
                ref_id=candidate.id,
                research_case_id=run.research_case_id,
                priority="high",
            )

    def _successful_terminal_status(self, run) -> str:
        """Require a concrete reviewable output before pausing for a human."""
        for task in self.repo.tasks_for_run(run.id):
            for proposal_id in self._result_output_ids(
                task.result, "proposed_proposal_ids"
            ):
                proposal = self.session.get(Proposal, proposal_id)
                if proposal is not None and proposal.kind == "evidence_link" and proposal.status == "pending":
                    return "waiting_for_review"
            if self._result_output_ids(task.result, "assessment_id"):
                return "waiting_for_review"
        return (
            "waiting_for_review"
            if self._pending_atomic_claims_for_run(run)
            else "succeeded"
        )

    def run_ids_for_output(self, *, key: str, value: uuid.UUID) -> set[uuid.UUID]:
        """Find runs whose persisted task output references one review item.

        Historical task results are JSON and can contain a scalar where newer
        writers use a list.  Treat malformed result values as no match rather
        than letting one bad task block unrelated review decisions.
        """
        run_ids: set[uuid.UUID] = set()
        # JSON containment is deliberately parsed in Python for SQLite and
        # PostgreSQL parity.  Project only the required columns; normalize
        # outputs into an indexed table when this lookup becomes high-volume.
        for run_id, result in self.session.execute(
            select(ResearchTask.run_id, ResearchTask.result).where(
                ResearchTask.result.is_not(None)
            )
        ):
            if value in self._result_output_ids(result, key):
                run_ids.add(run_id)
        return run_ids

    def reconcile_runs_for_output(
        self, *, key: str, value: uuid.UUID, trigger_ref: str
    ) -> list[uuid.UUID]:
        """Complete every run for which this decision removed the final gate."""
        return [
            run_id
            for run_id in self.run_ids_for_output(key=key, value=value)
            if self.reconcile_run(run_id, trigger_ref=trigger_ref)
        ]

    def reconcile_run(self, run_id: uuid.UUID, *, trigger_ref: str) -> bool:
        """Finish one waiting run iff it has no remaining run-local review gate."""
        run = self._lock_run_for_transition(run_id)
        if run is None or run.status != "waiting_for_review":
            return False
        if self._has_open_reviewable_output(run):
            return False
        self.repo.update_run(run, status="succeeded", stage="complete")
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="review_complete",
            status="completed",
            message="人工审核已完成；本次运行没有剩余待审项。",
            payload_json={
                "trigger_ref": trigger_ref,
                "status": "succeeded",
            },
        )
        return True

    def complete_runs_after_assessment_review(self, assessment_id: uuid.UUID) -> list[uuid.UUID]:
        """Compatibility wrapper for the assessment command's existing seam."""
        return self.reconcile_runs_for_output(
            key="assessment_id",
            value=assessment_id,
            trigger_ref=f"assessment:{assessment_id}",
        )

    @staticmethod
    def _result_output_ids(result: object, key: str) -> set[uuid.UUID]:
        raw_value = AutoResearchService._result_dict(result).get(key)
        if isinstance(raw_value, (list, tuple, set)):
            raw_values = raw_value
        else:
            raw_values = [raw_value]
        output_ids: set[uuid.UUID] = set()
        for raw_value in raw_values:
            try:
                output_ids.add(uuid.UUID(str(raw_value)))
            except (TypeError, ValueError, AttributeError):
                continue
        return output_ids

    @staticmethod
    def _claim_candidate_ids(payload: object) -> set[uuid.UUID]:
        if not isinstance(payload, dict):
            return set()
        raw_ids = payload.get("candidate_ids")
        if not isinstance(raw_ids, (list, tuple, set)):
            raw_ids = [raw_ids]
        candidate_ids: set[uuid.UUID] = set()
        for raw_id in raw_ids:
            try:
                candidate_ids.add(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError, AttributeError):
                continue
        return candidate_ids

    def _lock_run_for_transition(
        self, run_id: uuid.UUID, *, case_locked: bool = False
    ) -> ResearchRun | None:
        """Lock a mutable run transition in stable Case then ResearchRun order."""
        case_id = self.session.scalar(
            select(ResearchRun.research_case_id).where(ResearchRun.id == run_id)
        )
        if case_id is None:
            return None
        if not case_locked:
            lock_event_scope_case(self.session, case_id)
        return self.session.scalar(
            select(ResearchRun)
            .where(ResearchRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _has_open_reviewable_output(self, run) -> bool:
        """Check run-local review gates after one human decision is persisted."""
        for task in self.repo.tasks_for_run(run.id):
            result = task.result
            for proposal_id in self._result_output_ids(result, "proposed_proposal_ids"):
                proposal = self.session.get(Proposal, proposal_id)
                if proposal is None or proposal.kind != "evidence_link" or proposal.status != "pending":
                    continue
                review_task = self.task_repo.find_by_ref(
                    task_type="review_proposal", ref_type="proposal", ref_id=proposal.id
                )
                if review_task is None or review_task.status in {"open", "in_progress"}:
                    return True
            assessment_ids = self._result_output_ids(result, "assessment_id")
            for assessment_id in assessment_ids:
                assessment = self.session.get(AIAssessment, assessment_id)
                if assessment is None:
                    continue
                review_task = self.task_repo.find_by_ref(
                    task_type="review_assessment", ref_type="ai_assessment", ref_id=assessment.id
                )
                if review_task is None or review_task.status in {"open", "in_progress"}:
                    return True
        return bool(self._pending_atomic_claims_for_run(run))

    def _run_allowed_source_types(self, run) -> set[str]:
        """Read the immutable research-source-category boundary from run scope."""
        scope = self.session.scalar(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "scope")
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        raw_types = (scope.payload_json or {}).get("allowed_source_types", []) if scope else []
        return {str(value).strip() for value in raw_types if str(value).strip()}

    def _pending_versions_in_run_scope(
        self,
        run,
        *,
        allowed_source_types: set[str],
    ) -> list:
        """Select extractable documents and record exclusions from frozen scope."""
        candidates = _pending_versions(self.session, run.research_case_id)
        if not allowed_source_types:
            return candidates
        contracts = {
            item.document_version_id: item
            for item in self.session.scalars(
                select(SourceContract).where(
                    SourceContract.document_version_id.in_([item.id for item in candidates])
                )
            )
        }
        included = []
        excluded_by_reason: dict[str, int] = {}
        for document in candidates:
            contract = contracts.get(document.id)
            if contract is None:
                reason = "missing_source_contract"
            elif contract.research_source_type not in allowed_source_types:
                reason = "source_type_not_in_frozen_scope"
            elif not contract.allow_ai_processing or not source_contract_is_active(contract):
                reason = "source_contract_not_usable"
            else:
                included.append(document)
                continue
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
        if excluded_by_reason:
            ResearchRunEventRepository(self.session).append(
                run.id,
                stage="source_scope",
                status="completed",
                message="已按本次冻结的允许来源排除不在范围内的待处理资料",
                payload_json={
                    "allowed_source_types": sorted(allowed_source_types),
                    "excluded_count": sum(excluded_by_reason.values()),
                    "excluded_by_reason": excluded_by_reason,
                },
            )
        return included

    def _pending_atomic_claims(
        self,
        case_id: uuid.UUID,
        *,
        allowed_source_types: set[str] | None = None,
    ) -> list[AtomicClaimCandidate]:
        """Return only candidates from this Case that have no human decision."""
        reviewed = (
            select(AtomicClaimReview.id)
            .where(AtomicClaimReview.atomic_claim_candidate_id == AtomicClaimCandidate.id)
            .exists()
        )
        candidates = list(self.session.scalars(
            select(AtomicClaimCandidate)
            .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == SourceSpan.document_version_id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(~reviewed)
            .order_by(AtomicClaimCandidate.created_at, AtomicClaimCandidate.id)
        ))
        if not allowed_source_types:
            return candidates
        document_ids = {
            candidate.source_span_id: document_id
            for candidate, document_id in self.session.execute(
                select(AtomicClaimCandidate, SourceSpan.document_version_id)
                .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
                .where(AtomicClaimCandidate.id.in_([candidate.id for candidate in candidates]))
            )
        }
        contracts = {
            contract.document_version_id: contract
            for contract in self.session.scalars(
                select(SourceContract).where(
                    SourceContract.document_version_id.in_(list(document_ids.values()))
                )
            )
        }
        return [
            candidate
            for candidate in candidates
            if (contract := contracts.get(document_ids.get(candidate.source_span_id))) is not None
            and contract.research_source_type in allowed_source_types
            and contract.allow_ai_processing
            and source_contract_is_active(contract)
        ]

    def _pending_atomic_claims_for_run(self, run) -> list[AtomicClaimCandidate]:
        """Return unreviewed candidates explicitly recorded in this run's pause events."""
        candidate_ids: set[uuid.UUID] = set()
        events = self.session.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "claim_review")
        )
        for event in events:
            candidate_ids.update(self._claim_candidate_ids(event.payload_json))
        if not candidate_ids:
            return []
        reviewed = (
            select(AtomicClaimReview.id)
            .where(AtomicClaimReview.atomic_claim_candidate_id == AtomicClaimCandidate.id)
            .exists()
        )
        return list(
            self.session.scalars(
                select(AtomicClaimCandidate)
                .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
                .join(
                    CaseDocumentVersion,
                    CaseDocumentVersion.document_version_id
                    == SourceSpan.document_version_id,
                )
                .where(CaseDocumentVersion.research_case_id == run.research_case_id)
                .where(AtomicClaimCandidate.id.in_(candidate_ids))
                .where(~reviewed)
                .order_by(AtomicClaimCandidate.created_at, AtomicClaimCandidate.id)
            )
        )

    def _pause_for_atomic_claim_review(self, run, candidates: list[AtomicClaimCandidate], used: int) -> None:
        """Persist an explicit, replayable stop before any propose/assess work."""
        self.repo.update_run(
            run,
            status="waiting_for_review",
            stage="claim_review",
            budget_used=used,
            stop_reason="pending_atomic_claim_review",
        )
        for task in self.repo.queued_tasks_for_run(run.id, run.round):
            self.repo.update_task(
                task,
                status="blocked",
                stage="claim_review",
                result={
                    "task_type": task.task_type,
                    "blocked_by": "pending_atomic_claim_review",
                    "candidate_count": len(candidates),
                },
            )
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="claim_review",
            status="waiting_for_review",
            message=f"发现 {len(candidates)} 条待审核原子陈述，已在提议证据和生成结论前暂停",
            payload_json={
                "candidate_ids": [str(candidate.id) for candidate in candidates],
                "next_action": "review_atomic_claims",
            },
        )

    def _propose_for_task(
        self,
        proposer: EvidenceProposer,
        task,
        run,
        *,
        allowed_source_types: set[str],
    ) -> list[uuid.UUID]:
        """Call proposer once per task and avoid duplicate pending proposal hashes."""
        existing_before = self.repo.pending_proposal_hashes_for_thesis(task.thesis_id)
        proposed_ids = proposer.propose(
            task.thesis_id,
            self.session,
            before_persist=lambda: self._claim_task_output_slot(run, task),
            allowed_source_types=allowed_source_types or None,
        )
        unique: list[uuid.UUID] = []
        seen: set[str] = set()
        for proposal_id in proposed_ids:
            proposal = self.session.get(Proposal, proposal_id)
            if proposal is None:
                continue
            content_hash = proposal.content_hash
            if content_hash and content_hash in existing_before:
                continue
            if content_hash and content_hash in seen:
                continue
            if content_hash:
                seen.add(content_hash)
            unique.append(proposal_id)
        return unique

    def _create_gap_task(self, run, thesis_id, task_type, query, round, reason):
        existing = self.repo.tasks_for_run(run.id)
        if any(
            task.thesis_id == thesis_id
            and task.task_type == task_type
            and task.round == round
            and task.query == query
            and task.status != "failed"
            for task in existing
        ):
            return None
        task = self.repo.create_task(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=thesis_id,
            task_type=task_type,
            query=query,
            round=round,
        )
        self.repo.update_task(task, gap_reason=reason)
        return task

    def _create_balance_gaps(self, run, current_round):
        counts = self.repo.evidence_link_counts_by_thesis(run.research_case_id)
        theses = self._run_theses(run)
        for thesis in theses:
            current = counts.get(str(thesis.id), {})
            missing = [role for role in ("support", "contradict") if not current.get(role, 0)]
            for role in missing:
                self._create_gap_task(
                    run,
                    thesis.id,
                    role,
                    f"补充{role}证据: {thesis.statement}",
                    current_round + 1,
                    "evidence_balance",
                )

    def _evidence_count(self, thesis_id: uuid.UUID | None) -> int:
        if thesis_id is None:
            return 0
        return int(
            self.session.scalar(
                select(func.count(func.distinct(EvidenceLink.id)))
                .where(EvidenceLink.thesis_id == thesis_id)
            )
            or 0
        )

    def _case_evidence_count(self, research_case_id: uuid.UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count(func.distinct(EvidenceLink.id)))
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == research_case_id)
            )
            or 0
        )

    def _run_thesis_ids(self, run) -> list[uuid.UUID] | None:
        if run is None:
            return None
        if run.scope_thesis_ids is not None:
            return [uuid.UUID(value) for value in run.scope_thesis_ids]
        task_thesis_ids = list(
            self.session.scalars(
                select(ResearchTask.thesis_id)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.thesis_id.is_not(None))
                .distinct()
            )
        )
        if task_thesis_ids:
            return task_thesis_ids
        return None

    def _run_theses(self, run) -> list[Thesis]:
        thesis_ids = self._run_thesis_ids(run)
        stmt = select(Thesis).where(Thesis.research_case_id == run.research_case_id)
        if thesis_ids is not None:
            stmt = stmt.where(Thesis.id.in_(thesis_ids))
        return list(self.session.scalars(stmt))

    def _run_evidence_count(self, run) -> int:
        thesis_ids = self._run_thesis_ids(run)
        if thesis_ids is not None and not thesis_ids:
            return 0
        stmt = select(func.count(func.distinct(EvidenceLink.id))).join(
            Thesis, Thesis.id == EvidenceLink.thesis_id
        ).where(Thesis.research_case_id == run.research_case_id)
        if thesis_ids is not None:
            stmt = stmt.where(EvidenceLink.thesis_id.in_(thesis_ids))
        return int(self.session.scalar(stmt) or 0)
    
    def list_runs(
        self,
        case_id: uuid.UUID,
        *,
        limit: int = 20,
        after_created_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
    ) -> list[dict]:
        runs = self.repo.list_runs_for_case(
            case_id,
            limit=limit,
            after_created_at=after_created_at,
            after_id=after_id,
        )
        return [self._run_summary_dict(run) for run in runs]

    def cancel_run(self, run_id: uuid.UUID, *, actor: str, change_reason: str) -> dict:
        run = self._lock_run_for_transition(run_id)
        if run is None:
            raise ValueError(f"research run {run_id} not found")
        # Already cancelled is idempotent success; other terminal states conflict.
        if run.status == "cancelled":
            return self._run_summary_dict(run)
        if run.status not in {"running", "queued", "waiting_for_review"}:
            raise RuntimeError(f"research run {run_id} is terminal ({run.status})")
        self.repo.cancel_run(run)
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="stopped",
            status="cancelled",
            message="研究员停止本次运行；此前阶段和冻结范围保持可回放。",
            payload_json={
                "actor": actor.strip(),
                "change_reason": change_reason.strip(),
                "stop_reason": run.stop_reason,
            },
        )
        self.session.commit()
        return self._run_summary_dict(run)

    def detail(self, run_id: uuid.UUID) -> dict | None:
        run = self.repo.get_run(run_id)
        if run is None:
            return None
        tasks = self.repo.tasks_for_run(run.id)
        by_thesis = self.repo.evidence_link_counts_by_thesis(run.research_case_id)
        failed_tasks = [t for t in tasks if t.status == "failed"]
        gaps = [t for t in tasks if t.gap_reason or t.task_type == "alternative"]
        if failed_tasks:
            next_action = "处理失败任务"
        elif run.status == "waiting_for_review":
            next_action = "人工审核临时判断"
        elif run.status == "failed":
            next_action = "检查失败原因后重试"
        elif run.status == "cancelled":
            next_action = "查看取消前进度"
        else:
            next_action = "继续执行"
        proposal_ids: set[uuid.UUID] = set()
        for research_task in tasks:
            proposal_ids.update(
                self._result_output_ids(
                    research_task.result, "proposed_proposal_ids"
                )
            )
        pending_proposals = []
        review_tasks = []
        for proposal_id in proposal_ids:
            proposal = self.session.get(Proposal, proposal_id)
            if proposal is None or proposal.status != "pending":
                continue
            review_task = self.task_repo.find_by_ref(
                task_type="review_proposal", ref_type="proposal", ref_id=proposal.id
            )
            pending_proposals.append({
                "id": str(proposal.id),
                "thesis_id": (proposal.target_context or {}).get("thesis_id"),
                "task_id": str(review_task.id) if review_task else None,
                "status": proposal.status,
            })
            if review_task:
                review_tasks.append(self._review_task_dict(review_task))
        for research_task in tasks:
            for assessment_id in self._result_output_ids(
                research_task.result, "assessment_id"
            ):
                review_task = self.task_repo.find_by_ref(
                    task_type="review_assessment",
                    ref_type="ai_assessment",
                    ref_id=assessment_id,
                )
                if review_task:
                    review_tasks.append(self._review_task_dict(review_task))
        pending_assessments = []
        for research_task in tasks:
            for assessment_id in self._result_output_ids(
                research_task.result, "assessment_id"
            ):
                review_task = self.task_repo.find_by_ref(
                    task_type="review_assessment",
                    ref_type="ai_assessment",
                    ref_id=assessment_id,
                )
                assessment = self.session.get(AIAssessment, assessment_id)
                if (
                    assessment is None
                    or review_task is None
                    or review_task.status not in {"open", "in_progress"}
                ):
                    continue
                pending_assessments.append({
                    "assessment_id": str(assessment.id),
                    "conclusion": assessment.conclusion,
                    "rationale": assessment.rationale,
                    "gaps": list(assessment.gaps or []),
                    "task_id": str(review_task.id),
                    "task_status": review_task.status,
                })
        return {
            "id": str(run.id),
            "case_id": str(run.research_case_id),
            "status": run.status,
            "stage": run.stage,
            "round": run.round,
            "max_rounds": run.max_rounds,
            "budget": run.budget,
            "budget_used": run.budget_used,
            "stop_reason": run.stop_reason,
            "scope_thesis_ids": list(run.scope_thesis_ids or []),
            "progress": {"total": len(tasks), "completed": sum(t.status == "done" for t in tasks)},
            "evidence": {
                "support": sum(item.get("support", 0) for item in by_thesis.values()),
                "contradict": sum(item.get("contradict", 0) for item in by_thesis.values()),
            },
            "by_thesis": by_thesis,
            "gaps": [t.query for t in gaps],
            "gap_tasks": [self._task_dict(t) for t in gaps],
            "failed_tasks": [self._task_dict(t) for t in failed_tasks],
            "assessments": [
                result
                for task in tasks
                if (result := self._result_dict(task.result)).get("assessment_id")
            ],
            "pending_assessments": pending_assessments,
            "pending_proposals": pending_proposals,
            "review_tasks": review_tasks,
            "next_action": next_action,
            "tasks": [self._task_dict(t) for t in tasks],
        }

    def _run_summary_dict(self, run) -> dict:
        detail = self.detail(run.id) if run is not None else None
        return {
            "id": str(run.id),
            "status": run.status,
            "stage": run.stage,
            "round": run.round,
            "max_rounds": run.max_rounds,
            "budget": run.budget,
            "budget_used": run.budget_used,
            "stop_reason": run.stop_reason,
            "scope_thesis_ids": list(run.scope_thesis_ids or []),
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "next_action": detail["next_action"] if detail else "继续执行",
        }

    @staticmethod
    def _result_dict(result: object) -> dict:
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _review_task_dict(task: TaskItem) -> dict:
        return {
            "id": str(task.id),
            "status": task.status,
            "task_type": task.task_type,
            "ref_type": task.ref_type,
            "ref_id": str(task.ref_id) if task.ref_id else None,
        }

    @staticmethod
    def _task_dict(task) -> dict:
        return {
            "id": str(task.id),
            "thesis_id": str(task.thesis_id) if task.thesis_id else None,
            "status": task.status,
            "stage": task.stage,
            "round": task.round,
            "task_type": task.task_type,
            "query": task.query,
            "evidence_count": task.evidence_count,
            "gap_reason": task.gap_reason,
            "result": AutoResearchService._result_dict(task.result) or None,
        }
