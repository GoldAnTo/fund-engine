from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
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


@pytest.mark.parametrize("research_source_type", [0, False, "", "  "])
def test_event_intake_rejects_explicit_falsey_research_source_type(
    cmd_client, research_source_type
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"research_source_type": research_source_type},
        ),
    )

    assert created.status_code == 422
    assert "research_source_type is not supported" in created.json()["error"]["message"]


def test_event_intake_rejects_an_explicit_null_research_source_type(
    cmd_client,
) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_metadata={"research_source_type": None},
        ),
    )

    assert created.status_code == 422
    assert "research_source_type is not supported" in created.json()["error"]["message"]


def test_event_intake_rejects_company_disclosure_without_a_hostname(cmd_client) -> None:
    payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    payload["source_url"] = "https://@/report"

    created = cmd_client.post("/api/v1/event-research", json=payload)

    assert created.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in created.json()[
        "error"
    ]["message"]


def test_deduplicated_event_intake_validates_the_incoming_company_disclosure_url(
    cmd_client,
) -> None:
    first_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    first_payload["source_url"] = "https://www.cninfo.com.cn/new/disclosure/detail?stockCode=601138"
    first = cmd_client.post("/api/v1/event-research", json=first_payload)

    second_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    second_payload["event_title"] = "同正文的无效公司披露链接"
    second_payload["source_url"] = "event://not-http"
    second = cmd_client.post("/api/v1/event-research", json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in second.json()[
        "error"
    ]["message"]


def test_deduplicated_event_intake_rejects_a_different_declared_source_url(
    cmd_client,
) -> None:
    first_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    first_payload["source_url"] = "https://issuer-a.example.com/disclosures/annual-report"
    first = cmd_client.post("/api/v1/event-research", json=first_payload)

    second_payload = _event_payload(
        source_type="pasted_snapshot",
        source_metadata={"research_source_type": "company_disclosure"},
    )
    second_payload["event_title"] = "同正文的另一披露链接"
    second_payload["source_url"] = "https://issuer-b.example.com/disclosures/annual-report"
    second = cmd_client.post("/api/v1/event-research", json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 422
    assert "deduplicated original has a different source contract" in second.json()[
        "error"
    ]["message"]


def _frozen_supplement_document(cmd_session, raw: bytes) -> DocumentVersion:
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    return DocumentService(DocumentRepository(cmd_session)).freeze(
        raw=raw,
        source_url="supplement://original/recovery-text",
        parser_version="user-supplement-v1",
        parse_state="partial",
    )


def test_supplement_intake_rejects_company_disclosure_without_http_source_url(
    cmd_session,
) -> None:
    from app.services.source_governance import SourceGovernanceService

    supplement = _frozen_supplement_document(cmd_session, b"recovery text")

    with pytest.raises(
        ValueError, match="company_disclosure requires an HTTP\\(S\\) source_url"
    ):
        SourceGovernanceService(cmd_session).record_supplement_intake(
            document=supplement,
            original_contract=None,
            source_metadata={"research_source_type": "company_disclosure"},
            declared_by="human:researcher",
        )


@pytest.mark.parametrize(
    ("research_source_type", "message"),
    [
        ("licensed_provider", "deduplicated original has a different source contract"),
        ("unsupported_source", "research_source_type is not supported"),
        (None, "research_source_type is not supported"),
    ],
)
def test_deduplicated_supplement_intake_revalidates_research_source_type(
    cmd_session, research_source_type, message
) -> None:
    from app.services.source_governance import SourceGovernanceService

    supplement = _frozen_supplement_document(cmd_session, b"same recovery text")
    service = SourceGovernanceService(cmd_session)
    contract = service.record_supplement_intake(
        document=supplement,
        original_contract=None,
        source_metadata={},
        declared_by="human:researcher",
    )

    assert contract.research_source_type == "pasted_snapshot"
    with pytest.raises(ValueError, match=message):
        service.record_supplement_intake(
            document=supplement,
            original_contract=None,
            source_metadata={"research_source_type": research_source_type},
            declared_by="human:researcher",
        )


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


@pytest.mark.parametrize("include_null_scope", [False, True])
def test_licensed_provider_without_request_scope_replays_one_governance_bundle(
    cmd_session,
    include_null_scope,
) -> None:
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService
    from app.services.source_governance import SourceGovernanceService

    source_url = "juyuan://research-report/JRPT-no-scope"
    document = DocumentService(DocumentRepository(cmd_session)).freeze(
        raw=b"licensed provider report without request scope",
        source_url=source_url,
        parser_version="provider-snapshot-v1",
        parse_state="partial",
    )
    metadata = {
        "provider_name": "juyuan",
        "provider_record_id": "JRPT-no-scope",
        "retrieval_reference": source_url,
    }
    if include_null_scope:
        metadata["request_scope"] = None
    service = SourceGovernanceService(cmd_session)

    first = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="human:researcher",
    )
    second = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="human:researcher",
    )

    records = list(
        cmd_session.scalars(
            select(ProviderRecord).where(
                ProviderRecord.document_version_id == document.id
            )
        )
    )
    assert second.id == first.id
    assert first.intake_metadata["request_scope"] == {}
    assert len(records) == 1
    assert records[0].request_scope == {}


def test_legacy_licensed_provider_replay_uses_retrieval_reference_as_declaration_url(
    cmd_client, cmd_session
) -> None:
    """A pre-0051 contract has no internal declaration-url metadata key."""
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    retrieval_reference = "juyuan://research-report/JRPT-legacy-001"
    payload = _event_payload(
        source_type="licensed_provider",
        source_metadata={"retrieval_reference": retrieval_reference},
    )
    document = DocumentService(DocumentRepository(cmd_session)).freeze(
        raw=payload["raw_input"].encode("utf-8"),
        source_url="provider://unresolved-record",
        parser_version="provider-snapshot-v1",
        title=payload["event_title"],
        parse_state="partial",
    )
    cmd_session.add(
        SourceContract(
            document_version_id=document.id,
            source_type="licensed_provider",
            research_source_type="licensed_provider",
            provider_or_tenant="human:researcher",
            allow_ai_processing=False,
            allow_display=False,
            allow_export=False,
            allow_api=False,
            region="not_recorded",
            effective_from=None,
            effective_until=None,
            retention_policy="case_retained",
            deletion_policy="not_recorded",
            downstream_restrictions=["权限未完整记录；不得作为正式证据"],
            contract_version=None,
            intake_metadata={"retrieval_reference": retrieval_reference},
            declared_by="human:researcher",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_session.commit()

    replay = cmd_client.post("/api/v1/event-research", json=payload)

    assert replay.status_code == 201

    different_reference = _event_payload(
        source_type="licensed_provider",
        source_metadata={"retrieval_reference": "juyuan://research-report/JRPT-legacy-002"},
    )
    different_reference["event_title"] = "同正文的另一份供应商记录"
    rejected = cmd_client.post("/api/v1/event-research", json=different_reference)

    assert rejected.status_code == 422
    assert "deduplicated original has a different source contract" in rejected.json()[
        "error"
    ]["message"]


def test_new_licensed_provider_replay_uses_persisted_retrieval_reference(
    cmd_session,
) -> None:
    """A new contract keeps its declared provider reference over its freeze URL."""
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService
    from app.services.source_governance import (
        DECLARED_SOURCE_URL_EXPLICIT_METADATA_KEY,
        DECLARED_SOURCE_URL_METADATA_KEY,
        SourceGovernanceService,
    )

    retrieval_reference = "juyuan://research-report/JRPT-new-001"
    metadata = {"retrieval_reference": retrieval_reference}
    document = DocumentService(DocumentRepository(cmd_session)).freeze(
        raw=b"licensed provider report",
        source_url="https://licensed.example/report/frozen-copy",
        parser_version="provider-snapshot-v1",
        title="provider report",
        parse_state="partial",
    )
    service = SourceGovernanceService(cmd_session)

    created = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="human:researcher",
    )

    assert created.intake_metadata["retrieval_reference"] == retrieval_reference
    assert created.intake_metadata[DECLARED_SOURCE_URL_METADATA_KEY] == retrieval_reference
    assert created.intake_metadata[DECLARED_SOURCE_URL_EXPLICIT_METADATA_KEY] is False

    replay = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="human:researcher",
    )

    assert replay.id == created.id
    with pytest.raises(
        ValueError,
        match="deduplicated original has a different source contract",
    ):
        service.record_event_intake(
            document=document,
            source_type="licensed_provider",
            source_metadata={
                "retrieval_reference": "juyuan://research-report/JRPT-new-002"
            },
            declared_by="human:researcher",
        )


def test_legacy_licensed_provider_replay_prefers_explicit_document_url(
    cmd_client, cmd_session
) -> None:
    """A legacy provider contract retains its real frozen document URL."""
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    source_url = "https://licensed.example/report/legacy-explicit"
    retrieval_reference = "juyuan://legacy-different"
    payload = _event_payload(
        source_type="licensed_provider",
        source_metadata={"retrieval_reference": retrieval_reference},
    )
    payload["source_url"] = source_url
    document = DocumentService(DocumentRepository(cmd_session)).freeze(
        raw=payload["raw_input"].encode("utf-8"),
        source_url=source_url,
        parser_version="provider-snapshot-v1",
        title=payload["event_title"],
        parse_state="partial",
    )
    cmd_session.add(
        SourceContract(
            document_version_id=document.id,
            source_type="licensed_provider",
            research_source_type="licensed_provider",
            provider_or_tenant="human:researcher",
            allow_ai_processing=False,
            allow_display=False,
            allow_export=False,
            allow_api=False,
            region="not_recorded",
            effective_from=None,
            effective_until=None,
            retention_policy="case_retained",
            deletion_policy="not_recorded",
            downstream_restrictions=["权限未完整记录；不得作为正式证据"],
            contract_version=None,
            intake_metadata={"retrieval_reference": retrieval_reference},
            declared_by="human:researcher",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_session.commit()

    replay = cmd_client.post("/api/v1/event-research", json=payload)

    assert replay.status_code == 201

    different_source_url = _event_payload(
        source_type="licensed_provider",
        source_metadata={"retrieval_reference": retrieval_reference},
    )
    different_source_url["source_url"] = "https://licensed.example/report/legacy-other"
    rejected = cmd_client.post("/api/v1/event-research", json=different_source_url)

    assert rejected.status_code == 422
    assert "deduplicated original has a different source contract" in rejected.json()[
        "error"
    ]["message"]


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
