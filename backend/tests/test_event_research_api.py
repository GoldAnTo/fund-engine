from __future__ import annotations

import uuid
from datetime import datetime, timezone
import hashlib

from sqlalchemy import select

from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.proposals import Proposal
from app.repositories.operational import TaskRepository
from app.services.event_conclusion import EventConclusionService


def _confirmed_event() -> dict:
    return {
        "raw_input": "Alphabet 公布财报后上调全年资本开支指引，盘后股价下跌。",
        "source_url": "https://example.com/alphabet",
        "event_title": "Alphabet 财报后股价下跌",
        "company_name": "Alphabet",
        "ticker": "GOOGL",
        "market_reaction": "盘后下跌",
        "research_question": "资本开支上调是否是盘后下跌的主要因素？",
        "candidate_factors": [
            "资本开支上调可能加剧自由现金流担忧",
            "盈利前景与市场预期可能存在分歧",
            "估值重定价可能放大盘后波动",
        ],
        "created_by": "xiongjiali",
    }


def _evidence_proposal(
    cmd_session,
    case_id: uuid.UUID,
    *,
    source_url: str,
    title: str,
    document_case_id: uuid.UUID | None = None,
    thesis_case_id: uuid.UUID | None = None,
    thesis_id: uuid.UUID | None = None,
) -> Proposal:
    now = datetime.now(timezone.utc)
    thesis = cmd_session.get(Thesis, thesis_id) if thesis_id is not None else Thesis(
        research_case_id=thesis_case_id or case_id,
        statement=f"evidence from {title}",
        created_by="tester",
        created_at=now,
    )
    assert thesis is not None
    document = DocumentVersion(
        content_sha256=hashlib.sha256(source_url.encode()).hexdigest(),
        source_url=source_url,
        title=title,
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    if thesis_id is None:
        cmd_session.add(thesis)
    cmd_session.add(document)
    cmd_session.flush()
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=document_case_id or case_id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="Fixture evidence excerpt",
        locator={"kind": "fixture"},
    )
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text=f"Evidence statement from {title}",
        created_at=now,
    )
    cmd_session.add(statement)
    cmd_session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": f"Evidence proposed from {title}",
            "scope": {"period": "event"},
        },
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=now,
        research_case_id=case_id,
        status="pending",
    )
    cmd_session.add(proposal)
    cmd_session.commit()
    return proposal


def test_extract_event_keeps_unknown_fields_null_and_marks_confirmation(cmd_client) -> None:
    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={
            "raw_input": "公司宣布新指引，盘后下跌。",
            "source_url": "https://example.com/brief",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["company_name"] is None
    assert body["ticker"] is None
    assert body["confirmation_required"] is True
    assert body["research_question"]
    assert len(body["candidate_factors"]) in {3, 4, 5}


def test_create_event_case_enqueues_research_without_manual_run_button(cmd_client, cmd_session) -> None:
    response = cmd_client.post("/api/v1/event-research", json=_confirmed_event())

    assert response.status_code == 201
    body = response.json()
    case_id = body["case_id"]
    assert body["lifecycle"]["status"] == "researching"
    assert body["lifecycle"]["active_run_id"]
    assert body["lifecycle"]["next_human_action"] is None

    parsed_case_id = uuid.UUID(case_id)
    assert cmd_session.get(EventResearchBrief, uuid.UUID(body["brief_id"])).research_case_id == parsed_case_id
    assert cmd_session.get(EventResearchLifecycle, parsed_case_id).active_run_id
    assert len(cmd_session.query(EventResearchFactorDraft).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert len(cmd_session.query(Thesis).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert cmd_session.get(ResearchRun, uuid.UUID(body["lifecycle"]["active_run_id"]))
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == parsed_case_id,
            EventResearchScopeVersion.version == 1,
        )
    )
    assert scope is not None
    assert list(
        cmd_session.scalars(
            select(EventResearchScopeFactor.statement)
            .where(EventResearchScopeFactor.scope_version_id == scope.id)
            .order_by(EventResearchScopeFactor.position)
        )
    ) == _confirmed_event()["candidate_factors"]


def test_create_event_case_rejects_candidate_factors_duplicate_after_trimming(cmd_client) -> None:
    payload = _confirmed_event()
    payload["candidate_factors"] = ["factor a", " factor a ", "factor c"]

    response = cmd_client.post("/api/v1/event-research", json=payload)

    assert response.status_code == 422


def test_confirmed_event_proposal_is_mapped_into_current_scope_conclusion(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    active_thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == _confirmed_event()["candidate_factors"][0],
        )
    )
    assert active_thesis is not None
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified investor relations release",
        thesis_id=active_thesis.id,
    )

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "verified primary source supports the active factor",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 201
    evidence_link_id = uuid.UUID(response.json()["published_entity_id"])
    assignment = cmd_session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == evidence_link_id
        )
    )
    assert assignment is not None
    assert assignment.disposition == "mapped"
    assert assignment.factor_statement == active_thesis.statement


def test_confirmed_event_proposal_is_assigned_to_latest_scope_version(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    active_thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == _confirmed_event()["candidate_factors"][0],
        )
    )
    assert active_thesis is not None
    scope = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                active_thesis.statement,
                "广告业务增长弱于市场预期",
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    assert scope.status_code == 200
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified release after scope update",
        thesis_id=active_thesis.id,
    )

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "publish after scope update",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 201
    assignment = cmd_session.scalar(
        select(EventResearchScopeEvidenceAssignment)
        .join(
            EventResearchScopeVersion,
            EventResearchScopeVersion.id
            == EventResearchScopeEvidenceAssignment.scope_version_id,
        )
        .where(
            EventResearchScopeEvidenceAssignment.evidence_link_id
            == uuid.UUID(response.json()["published_entity_id"]),
            EventResearchScopeVersion.research_case_id == case_id,
        )
    )
    assert assignment is not None
    assert assignment.disposition == "mapped"
    assert cmd_session.scalar(
        select(EventResearchScopeVersion.version).where(
            EventResearchScopeVersion.id == assignment.scope_version_id
        )
    ) == 2


def test_create_event_case_freezes_and_attaches_pasted_news(cmd_client, cmd_session) -> None:
    payload = _confirmed_event()
    response = cmd_client.post("/api/v1/event-research", json=payload)

    assert response.status_code == 201
    case_id = uuid.UUID(response.json()["case_id"])
    documents = cmd_session.execute(
        select(DocumentVersion)
        .join(
            CaseDocumentVersion,
            CaseDocumentVersion.document_version_id == DocumentVersion.id,
        )
        .where(CaseDocumentVersion.research_case_id == case_id)
    ).scalars().all()
    assert len(documents) == 1
    document = documents[0]
    assert document.source_url == payload["source_url"]
    assert document.parser_version == "user-pasted-v1"
    assert document.parse_state == "partial"
    spans = cmd_session.scalars(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    ).all()
    assert len(spans) == 1
    assert spans[0].locator == {"kind": "user_pasted_news"}
    assert spans[0].verbatim_text == payload["raw_input"]


def test_invalid_fixture_evidence_is_auditable_but_cannot_publish_formal_link(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    proposal = _evidence_proposal(
        cmd_session,
        uuid.UUID(created["case_id"]),
        source_url="https://example.com/fixture",
        title="Fixture article",
    )

    queue = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/review-queue"
    )
    assert queue.status_code == 200
    item = next(
        item for item in queue.json()["items"] if item["proposal_id"] == str(proposal.id)
    )
    assert item["source_status"] == "invalid"
    assert item["can_accept"] is False

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "attempt to publish a fixture",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 422
    assert cmd_session.get(Proposal, proposal.id).status == "pending"
    assert cmd_session.scalar(
        select(EvidenceLink.id).where(
            EvidenceLink.thesis_id == uuid.UUID(proposal.target_context["thesis_id"])
        )
    ) is None


def test_event_evidence_cannot_use_document_bound_to_another_case(
    cmd_client, cmd_session
) -> None:
    current = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    other_payload = _confirmed_event()
    other_payload["event_title"] = "另一独立事件"
    other_payload["research_question"] = "另一事件需要什么证据？"
    other = cmd_client.post("/api/v1/event-research", json=other_payload).json()
    proposal = _evidence_proposal(
        cmd_session,
        uuid.UUID(current["case_id"]),
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Another case's verified source",
        document_case_id=uuid.UUID(other["case_id"]),
    )

    queue = cmd_client.get(
        f"/api/v1/event-research/{current['case_id']}/review-queue"
    )
    assert queue.status_code == 200
    item = next(
        item for item in queue.json()["items"] if item["proposal_id"] == str(proposal.id)
    )
    assert item["source_status"] == "invalid"
    assert item["can_accept"] is False
    assert item["document_source_url"] is None
    assert item["verbatim_text"] is None

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "cross-case source must not publish",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 422
    assert cmd_session.get(Proposal, proposal.id).status == "pending"
    assert cmd_session.scalar(
        select(EvidenceLink.id).where(
            EvidenceLink.source_statement_id
            == uuid.UUID(proposal.payload["source_statement_id"])
        )
    ) is None


def test_event_evidence_cannot_target_thesis_from_another_case(
    cmd_client, cmd_session
) -> None:
    current = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    other_payload = _confirmed_event()
    other_payload["event_title"] = "另一独立命题事件"
    other_payload["research_question"] = "另一事件需要什么证据？"
    other = cmd_client.post("/api/v1/event-research", json=other_payload).json()
    proposal = _evidence_proposal(
        cmd_session,
        uuid.UUID(current["case_id"]),
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Current case verified source",
        thesis_case_id=uuid.UUID(other["case_id"]),
    )

    queue = cmd_client.get(
        f"/api/v1/event-research/{current['case_id']}/review-queue"
    )
    assert queue.status_code == 200
    item = next(
        item for item in queue.json()["items"] if item["proposal_id"] == str(proposal.id)
    )
    assert item["source_status"] == "invalid"
    assert item["can_accept"] is False

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "cross-case thesis must not publish",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 422
    assert cmd_session.get(Proposal, proposal.id).status == "pending"
    assert cmd_session.scalar(
        select(EvidenceLink.id).where(
            EvidenceLink.source_statement_id
            == uuid.UUID(proposal.payload["source_statement_id"])
        )
    ) is None


def test_invalid_fixture_decision_does_not_block_valid_event_evidence_progression(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    invalid = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://example.com/fixture",
        title="Fixture article",
    )
    valid = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified investor relations release",
    )
    tasks = TaskRepository(cmd_session)
    invalid_task = tasks.add_task(
        title="review fixture evidence",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case_id,
    )
    valid_task = tasks.add_task(
        title="review verified evidence",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=valid.id,
        research_case_id=case_id,
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = "awaiting_key_review"
    lifecycle.next_human_action = "审核 2 条关键证据"
    cmd_session.commit()

    queue = cmd_client.get(f"/api/v1/event-research/{case_id}/review-queue")
    assert queue.status_code == 200
    assert queue.json()["summary"]["invalid_source"] == 1
    assert queue.json()["summary"]["pending"] == 1

    rejected = cmd_client.post(
        f"/api/v1/review-proposals/{invalid.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "fixture must not publish",
            "reviewer_id": "reviewer",
            "expected_version": invalid.version,
        },
    )

    assert rejected.status_code == 422
    assert cmd_session.get(Proposal, invalid.id).status == "pending"
    assert cmd_session.scalar(
        select(EvidenceLink.id).where(
            EvidenceLink.thesis_id == uuid.UUID(invalid.target_context["thesis_id"])
        )
    ) is None

    accepted = cmd_client.post(
        f"/api/v1/review-proposals/{valid.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "verified primary source supports the factor",
            "reviewer_id": "reviewer",
            "expected_version": valid.version,
        },
    )

    assert accepted.status_code == 201
    cmd_session.refresh(lifecycle)
    cmd_session.refresh(invalid_task)
    cmd_session.refresh(valid_task)
    assert lifecycle.status == "continuing"
    assert lifecycle.next_human_action is None
    assert invalid_task.status == "open"
    assert valid_task.status == "done"
    assert cmd_session.scalar(
        select(EvidenceLink.id).where(
            EvidenceLink.thesis_id == uuid.UUID(valid.target_context["thesis_id"])
        )
    ) is not None


def test_create_event_requires_confirmed_question_and_three_to_five_factors(cmd_client) -> None:
    payload = _confirmed_event()
    payload["candidate_factors"] = payload["candidate_factors"][:2]

    response = cmd_client.post("/api/v1/event-research", json=payload)
    assert response.status_code == 422


def test_event_list_orders_independent_events_by_last_update(cmd_client, cmd_session) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "台积电上调 CoWoS 指引后下跌"
    second_payload["ticker"] = "TSM"
    second_payload["research_question"] = "产能扩张是否是价格反应的主要因素？"
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()

    first_lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(first["case_id"]))
    second_lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(second["case_id"]))
    second_lifecycle.updated_at = first_lifecycle.updated_at.replace(year=first_lifecycle.updated_at.year + 1)
    cmd_session.commit()

    response = cmd_client.get("/api/v1/event-research", params={"status": "researching"})
    assert response.status_code == 200
    body = response.json()
    assert [item["case_id"] for item in body["items"]] == [second["case_id"], first["case_id"]]
    assert body["items"][0]["event_title"] == "台积电上调 CoWoS 指引后下跌"
    assert body["items"][0]["ticker"] == "TSM"
    assert body["items"][0]["lifecycle_status"] == "researching"
    assert body["items"][0]["next_human_action"] is None


def test_event_workbench_never_surfaces_another_case_factors_or_lifecycle(cmd_client) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "另一独立新闻事件"
    second_payload["research_question"] = "另一事件的主要因素是什么？"
    second_payload["candidate_factors"] = ["因素甲", "因素乙", "因素丙"]
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()

    first_view = cmd_client.get(f"/api/v1/event-research/{first['case_id']}/workbench")
    second_view = cmd_client.get(f"/api/v1/event-research/{second['case_id']}/workbench")

    assert first_view.status_code == 200
    assert first_view.json()["event"]["event_title"] == "Alphabet 财报后股价下跌"
    assert {item["statement"] for item in first_view.json()["factors"]} == set(
        _confirmed_event()["candidate_factors"]
    )
    assert second_view.json()["event"]["event_title"] == "另一独立新闻事件"
    assert {item["statement"] for item in second_view.json()["factors"]} == {"因素甲", "因素乙", "因素丙"}
    assert all(item["case_id"] == first["case_id"] for item in first_view.json()["evidence"])


def test_event_workbench_exposes_a_reviewable_draft_when_research_is_ready(cmd_client, cmd_session) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(created["case_id"]))
    lifecycle.status = "draft_ready"
    lifecycle.status_summary = "关键证据已审核，等待结论复核"
    lifecycle.next_human_action = "审核结论草案"
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{created['case_id']}/workbench")

    assert response.status_code == 200
    assert response.json()["conclusion"]["state"] == "ai_draft"
    assert "已审核" in response.json()["conclusion"]["text"]


def test_event_conclusion_publish_appends_a_human_confirmed_result(cmd_client, cmd_session) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = "draft_ready"
    for index, factor in enumerate(_confirmed_event()["candidate_factors"]):
        thesis = cmd_session.scalar(
            select(Thesis).where(
                Thesis.research_case_id == case_id,
                Thesis.statement == factor,
            )
        )
        assert thesis is not None
        proposal = _evidence_proposal(
            cmd_session,
            case_id,
            source_url=f"https://investor.tsmc.com/english/quarterly-results/{index}",
            title=f"Verified release {index}",
            thesis_id=thesis.id,
        )
        confirmed = cmd_client.post(
            f"/api/v1/review-proposals/{proposal.id}/decisions",
            json={
                "outcome": "confirmed",
                "reason": "verified primary source supports the active factor",
                "reviewer_id": "reviewer",
                "expected_version": proposal.version,
            },
        )
        assert confirmed.status_code == 201
    EventConclusionService(cmd_session).create_draft(case_id)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{created['case_id']}/conclusion/publish",
        json={"text": "人工确认：当前材料不足以断定唯一原因。", "reviewer": "xiongjiali"},
    )

    assert response.status_code == 201
    assert response.json()["state"] == "published"
    view = cmd_client.get(f"/api/v1/event-research/{created['case_id']}/workbench").json()
    assert view["lifecycle"]["status"] == "published"
    assert view["conclusion"]["state"] == "published"
    assert view["conclusion"]["text"] == "人工确认：当前材料不足以断定唯一原因。"
