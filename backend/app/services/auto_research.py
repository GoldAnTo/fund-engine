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
from app.ai.extraction import StatementExtractor
from app.ai.proposal import EvidenceProposer
from app.errors import ValidationFailedError
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseDocumentVersion,
    AtomicClaimReview,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.proposals import Proposal
from app.models.operational import Job, ResearchRun, ResearchTask, TaskItem
from app.models.research_monitor import CaseMonitorVersion
from app.models.research_expression import KeyFactor
from app.models.event_research import EventResearchConclusion
from app.repositories.operational import TaskRepository
from app.repositories.auto_research import AutoResearchRepository
from app.repositories.event_research import EventResearchLifecycleRepository
from app.scripts.run_ai_engine import _pending_versions
from app.services.compliance import ComplianceRefusedError
from app.services.event_review_queue import EventReviewQueueService
from app.services.research_protocol import ResearchProtocolService
from app.services.case_monitor import ResearchRunEventRepository
from app.services.event_research_scope_evidence import (
    lock_event_scope_case,
    lock_event_research_lifecycle,
)


class AutoResearchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AutoResearchRepository(session)
        self.task_repo = TaskRepository(session)
        self.client = LLMClient.from_env()

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
        monitor = self.session.scalar(select(CaseMonitorVersion).where(CaseMonitorVersion.research_case_id == case_id).order_by(CaseMonitorVersion.version.desc()).limit(1))
        if monitor is None or str(factor.thesis_id) not in monitor.factor_ids:
            raise ValueError("linked key factor is not in the current CaseMonitor scope")
        source_types = [source for source in monitor.allowed_source_types if source in factor.allowed_source_types]
        if not source_types:
            raise ValueError("key factor has no allowed source shared with the current CaseMonitor")
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
        for current_round in range(max(1, run.round + 1), run.max_rounds + 1):
            if self._is_cancelled(run):
                break
            run.round = current_round
            if used >= run.budget:
                self.repo.update_run(
                    run,
                    status="waiting_for_review",
                    stage="stopped",
                    budget_used=used,
                    stop_reason="budget_exhausted",
                )
                break
            for version in _pending_versions(self.session, run.research_case_id):
                if self._is_cancelled(run):
                    break
                if used >= run.budget:
                    break
                try:
                    StatementExtractor(self.client).extract(version.id, self.session)
                except ComplianceRefusedError:
                    used += 1
                except Exception:
                    used += 1
                self.session.commit()
            pending_claims = self._pending_atomic_claims(run.research_case_id)
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
                        proposed_ids = self._propose_for_task(proposer, task, run)
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
                except ComplianceRefusedError as exc:
                    if self._is_cancelled(run, task):
                        cancelled_during_task = True
                    else:
                        task.status, task.stage = "failed", "failed"
                        task.result = {"task_type": task.task_type, "error": str(exc), "error_type": "compliance_refused"}
                        failed = True
                except Exception as exc:
                    if self._is_cancelled(run, task):
                        cancelled_during_task = True
                    else:
                        task.status, task.stage = "failed", "failed"
                        task.result = {"task_type": task.task_type, "error": str(exc), "error_type": type(exc).__name__}
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
                    self.session.commit()

            if run.status == "cancelled":
                break

            self._create_balance_gaps(run, current_round)
            now = self._run_evidence_count(run)
            self.repo.update_run(run, budget_used=used, round=current_round)
            if used >= run.budget:
                self.repo.update_run(run, status="waiting_for_review", stage="stopped", stop_reason="budget_exhausted")
                break
            if current_round >= run.max_rounds:
                self.repo.update_run(run, status="failed" if failed else "waiting_for_review", stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "max_rounds_reached")
                break
            next_tasks = self.repo.queued_tasks_for_run(run.id, current_round + 1)
            if now <= previous and not next_tasks:
                self.repo.update_run(run, status="failed" if failed else "waiting_for_review", stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "no_new_evidence")
                break
            previous = now
        else:
            self.repo.update_run(run, status="failed" if failed else "waiting_for_review", stage="failed" if failed else "stopped", stop_reason="task_failed" if failed else "max_rounds_reached")
        self.session.flush()
        if run.status == "waiting_for_review":
            self._handoff_for_review(run)
        self.refresh_event_lifecycle(run)
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage="complete" if run.status != "failed" else "failed",
            status="completed" if run.status != "failed" else "failed",
            message="运行已结束，等待后续人工动作" if run.status != "failed" else "运行失败，需检查失败项",
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
        pending_claim_count = len(self._pending_atomic_claims(run.research_case_id))
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
            )
            current_task = self.session.scalar(
                select(ResearchTask)
                .where(ResearchTask.id == task.id)
                .with_for_update()
            )
            job = self.session.scalar(
                select(Job)
                .where(Job.kind == "research_run")
                .where(Job.target_type == "research_run")
                .where(Job.target_id == run.id)
                .order_by(Job.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        return bool(
            current_run is not None
            and current_run.status != "cancelled"
            and current_task is not None
            and current_task.status != "cancelled"
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
            result = research_task.result or {}
            for raw_id in result.get("proposed_proposal_ids", []):
                try:
                    proposal_ids.add(uuid.UUID(str(raw_id)))
                except (TypeError, ValueError):
                    continue
            raw_assessment = result.get("assessment_id")
            if raw_assessment:
                try:
                    assessment_ids.add(uuid.UUID(str(raw_assessment)))
                except (TypeError, ValueError):
                    continue
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
        for candidate in self._pending_atomic_claims(run.research_case_id):
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

    def _pending_atomic_claims(self, case_id: uuid.UUID) -> list[AtomicClaimCandidate]:
        """Return only candidates from this Case that have no human decision."""
        reviewed = (
            select(AtomicClaimReview.id)
            .where(AtomicClaimReview.atomic_claim_candidate_id == AtomicClaimCandidate.id)
            .exists()
        )
        return list(self.session.scalars(
            select(AtomicClaimCandidate)
            .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == SourceSpan.document_version_id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(~reviewed)
            .order_by(AtomicClaimCandidate.created_at, AtomicClaimCandidate.id)
        ))

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
        self, proposer: EvidenceProposer, task, run
    ) -> list[uuid.UUID]:
        """Call proposer once per task and avoid duplicate pending proposal hashes."""
        existing_before = self.repo.pending_proposal_hashes_for_thesis(task.thesis_id)
        proposed_ids = proposer.propose(
            task.thesis_id,
            self.session,
            before_persist=lambda: self._claim_task_output_slot(run, task),
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
        run = self.repo.get_run(run_id)
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
            for raw_id in (research_task.result or {}).get("proposed_proposal_ids", []):
                try:
                    proposal_ids.add(uuid.UUID(str(raw_id)))
                except (TypeError, ValueError):
                    pass
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
            raw_id = (research_task.result or {}).get("assessment_id")
            if not raw_id:
                continue
            try:
                assessment_id = uuid.UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            review_task = self.task_repo.find_by_ref(
                task_type="review_assessment", ref_type="ai_assessment", ref_id=assessment_id
            )
            if review_task:
                review_tasks.append(self._review_task_dict(review_task))
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
            "assessments": [t.result for t in tasks if t.result and t.result.get("assessment_id")],
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
            "result": task.result,
        }
