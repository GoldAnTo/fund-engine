from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.event_research import (
    EventResearchBrief,
    EventResearchFactorDraft,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    ReviewDecision,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    ResearchRun,
    ResearchTask,
)
from app.models.research_monitor import ResearchRunEvent
from app.models.research_preparation import ResearchPreparation
from app.models.research_protocol import (
    CaseMechanismSelectionVersion,
    OutcomeBindingVersion,
    VerificationRuleVersion,
)
from app.repositories.auto_research import AutoResearchRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction
from app.services.event_research import EventResearchService, InitialUploadedOriginal


class FakeExtractor:
    def __init__(self, *, input_kind: str = "topic") -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.input_kind = input_kind

    def extract(self, raw_input: str, source_url: str | None) -> EventExtraction:
        self.calls.append((raw_input, source_url))
        return EventExtraction(
            event_title=raw_input[:80],
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
            input_kind=self.input_kind,
        )


def _scope_event(session, run_id: uuid.UUID) -> ResearchRunEvent:
    event = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run_id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    return event


def _automatic_request(raw_input: str) -> CreateEventResearchRequest:
    return CreateEventResearchRequest(
        raw_input=raw_input,
        source_type="uploaded_file",
        source_metadata={
            "authority_level": "user_supplied",
            "permissions": {"ai_processing": True, "display": True},
        },
        event_title="自动上传研究",
        research_question="上传材料的关键变化是什么？",
        candidate_factors=["需求变化", "供给约束", "替代解释"],
        research_protocol_required=False,
        created_by="tenant:upload-team",
    )


def test_topic_input_creates_one_automatic_case_and_queued_run(session) -> None:
    extractor = FakeExtractor()
    raw_input = "光模块行业需求会如何变化"

    started = AutomaticResearchIntakeService(session, extractor=extractor).start(
        raw_input,
        tenant_id="research-team",
        actor_subject_id="researcher",
    )

    case_id = uuid.UUID(started.case_id)
    run_id = uuid.UUID(started.run_id)
    brief = session.scalar(
        select(EventResearchBrief).where(EventResearchBrief.research_case_id == case_id)
    )
    lifecycle = session.get(EventResearchLifecycle, case_id)
    runs = list(
        session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id))
    )
    case = session.get(ResearchCase, case_id)
    admission = session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    factor_drafts = list(
        session.scalars(
            select(EventResearchFactorDraft).where(
                EventResearchFactorDraft.research_case_id == case_id
            )
        )
    )
    theses = list(
        session.scalars(select(Thesis).where(Thesis.research_case_id == case_id))
    )
    scope_version = session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    preparations = list(
        session.scalars(
            select(ResearchPreparation).where(
                ResearchPreparation.research_case_id == case_id
            )
        )
    )
    jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind == "research_run", Job.research_case_id == case_id
            )
        )
    )
    tasks = list(
        session.scalars(select(ResearchTask).where(ResearchTask.run_id == run_id))
    )
    assert extractor.calls == [(raw_input, None)]
    assert brief is not None
    assert brief.workflow_mode == "automatic"
    assert brief.source_metadata["intake_role"] == "research_prompt"
    assert brief.extraction_state == "system_generated"
    assert lifecycle is not None
    assert lifecycle.status == "researching"
    assert lifecycle.active_run_id == run_id
    assert lifecycle.next_human_action is None
    assert len(runs) == 1
    assert runs[0].id == run_id
    assert runs[0].status == "queued"
    assert case is not None and case.created_by == "human:researcher"
    assert admission is not None
    assert admission.admitted_by == "human:researcher"
    assert {draft.created_by for draft in factor_drafts} == {
        "system:automatic-intake"
    }
    assert {thesis.created_by for thesis in theses} == {
        "system:automatic-intake"
    }
    assert {thesis.creator_type for thesis in theses} == {"ai"}
    assert scope_version is not None
    assert scope_version.changed_by == "system:automatic-intake"
    scope = _scope_event(session, run_id).payload_json
    thesis_ids = {str(thesis.id) for thesis in theses}
    assert preparations == []
    assert list(session.scalars(select(ReviewDecision))) == []
    assert list(session.scalars(select(OutcomeBindingVersion))) == []
    assert list(session.scalars(select(CaseMechanismSelectionVersion))) == []
    assert list(session.scalars(select(VerificationRuleVersion))) == []
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].target_id == run_id
    assert set(runs[0].scope_thesis_ids or []) == thesis_ids
    assert set(scope["factor_ids"]) == thesis_ids
    assert scope["input_kind"] == "topic"
    assert {str(task.thesis_id) for task in tasks} == thesis_ids
    assert all(task.task_type != "intake_material" for task in tasks)
    assert scope["automatic_protocol"] == {
        "generated_by": "system",
        "research_question": f"{raw_input} 的关键变化是什么？",
        "factors": ["需求变化", "供给约束", "替代解释"],
        "conclusion_rule": "report support, contradiction, and insufficiency separately",
    }
    assert len(scope["automatic_evidence_plan"]["items"]) == 3


def test_pasted_material_creates_queued_run_without_human_preparation(session) -> None:
    raw_input = (
        "公司披露最新产能建设进度低于原计划，同时下游客户的订单节奏发生变化。"
    )

    started = AutomaticResearchIntakeService(
        session, extractor=FakeExtractor(input_kind="material")
    ).start(
        raw_input,
        tenant_id="material-team",
        actor_subject_id="researcher",
    )

    case_id = uuid.UUID(started.case_id)
    run_id = uuid.UUID(started.run_id)
    run = session.get(ResearchRun, run_id)
    brief = session.scalar(
        select(EventResearchBrief).where(EventResearchBrief.research_case_id == case_id)
    )
    admission = session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    preparations = list(
        session.scalars(
            select(ResearchPreparation).where(
                ResearchPreparation.research_case_id == case_id
            )
        )
    )
    assert run is not None
    assert run.research_case_id == case_id
    assert run.status == "queued"
    assert brief is not None
    assert admission is not None
    assert brief.source_metadata["intake_role"] == "provided_material"
    scope = _scope_event(session, run_id).payload_json
    assert scope["input_kind"] == "material"
    assert scope["intake_material_document_version_id"] == str(
        admission.initial_document_version_id
    )
    material_tasks = list(
        session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run_id,
                ResearchTask.task_type == "intake_material",
            )
        )
    )
    assert len(material_tasks) == 3
    assert {str(task.thesis_id) for task in material_tasks} == set(
        run.scope_thesis_ids or []
    )
    assert all(task.round == 1 and task.status == "queued" for task in material_tasks)
    assert started.preparation_id is None
    assert preparations == []


def test_blank_input_is_rejected_before_extraction(session) -> None:
    extractor = FakeExtractor()

    with pytest.raises(ValueError, match="automatic research input must not be blank"):
        AutomaticResearchIntakeService(session, extractor=extractor).start(
            "  \n\t ",
            tenant_id="research-team",
            actor_subject_id="researcher",
        )

    assert extractor.calls == []


@pytest.mark.parametrize(
    ("tenant_id", "message"),
    [
        ("", "automatic research tenant_id must not be blank"),
        ("  \n\t ", "automatic research tenant_id must not be blank"),
        (
            "t" * 122,
            "automatic research tenant_id must not exceed 121 characters",
        ),
    ],
)
def test_invalid_tenant_is_rejected_before_extraction_or_writes(
    session, tenant_id: str, message: str
) -> None:
    extractor = FakeExtractor()

    with pytest.raises(ValueError, match=message):
        AutomaticResearchIntakeService(session, extractor=extractor).start(
            "有效的研究主题",
            tenant_id=tenant_id,
            actor_subject_id="researcher",
        )

    assert extractor.calls == []
    assert list(session.scalars(select(ResearchCase))) == []


def test_padded_tenant_is_normalized_for_case_and_admission(session) -> None:
    started = AutomaticResearchIntakeService(
        session, extractor=FakeExtractor()
    ).start(
        "有效的研究主题",
        tenant_id="  padded-team \n",
        actor_subject_id="researcher",
    )

    case_id = uuid.UUID(started.case_id)
    case = session.get(ResearchCase, case_id)
    admission = session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    assert case is not None
    assert case.created_by == "human:researcher"
    assert admission is not None
    assert admission.tenant_id == "padded-team"
    assert admission.admitted_by == "human:researcher"


@pytest.mark.parametrize("tenant_id", ["  padded-\x00team \n", "  padded-\u0085team \n"])
def test_tenant_control_characters_remaining_after_normalization_are_rejected(
    session, tenant_id: str
) -> None:
    extractor = FakeExtractor()

    with pytest.raises(
        ValueError,
        match="automatic research tenant_id must not contain control characters",
    ):
        AutomaticResearchIntakeService(session, extractor=extractor).start(
            "有效的研究主题",
            tenant_id=tenant_id,
            actor_subject_id="researcher",
        )

    assert extractor.calls == []
    assert list(session.scalars(select(ResearchCase))) == []


def test_automatic_uploaded_original_reuses_and_transitions_one_lifecycle(
    session,
) -> None:
    created = EventResearchService(session).create(
        _automatic_request("这是自动研究上传材料的摘要。"),
        tenant_id="upload-team",
        workflow_mode="automatic",
        initial_uploaded_original=InitialUploadedOriginal(
            raw="这是自动研究上传材料的冻结原文。".encode(),
            file_name="automatic-original.txt",
            mime_type="text/plain",
            source_metadata={
                "authority_level": "user_supplied",
                "permissions": {"ai_processing": True, "display": True},
            },
        ),
    )

    case_id = uuid.UUID(created.case_id)
    lifecycles = list(
        session.scalars(
            select(EventResearchLifecycle).where(
                EventResearchLifecycle.research_case_id == case_id
            )
        )
    )
    assert len(lifecycles) == 1
    assert lifecycles[0].status == "researching"
    assert lifecycles[0].active_run_id == uuid.UUID(created.run_id)
    assert lifecycles[0].current_round == 1
    assert lifecycles[0].status_summary == "自动研究已排队"
    assert lifecycles[0].current_gap is None
    assert lifecycles[0].next_human_action is None
    assert created.preparation_id is None


def test_automatic_enqueue_failure_rolls_back_the_entire_intake(
    session, monkeypatch
) -> None:
    def fail_enqueue(self, run):
        raise RuntimeError("automatic run enqueue failed")

    monkeypatch.setattr(AutoResearchRepository, "enqueue_run_job", fail_enqueue)

    with pytest.raises(RuntimeError, match="automatic run enqueue failed"):
        EventResearchService(session).create(
            _automatic_request("自动研究事务回滚测试。"),
            tenant_id="rollback-team",
            workflow_mode="automatic",
        )

    assert list(session.scalars(select(ResearchCase))) == []
    assert list(session.scalars(select(DocumentVersion))) == []
    assert list(session.scalars(select(CaseTenantAdmission))) == []
    assert list(session.scalars(select(EventResearchBrief))) == []
    assert list(session.scalars(select(EventResearchFactorDraft))) == []
    assert list(session.scalars(select(EventResearchScopeVersion))) == []
    assert list(session.scalars(select(Thesis))) == []
    assert list(session.scalars(select(ResearchRun))) == []
    assert list(session.scalars(select(ResearchTask))) == []
    assert list(session.scalars(select(Job))) == []
    assert list(session.scalars(select(ResearchRunEvent))) == []
    assert list(session.scalars(select(EventResearchLifecycle))) == []
