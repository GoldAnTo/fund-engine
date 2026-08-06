"""Synchronous automatic research orchestration."""
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
from app.models.ledger import EvidenceLink, ResearchCase, Thesis
from app.models.proposals import Proposal
from app.models.operational import TaskItem
from app.repositories.operational import TaskRepository
from app.repositories.auto_research import AutoResearchRepository
from app.scripts.run_ai_engine import _pending_versions
from app.services.compliance import ComplianceRefusedError


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
        auto_execute: bool = True,
    ):
        case = self.session.get(ResearchCase, case_id)
        if case is None:
            raise ValueError(f"research case {case_id} not found")
        run = self.repo.create_run(
            research_case_id=case_id,
            max_rounds=max(1, min(max_rounds, 3)),
            budget=max(1, budget),
        )
        theses = list(
            self.session.scalars(select(Thesis).where(Thesis.research_case_id == case_id))
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
        # Persist the queued run and its initial tasks before any potentially
        # long-running provider work. This keeps the run queryable even when a
        # synchronous execution is interrupted or the provider fails.
        self.session.commit()
        if auto_execute:
            try:
                self.execute(run)
            except Exception as exc:
                self.repo.update_run(
                    run,
                    status="failed",
                    stage="failed",
                    stop_reason="execution_failed",
                )
                self.session.commit()
                raise RuntimeError(f"automatic research execution failed: {exc}") from exc
        return run

    def execute(self, run):
        self.repo.update_run(run, status="running", stage="extract")
        used = run.budget_used or 0
        previous = self._evidence_count(run.research_case_id)
        failed = False
        for current_round in range(max(1, run.round + 1), run.max_rounds + 1):
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
            for version in _pending_versions(self.session):
                if used >= run.budget:
                    break
                try:
                    StatementExtractor(self.client).extract(version.id, self.session)
                except ComplianceRefusedError:
                    used += 1
                except Exception:
                    used += 1
            theses = list(
                self.session.scalars(
                    select(Thesis).where(Thesis.research_case_id == run.research_case_id)
                )
            )
            proposer, generator = EvidenceProposer(self.client), AssessmentGenerator(self.client)
            for task in self.repo.queued_tasks_for_run(run.id, current_round):
                if used >= run.budget:
                    break
                task.status, task.stage = "running", "research"
                try:
                    if task.task_type in {"support", "contradict", "alternative"}:
                        proposed_ids = self._propose_for_task(proposer, task)
                        task.result = {
                            "task_type": task.task_type,
                            "proposed_proposal_ids": [str(item) for item in proposed_ids],
                        }
                        task.evidence_count = self._evidence_count(task.thesis_id)
                    else:
                        assessment = generator.generate(
                            task.thesis_id, datetime.now(timezone.utc), self.session
                        )
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
                    task.status, task.stage = "done", "completed"
                except ComplianceRefusedError as exc:
                    task.status, task.stage = "failed", "failed"
                    task.result = {"task_type": task.task_type, "error": str(exc), "error_type": "compliance_refused"}
                    failed = True
                except Exception as exc:
                    task.status, task.stage = "failed", "failed"
                    task.result = {"task_type": task.task_type, "error": str(exc), "error_type": type(exc).__name__}
                    failed = True
                finally:
                    task.updated_at = datetime.now(timezone.utc)
                    used += 1

            self._create_balance_gaps(run, current_round)
            now = self._evidence_count(run.research_case_id)
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
        self.session.flush()

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

    def _propose_for_task(self, proposer: EvidenceProposer, task) -> list[uuid.UUID]:
        """Call proposer once per task and avoid duplicate pending proposal hashes."""
        existing_before = self.repo.pending_proposal_hashes_for_thesis(task.thesis_id)
        proposed_ids = proposer.propose(task.thesis_id, self.session)
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
        theses = list(self.session.scalars(select(Thesis).where(Thesis.research_case_id == run.research_case_id)))
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

    def cancel_run(self, run_id: uuid.UUID) -> dict:
        run = self.repo.get_run(run_id)
        if run is None:
            raise ValueError(f"research run {run_id} not found")
        # Already cancelled is idempotent success; other terminal states conflict.
        if run.status == "cancelled":
            return self._run_summary_dict(run)
        if run.status not in {"running", "queued", "waiting_for_review"}:
            raise RuntimeError(f"research run {run_id} is terminal ({run.status})")
        self.repo.cancel_run(run)
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
