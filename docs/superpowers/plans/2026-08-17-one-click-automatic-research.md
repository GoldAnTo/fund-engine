# One-Click Automatic Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-input, one-click research flow that automatically frames the question, acquires governed sources, admits valid evidence, analyzes it, and displays a clearly labelled system-generated result without human gates.

**Architecture:** Add an `automatic` workflow mode beside the existing reviewed workflow. A one-click intake service reuses event extraction and Case creation, then queues the existing durable `ResearchRun`; a focused automatic pipeline dispatches governed `AcquisitionJob` records, waits without busy-spinning, resumes after acquisition workers finish, analyzes automatically admitted evidence, and persists a system-generated conclusion. A dedicated read model projects internal jobs and events into five user-facing stages, while the existing professional workbench remains available but outside the default route.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, React 18, TypeScript, React Router, Vitest, Testing Library, Playwright.

---

## File map

### Backend

- Create `backend/alembic/versions/0059_one_click_automatic_research.py`: persist workflow mode and add the terminal `completed` lifecycle state.
- Modify `backend/app/models/event_research.py`: add immutable `workflow_mode` to the event brief.
- Modify `backend/app/models/operational.py`: recognize `completed` lifecycle rows and source-waiting run jobs.
- Create `backend/app/domain/automatic_research.py`: stable five-stage and automatic-status vocabulary.
- Create `backend/app/services/automatic_research_intake.py`: turn one text input into an automatic Case and queued run.
- Create `backend/app/services/automatic_research_pipeline.py`: dispatch acquisition jobs, reconcile local failures, run assessments, and finalize results.
- Modify `backend/app/services/event_research.py`: support reviewed and automatic creation without duplicating Case setup.
- Modify `backend/app/services/auto_research.py`: route automatic runs through the new pipeline while leaving reviewed runs unchanged.
- Modify `backend/app/repositories/auto_research.py`: park and requeue research jobs waiting on acquisition.
- Modify `backend/app/scripts/run_research_worker.py`: wake source-ready automatic runs before claiming work.
- Modify `backend/app/services/event_conclusion.py`: persist a `system_generated` conclusion from automatic evidence and assessments.
- Create `backend/app/queries/automatic_research.py`: project stages, counters, safe sources, limitations, and result text.
- Create `backend/app/schemas/v1/automatic_research.py`: one-click start, status, result, source, and retry DTOs.
- Create `backend/app/api/v1/automatic_research.py`: start/read/retry endpoints.
- Modify `backend/app/api/v1/router.py`: register the new router.
- Modify `backend/app/schemas/v1/event_research.py`: expose `workflow_mode` on event list items.
- Modify `backend/app/queries/event_research.py`: populate `workflow_mode` without changing reviewed Case behavior.

### Frontend

- Create `frontend/src/domain/automaticResearch.ts`: typed automatic research view and input contract.
- Modify `frontend/src/domain/eventResearch.ts`: carry `workflowMode` on list items.
- Modify `frontend/src/domain/prototypeTypes.ts`: add automatic research client methods.
- Modify `frontend/src/data/researchClient.ts`: delegate the new methods.
- Modify `frontend/src/data/httpResearchAdapter.ts`: bind the generated API DTOs.
- Modify `frontend/src/data/mockResearchAdapter.ts`: deterministic automatic run fixture for local/e2e use.
- Replace `frontend/src/features/events/EventCreatePage.tsx`: render one textarea and one start button.
- Create `frontend/src/features/events/AutomaticResearchPage.tsx`: poll and render conclusion, five stages, counters, process details, and retry.
- Modify `frontend/src/domain/eventResearchPresentation.ts`: route automatic Cases to their simple research page.
- Modify `frontend/src/app/routes.tsx`: add `/automatic-research/:caseId`.
- Create `frontend/src/styles/automatic-research.css`: keep new layout isolated from the already large shared stylesheet.
- Modify `frontend/src/main.tsx`: import the isolated stylesheet.
- Regenerate `frontend/openapi.json` and `frontend/src/contracts/v1.ts`.

### Tests and docs

- Create `backend/tests/test_automatic_research_schema.py`.
- Create `backend/tests/test_automatic_research_intake.py`.
- Create `backend/tests/test_automatic_research_pipeline.py`.
- Create `backend/tests/test_automatic_research_api.py`.
- Modify `backend/tests/test_research_worker_entrypoint.py`.
- Create `frontend/src/domain/automaticResearch.test.ts`.
- Create `frontend/src/tests/AutomaticResearchPage.test.tsx`.
- Create `frontend/src/tests/AutomaticResearchAdapter.test.ts`.
- Create `frontend/e2e/automatic-research.spec.ts`.
- Modify `README.md`: document the automatic runtime and its two workers.

## Task 1: Persist automatic workflow identity and terminal state

**Files:**
- Create: `backend/alembic/versions/0059_one_click_automatic_research.py`
- Modify: `backend/app/models/event_research.py`
- Modify: `backend/app/models/operational.py`
- Create: `backend/app/domain/automatic_research.py`
- Test: `backend/tests/test_automatic_research_schema.py`

- [ ] **Step 1: Write the failing schema tests**

```python
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ledger import Base, ResearchCase
from app.models.event_research import EventResearchBrief
from app.models.operational import EventResearchLifecycle


def test_event_brief_defaults_to_reviewed_workflow():
    column = EventResearchBrief.__table__.c.workflow_mode
    assert column.default.arg == "reviewed"
    assert str(column.server_default.arg) == "reviewed"


def test_sqlite_accepts_automatic_brief_and_completed_lifecycle():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        case = ResearchCase(
            title="自动研究",
            industry_topic="事件研究",
            created_by="tenant:test-team",
            created_at=datetime.now(UTC),
        )
        session.add(case)
        session.flush()
        session.add_all([
            EventResearchBrief(
                research_case_id=case.id,
                raw_input="AI 服务器电力需求",
                source_type="pasted_snapshot",
                event_title="AI 服务器电力需求",
                research_question="电力需求如何变化？",
                extraction_state="system_generated",
                workflow_mode="automatic",
                created_at=datetime.now(UTC),
            ),
            EventResearchLifecycle(
                research_case_id=case.id,
                status="completed",
                current_round=1,
                status_summary="自动研究已完成",
                updated_at=datetime.now(UTC),
            ),
        ])
        session.commit()


def test_sqlite_rejects_unknown_workflow_mode():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        case = ResearchCase(
            title="错误模式",
            industry_topic="事件研究",
            created_by="tenant:test-team",
            created_at=datetime.now(UTC),
        )
        session.add(case)
        session.flush()
        session.add(EventResearchBrief(
            research_case_id=case.id,
            raw_input="材料",
            source_type="pasted_snapshot",
            event_title="错误模式",
            research_question="问题",
            extraction_state="system_generated",
            workflow_mode="unknown",
            created_at=datetime.now(UTC),
        ))
        with pytest.raises(IntegrityError):
            session.commit()
```

- [ ] **Step 2: Run the tests to verify the missing fields fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_schema.py`

Expected: FAIL because `workflow_mode` and lifecycle state `completed` are not defined.

- [ ] **Step 3: Add the stable domain vocabulary**

```python
# backend/app/domain/automatic_research.py
from typing import Final, Literal, TypeAlias

AutomaticResearchStatus: TypeAlias = Literal[
    "queued", "running", "completed", "failed"
]
AutomaticResearchStage: TypeAlias = Literal[
    "acquire", "parse", "admit", "analyze", "conclude"
]

AUTOMATIC_STAGE_ORDER: Final = (
    "acquire", "parse", "admit", "analyze", "conclude"
)
AUTOMATIC_SOURCE_JOB_TERMINAL: Final = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)
```

- [ ] **Step 4: Add model fields and constraints**

In `EventResearchBrief.__table_args__`, add:

```python
CheckConstraint(
    "workflow_mode IN ('reviewed', 'automatic')",
    name="ck_event_research_briefs_workflow_mode",
),
```

Add the mapped field:

```python
workflow_mode: Mapped[str] = mapped_column(
    String(16), nullable=False, default="reviewed", server_default="reviewed"
)
```

Add `"completed"` to `EVENT_RESEARCH_LIFECYCLE_STATES` and to `ck_event_research_lifecycle_status`. Update the `JobStatus` type alias and active-run repository filters to recognize `waiting_for_sources` without changing the database shape of `jobs`.

- [ ] **Step 5: Add migration 0059**

```python
"""Add one-click automatic research workflow identity."""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_research_briefs",
        sa.Column(
            "workflow_mode",
            sa.String(16),
            nullable=False,
            server_default="reviewed",
        ),
    )
    with op.batch_alter_table("event_research_briefs") as batch:
        batch.create_check_constraint(
            "ck_event_research_briefs_workflow_mode",
            "workflow_mode IN ('reviewed', 'automatic')",
        )
    with op.batch_alter_table("event_research_lifecycles") as batch:
        batch.drop_constraint(
            "ck_event_research_lifecycle_status", type_="check"
        )
        batch.create_check_constraint(
            "ck_event_research_lifecycle_status",
            "status IN ('extracting', 'researching', 'awaiting_key_review', "
            "'continuing', 'awaiting_scope', 'draft_ready', 'published', "
            "'exhausted', 'completed')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE event_research_lifecycles "
        "SET status='draft_ready' WHERE status='completed'"
    )
    with op.batch_alter_table("event_research_lifecycles") as batch:
        batch.drop_constraint(
            "ck_event_research_lifecycle_status", type_="check"
        )
        batch.create_check_constraint(
            "ck_event_research_lifecycle_status",
            "status IN ('extracting', 'researching', 'awaiting_key_review', "
            "'continuing', 'awaiting_scope', 'draft_ready', 'published', "
            "'exhausted')",
        )
    with op.batch_alter_table("event_research_briefs") as batch:
        batch.drop_constraint(
            "ck_event_research_briefs_workflow_mode", type_="check"
        )
        batch.drop_column("workflow_mode")
```

- [ ] **Step 6: Run schema tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_schema.py tests/test_event_research_api.py`

Expected: PASS.

```bash
git add backend/alembic/versions/0059_one_click_automatic_research.py backend/app/domain/automatic_research.py backend/app/models/event_research.py backend/app/models/operational.py backend/tests/test_automatic_research_schema.py
git commit -m "feat: persist automatic research workflow mode"
```

## Task 2: Create an automatic Case and run from one input

**Files:**
- Create: `backend/app/services/automatic_research_intake.py`
- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/services/auto_research.py`
- Test: `backend/tests/test_automatic_research_intake.py`

- [ ] **Step 1: Write failing intake tests for topic and material input**

Use a fake extractor so these tests never call a live model:

```python
from datetime import UTC, datetime
import uuid

from sqlalchemy import select

from app.models.event_research import EventResearchBrief
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction


class FakeExtractor:
    def extract(self, *, raw_input: str, source_url: str | None):
        return EventExtraction(
            event_title=raw_input[:80],
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


def test_topic_input_creates_one_automatic_run(session):
    created = AutomaticResearchIntakeService(
        session, extractor=FakeExtractor()
    ).start("AI 服务器电力需求", tenant_id="test-team")
    brief = session.scalar(select(EventResearchBrief))
    lifecycle = session.get(EventResearchLifecycle, uuid.UUID(created.case_id))
    run = session.get(ResearchRun, created.run_id)
    assert brief.workflow_mode == "automatic"
    assert brief.extraction_state == "system_generated"
    assert lifecycle.status == "researching"
    assert lifecycle.next_human_action is None
    assert run.status == "queued"
    scope_event = latest_scope_event(session, run.id)
    assert scope_event.payload_json["automatic_protocol"]["research_question"]
    assert len(scope_event.payload_json["automatic_evidence_plan"]["items"]) == 3
    assert scope_event.payload_json["automatic_protocol"]["generated_by"] == "system"


def test_material_input_freezes_context_but_does_not_create_preparation(session):
    created = AutomaticResearchIntakeService(
        session, extractor=FakeExtractor()
    ).start("公司公告显示订单增长，但毛利率下降。", tenant_id="test-team")
    assert created.run_id is not None
    assert created.case_id is not None
    assert session.execute(
        select(ResearchRun).where(ResearchRun.research_case_id == created.case_id)
    ).scalar_one().status == "queued"
    assert created.preparation_id is None
```

- [ ] **Step 2: Run the tests to verify the intake service is missing**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_intake.py`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Extend event creation without duplicating reviewed setup**

Change the service signature to:

```python
def create(
    self,
    payload: CreateEventResearchRequest,
    *,
    tenant_id: str,
    initial_uploaded_original: InitialUploadedOriginal | None = None,
    workflow_mode: str = "reviewed",
) -> CreatedEventResearch:
```

Set `EventResearchBrief.workflow_mode` and `extraction_state` from the mode. For `reviewed`, keep the current `ResearchPreparationService.create_for_case(...)` branch unchanged. For `automatic`, do not create a human-review `ResearchPreparation`; create an explicitly machine-generated protocol snapshot and evidence plan, then create a lifecycle and run in the same transaction. The snapshots are constraints for this run, not a `ReviewDecision` and not a reviewed `ResearchProtocolService` record:

```python
run = AutoResearchService(self._session).start(
    case.id,
    max_rounds=3,
    budget=100,
    thesis_ids=[thesis.id for thesis in theses],
    trigger="automatic_intake",
    commit=False,
    scope_context={
        "workflow_mode": "automatic",
        "automatic_protocol": {
            "generated_by": "system",
            "research_question": payload.research_question,
            "factors": list(payload.candidate_factors),
            "conclusion_rule": "report support, contradiction, and insufficiency separately",
        },
        "automatic_evidence_plan": {
            "items": [
                {
                    "factor": factor,
                    "objectives": ["support", "contradict", "alternative_explanation"],
                    "allowed_source_roles": ["company_disclosure", "licensed_provider"],
                }
                for factor in payload.candidate_factors
            ],
            "max_rounds": 3,
            "budget": 100,
        },
    },
)
lifecycle = EventResearchLifecycle(
    research_case_id=case.id,
    status="researching",
    active_run_id=run.id,
    current_round=1,
    status_summary="自动研究已排队",
    current_gap=None,
    next_human_action=None,
    updated_at=_utcnow(),
)
```

Capture the created theses in a local list as they are added. `CreatedEventResearch` gains `run_id: str | None` and `preparation_id: str | None`; reviewed callers receive `run_id=None` and the current preparation id, while automatic callers receive the new run id and `preparation_id=None`.

The automatic pipeline in later tasks must read allowed roles, rounds, and budget from these frozen scope snapshots. It must not reconstruct a broader plan from live settings.

- [ ] **Step 4: Implement the one-input service**

```python
# backend/app/services/automatic_research_intake.py
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.event_extraction import EventExtractionService
from app.services.event_research import EventResearchService


@dataclass(frozen=True, slots=True)
class AutomaticResearchStart:
    case_id: str
    run_id: str
    preparation_id: None = None


class AutomaticResearchIntakeService:
    def __init__(self, session: Session, *, extractor=None) -> None:
        self._session = session
        self._extractor = extractor or EventExtractionService()

    def start(self, raw_input: str, *, tenant_id: str) -> AutomaticResearchStart:
        text = raw_input.strip()
        if not text:
            raise ValueError("automatic research input must not be blank")
        extracted = self._extractor.extract(raw_input=text, source_url=None)
        payload = CreateEventResearchRequest(
            raw_input=text,
            source_url=None,
            source_type="pasted_snapshot",
            source_metadata={
                "authority_level": "user_supplied",
                "intake_role": "research_prompt",
                "permissions": {
                    "ai_processing": True,
                    "display": True,
                    "export": False,
                    "api": False,
                },
            },
            event_title=extracted.event_title or text[:80],
            company_name=extracted.company_name,
            ticker=extracted.ticker,
            event_at=extracted.event_at,
            market_reaction=extracted.market_reaction,
            research_question=extracted.research_question,
            candidate_factors=list(extracted.candidate_factors),
            research_protocol_required=False,
            created_by=f"tenant:{tenant_id}",
        )
        created = EventResearchService(self._session).create(
            payload, tenant_id=tenant_id, workflow_mode="automatic"
        )
        if created.run_id is None:
            raise RuntimeError("automatic research run was not created")
        return AutomaticResearchStart(
            case_id=created.case_id,
            run_id=created.run_id,
        )
```

- [ ] **Step 5: Verify automatic and reviewed creation stay separate**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_intake.py tests/test_research_preparation_integration.py tests/test_event_research_api.py`

Expected: PASS; reviewed tests still create preparation jobs, automatic tests do not.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/automatic_research_intake.py backend/app/services/event_research.py backend/app/services/auto_research.py backend/tests/test_automatic_research_intake.py
git commit -m "feat: start automatic research from one input"
```

## Task 3: Dispatch governed acquisition and park the research worker

**Files:**
- Create: `backend/app/services/automatic_research_pipeline.py`
- Modify: `backend/app/repositories/auto_research.py`
- Modify: `backend/app/scripts/run_research_worker.py`
- Test: `backend/tests/test_automatic_research_pipeline.py`
- Test: `backend/tests/test_research_worker_entrypoint.py`

- [ ] **Step 1: Write failing tests for deterministic acquisition requests**

```python
from app.models.acquisition import AcquisitionJob
from app.services.automatic_research_pipeline import AutomaticResearchPipeline


def test_dispatch_creates_one_job_per_evidence_objective(automatic_run, session):
    outcome = AutomaticResearchPipeline(session).dispatch_sources(automatic_run)
    jobs = session.query(AcquisitionJob).order_by(AcquisitionJob.id).all()
    assert outcome == "waiting_for_sources"
    assert {job.request_snapshot["objective"] for job in jobs} == {
        "support", "contradict", "alternative_explanation"
    }
    assert all(job.research_run_id == automatic_run.id for job in jobs)
    assert all(job.request_snapshot["cutoff"] for job in jobs)


def test_dispatch_is_idempotent_for_the_same_round(automatic_run, session):
    pipeline = AutomaticResearchPipeline(session)
    pipeline.dispatch_sources(automatic_run)
    pipeline.dispatch_sources(automatic_run)
    assert session.query(AcquisitionJob).count() == 3
```

The `automatic_run` fixture creates one automatic brief, one Case admission, one thesis, and a run whose scope event contains `workflow_mode=automatic`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_pipeline.py -k dispatch`

Expected: FAIL because the pipeline service does not exist.

- [ ] **Step 3: Build frozen requests from run/task identity**

Implement `dispatch_sources` with the existing `AcquisitionModule`. Map task types exactly:

```python
_OBJECTIVES = {
    "support": (EvidenceObjective.SUPPORT, "supports"),
    "contradict": (EvidenceObjective.CONTRADICT, "contradicts"),
    "alternative": (
        EvidenceObjective.ALTERNATIVE_EXPLANATION,
        "contextualizes",
    ),
}
```

For every queued non-result task in `run.round or 1`, construct:

```python
request = AcquisitionRequest(
    tenant_id=tenant_id,
    case_id=run.research_case_id,
    thesis_id=task.thesis_id,
    research_run_id=run.id,
    round=current_round,
    objective=objective,
    target_link_role=target_role,
    thesis_statement=thesis.statement,
    entity_names=(brief.company_name,) if brief.company_name else (),
    security_codes=(brief.ticker,) if brief.ticker else (),
    metric_terms=(thesis.statement,),
    period_start=(run.created_at.date() - timedelta(days=365 * 3)).isoformat(),
    period_end=run.created_at.date().isoformat(),
    cutoff=run.created_at,
    allowed_source_roles=frozenset(
        role
        for role in frozen_plan_item["allowed_source_roles"]
        if role in B_SCOPE_POLICY.allowed_source_roles
    ),
    source_policy_version=B_SCOPE_POLICY.version,
    idempotency_key=(
        f"automatic:{run.id}:{current_round}:{task.thesis_id}:{objective.value}"
    ),
)
```

Resolve `frozen_plan_item` by the task thesis statement from the scope event's `automatic_evidence_plan`. Fail the run closed if the plan is absent, the factor is absent, or the role intersection is empty. Set `run.round = max(1, run.round)` before selecting tasks. Use `AcquisitionPrincipal(tenant_id, "system:research-worker")`. Store the returned job id and objective in `ResearchTask.result`, set the task stage to `acquire`, and append one `ResearchRunEvent(stage="retrieve", status="waiting")` after all jobs are queued.

- [ ] **Step 4: Add explicit source-wait job transitions**

Add these repository methods:

```python
def wait_for_sources(self, run: ResearchRun, job: Job) -> None:
    run.status = "waiting_for_sources"
    run.stage = "retrieve"
    run.updated_at = _utcnow()
    job.status = "waiting_for_sources"
    job.step = "retrieve"
    job.finished_at = None
    self._append_job_event(
        job,
        status="waiting_for_sources",
        step="retrieve",
        message="waiting for governed acquisition jobs",
    )


def requeue_source_ready_runs(self) -> int:
    waiting = list(self._session.scalars(
        select(ResearchRun)
        .join(Job, Job.target_id == ResearchRun.id)
        .where(Job.kind == "research_run")
        .where(Job.status == "waiting_for_sources")
    ))
    requeued = 0
    for run in waiting:
        source_statuses = list(self._session.scalars(
            select(AcquisitionJob.status).where(
                AcquisitionJob.research_run_id == run.id
            )
        ))
        if not source_statuses or not all(
            status in AUTOMATIC_SOURCE_JOB_TERMINAL for status in source_statuses
        ):
            continue
        job = self.job_for_run(run.id)
        if job is None or job.status != "waiting_for_sources":
            continue
        run.status = "queued"
        run.stage = "analyze"
        job.status = "queued"
        job.step = "analyze"
        job.attempt += 1
        self._append_job_event(
            job, status="queued", step="analyze", message="sources ready"
        )
        requeued += 1
    return requeued
```

- [ ] **Step 5: Wake ready runs before claiming work**

At the start of `run_once`, after stale recovery and before `claim_next_run_job`, call `service.repo.requeue_source_ready_runs()`. Add worker tests asserting:

```python
assert run_research_worker.run_once()
assert research_job.status == "waiting_for_sources"
assert run.status == "waiting_for_sources"

# Mark all linked acquisition jobs terminal.
assert run_research_worker.run_once()
assert research_job.attempt == 2
```

The worker must call `wait_for_sources` instead of `record_job_completion` when the run returns in `waiting_for_sources`.

- [ ] **Step 6: Run pipeline and worker tests**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_pipeline.py tests/test_research_worker_entrypoint.py`

Expected: PASS, with no rapid requeue while any acquisition job is non-terminal.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/automatic_research_pipeline.py backend/app/repositories/auto_research.py backend/app/scripts/run_research_worker.py backend/tests/test_automatic_research_pipeline.py backend/tests/test_research_worker_entrypoint.py
git commit -m "feat: wait durably for automatic source acquisition"
```

## Task 4: Analyze admitted evidence and finish without human gates

**Files:**
- Modify: `backend/app/services/automatic_research_pipeline.py`
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/services/event_conclusion.py`
- Test: `backend/tests/test_automatic_research_pipeline.py`

- [ ] **Step 1: Add failing tests for local failure, completion, and no-evidence failure**

```python
def test_one_failed_source_job_does_not_block_an_admitted_result(
    automatic_run, session, fake_assessment_generator
):
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=fake_assessment_generator
    )
    pipeline.dispatch_sources(automatic_run)
    mark_job_partial_with_one_admitted_link(session, automatic_run)
    mark_other_jobs_failed(session, automatic_run)
    outcome = pipeline.advance(automatic_run)
    assert outcome == "completed"
    assert automatic_run.status == "succeeded"
    assert automatic_run.stop_reason == "automatic_completed"
    assert latest_conclusion(session).state == "system_generated"
    assert latest_lifecycle(session).status == "completed"
    assert latest_lifecycle(session).next_human_action is None


def test_all_sources_without_admitted_evidence_fail_the_run(automatic_run, session):
    pipeline = AutomaticResearchPipeline(session)
    pipeline.dispatch_sources(automatic_run)
    mark_all_jobs_failed(session, automatic_run)
    outcome = pipeline.advance(automatic_run)
    assert outcome == "failed"
    assert automatic_run.stop_reason == "no_usable_evidence"


def test_max_rounds_with_some_evidence_produces_insufficient_result(
    automatic_run, session, fake_insufficient_assessment_generator
):
    automatic_run.max_rounds = 1
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=fake_insufficient_assessment_generator
    )
    pipeline.dispatch_sources(automatic_run)
    mark_job_partial_with_one_admitted_link(session, automatic_run)
    assert pipeline.advance(automatic_run) == "completed"
    assert "证据不足" in latest_conclusion(session).text
```

- [ ] **Step 2: Run the tests and verify the pipeline stops too early**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_pipeline.py -k 'failed_source or admitted_result or max_rounds'`

Expected: FAIL because source reconciliation and automatic finalization are not implemented.

- [ ] **Step 3: Reconcile each acquisition task independently**

For every acquisition task, read its persisted `AcquisitionJob` and counters. Mark only that task failed when the job is `failed` or `cancelled`; otherwise mark it done and preserve counters:

```python
task.result = {
    **(task.result or {}),
    "acquisition_status": source_job.status,
    "reference_count": source_job.reference_count,
    "frozen_count": source_job.frozen_count,
    "admitted_count": source_job.admitted_count,
    "exception_count": source_job.exception_count,
}
task.status = "done" if source_job.status in {"succeeded", "partial"} else "failed"
task.stage = "completed" if task.status == "done" else "failed"
```

Append current-scope assignments for admitted links after all source jobs are terminal. Read each link through `AcquisitionRepository.admitted_evidence(job.id)`, load its `EvidenceLink` and `Thesis`, then call `append_current_scope_evidence_assignment(...)`. This happens in the research worker transaction after acquisition has released its lease, preserving the Case → lifecycle lock order.

- [ ] **Step 4: Generate provisional assessments directly from admitted links**

For each queued `result` task in the current round:

```python
assessment = self._assessment_generator.generate(
    task.thesis_id,
    datetime.now(UTC),
    self._session,
)
task.result = {
    "task_type": "result",
    "assessment_id": str(assessment.id),
    "conclusion": assessment.conclusion,
    "gaps": list(assessment.gaps),
}
task.status = "done"
task.stage = "completed"
```

Do not call `EvidenceProposer`, `_pause_for_atomic_claim_review`, `_handoff_for_review`, or create `TaskItem` review tasks in the automatic branch. Automatically admitted `EvidenceLink` records are already formal machine evidence and retain their `AutomaticAdmissionDecision` provenance.

- [ ] **Step 5: Add bounded replenishment**

When any latest assessment has gaps and `run.round < run.max_rounds` and budget remains, create the next round's support, contradict, alternative, and result tasks with queries derived from the frozen thesis plus gap text. Increment `run.round`, dispatch new acquisition jobs, and return `waiting_for_sources`. If the limit is reached with at least one admitted link, finalize normally and include `insufficient_evidence` gaps in the result.

The automatic branch follows this single durable transition function:

```python
def advance(self, run: ResearchRun) -> str:
    jobs = self._source_jobs(run.id, round=max(1, run.round))
    if not jobs:
        run.round = max(1, run.round)
        return self.dispatch_sources(run)
    if any(job.status not in AUTOMATIC_SOURCE_JOB_TERMINAL for job in jobs):
        self._repository.update_run(
            run, status="waiting_for_sources", stage="retrieve"
        )
        return "waiting_for_sources"

    self._reconcile_source_tasks(run, jobs)
    self._map_admitted_evidence(run, jobs)
    admitted_count = self._run_admitted_count(run.id)
    if admitted_count == 0 and run.round >= run.max_rounds:
        return self._fail(run, "no_usable_evidence")

    assessments = self._assess_current_round(run)
    needs_more = any(item.gaps for item in assessments)
    budget_left = run.budget_used < run.budget
    if needs_more and run.round < run.max_rounds and budget_left:
        self._create_next_round_tasks(run, assessments)
        run.round += 1
        return self.dispatch_sources(run)
    if admitted_count == 0:
        return self._fail(run, "no_usable_evidence")
    EventConclusionService(self._session).create_automatic_result(
        run.research_case_id, run.id
    )
    self._repository.update_run(
        run,
        status="succeeded",
        stage="complete",
        stop_reason="automatic_completed",
    )
    return "completed"
```

Use this exact continuation test:

```python
assert first_advance == "waiting_for_sources"
assert run.round == 2
assert {task.round for task in repo.tasks_for_run(run.id)} == {1, 2}
assert session.query(AcquisitionJob).filter_by(research_run_id=run.id).count() == 6
```

- [ ] **Step 6: Persist a system-generated conclusion**

Add `EventConclusionService.create_automatic_result(case_id, run_id)` that:

1. verifies `EventResearchBrief.workflow_mode == "automatic"`;
2. loads assessment ids from this run's result tasks;
3. loads automatically admitted evidence links assigned to the current scope;
4. creates one immutable `EventResearchConclusion(state="system_generated")`;
5. stores the exact evidence-link id snapshot;
6. updates lifecycle to `completed` with no human action.

Build deterministic text from the assessments:

```python
labels = {
    "supported": "得到当前证据支持",
    "contradicted": "受到当前证据反驳",
    "insufficient_evidence": "证据不足",
}
parts = [
    f"{thesis.statement}：{labels[assessment.conclusion]}。{assessment.rationale}"
    for thesis, assessment in rows
]
text = "\n".join(parts)
```

Set `run.status="succeeded"`, `run.stage="complete"`, and `run.stop_reason="automatic_completed"`. If all rounds finish with zero admitted links, set `status="failed"`, `stage="failed"`, `stop_reason="no_usable_evidence"`, and do not create a conclusion.

- [ ] **Step 7: Route only automatic runs through the pipeline**

At the top of `AutoResearchService.execute`:

```python
if self._workflow_mode(run) == "automatic":
    return AutomaticResearchPipeline(
        self.session,
        client=self.client,
        repository=self.repo,
    ).advance(run)
```

`_workflow_mode` reads the immutable scope event payload and defaults to `reviewed`. The existing reviewed execution body remains byte-for-byte behaviorally unchanged.

- [ ] **Step 8: Run automatic and reviewed orchestration suites**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_pipeline.py tests/test_auto_research_api.py tests/test_research_worker_entrypoint.py tests/test_automatic_admission.py`

Expected: PASS; existing reviewed runs still stop at their human gates, automatic runs never do.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/automatic_research_pipeline.py backend/app/services/auto_research.py backend/app/services/event_conclusion.py backend/tests/test_automatic_research_pipeline.py
git commit -m "feat: complete automatic research without review gates"
```

## Task 5: Expose start, progress, result, and retry APIs

**Files:**
- Create: `backend/app/queries/automatic_research.py`
- Create: `backend/app/schemas/v1/automatic_research.py`
- Create: `backend/app/api/v1/automatic_research.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/queries/event_research.py`
- Test: `backend/tests/test_automatic_research_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_start_requires_only_one_input(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.automatic_research_intake.EventExtractionService",
        lambda: FakeExtractor(),
    )
    response = client.post("/api/v1/automatic-research", json={"input": "AI 服务器电力需求"})
    assert response.status_code == 201
    assert set(response.json()) == {"case_id", "run_id", "status"}
    assert response.json()["status"] == "queued"


def test_read_returns_five_stages_and_machine_label(client, completed_automatic_case):
    response = client.get(
        f"/api/v1/automatic-research/{completed_automatic_case.case_id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["key"] for item in body["stages"]] == [
        "acquire", "parse", "admit", "analyze", "conclude"
    ]
    assert body["result"]["label"] == "系统生成，未经人工审核"
    assert body["result"]["human_reviewed"] is False


def test_retry_creates_a_new_run_without_overwriting_failure(client, failed_automatic_case):
    response = client.post(
        f"/api/v1/automatic-research/{failed_automatic_case.case_id}/retry"
    )
    assert response.status_code == 201
    assert response.json()["run_id"] != str(failed_automatic_case.run_id)
```

- [ ] **Step 2: Run API tests to verify routes are missing**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_api.py`

Expected: FAIL with 404 responses.

- [ ] **Step 3: Define the wire contract**

```python
# backend/app/schemas/v1/automatic_research.py
from datetime import datetime
from typing import Literal
from pydantic import Field

from app.schemas.v1.common import V1Model


class AutomaticResearchStartRequest(V1Model):
    input: str = Field(min_length=1, max_length=100_000)


class AutomaticResearchStartResponse(V1Model):
    case_id: str
    run_id: str
    status: Literal["queued"]


class AutomaticResearchStageDTO(V1Model):
    key: Literal["acquire", "parse", "admit", "analyze", "conclude"]
    label: str
    status: Literal["pending", "running", "completed", "failed"]
    summary: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AutomaticResearchSourceDTO(V1Model):
    title: str | None
    url: str | None
    role: str
    review_state: Literal["automatically_admitted"]


class AutomaticResearchResultDTO(V1Model):
    label: Literal["系统生成，未经人工审核"]
    human_reviewed: Literal[False]
    conclusion: str
    key_findings: list[str]
    counter_evidence: list[str]
    limitations: list[str]
    sources: list[AutomaticResearchSourceDTO]


class AutomaticResearchStatsDTO(V1Model):
    source_count: int
    admitted_evidence_count: int
    skipped_count: int
    duration_seconds: int


class AutomaticResearchExceptionDTO(V1Model):
    reason: str
    stage: str
    count: int


class AutomaticResearchViewDTO(V1Model):
    case_id: str
    run_id: str
    title: str
    status: Literal["queued", "running", "completed", "failed"]
    stages: list[AutomaticResearchStageDTO]
    stats: AutomaticResearchStatsDTO
    recent_activity: list[str]
    exceptions: list[AutomaticResearchExceptionDTO]
    failure_reason: str | None
    result: AutomaticResearchResultDTO | None
```

- [ ] **Step 4: Implement the read projection**

`AutomaticResearchQueries.get(case_id, tenant_id)` must authorize through `CaseTenantAccess`, verify the brief is automatic, select the lifecycle's active run, and combine:

- `ResearchRun` and `ResearchRunEvent` for analysis/conclusion timing;
- linked `AcquisitionJob` and `AcquisitionJobEvent` for acquire/parse/admit timing;
- acquisition counters for source/admitted/skipped totals;
- grouped `AcquisitionException.reason_code` values mapped to safe Chinese explanations;
- `EventResearchConclusion(state="system_generated")` for conclusion text;
- run-local `AIAssessment` rows for key findings and limitations;
- automatically admitted `EvidenceLink` rows for counter-evidence and safe sources.

Map acquisition stage to the five-stage projection with pure helpers:

```python
ACQUISITION_STAGE = {
    "queued": "acquire",
    "searching": "acquire",
    "fetching": "acquire",
    "freezing": "parse",
    "extracting": "parse",
    "admitting": "admit",
    "succeeded": "admit",
    "partial": "admit",
    "failed": "admit",
}
```

Expose a source title, URL, and excerpt only when its `SourceContract.allow_display` is true; otherwise return `title=None`, `url=None` and keep the evidence count. Deduplicate limitations while preserving order.

- [ ] **Step 5: Implement start/read/retry routes**

```python
router = APIRouter(
    prefix="/automatic-research",
    tags=["automatic-research-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.post("", response_model=AutomaticResearchStartResponse, status_code=201)
def start_automatic_research(
    payload: AutomaticResearchStartRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    started = AutomaticResearchIntakeService(db).start(
        payload.input, tenant_id=tenant_id
    )
    return AutomaticResearchStartResponse(
        case_id=started.case_id, run_id=started.run_id, status="queued"
    )


@router.get("/{case_id}", response_model=AutomaticResearchViewDTO)
def get_automatic_research(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    return AutomaticResearchQueries(db).get(case_id, tenant_id=tenant_id)
```

The retry route locks the Case, verifies the previous active run is `failed`, starts a new automatic run from the same frozen thesis ids with `trigger="retry"`, updates the lifecycle to `researching`, and returns 409 if an active run already exists.

- [ ] **Step 6: Expose workflow mode in the event list**

Add `workflow_mode: Literal["reviewed", "automatic"]` to `EventResearchListItemDTO`. Populate it from `EventResearchBrief.workflow_mode` in `_list_item`. Existing stored rows use the migration default `reviewed`.

- [ ] **Step 7: Run API, tenant, and event-list tests**

Run: `cd backend && .venv/bin/pytest -q tests/test_automatic_research_api.py tests/test_event_research_api.py tests/test_case_tenant_isolation.py`

Expected: PASS, including 404/non-disclosure behavior for another tenant's Case.

- [ ] **Step 8: Commit**

```bash
git add backend/app/queries/automatic_research.py backend/app/schemas/v1/automatic_research.py backend/app/api/v1/automatic_research.py backend/app/api/v1/router.py backend/app/schemas/v1/event_research.py backend/app/queries/event_research.py backend/tests/test_automatic_research_api.py
git commit -m "feat: expose automatic research progress and result"
```

## Task 6: Bind the frontend data contract

**Files:**
- Create: `frontend/src/domain/automaticResearch.ts`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Test: `frontend/src/tests/AutomaticResearchAdapter.test.ts`
- Regenerate: `frontend/openapi.json`
- Regenerate: `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Regenerate the contract after backend endpoints exist**

Run: `bash scripts/sync-contract.sh --update`

Expected: `frontend/openapi.json` and `frontend/src/contracts/v1.ts` contain `AutomaticResearchViewDTO` and the three new paths.

- [ ] **Step 2: Write failing adapter tests**

```typescript
import { describe, expect, it, vi } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";

describe("automatic research adapter", () => {
  it("starts with one input and maps the five-stage view", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        case_id: "case-1", run_id: "run-1", status: "queued",
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        case_id: "case-1", run_id: "run-1", title: "AI 电力需求",
        status: "completed",
        stages: [
          { key: "acquire", label: "资料获取", status: "completed", summary: "完成", started_at: null, completed_at: null },
          { key: "parse", label: "内容解析", status: "completed", summary: "完成", started_at: null, completed_at: null },
          { key: "admit", label: "证据校验", status: "completed", summary: "完成", started_at: null, completed_at: null },
          { key: "analyze", label: "分析判断", status: "completed", summary: "完成", started_at: null, completed_at: null },
          { key: "conclude", label: "生成结论", status: "completed", summary: "完成", started_at: null, completed_at: null },
        ],
        stats: { source_count: 3, admitted_evidence_count: 2, skipped_count: 1, duration_seconds: 12 },
        recent_activity: ["资料获取完成"], failure_reason: null,
        exceptions: [{ reason: "资料未通过自动准入", stage: "证据校验", count: 1 }],
        result: { label: "系统生成，未经人工审核", human_reviewed: false, conclusion: "结论", key_findings: [], counter_evidence: [], limitations: [], sources: [] },
      }), { status: 200 }));
    const client = new HttpResearchAdapter({ baseUrl: "/api/v1" });
    expect(await client.startAutomaticResearch("AI 电力需求")).toEqual({
      caseId: "case-1", runId: "run-1", status: "queued",
    });
    expect((await client.getAutomaticResearch("case-1")).stages).toHaveLength(5);
    expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ input: "AI 电力需求" }));
  });
});
```

- [ ] **Step 3: Add frontend domain types**

```typescript
export type AutomaticResearchStatus = "queued" | "running" | "completed" | "failed";
export type AutomaticStageKey = "acquire" | "parse" | "admit" | "analyze" | "conclude";

export interface AutomaticResearchView {
  caseId: string;
  runId: string;
  title: string;
  status: AutomaticResearchStatus;
  stages: Array<{
    key: AutomaticStageKey;
    label: string;
    status: "pending" | "running" | "completed" | "failed";
    summary: string;
    startedAt: string | null;
    completedAt: string | null;
  }>;
  stats: {
    sourceCount: number;
    admittedEvidenceCount: number;
    skippedCount: number;
    durationSeconds: number;
  };
  recentActivity: string[];
  exceptions: Array<{ reason: string; stage: string; count: number }>;
  failureReason: string | null;
  result: null | {
    label: "系统生成，未经人工审核";
    humanReviewed: false;
    conclusion: string;
    keyFindings: string[];
    counterEvidence: string[];
    limitations: string[];
    sources: Array<{
      title: string | null;
      url: string | null;
      role: string;
      reviewState: "automatically_admitted";
    }>;
  };
}
```

Add to `EventResearchListItem`:

```typescript
workflowMode: "reviewed" | "automatic";
```

- [ ] **Step 4: Add client methods and exact DTO mapping**

Add these methods to `ActiveResearchClient`, `researchClient`, and `HttpResearchAdapter`:

```typescript
startAutomaticResearch(input: string): Promise<{ caseId: string; runId: string; status: "queued" }>;
getAutomaticResearch(caseId: string): Promise<AutomaticResearchView>;
retryAutomaticResearch(caseId: string): Promise<{ caseId: string; runId: string; status: "queued" }>;
```

Use generated schema types for the HTTP DTO. Map every snake-case field explicitly; do not pass API objects through with type assertions.

- [ ] **Step 5: Run adapter tests, typecheck, and contract check**

Run: `cd frontend && npm test -- --run src/tests/AutomaticResearchAdapter.test.ts`

Expected: PASS.

Run: `cd frontend && npm run typecheck`

Expected: exit 0.

Run: `bash scripts/sync-contract.sh`

Expected: `contract is in sync.`

- [ ] **Step 6: Commit**

```bash
git add frontend/openapi.json frontend/src/contracts/v1.ts frontend/src/domain/automaticResearch.ts frontend/src/domain/eventResearch.ts frontend/src/domain/prototypeTypes.ts frontend/src/data/researchClient.ts frontend/src/data/httpResearchAdapter.ts frontend/src/tests/AutomaticResearchAdapter.test.ts
git commit -m "feat: bind automatic research frontend contract"
```

## Task 7: Replace the multi-step intake with one input and one button

**Files:**
- Replace: `frontend/src/features/events/EventCreatePage.tsx`
- Create: `frontend/src/styles/automatic-research.css`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/tests/AutomaticResearchPage.test.tsx`

- [ ] **Step 1: Write the failing one-click intake test**

```tsx
it("starts topic or pasted material with one click", async () => {
  const client = automaticClient({
    startAutomaticResearch: vi.fn().mockResolvedValue({
      caseId: "case-auto", runId: "run-auto", status: "queued",
    }),
  });
  setResearchClient(client);
  render(<MemoryRouter><EventCreatePage /></MemoryRouter>);
  expect(screen.queryByText("来源接入方式")).not.toBeInTheDocument();
  expect(screen.queryByText("关键因素（至少 3 个）")).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByLabelText("研究主题或材料"),
    "AI 服务器电力需求",
  );
  await userEvent.click(screen.getByRole("button", { name: "开始研究" }));
  expect(client.startAutomaticResearch).toHaveBeenCalledWith("AI 服务器电力需求");
});
```

- [ ] **Step 2: Run and verify the old multi-step UI fails the test**

Run: `cd frontend && npm test -- --run src/tests/AutomaticResearchPage.test.tsx`

Expected: FAIL because the page still renders governance and factor-confirmation controls.

- [ ] **Step 3: Replace the page with the minimal form**

The component keeps only `input`, `busy`, and `error` state:

```tsx
export function EventCreatePage() {
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(event: FormEvent) {
    event.preventDefault();
    const value = input.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await researchClient.startAutomaticResearch(value);
      navigate(`/automatic-research/${created.caseId}`);
    } catch {
      setError("研究未能启动，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return <main className="ros-page auto-start">
    <form className="auto-start__card" onSubmit={start}>
      <p className="ros-eyebrow">全自动研究</p>
      <h1>输入主题或粘贴材料</h1>
      <p>系统会自动查找资料、校验证据、完成分析并展示结果。</p>
      <label>
        <span>研究主题或材料</span>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="例如：AI 服务器电力需求；也可以直接粘贴公告或研报内容"
        />
      </label>
      {error && <p className="ros-error" role="alert">{error}</p>}
      <button className="ros-button ros-button--primary" disabled={!input.trim() || busy}>
        {busy ? "正在启动…" : "开始研究"}
      </button>
    </form>
  </main>;
}
```

- [ ] **Step 4: Add isolated responsive styles**

Define `.auto-start`, `.auto-start__card`, `.auto-research`, `.auto-stage-list`, `.auto-result`, and `.auto-process` in the new stylesheet. Keep the input card at `max-width: 760px`, use existing color variables, preserve visible focus outlines, and collapse all two-column areas below `760px`. Import it after the existing two research styles in `main.tsx`.

```css
.auto-start {
  display: grid;
  min-height: calc(100vh - 9rem);
  place-items: center;
}

.auto-start__card {
  width: min(100%, 760px);
  padding: clamp(1.5rem, 4vw, 3rem);
  border: 1px solid var(--ros-border);
  background: var(--ros-paper);
}

.auto-start__card label,
.auto-start__card label > span {
  display: grid;
  gap: .6rem;
}

.auto-start__card textarea {
  min-height: 14rem;
  resize: vertical;
}

.auto-start__card :focus-visible,
.auto-research :focus-visible {
  outline: 3px solid var(--ros-focus);
  outline-offset: 3px;
}

.auto-research__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(18rem, .42fr);
  gap: 1.5rem;
}

.auto-stage-list {
  display: grid;
  gap: .75rem;
  padding: 0;
  list-style: none;
}

.auto-result,
.auto-process {
  border: 1px solid var(--ros-border);
  background: var(--ros-paper);
  padding: 1.25rem;
}

@media (max-width: 760px) {
  .auto-research__body {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 5: Run the page test and typecheck**

Run: `cd frontend && npm test -- --run src/tests/AutomaticResearchPage.test.tsx && npm run typecheck`

Expected: PASS and exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/events/EventCreatePage.tsx frontend/src/styles/automatic-research.css frontend/src/main.tsx frontend/src/tests/AutomaticResearchPage.test.tsx
git commit -m "feat: simplify research intake to one click"
```

## Task 8: Build the read-only process and result page

**Files:**
- Create: `frontend/src/features/events/AutomaticResearchPage.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/domain/eventResearchPresentation.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/tests/AutomaticResearchPage.test.tsx`
- Create: `frontend/src/domain/automaticResearch.test.ts`

- [ ] **Step 1: Write failing presentation and page tests**

```typescript
it("routes automatic cases to the simple process page", () => {
  expect(eventDeskRoute({
    ...eventFixture,
    workflowMode: "automatic",
  })).toBe("/automatic-research/case-1");
});
```

```tsx
it("shows progress without human review controls", async () => {
  setResearchClient(automaticClient({
    getAutomaticResearch: vi.fn().mockResolvedValue(completedView),
  }));
  render(
    <MemoryRouter initialEntries={["/automatic-research/case-1"]}>
      <Routes>
        <Route path="/automatic-research/:caseId" element={<AutomaticResearchPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("系统生成，未经人工审核")).toBeInTheDocument();
  expect(screen.getAllByRole("listitem", { name: /阶段/ })).toHaveLength(5);
  expect(screen.queryByRole("button", { name: /确认|审核|授权|发布/ })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and verify route/page failures**

Run: `cd frontend && npm test -- --run src/domain/automaticResearch.test.ts src/tests/AutomaticResearchPage.test.tsx`

Expected: FAIL because the page and automatic route do not exist.

- [ ] **Step 3: Implement polling with a terminal stop**

`AutomaticResearchPage` loads immediately and polls every two seconds only while status is `queued` or `running`:

```tsx
useEffect(() => {
  let active = true;
  let timer: number | undefined;
  const load = async () => {
    try {
      const next = await researchClient.getAutomaticResearch(caseId);
      if (!active) return;
      setView(next);
      setError(null);
      if (next.status === "queued" || next.status === "running") {
        timer = window.setTimeout(load, 2_000);
      }
    } catch {
      if (active) setError("暂时无法读取研究过程，请稍后重试。");
    }
  };
  void load();
  return () => {
    active = false;
    if (timer !== undefined) window.clearTimeout(timer);
  };
}, [caseId]);
```

Render, in order:

1. title and overall status;
2. result card when present, with the machine label above the conclusion;
3. five stages with textual state, summary, and time;
4. source/admitted/skipped/duration counters;
5. a native `<details>` labelled “查看过程” containing recent activity, grouped exception reasons, limitations, and sources;
6. a “查看详情” link to `/events/:caseId`;
7. only on `failed`, one “重新运行” button calling `retryAutomaticResearch` and then polling the new run.

- [ ] **Step 4: Add the route and desk routing rule**

Add:

```tsx
<Route
  path="automatic-research/:caseId"
  element={<AutomaticResearchPage />}
/>
```

At the top of `eventDeskRoute`:

```typescript
if (event.workflowMode === "automatic") {
  return `/automatic-research/${event.id}`;
}
```

- [ ] **Step 5: Implement deterministic mock behavior**

Add a map of automatic views to `MockResearchAdapter`. `startAutomaticResearch` creates a `running` view with five stages; `getAutomaticResearch` returns it; `retryAutomaticResearch` changes failed state back to running with a new run id. The default mock fixture may complete immediately for stable e2e, but it must contain one skipped source, one automatically admitted source, one counter-evidence entry, and the machine label.

- [ ] **Step 6: Run focused frontend suites**

Run: `cd frontend && npm test -- --run src/domain/automaticResearch.test.ts src/tests/AutomaticResearchPage.test.tsx src/tests/AutomaticResearchAdapter.test.ts src/domain/eventResearchPresentation.test.ts`

Expected: PASS.

Run: `cd frontend && npm run typecheck`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/events/AutomaticResearchPage.tsx frontend/src/app/routes.tsx frontend/src/domain/eventResearchPresentation.ts frontend/src/data/mockResearchAdapter.ts frontend/src/tests/AutomaticResearchPage.test.tsx frontend/src/domain/automaticResearch.test.ts
git commit -m "feat: show automatic research process and result"
```

## Task 9: Prove the one-click flow end to end

**Files:**
- Create: `frontend/e2e/automatic-research.spec.ts`
- Modify: `README.md`

- [ ] **Step 1: Add mock-mode browser acceptance**

```typescript
import { expect, test } from "@playwright/test";

test("one input reaches a system-generated result without review gates", async ({ page }) => {
  await page.goto("/events/new?client=mock");
  await page.getByLabel("研究主题或材料").fill("AI 服务器电力需求");
  await page.getByRole("button", { name: "开始研究" }).click();
  await expect(page).toHaveURL(/\/automatic-research\//);
  await expect(page.getByText("系统生成，未经人工审核")).toBeVisible();
  await expect(page.getByText("资料获取")).toBeVisible();
  await expect(page.getByText("内容解析")).toBeVisible();
  await expect(page.getByText("证据校验")).toBeVisible();
  await expect(page.getByText("分析判断")).toBeVisible();
  await expect(page.getByText("生成结论")).toBeVisible();
  await expect(page.getByRole("button", { name: /确认|审核|授权|发布/ })).toHaveCount(0);
});
```

- [ ] **Step 2: Add a backend worker-chain integration test**

Extend `backend/tests/test_automatic_research_pipeline.py` with a SQLite file-backed session factory and fake source adapters. Run one intake, one research worker claim, acquisition claims to terminal, and the resumed research worker. Assert:

```python
assert lifecycle.status == "completed"
assert run.status == "succeeded"
assert conclusion.state == "system_generated"
assert session.query(TaskItem).filter(
    TaskItem.research_case_id == case.id,
    TaskItem.status.in_(("open", "in_progress")),
).count() == 0
```

- [ ] **Step 3: Document the required runtime**

Add a concise README section with the three supervised processes:

```bash
cd backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m app.scripts.run_research_worker --loop
.venv/bin/python -m app.scripts.run_acquisition_worker --loop
```

State explicitly that the one-click UI is only truly automatic when both workers are supervised and source credentials are configured. Preparation worker remains necessary only for legacy reviewed Cases.

- [ ] **Step 4: Run the full backend verification**

Run: `cd backend && .venv/bin/pytest -q`

Expected: PASS with only environment-gated PostgreSQL/Neo4j skips.

- [ ] **Step 5: Run the full frontend verification**

Run: `cd frontend && npm run typecheck && npm test`

Expected: exit 0 and all Vitest suites PASS.

Run: `cd frontend && npm run e2e -- automatic-research.spec.ts`

Expected: PASS.

- [ ] **Step 6: Run contract and migration checks**

Run: `bash scripts/sync-contract.sh`

Expected: `contract is in sync.`

Run: `cd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic downgrade 0058 && .venv/bin/alembic upgrade head`

Expected: all three commands exit 0 on a disposable test database.

- [ ] **Step 7: Check the first-version product boundary**

Run:

```bash
rg -n "确认候选|确认研究协议|授权补证|证据审核|发布结论" \
  frontend/src/features/events/EventCreatePage.tsx \
  frontend/src/features/events/AutomaticResearchPage.tsx
```

Expected: no matches.

- [ ] **Step 8: Commit**

```bash
git add frontend/e2e/automatic-research.spec.ts README.md
git commit -m "test: verify one-click automatic research flow"
```

## Final acceptance checklist

- [ ] Topic input and pasted-material input both use the same field and button.
- [ ] A normal automatic run never enters any preparation, claim-review, proposal-review, or conclusion-review wait state.
- [ ] Governed source acquisition and automatic admission remain mandatory; no unrestricted web fallback is added.
- [ ] A failed source is counted and displayed but does not block other sources.
- [ ] No usable evidence across all allowed attempts produces an overall failure and one retry action.
- [ ] Some usable evidence plus unresolved gaps produces a completed, explicitly limited result.
- [ ] API or worker restart resumes from persisted research and acquisition jobs.
- [ ] The result and every automatically admitted citation remain labelled as machine-generated and not human-reviewed.
- [ ] Existing reviewed Cases, preparation pages, audit history, and professional detail routes remain readable.
- [ ] The default automatic UI exposes no configuration, manual scope edit, review, authorization, pause, or publish controls.
