from __future__ import annotations

import uuid
from datetime import datetime, timezone
import hashlib

import pytest
from sqlalchemy import event as sqlalchemy_event, select

from app.models.event_research import (
    CaseRelation,
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
from app.services.event_review_queue import EventReviewQueueService
from app.services.source_governance import SourceGovernanceService


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
        # Existing workflow tests deliberately exercise the legacy Case path.
        # New intake must use the API default and is covered separately below.
        "research_protocol_required": False,
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
    if thesis_id is not None:
        thesis = cmd_session.get(Thesis, thesis_id)
    elif thesis_case_id is not None:
        thesis = Thesis(
            research_case_id=thesis_case_id,
            statement=f"evidence from {title}",
            created_by="tester",
            created_at=now,
        )
    else:
        # Event evidence must target an active current-scope factor.  Tests
        # that exercise cross-case handling opt in through thesis_case_id.
        thesis = cmd_session.scalar(
            select(Thesis)
            .where(Thesis.research_case_id == case_id)
            .order_by(Thesis.created_at, Thesis.id)
            .limit(1)
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
    if thesis_id is None and thesis_case_id is not None:
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


def test_extract_event_keeps_unknown_fields_null_and_marks_confirmation(
    cmd_client, monkeypatch
) -> None:
    from app.api.v1 import event_research as event_research_api
    from app.services.event_extraction import EventExtractionService

    class ValidExtractionClient:
        def chat_json(self, messages, schema_hint):
            return {
                "research_question": "新指引是否改变了市场预期？",
                "candidate_factors": ["新指引", "盘后交易", "市场预期"],
            }

    monkeypatch.setattr(
        event_research_api,
        "EventExtractionService",
        lambda: EventExtractionService(client=ValidExtractionClient()),
    )
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


def test_extract_event_maps_provider_setup_failure_to_safe_503(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env",
        lambda: (_ for _ in ()).throw(
            RuntimeError("provider setup failed with secret sk-private")
        ),
    )

    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={"raw_input": "公司宣布新指引，盘后下跌。", "source_url": None},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert (
        error["message"]
        == "event extraction LLM is unavailable or returned an invalid response"
    )
    assert "sk-private" not in response.text


def test_extract_event_redacts_provider_http_exception(
    cmd_client, monkeypatch
) -> None:
    from fastapi import HTTPException

    class HttpFailureClient:
        def chat_json(self, messages, schema_hint):
            raise HTTPException(
                status_code=418,
                detail="provider HTTP failure exposed secret sk-private",
            )

    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env", HttpFailureClient
    )

    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={"raw_input": "公司宣布新指引，盘后下跌。", "source_url": None},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert (
        error["message"]
        == "event extraction LLM is unavailable or returned an invalid response"
    )
    assert "sk-private" not in response.text
    assert "provider HTTP failure" not in response.text


@pytest.mark.parametrize("programming_error", [AssertionError, AttributeError])
def test_extract_event_does_not_map_programming_errors_to_upstream_503(
    cmd_client, monkeypatch, programming_error
) -> None:
    class BrokenClient:
        def chat_json(self, messages, schema_hint):
            raise programming_error("programming defect")

    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env", BrokenClient
    )

    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={"raw_input": "公司宣布新指引，盘后下跌。", "source_url": None},
    )

    assert response.status_code == 500
    assert "upstream_unavailable" not in response.text


def test_extract_event_maps_provider_call_failure_to_safe_503(
    cmd_client, monkeypatch
) -> None:
    from app.api.v1 import event_research as event_research_api
    from app.services.event_extraction import EventExtractionService

    class FailingExtractionClient:
        def chat_json(self, messages, schema_hint):
            raise ConnectionError("provider call exposed secret sk-private")

    monkeypatch.setattr(
        event_research_api,
        "EventExtractionService",
        lambda: EventExtractionService(client=FailingExtractionClient()),
    )

    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={"raw_input": "公司宣布新指引，盘后下跌。", "source_url": None},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert (
        error["message"]
        == "event extraction LLM is unavailable or returned an invalid response"
    )
    assert "sk-private" not in response.text


def test_extract_event_maps_invalid_provider_result_to_safe_503(
    cmd_client, monkeypatch
) -> None:
    from app.api.v1 import event_research as event_research_api
    from app.services.event_extraction import EventExtractionService

    class InvalidExtractionClient:
        def chat_json(self, messages, schema_hint):
            return {
                "research_question": "哪些因素需要验证？",
                "candidate_factors": ["重复因素", "重复因素"],
            }

    monkeypatch.setattr(
        event_research_api,
        "EventExtractionService",
        lambda: EventExtractionService(client=InvalidExtractionClient()),
    )

    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={"raw_input": "公司宣布新指引，盘后下跌。", "source_url": None},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert (
        error["message"]
        == "event extraction LLM is unavailable or returned an invalid response"
    )


def test_create_event_case_freezes_intake_and_waits_for_human_review_before_any_run(cmd_client, cmd_session) -> None:
    payload = _confirmed_event()
    payload.update({"source_type": "uploaded_file", "source_metadata": {"file_name": "event-note.txt", "mime_type": "text/plain", "byte_size": 42}})
    response = cmd_client.post("/api/v1/event-research", json=payload)

    assert response.status_code == 201
    body = response.json()
    case_id = body["case_id"]
    assert body["lifecycle"]["status"] == "awaiting_key_review"
    assert body["lifecycle"]["active_run_id"] is None
    assert body["lifecycle"]["next_human_action"] == "核验原文资料并完成研究协议"

    parsed_case_id = uuid.UUID(case_id)
    assert cmd_session.get(EventResearchBrief, uuid.UUID(body["brief_id"])).research_case_id == parsed_case_id
    brief = cmd_session.get(EventResearchBrief, uuid.UUID(body["brief_id"]))
    assert brief.source_type == "uploaded_file"
    assert brief.source_metadata["file_name"] == "event-note.txt"
    assert cmd_session.get(EventResearchLifecycle, parsed_case_id).active_run_id is None
    assert len(cmd_session.query(EventResearchFactorDraft).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert len(cmd_session.query(Thesis).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert cmd_session.query(ResearchRun).filter_by(research_case_id=parsed_case_id).count() == 0
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


def test_public_url_intake_keeps_the_url_type_and_unverified_snapshot_boundary(
    cmd_client, cmd_session
) -> None:
    payload = _confirmed_event()
    payload.update(
        {
            "source_type": "public_url",
            "source_url": "https://www.szse.cn/disclosure/listed/notice/index.html",
            "source_metadata": {"permissions": {"ai_processing": False, "display": True}},
        }
    )

    response = cmd_client.post("/api/v1/event-research", json=payload)

    assert response.status_code == 201
    case_id = uuid.UUID(response.json()["case_id"])
    brief = cmd_session.get(EventResearchBrief, uuid.UUID(response.json()["brief_id"]))
    assert brief is not None
    assert brief.source_type == "public_url"
    document = cmd_session.scalar(
        select(DocumentVersion)
        .join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == DocumentVersion.id)
        .where(CaseDocumentVersion.research_case_id == case_id)
    )
    assert document is not None
    assert document.source_url == payload["source_url"]
    assert document.parser_version == "user-pasted-public-url-v1"
    documents = cmd_client.get("/api/v1/documents", params={"case_id": str(case_id)})
    item = documents.json()["items"][0]
    assert item["source_contract"]["source_type"] == "public_url"
    assert item["source_contract"]["permissions"]["ai_processing"] is False
    assert "不得作为正式证据" in item["source_contract"]["downstream_restrictions"][0]


def test_public_url_intake_requires_a_public_url(cmd_client) -> None:
    payload = _confirmed_event()
    payload.update({"source_type": "public_url", "source_url": None})

    response = cmd_client.post("/api/v1/event-research", json=payload)

    assert response.status_code == 422


def test_protocol_required_event_cannot_start_a_research_run_before_its_gate_is_ready(cmd_client) -> None:
    payload = _confirmed_event()
    payload["research_protocol_required"] = True
    created = cmd_client.post("/api/v1/event-research", json=payload).json()

    response = cmd_client.post(
        f"/api/v1/research-cases/{created['case_id']}/runs",
        json={"max_rounds": 1, "budget": 10},
    )

    assert response.status_code == 422
    assert "missing_outcome_binding" in response.json()["error"]["message"]


def test_new_event_requires_the_research_protocol_by_default(cmd_client, cmd_session) -> None:
    payload = _confirmed_event()
    payload.pop("research_protocol_required")
    created = cmd_client.post("/api/v1/event-research", json=payload)

    assert created.status_code == 201
    theses = list(cmd_session.scalars(
        select(Thesis).where(Thesis.research_case_id == uuid.UUID(created.json()["case_id"]))
    ))
    assert theses and all(thesis.research_protocol_required for thesis in theses)

    response = cmd_client.post(
        f"/api/v1/research-cases/{created.json()['case_id']}/runs",
        json={"max_rounds": 1, "budget": 10},
    )
    assert response.status_code == 422
    assert "missing_outcome_binding" in response.json()["error"]["message"]


def test_research_network_keeps_reviewed_relations_separate_from_ai_candidates(
    cmd_client, cmd_session
) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    other_payload = _confirmed_event()
    other_payload["event_title"] = "Alphabet 后续验证事件"
    other = cmd_client.post("/api/v1/event-research", json=other_payload).json()
    now = datetime.now(timezone.utc)
    cmd_session.add_all([
        CaseRelation(
            source_case_id=uuid.UUID(first["case_id"]),
            target_case_id=uuid.UUID(other["case_id"]),
            relation_type="shared_driver",
            reason="两项研究都需要验证资本开支的预期差。",
            created_by="human:researcher",
            review_state="reviewed",
            created_at=now,
        ),
        CaseRelation(
            source_case_id=uuid.UUID(other["case_id"]),
            target_case_id=uuid.UUID(first["case_id"]),
            relation_type="potential_conflict",
            reason="AI 发现了可能冲突的解释，等待人工复核。",
            created_by="ai:relation-proposal",
            review_state="machine_generated",
            created_at=now,
        ),
    ])
    cmd_session.commit()

    response = cmd_client.get("/api/v1/event-research/network")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewed_relations"][0]["relation_type"] == "shared_driver"
    assert payload["reviewed_relations"][0]["reason"] == "两项研究都需要验证资本开支的预期差。"
    assert payload["candidate_relations"][0]["review_state"] == "machine_generated"
    assert payload["candidate_relations"][0]["target_case"]["title"] == "Alphabet 财报后股价下跌"


def test_case_relations_only_returns_associations_for_the_current_case(
    cmd_client, cmd_session
) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "Alphabet 后续验证事件"
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()
    third_payload = _confirmed_event()
    third_payload["event_title"] = "无关的第三个事件"
    third = cmd_client.post("/api/v1/event-research", json=third_payload).json()
    now = datetime.now(timezone.utc)
    cmd_session.add_all([
        CaseRelation(source_case_id=uuid.UUID(first["case_id"]), target_case_id=uuid.UUID(second["case_id"]), relation_type="shared_driver", reason="共同验证资本开支。", created_by="human:researcher", review_state="reviewed", created_at=now),
        CaseRelation(source_case_id=uuid.UUID(second["case_id"]), target_case_id=uuid.UUID(third["case_id"]), relation_type="potential_conflict", reason="与当前 Case 无关。", created_by="ai:relation-proposal", review_state="machine_generated", created_at=now),
    ])
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{first['case_id']}/relations")

    assert response.status_code == 200
    payload = response.json()
    assert [relation["target_case"]["case_id"] for relation in payload["reviewed_relations"]] == [second["case_id"]]
    assert payload["candidate_relations"] == []


def test_existing_case_material_is_frozen_and_attached_without_starting_a_new_case_or_run(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    lifecycle_before = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle_before is not None

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/materials",
        json={
            "raw_input": "公司补充说明订单交付节奏，需进入现有 Case 由研究员核验。",
            "source_type": "uploaded_file",
            "source_metadata": {
                "file_name": "delivery-note.txt",
                "mime_type": "text/plain",
                "authority_level": "primary_disclosure",
            },
            "actor": "human:researcher",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    document = cmd_session.get(DocumentVersion, uuid.UUID(payload["document_version_id"]))
    assert document is not None
    assert document.parser_version == "uploaded-text-v1"
    assert cmd_session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case_id,
            CaseDocumentVersion.document_version_id == document.id,
        )
    ) is not None
    lifecycle_after = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle_after is not None
    assert lifecycle_after.status == lifecycle_before.status
    assert lifecycle_after.active_run_id is None


def test_document_read_exposes_the_frozen_provider_record_for_reproducibility(
    cmd_client,
    cmd_session,
) -> None:
    from app.models.source_governance import ProviderRecord

    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = created["case_id"]
    attached = cmd_client.post(
        f"/api/v1/event-research/{case_id}/materials",
        json={
            "raw_input": "供应商研报的原始记录必须可回到具体资料。",
            "source_type": "licensed_provider",
            "source_metadata": {
                "provider_name": "聚源",
                "provider_record_id": "report-2026-003",
                "request_scope": {"report_type": "industry"},
                "retrieval_reference": "provider://report-2026-003",
                "permissions": {"ai_processing": True, "display": True},
            },
            "actor": "human:researcher",
        },
    )
    assert attached.status_code == 201

    documents = cmd_client.get("/api/v1/documents", params={"case_id": case_id})

    assert documents.status_code == 200
    provider_document = next(
        item
        for item in documents.json()["items"]
        if item["id"] == attached.json()["document_version_id"]
    )
    provider_record = cmd_session.scalar(select(ProviderRecord))
    assert provider_record is not None
    assert provider_document["source_contract"]["provider_record"] == {
        "provider_name": "聚源",
        "provider_record_id": "report-2026-003",
        "request_scope": {"report_type": "industry"},
        "retrieval_reference": "provider://report-2026-003",
        "content_sha256": provider_record.content_sha256,
        "retrieved_at": provider_record.retrieved_at.isoformat(),
        "contract_version": None,
    }


def test_existing_case_material_cannot_bypass_a_published_case_change_decision(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "published"
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/materials",
        json={
            "raw_input": "这份新材料不能绕过已发布结论的变化比较。",
            "source_type": "pasted_snapshot",
            "actor": "human:researcher",
        },
    )

    assert response.status_code == 422
    assert "published case" in response.json()["error"]["message"]
    assert cmd_session.query(DocumentVersion).count() == 1


def test_reviewing_a_case_relation_candidate_appends_a_reviewed_relation_without_rewriting_the_candidate(
    cmd_client, cmd_session
) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "关联验证事件"
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()
    candidate = CaseRelation(
        source_case_id=uuid.UUID(first["case_id"]),
        target_case_id=uuid.UUID(second["case_id"]),
        relation_type="potential_conflict",
        reason="AI 发现两项解释存在冲突。",
        created_by="ai:relation-proposal",
        review_state="machine_generated",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(candidate)
    cmd_session.commit()

    reviewed = cmd_client.post(
        f"/api/v1/event-research/case-relations/{candidate.id}/reviews",
        json={
            "outcome": "modified",
            "relation_type": "follow_up_validation",
            "reviewer": "human:reviewer",
            "reason": "改为后续验证关系，需在两个 Case 中分别核对。",
            "idempotency_key": "review-candidate-1",
        },
    )

    assert reviewed.status_code == 201
    payload = reviewed.json()
    assert payload["outcome"] == "modified"
    assert payload["reviewed_relation_id"]
    assert cmd_session.get(CaseRelation, candidate.id).review_state == "machine_generated"
    network = cmd_client.get("/api/v1/event-research/network").json()
    assert network["candidate_relations"] == []
    assert network["reviewed_relations"][0]["relation_type"] == "follow_up_validation"
    assert network["reviewed_relations"][0]["created_by"] == "human:reviewer"
    assert network["reviewed_relations"][0]["reason"] == "改为后续验证关系，需在两个 Case 中分别核对。"
    graph = cmd_client.get(
        f"/api/v1/research-cases/{first['case_id']}/graph?research_mode=true"
    )
    assert graph.status_code == 200
    relation_edges = [
        edge for edge in graph.json()["edges"] if edge["semantic_kind"] == "case_relation"
    ]
    assert [edge["id"] for edge in relation_edges] == [payload["reviewed_relation_id"]]
    assert relation_edges[0]["properties"]["reviewer"] == "human:reviewer"
    assert relation_edges[0]["properties"]["review_reason"] == "改为后续验证关系，需在两个 Case 中分别核对。"
    assert relation_edges[0]["properties"]["reviewed_at"]


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


def test_event_review_queue_exposes_the_proposal_version_required_for_human_decision(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Versioned review source",
    )

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/review-queue")

    assert response.status_code == 200
    assert response.json()["items"][0]["proposal_version"] == proposal.version


def test_event_reviewer_can_request_more_evidence_without_publishing_candidate(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Primary source still needs a counterexample",
    )

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "needs_more_evidence",
            "reason": "需要补充反证与下一期实际数据，不能先采纳该关系。",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 201
    assert response.json()["outcome"] == "needs_more_evidence"
    assert response.json()["published_entity_id"] is None
    assert cmd_session.get(Proposal, proposal.id).status == "decided"


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
    assert document.title == payload["event_title"]
    assert document.parser_version == "user-pasted-v1"
    assert document.parse_state == "partial"
    spans = cmd_session.scalars(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    ).all()
    assert len(spans) == 1
    assert spans[0].locator == {"kind": "pasted_snapshot", "source_metadata": {}}
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

    response = cmd_client.get("/api/v1/event-research", params={"status": "awaiting_key_review"})
    assert response.status_code == 200
    body = response.json()
    assert [item["case_id"] for item in body["items"]] == [second["case_id"], first["case_id"]]
    assert body["items"][0]["event_title"] == "台积电上调 CoWoS 指引后下跌"
    assert body["items"][0]["ticker"] == "TSM"
    assert body["items"][0]["lifecycle_status"] == "awaiting_key_review"
    assert body["items"][0]["next_human_action"] == "核验原文资料并完成研究协议"


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


def test_event_workbench_uses_summary_without_loading_review_queue_items(
    cmd_client, monkeypatch
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()

    def fail_if_full_queue_is_loaded(*_args, **_kwargs):
        raise AssertionError("workbench must not load review queue items")

    monkeypatch.setattr(EventReviewQueueService, "review_queue", fail_if_full_queue_is_loaded)

    response = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    )

    assert response.status_code == 200
    assert response.json()["progress"] == {
        "verified": 0,
        "pending": 0,
        "invalid_source": 0,
        "current_gap": "原文资料、来源许可与研究协议尚未完成核验",
    }


def test_event_workbench_exposes_current_scope_progress_and_action_priority(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    first_factor = _confirmed_event()["candidate_factors"][0]
    thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == first_factor,
        )
    )
    assert thesis is not None
    valid = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified investor relations release",
        thesis_id=thesis.id,
    )
    _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://example.com/fixture",
        title="Invalid fixture",
        thesis_id=thesis.id,
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "awaiting_key_review"
    lifecycle.current_gap = "缺少对资本开支解释的反证"
    lifecycle.next_human_action = "审核 1 条关键证据"
    cmd_session.commit()

    awaiting_review = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workbench"
    )

    assert awaiting_review.status_code == 200
    body = awaiting_review.json()
    assert body["progress"] == {
        "verified": 0,
        "pending": 1,
        "invalid_source": 1,
        "current_gap": "缺少对资本开支解释的反证",
    }
    assert body["scope"] == {
        "version": 1,
        "factors": [
            {"statement": statement, "description": None}
            for statement in _confirmed_event()["candidate_factors"]
        ],
        "unmapped_evidence_count": 0,
    }
    assert body["next_action"] == {
        "kind": "review_evidence",
        "label": "审核 1 条关键证据",
        "count": 1,
    }

    accepted = cmd_client.post(
        f"/api/v1/review-proposals/{valid.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "verified primary source supports the active factor",
            "reviewer_id": "reviewer",
            "expected_version": valid.version,
        },
    )
    assert accepted.status_code == 201
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "exhausted"
    lifecycle.current_gap = "缺少对资本开支解释的反证"
    lifecycle.next_human_action = None
    cmd_session.commit()

    exhausted = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workbench"
    ).json()
    assert exhausted["progress"]["verified"] == 1
    assert exhausted["factors"][0] == {
        "thesis_id": str(thesis.id),
        "statement": first_factor,
        "description": None,
        "position": 1,
        "reviewed_support_count": 1,
        "reviewed_contradiction_count": 0,
        "pending_proposal_count": 0,
        "current_gap": None,
    }
    assert exhausted["next_action"] == {
        "kind": "edit_factors",
        "label": "编辑并继续自动研究",
        "count": None,
    }

    factors = [
        first_factor,
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]
    updated = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": factors, "changed_by": "reviewer"},
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "version": 2,
        "factors": [{"statement": statement, "description": None} for statement in factors],
        "reclassified_evidence_count": 1,
        "unmapped_evidence_count": 0,
    }
    refreshed = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench").json()
    assert refreshed["scope"] == {
        "version": 2,
        "factors": [{"statement": statement, "description": None} for statement in factors],
        "unmapped_evidence_count": 0,
    }


def test_event_workbench_evidence_links_only_to_the_case_frozen_document(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified investor relations release",
    )
    document = cmd_session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url
            == "https://investor.tsmc.com/english/quarterly-results"
        )
    )
    assert document is not None
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="uploaded_file",
        source_metadata={},
        declared_by="tester",
    )
    accepted = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "verified primary source supports the factor",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )
    assert accepted.status_code == 201

    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")

    assert workbench.status_code == 200
    evidence = workbench.json()["evidence"]
    assert evidence[0]["document_version_id"] == str(document.id)
    assert evidence[0]["source_visible_in_case"] is True


def test_event_workbench_redacts_evidence_from_a_source_not_allowed_for_display(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    proposal = _evidence_proposal(
        cmd_session,
        case_id,
        source_url="https://investor.tsmc.com/restricted-quarterly-results",
        title="Restricted investor relations release",
    )
    document = cmd_session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url
            == "https://investor.tsmc.com/restricted-quarterly-results"
        )
    )
    assert document is not None
    accepted = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "reviewed under the permitted workflow",
            "reviewer_id": "reviewer",
            "expected_version": proposal.version,
        },
    )
    assert accepted.status_code == 201
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="uploaded_file",
        source_metadata={"permissions": {"display": False}},
        declared_by="tester",
    )

    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")

    assert workbench.status_code == 200
    evidence = workbench.json()["evidence"][0]
    assert evidence["source_visible_in_case"] is False
    assert evidence["source_title"] is None
    assert evidence["source_url"] is None
    assert evidence["excerpt"] == ""
    assert evidence["locator"] == {}


def test_event_workbench_action_priority_covers_conclusion_lifecycle(cmd_client, cmd_session) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None

    for status, expected_kind in [
        ("awaiting_scope", "edit_factors"),
        ("exhausted", "edit_factors"),
        ("researching", "wait"),
        ("continuing", "wait"),
        ("draft_ready", "review_conclusion"),
        ("published", "view_conclusion_change"),
    ]:
        lifecycle.status = status
        lifecycle.next_human_action = "过时的旧动作"
        cmd_session.commit()
        response = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
        assert response.status_code == 200
        assert response.json()["next_action"]["kind"] == expected_kind


def test_draft_workbench_exposes_only_current_reviewed_evidence_and_factor_pending_counts(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])
    factors = _confirmed_event()["candidate_factors"]
    for factor in factors:
        thesis = cmd_session.scalar(select(Thesis).where(
            Thesis.research_case_id == case_id, Thesis.statement == factor
        ))
        assert thesis is not None
        proposal = _evidence_proposal(
            cmd_session, case_id, source_url=f"https://investor.tsmc.com/{thesis.id}",
            title=f"Reviewed {factor}", thesis_id=thesis.id,
        )
        accepted = cmd_client.post(
            f"/api/v1/review-proposals/{proposal.id}/decisions",
            json={"outcome": "confirmed", "reason": "reviewed", "reviewer_id": "reviewer", "expected_version": proposal.version},
        )
        assert accepted.status_code == 201

    pending_thesis = cmd_session.scalar(select(Thesis).where(
        Thesis.research_case_id == case_id, Thesis.statement == factors[1]
    ))
    assert pending_thesis is not None
    _evidence_proposal(
        cmd_session, case_id, source_url="https://investor.tsmc.com/pending",
        title="Pending only for factor two", thesis_id=pending_thesis.id,
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "draft_ready"
    lifecycle.current_gap = "不应作为每个因素的缺口重复展示"
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench").json()

    assert body["conclusion"]["confidence"] == "medium"
    assert len(body["evidence"]) == 3
    assert {item["review_state"] for item in body["evidence"]} == {"reviewed"}
    assert all(item["factor_statement"] in factors for item in body["evidence"])
    assert [item["pending_proposal_count"] for item in body["factors"]] == [0, 1, 0]
    assert [item["current_gap"] for item in body["factors"]] == [None, "有关键证据待审核", None]

    pending = cmd_session.scalar(select(Proposal).where(
        Proposal.research_case_id == case_id, Proposal.status == "pending"
    ))
    assert pending is not None
    pending.status = "decided"
    cmd_session.commit()
    assert cmd_client.get(f"/api/v1/event-research/{case_id}/workbench").json()["conclusion"]["confidence"] == "high"


def test_scope_update_persists_optional_factor_descriptions_and_accepts_legacy_strings(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = uuid.UUID(created["case_id"])

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                {"statement": "资本开支压力", "description": "关注自由现金流与投入回收期"},
                "盈利预期变化",
                {"statement": "估值重定价", "description": None},
            ],
            "changed_by": "reviewer",
        },
    )

    assert response.status_code == 200
    assert response.json()["factors"] == [
        {"statement": "资本开支压力", "description": "关注自由现金流与投入回收期"},
        {"statement": "盈利预期变化", "description": None},
        {"statement": "估值重定价", "description": None},
    ]
    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench").json()
    assert workbench["scope"]["factors"] == response.json()["factors"]
    saved = list(
        cmd_session.scalars(
            select(EventResearchScopeFactor)
            .where(EventResearchScopeFactor.scope_version_id == cmd_session.scalar(
                select(EventResearchScopeVersion.id)
                .where(EventResearchScopeVersion.research_case_id == case_id)
                .order_by(EventResearchScopeVersion.version.desc())
                .limit(1)
            ))
            .order_by(EventResearchScopeFactor.position)
        )
    )
    assert [factor.description for factor in saved] == ["关注自由现金流与投入回收期", None, None]


def test_event_workbench_factor_statistics_use_a_fixed_query_count(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    case_id = created["case_id"]
    engine = cmd_session.get_bind()

    def workbench_select_count() -> int:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sqlalchemy_event.listen(engine, "before_cursor_execute", record)
        try:
            response = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
            assert response.status_code == 200
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    three_factor_count = workbench_select_count()
    updated = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                *_confirmed_event()["candidate_factors"],
                "竞争对手定价变化可能影响市场反应",
            ],
            "changed_by": "reviewer",
        },
    )
    assert updated.status_code == 200

    four_factor_count = workbench_select_count()

    assert four_factor_count == three_factor_count


def test_event_workbench_exposes_the_thesis_id_for_each_factor_protocol(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()

    response = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    )

    assert response.status_code == 200
    factors = response.json()["factors"]
    assert all(uuid.UUID(factor["thesis_id"]) for factor in factors)
    assert [factor["statement"] for factor in factors] == _confirmed_event()[
        "candidate_factors"
    ]


def test_event_case_can_explicitly_require_the_research_protocol(cmd_client, cmd_session) -> None:
    payload = _confirmed_event()
    payload["research_protocol_required"] = True

    created = cmd_client.post("/api/v1/event-research", json=payload)

    assert created.status_code == 201
    theses = list(cmd_session.scalars(
        select(Thesis).where(Thesis.research_case_id == uuid.UUID(created.json()["case_id"]))
    ))
    assert theses and all(thesis.research_protocol_required for thesis in theses)


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
