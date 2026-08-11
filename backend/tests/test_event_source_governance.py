from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, update

from app.models.ledger import CaseDocumentVersion, DocumentVersion, ImmutableLedgerError, SourceSpan, SourceStatement, Thesis
from app.models.proposals import Proposal
from app.models.source_governance import ProviderRecord, SourceContract


def _event_payload(*, source_type: str, source_metadata: dict[str, object]) -> dict[str, object]:
    return {
        "raw_input": "某公司宣布扩大 AI 基础设施投入，市场正在等待下一次财报验证。",
        "source_type": source_type,
        "source_metadata": source_metadata,
        "event_title": "AI 基础设施投入事件",
        "company_name": "示例公司",
        "ticker": "000001",
        "research_question": "新增投入是否会改变后续收入和毛利率预期？",
        "candidate_factors": ["资本开支", "订单交付", "毛利率"],
        "created_by": "human:researcher",
    }


def test_event_intake_creates_a_displayable_but_export_restricted_user_source_contract(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"original_url": "https://example.test/news/ai"},
        ),
    )

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    assert document_id is not None
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_id)
    )
    assert contract is not None
    assert contract.source_type == "pasted_snapshot"
    assert contract.research_source_type == "pasted_snapshot"
    assert contract.allow_ai_processing is True
    assert contract.allow_display is True
    assert contract.allow_export is False
    assert contract.allow_api is False
    assert contract.retention_policy == "case_retained"

    detail = cmd_client.get(f"/api/v1/documents/{document_id}")

    assert detail.status_code == 200
    source = detail.json()["document"]["source_contract"]
    assert source["source_type"] == "pasted_snapshot"
    assert source["research_source_type"] == "pasted_snapshot"
    assert source["permissions"] == {
        "ai_processing": True,
        "display": True,
        "export": False,
        "api": False,
    }
    assert source["status"] == "admitted"

    with __import__("pytest").raises(ImmutableLedgerError):
        cmd_session.execute(
            update(SourceContract).where(SourceContract.id == contract.id).values(allow_export=True)
        )


def test_event_intake_rejects_company_disclosure_without_http_source_url(
    cmd_client,
) -> None:
    payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    payload["source_url"] = "event://pasted-news"

    created = cmd_client.post("/api/v1/event-research", json=payload)

    assert created.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in created.json()[
        "error"
    ]["message"]


def test_event_intake_keeps_acquisition_type_and_reads_research_source_type(
    cmd_client, cmd_session
) -> None:
    payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    payload["source_url"] = "https://www.cninfo.com.cn/new/disclosure/detail?stockCode=601138"

    created = cmd_client.post("/api/v1/event-research", json=payload)

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_id)
    )
    assert contract is not None
    assert contract.source_type == "pasted_snapshot"
    assert contract.research_source_type == "company_disclosure"

    detail = cmd_client.get(f"/api/v1/documents/{document_id}")

    assert detail.status_code == 200
    source = detail.json()["document"]["source_contract"]
    assert source["source_type"] == "pasted_snapshot"
    assert source["research_source_type"] == "company_disclosure"


def test_event_intake_rejects_an_unknown_research_source_type(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"research_source_type": "unverified_press_release"},
        ),
    )

    assert created.status_code == 422
    assert "research_source_type is not supported" in created.json()["error"]["message"]


def test_licensed_provider_intake_preserves_provider_record_and_contract_version(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="licensed_provider",
            source_metadata={
                "provider_name": "聚源",
                "provider_record_id": "JRPT-20260809-001",
                "request_scope": {"dataset": "research_report", "symbol": "000001"},
                "retrieval_reference": "juyuan://research-report/JRPT-20260809-001",
                "contract_version": "juyuan-research-v3",
                "permissions": {"ai_processing": True, "display": True, "export": False, "api": False},
            },
        ),
    )

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_id)
    )
    provider = cmd_session.scalar(
        select(ProviderRecord).where(ProviderRecord.document_version_id == document_id)
    )

    assert contract is not None
    assert contract.provider_or_tenant == "聚源"
    assert contract.contract_version == "juyuan-research-v3"
    assert provider is not None
    assert provider.provider_record_id == "JRPT-20260809-001"
    assert provider.request_scope == {"dataset": "research_report", "symbol": "000001"}
    assert provider.retrieval_reference == "juyuan://research-report/JRPT-20260809-001"
    assert provider.content_sha256 == cmd_session.get(DocumentVersion, document_id).content_sha256


def test_event_intake_preserves_declared_contract_validity_window(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="licensed_provider",
            source_metadata={
                "provider_name": "聚源",
                "provider_record_id": "JRPT-20260810-002",
                "request_scope": {"declared_scope": "研报 / 标的 000001 / 2026H1"},
                "effective_from": "2026-01-01",
                "effective_until": "2026-12-31",
                "contract_version": "juyuan-research-v4",
                "permissions": {"ai_processing": True, "display": True},
            },
        ),
    )

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_id)
    )

    assert contract is not None
    assert contract.effective_from is not None
    assert contract.effective_until is not None
    assert contract.effective_from.date() == date(2026, 1, 1)
    assert contract.effective_until.date() == date(2026, 12, 31)
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    source = detail.json()["document"]["source_contract"]
    assert source["effective_from"].startswith("2026-01-01T00:00:00")
    assert source["effective_until"].startswith("2026-12-31T00:00:00")


def test_event_intake_rejects_a_contract_window_that_ends_before_it_starts(
    cmd_client,
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={
                "effective_from": "2026-12-31",
                "effective_until": "2026-01-01",
            },
        ),
    )

    assert created.status_code == 422
    assert (
        created.json()["error"]["message"]
        == "effective_until must not be before effective_from"
    )


def test_expired_contract_is_visible_as_restricted_in_the_document_reader(
    cmd_client,
    cmd_session,
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={
                "effective_from": "2025-01-01",
                "effective_until": "2025-01-31",
            },
        ),
    )

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")

    assert detail.status_code == 200
    assert detail.json()["document"]["source_contract"]["status"] == "restricted"


def test_non_displayable_source_returns_auditable_metadata_without_its_content(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={
                "original_url": "https://example.test/restricted-source",
                "permissions": {"ai_processing": False, "display": False},
            },
        ),
    )

    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    assert document_id is not None

    detail = cmd_client.get(
        f"/api/v1/event-research/{case_id}/documents/{document_id}"
    )

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["document"]["source_contract"]["status"] == "restricted"
    assert payload["document"]["source_url"] is None
    assert payload["document"]["title"] is None
    assert payload["document"]["span_count"] == 0
    assert payload["document"]["statement_count"] == 0
    assert payload["spans"] == []


def test_event_intake_freezes_declared_source_authority_and_exposes_it_to_readers(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="uploaded_file",
            source_metadata={"authority_level": "primary_disclosure", "issuer": "示例公司"},
        ),
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    document = cmd_session.get(DocumentVersion, document_id)

    assert document.source_authority == "primary_disclosure"
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["source_authority"] == "primary_disclosure"


def test_reusing_the_same_frozen_snapshot_rejects_an_incompatible_contract(
    cmd_client, cmd_session
) -> None:
    first = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"tenant": "team-a", "provider_name": "shared-provider"},
        ),
    )
    second_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={
            "tenant": "team-b",
            "provider_name": "shared-provider",
            "permissions": {"export": True},
        },
    )
    second_payload["event_title"] = "复用同一内容的后续验证事件"
    second = cmd_client.post("/api/v1/event-research", json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 422
    assert "deduplicated original has a different source contract" in second.json()[
        "error"
    ]["message"]
    first_document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == uuid.UUID(first.json()["case_id"])
        )
    )
    contracts = list(
        cmd_session.scalars(
            select(SourceContract).where(SourceContract.document_version_id == first_document_id)
        )
    )

    assert len(contracts) == 1
    assert contracts[0].provider_or_tenant == "team-a"
    assert contracts[0].allow_export is False


def test_reusing_the_same_snapshot_rejects_a_different_research_source_type(
    cmd_client,
) -> None:
    first = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"research_source_type": "public_url"},
        ),
    )
    second_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "uploaded_file"},
    )
    second_payload["event_title"] = "同一快照的不同研究来源类别"
    second = cmd_client.post("/api/v1/event-research", json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 422
    assert "deduplicated original has a different source contract" in second.json()[
        "error"
    ]["message"]


def test_contract_that_forbids_ai_processing_blocks_formal_evidence_acceptance(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"permissions": {"ai_processing": False, "display": True}},
        ),
    ).json()
    case_id = uuid.UUID(created["case_id"])
    thesis = cmd_session.scalar(select(Thesis).where(Thesis.research_case_id == case_id))
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    )
    now = datetime.now(timezone.utc)
    span = SourceSpan(
        document_version_id=document_id,
        locator={"kind": "pasted_snapshot"},
        verbatim_text="冻结原文片段",
    )
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="research_opinion",
        normalized_text="待审核候选",
        created_at=now,
    )
    cmd_session.add(statement)
    cmd_session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={"source_statement_id": str(statement.id), "role": "supports", "reason": "候选关系", "scope": {}},
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=now,
        research_case_id=case_id,
        status="pending",
    )
    cmd_session.add(proposal)
    cmd_session.commit()

    queue = cmd_client.get(f"/api/v1/event-research/{case_id}/review-queue")
    item = next(row for row in queue.json()["items"] if row["proposal_id"] == str(proposal.id))
    decision = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={"outcome": "confirmed", "reason": "尝试采纳", "reviewer_id": "human:reviewer", "expected_version": proposal.version},
    )

    assert item["source_status"] == "restricted"
    assert item["can_accept"] is False
    assert "禁止 AI 处理" in item["source_status_reason"]
    assert decision.status_code == 422
