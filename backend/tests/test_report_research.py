"""Report-first research input contracts and immutable provenance."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from threading import Barrier, Event, Thread

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select, update

from app.datasources.docling import PdfParseError
from app.documents.locators import coerce_locator_v1, compute_text_sha256
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentBlob,
    DocumentSourceRecord,
    DocumentVersion,
    ImmutableLedgerError,
    ReportCaseInitialAdmission,
    ResearchCase,
    RecoverySupplementSnapshot,
    SourceSpan,
    SourceStatement,
)
# Register Task 2's report-ledger tables before the SQLite fixture builds
# Base.metadata.  PostgreSQL receives the same tables through Alembic.
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportExtractionClaim,
    ReportRelation,
)
from app.models.operational import ResearchTask


def test_intake_only_freezes_sources_and_returns_truthful_next_actions(
    session, monkeypatch, tmp_path
) -> None:
    """Intake must not promote unreviewed source text into formal research."""
    from app.datasources.docling import ParsedSpan
    from app.repositories.source_contracts import SourceContractRepository
    from app.schemas.v1.report_research import CreateReportResearchRequest
    from app.services.report_research import ReportResearchService

    def _parse_one_span(self, raw: bytes, *, document_sha256: str):
        locator = coerce_locator_v1(
            {"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
            document_sha256=document_sha256,
            parser_version="pypdf-v1",
        )
        return [
            ParsedSpan(
                locator=locator,
                verbatim_text="PDF 正文",
                text_sha256=compute_text_sha256("PDF 正文"),
                context_hash="fixture",
            )
        ]

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_one_span
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    contracts = SourceContractRepository(session)
    original_contract = contracts.append_contract(
        provider_name="fixture original",
        tenant_id="tenant-a",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="delete on expiry",
        downstream_restrictions="internal only",
        approved_by="legal",
        reason="fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    service = ReportResearchService(session)
    text = service.create_text(
        CreateReportResearchRequest(
            input_kind="pasted_text",
            title="文本研报",
            content="研报观点：订单增长。",
            source_contract_id=original_contract.id,
        )
    )
    pdf = service.create_pdf(
        raw=b"%PDF-success",
        title="PDF 研报",
        publisher=None,
        published_at=None,
        filename="report.pdf",
        created_by="tester",
        source_contract_id=original_contract.id,
    )
    supplement_contract = contracts.append_contract(
        provider_name="fixture supplement",
        tenant_id="tenant-a",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=True,
        may_api_use=True,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="delete on expiry",
        downstream_restrictions="no redistribution",
        approved_by="legal",
        reason="fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    recovery = service.supplement_text(
        research_case_id=pdf.case.id,
        document_version_id=pdf.document.id,
        content="补充的可读正文",
        page_reference="第 2 页",
        created_by="analyst-a",
        source_contract_id=supplement_contract.id,
    )

    assert text.outcome.state == "pending_candidate_extraction"
    assert text.outcome.next_action == "extract_candidates"
    assert pdf.outcome.state == "pending_candidate_extraction"
    assert recovery.outcome.state == "pending_candidate_extraction"
    assert recovery.outcome.next_action == "extract_candidates"
    assert recovery.document.id != pdf.document.id
    assert text.initial_scope_version is None
    assert text.source_statement_ids == pdf.source_statement_ids == []
    assert session.query(SourceStatement).count() == 0
    assert session.query(ReportClaim).count() == 0
    from app.models.report_research import ReportResearchScopeVersion
    from app.models.operational import ResearchRun

    assert session.query(ReportResearchScopeVersion).count() == 0
    assert session.query(ResearchTask).count() == 0
    assert session.query(ResearchRun).count() == 0


def test_pasted_report_is_frozen_and_creates_case(cmd_client, cmd_session) -> None:
    published_at = "2026-08-01T08:00:00Z"
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "服务器产业链更新",
            "publisher": "某券商",
            "published_at": published_at,
            "content": "资料描述：订单增长。",
            "source_contract_id": cmd_client.report_source_contract_id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["document"]["kind"] == "research_report"
    assert body["document"]["input_kind"] == "pasted_text"
    assert body["document"]["publisher"] == "某券商"
    assert datetime.fromisoformat(body["document"]["published_at"]).astimezone(
        timezone.utc
    ) == datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    assert body["state"] == "pending_candidate_extraction"
    assert body["next_action"] == "extract_candidates"
    assert body["blocking_reason"] is None
    assert "initial_scope_version" not in body
    assert "source_statement_ids" not in body

    case_id = uuid.UUID(body["case"]["id"])
    document_id = uuid.UUID(body["document"]["id"])
    case = cmd_session.get(ResearchCase, case_id)
    document = cmd_session.get(DocumentVersion, document_id)
    assert case is not None
    assert case.title == "服务器产业链更新"
    assert document is not None
    assert document.title == "服务器产业链更新"
    assert document.published_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 8, 1, 8, tzinfo=timezone.utc
    )
    assert document.parse_state == "partial"
    assert cmd_session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case_id,
            CaseDocumentVersion.document_version_id == document_id,
        )
    ) is not None
    span = cmd_session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document_id)
    )
    assert span is not None
    assert span.locator["input_kind"] == "pasted_text"
    assert span.locator["publisher"] == "某券商"
    assert datetime.fromisoformat(span.locator["published_at"]).astimezone(
        timezone.utc
    ) == datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    assert cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    ) is None
    record = cmd_session.scalar(
        select(DocumentSourceRecord).where(
            DocumentSourceRecord.document_version_id == document_id
        )
    )
    assert record is not None
    assert record.admission_type == "pasted_snapshot"
    assert record.tenant_id == "tenant-a"


def test_web_report_preserves_origin_url_without_creating_statement(
    cmd_client, cmd_session
) -> None:
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "web_content",
            "title": "算力产业观察",
            "publisher": "行业观察站",
            "published_at": "2026-08-02T09:30:00Z",
            "source_url": "https://research.example.com/ai-chain",
            "content": "观点：服务器出货将提升。",
        },
    )

    assert response.status_code == 201
    document_id = uuid.UUID(response.json()["document"]["id"])
    document = cmd_session.get(DocumentVersion, document_id)
    assert document is not None
    assert document.source_url == "https://research.example.com/ai-chain"
    span = cmd_session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.locator["input_kind"] == "web_content"
    assert cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    ) is None
    record = cmd_session.scalar(
        select(DocumentSourceRecord).where(
            DocumentSourceRecord.document_version_id == document.id
        )
    )
    assert record is not None
    assert record.admission_type == "public_url"


def test_created_report_keeps_claim_extraction_pending(
    cmd_client, cmd_session
) -> None:
    """Intake freezes source text but leaves all formal research for a later command."""
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "服务器供应链观点",
            "publisher": "某券商",
            "content": "研报观点：未上市供应商甲是星海科技的供应商。",
        },
    )

    assert response.status_code == 201
    assert response.json()["state"] == "pending_candidate_extraction"
    assert "initial_scope_version" not in response.json()
    case_id = uuid.UUID(response.json()["case"]["id"])
    claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == case_id)
    )
    assert claim is None
    assert cmd_session.scalar(
        select(SourceStatement)
        .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
        .where(SourceSpan.document_version_id == uuid.UUID(response.json()["document"]["id"]))
    ) is None
    assert cmd_session.scalar(
        select(ResearchTask).where(ResearchTask.research_case_id == case_id)
    ) is None


def test_relation_like_text_is_not_promoted_during_intake(
    cmd_client, cmd_session
) -> None:
    """Even explicit relation prose remains a candidate until extraction is invoked."""
    from app.models.ledger import Company

    plain = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "客户关系",
            "content": "未上市客户乙是星海科技的客户。",
        },
    )
    narrated = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "供应商关系",
            "content": "公司认为未上市供应商甲是星海科技的供应商。",
        },
    )
    competitor = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "竞争关系",
            "content": "甲公司与乙公司存在竞争。",
        },
    )

    assert plain.status_code == 201
    assert narrated.status_code == 201
    assert competitor.status_code == 201
    plain_case = uuid.UUID(plain.json()["case"]["id"])
    narrated_case = uuid.UUID(narrated.json()["case"]["id"])
    competitor_case = uuid.UUID(competitor.json()["case"]["id"])
    assert cmd_session.scalars(
        select(ReportClaim).where(
            ReportClaim.research_case_id.in_((plain_case, narrated_case, competitor_case))
        )
    ).all() == []
    assert cmd_session.scalars(
        select(Company).where(
            Company.name.in_(
                ("未上市客户乙", "未上市供应商甲", "星海科技", "甲公司", "乙公司")
            )
        )
    ).all() == []


def test_adversarial_narrator_prefixes_create_no_intake_relations(
    cmd_client, cmd_session
) -> None:
    prefixes = (
        "我们认为",
        "分析师认为",
        "本文认为",
        "据悉",
        "我们预计",
        "券商认为",
        "研究员认为",
        "本公司预计",
    )
    case_ids: list[uuid.UUID] = []
    for index, prefix in enumerate(prefixes):
        response = cmd_client.post(
            "/api/v1/report-research",
            json={
                "input_kind": "pasted_text",
                "title": f"前缀案例-{index}",
                "content": f"{prefix}未上市供应商甲是星海科技的供应商。",
            },
        )
        assert response.status_code == 201
        case_ids.append(uuid.UUID(response.json()["case"]["id"]))

    relations = list(
        cmd_session.scalars(
            select(ReportRelation)
            .join(ReportClaim, ReportClaim.id == ReportRelation.claim_id)
            .where(ReportClaim.research_case_id.in_(case_ids))
        )
    )
    assert relations == []


def test_same_report_bytes_are_case_owned_without_intake_claims(
    cmd_client, cmd_session
) -> None:
    payload = {
        "input_kind": "pasted_text",
        "title": "共享研报",
        "content": "未上市供应商甲是星海科技的供应商。",
    }
    first = cmd_client.post("/api/v1/report-research", json=payload)
    second = cmd_client.post("/api/v1/report-research", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    first_case = uuid.UUID(first.json()["case"]["id"])
    second_case = uuid.UUID(second.json()["case"]["id"])
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    assert cmd_session.scalars(
        select(ReportClaim).where(ReportClaim.research_case_id.in_((first_case, second_case)))
    ).all() == []
    first_spans = list(
        cmd_session.scalars(
            select(ReportCaseSourceSpan.source_span_id).where(
                ReportCaseSourceSpan.research_case_id == first_case
            )
        )
    )
    second_spans = list(
        cmd_session.scalars(
            select(ReportCaseSourceSpan.source_span_id).where(
                ReportCaseSourceSpan.research_case_id == second_case
            )
        )
    )
    assert len(first_spans) == len(second_spans) == 1
    assert first_spans[0] != second_spans[0]
    assert cmd_session.scalars(
        select(ReportExtractionClaim).where(
            ReportExtractionClaim.research_case_id.in_((first_case, second_case))
        )
    ).all() == []


def test_invalid_later_draft_rolls_back_all_report_extraction_output_and_retries(
    session, document_service, research_service
) -> None:
    from app.services.report_research import ExtractedReportClaim, ReportClaimExtractor

    report_case = research_service.add_case(
        title="原子抽取", industry_topic="研报研究", created_by="tester"
    )
    document = document_service.freeze(
        raw=b"atomic", source_url="report://fixture/atomic", title="原子抽取"
    )
    document_service.attach_to_case(
        research_case_id=report_case.id, document_version_id=document.id
    )
    first_span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="第一句",
    )
    second_span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1, "paragraph": 2},
        verbatim_text="第二句",
    )
    for span in (first_span, second_span):
        session.add(
            ReportCaseSourceSpan(
                research_case_id=report_case.id,
                document_version_id=document.id,
                source_span_id=span.id,
            )
        )
    session.flush()
    initial_statement_count = session.query(SourceStatement).count()

    class LaterInvalidExtractor:
        def extract(self, *, span):
            kind = "report_opinion" if span.id == first_span.id else "unknown"
            return [ExtractedReportClaim(kind=kind, statement=span.verbatim_text)]

    with pytest.raises(ValueError, match="unsupported report claim kind"):
        ReportClaimExtractor(session, LaterInvalidExtractor()).extract(report_case.id)
    assert session.query(ReportClaim).count() == 0
    assert session.query(ReportExtractionClaim).count() == 0
    assert session.query(SourceStatement).count() == initial_statement_count

    class ValidExtractor:
        def extract(self, *, span):
            return [ExtractedReportClaim(kind="report_opinion", statement=span.verbatim_text)]

    claims = ReportClaimExtractor(session, ValidExtractor()).extract(report_case.id)
    assert len(claims) == 2
    assert session.query(ReportExtractionClaim).count() == 1


def test_failed_pdf_upload_is_frozen_and_returns_recoverable_state(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    raw = b"%PDF-fixture-that-cannot-be-parsed"
    response = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={
            "title": "无法解析的公司研报",
            "publisher": "某券商",
            "published_at": "2026-08-03T08:00:00Z",
            "filename": "broken-report.pdf",
        },
        content=raw,
        headers={"content-type": "application/pdf"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "needs_supplement"
    assert body["next_action"] == "supplement_text"
    assert body["blocking_reason"] == "PDF text extraction failed: PdfParseError"
    assert body["needs_text_or_pages"] is True
    document_id = uuid.UUID(body["document"]["id"])
    document = cmd_session.get(DocumentVersion, document_id)
    assert document is not None
    assert document.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert document.parse_state == "failed"
    span = cmd_session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.locator["input_kind"] == "pdf_upload"
    assert span.locator["parse_state"] == "failed"
    assert cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    ) is None
    record = cmd_session.scalar(
        select(DocumentSourceRecord).where(
            DocumentSourceRecord.document_version_id == document.id
        )
    )
    assert record is not None
    assert record.admission_type == "uploaded_file"
    blob = cmd_session.scalar(
        select(DocumentBlob).where(DocumentBlob.document_version_id == document.id)
    )
    assert blob is not None
    assert blob.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert (tmp_path / "report-blobs" / blob.storage_key).read_bytes() == raw
    retrieved = cmd_client.get(
        f"/api/v1/report-research/documents/{document.id}/original"
    )
    assert retrieved.status_code == 200
    assert retrieved.headers["content-type"].startswith("application/pdf")
    assert retrieved.content == raw
    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(
            update(DocumentBlob)
            .where(DocumentBlob.id == blob.id)
            .values(storage_key="sha256/replaced")
        )


def test_supplement_endpoint_accepts_authorized_failed_pdf_recovery(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    created = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "需补正文 PDF", "filename": "scan.pdf"},
        content=b"%PDF-no-text",
        headers={"content-type": "application/pdf"},
    )
    assert created.status_code == 201
    original = created.json()

    resumed = cmd_client.post(
        f"/api/v1/report-research/{original['case']['id']}/documents/{original['document']['id']}/supplement",
        json={
            "content": "研报观点：未上市供应商甲是星海科技的供应商。",
            "page_reference": "第 3 页",
            "created_by": "analyst-a",
        },
    )

    assert resumed.status_code == 201
    body = resumed.json()
    assert body["case"]["id"] == original["case"]["id"]
    assert body["document"]["id"] != original["document"]["id"]
    assert body["document"]["input_kind"] == "recovery_text"
    assert body["state"] == "pending_candidate_extraction"
    assert body["next_action"] == "extract_candidates"
    assert body["recovery_snapshot"] is None
    document_id = uuid.UUID(original["document"]["id"])
    spans = list(
        cmd_session.scalars(
            select(SourceSpan)
            .where(SourceSpan.document_version_id == document_id)
            .order_by(SourceSpan.id)
        )
    )
    assert [span.locator["input_kind"] for span in spans] == ["pdf_upload"]
    recovery_span = cmd_session.scalar(
        select(SourceSpan).where(
            SourceSpan.document_version_id == uuid.UUID(body["document"]["id"])
        )
    )
    assert recovery_span is not None
    assert recovery_span.locator["claimed_page_reference"] == "第 3 页"
    claims = list(
        cmd_session.scalars(
            select(ReportClaim).where(
                ReportClaim.research_case_id == uuid.UUID(original["case"]["id"])
            )
        )
    )
    assert claims == []
    scope = cmd_client.get(
        f"/api/v1/report-research/{original['case']['id']}/scopes/current"
    )
    assert scope.status_code == 404
    intake = cmd_client.get(
        f"/api/v1/report-research/{original['case']['id']}/intake"
    )
    assert intake.status_code == 200
    intake_body = intake.json()
    assert intake_body["primary_document"] == original["document"]
    assert intake_body["supplement_artifacts"] == []
    assert intake_body["supplement_documents"] == [
        {
            "document": body["document"],
            "original_document_id": original["document"]["id"],
            "claimed_page_reference": "第 3 页",
        }
    ]
    assert intake_body["state"] == "pending_candidate_extraction"
    assert intake_body["next_action"] == "extract_candidates"


def test_supplement_endpoint_accepts_authorized_text_recovery(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "先无主张的研报",
            "content": "资料描述：订单出现变化。",
        },
    )
    assert created.status_code == 201
    original = created.json()
    assert "initial_scope_version" not in original

    resumed = cmd_client.post(
        f"/api/v1/report-research/{original['case']['id']}/documents/{original['document']['id']}/supplement",
        json={"content": "研报观点：未上市供应商甲是星海科技的供应商。"},
    )

    assert resumed.status_code == 201
    assert resumed.json()["document"]["id"] != original["document"]["id"]
    assert resumed.json()["state"] == "pending_candidate_extraction"


def test_duplicate_recovery_bytes_return_case_local_artifact_idempotently(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    content = "其他案例已经冻结的同字节正文"
    other = cmd_client.post(
        "/api/v1/report-research",
        json={"input_kind": "pasted_text", "title": "既有案例", "content": content},
    )
    assert other.status_code == 201
    original = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "待恢复 PDF", "filename": "failed.pdf"},
        content=b"%PDF-collision",
        headers={"content-type": "application/pdf"},
    )
    assert original.status_code == 201
    recovery_url = (
        f"/api/v1/report-research/{original.json()['case']['id']}/documents/"
        f"{original.json()['document']['id']}/supplement"
    )
    payload = {"content": content, "page_reference": "第 9 页"}
    first = cmd_client.post(recovery_url, json=payload)
    second = cmd_client.post(recovery_url, json=payload)

    assert first.status_code == second.status_code == 201
    first_body = first.json()
    second_body = second.json()
    assert first_body["document"] == original.json()["document"]
    artifact = first_body["recovery_snapshot"]
    assert artifact is not None
    assert artifact["input_kind"] == "recovery_text"
    assert artifact["claimed_page_reference"] == "第 9 页"
    assert second_body["recovery_snapshot"]["id"] == artifact["id"]
    assert first_body["state"] == "artifact_extraction_unavailable"
    assert first_body["next_action"] == "view_saved_snapshot"
    assert "controlled candidate adapter" in first_body["blocking_reason"]
    assert second_body["state"] == "artifact_extraction_unavailable"
    assert second_body["next_action"] == "view_saved_snapshot"
    assert cmd_session.query(RecoverySupplementSnapshot).count() == 1
    other_document_id = uuid.UUID(other.json()["document"]["id"])
    recovery_case_id = uuid.UUID(original.json()["case"]["id"])
    assert cmd_session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == recovery_case_id,
            CaseDocumentVersion.document_version_id == other_document_id,
        )
    ) is None
    from app.services.source_contracts import RecoverySupplementSnapshotService

    read = RecoverySupplementSnapshotService(cmd_session).read_case_artifact(
        research_case_id=recovery_case_id, snapshot_id=uuid.UUID(artifact["id"])
    )
    assert read.content == content
    assert read.locator == {
        "input_kind": "recovery_text",
        "claimed_page_reference": "第 9 页",
        "parser": "user-pasted-report-v1",
    }
    with pytest.raises(ValueError, match="not found"):
        RecoverySupplementSnapshotService(cmd_session).read_case_artifact(
            research_case_id=uuid.UUID(other.json()["case"]["id"]),
            snapshot_id=uuid.UUID(artifact["id"]),
        )


def test_intake_http_rejects_missing_or_unknown_source_contract(cmd_client) -> None:
    missing = cmd_client.post(
        "/api/v1/report-research",
        json={"input_kind": "pasted_text", "title": "缺合同", "content": "正文"},
        headers={"x-test-omit-source-contract": "1"},
    )
    unknown = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "未知合同",
            "content": "正文",
            "source_contract_id": str(uuid.uuid4()),
        },
    )
    missing_pdf = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "缺合同 PDF"},
        content=b"%PDF-missing-contract",
        headers={
            "content-type": "application/pdf",
            "x-test-omit-source-contract": "1",
        },
    )
    unknown_pdf = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={
            "title": "未知合同 PDF",
            "source_contract_id": str(uuid.uuid4()),
        },
        content=b"%PDF-unknown-contract",
        headers={"content-type": "application/pdf"},
    )
    original = cmd_client.post(
        "/api/v1/report-research",
        json={"input_kind": "pasted_text", "title": "补充合同", "content": "正文"},
    ).json()
    supplement_url = (
        f"/api/v1/report-research/{original['case']['id']}/documents/"
        f"{original['document']['id']}/supplement"
    )
    missing_supplement = cmd_client.post(
        supplement_url,
        json={"content": "缺合同补充"},
        headers={"x-test-omit-source-contract": "1"},
    )
    unknown_supplement = cmd_client.post(
        supplement_url,
        json={"content": "未知合同补充", "source_contract_id": str(uuid.uuid4())},
    )

    assert missing.status_code == 422
    assert unknown.status_code == 422
    assert missing_pdf.status_code == 422
    assert unknown_pdf.status_code == 422
    assert missing_supplement.status_code == 422
    assert unknown_supplement.status_code == 422
    assert unknown.json()["error"]["message"] == "source contract not found"


def test_intake_read_returns_the_primary_document_and_one_pending_action(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "待读取研报",
            "content": "研报观点：订单增长。",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case"]["id"]

    response = cmd_client.get(f"/api/v1/report-research/{case_id}/intake")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "case",
        "primary_document",
        "supplement_documents",
        "supplement_artifacts",
        "state",
        "blocking_reason",
        "next_action",
    }
    assert body["case"] == created.json()["case"]
    assert body["primary_document"] == created.json()["document"]
    assert body["supplement_documents"] == []
    assert body["supplement_artifacts"] == []
    assert body["state"] == "pending_candidate_extraction"
    assert body["blocking_reason"] is None
    assert body["next_action"] == "extract_candidates"


def test_list_report_research_returns_safe_intake_summary_only(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "授权研报",
            "content": "研报观点：订单增长。",
        },
    )
    assert created.status_code == 201
    created_body = created.json()

    response = cmd_client.get(
        "/api/v1/report-research", headers=cmd_client.report_tenant_headers
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item == {
        "case_id": created_body["case"]["id"],
        "title": "授权研报",
        "document_id": created_body["document"]["id"],
        "input_kind": "pasted_text",
        "intake_state": "pending_candidate_extraction",
        "next_action": "extract_candidates",
        "blocking_reason": None,
        "updated_at": item["updated_at"],
    }
    assert datetime.fromisoformat(item["updated_at"]).astimezone(timezone.utc)
    assert "scope" not in item
    assert "claims" not in item
    assert "conclusion" not in item


def test_list_report_research_only_exposes_primary_source_records_for_caller_tenant(
    cmd_client, cmd_session, monkeypatch
) -> None:
    """The dispatch list must never use a caller-selected tenant filter."""
    from app.repositories.source_contracts import SourceContractRepository

    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS",
        json.dumps({"tenant-a-token": "tenant-a", "tenant-b-token": "tenant-b"}),
    )
    tenant_a = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "Tenant A only",
            "content": "A 租户的授权研报正文。",
        },
    )
    assert tenant_a.status_code == 201

    tenant_b_contract = SourceContractRepository(cmd_session).append_contract(
        provider_name="tenant-b report intake contract",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cmd_session.commit()
    tenant_b = cmd_client.post(
        "/api/v1/report-research",
        json={
            "source_contract_id": str(tenant_b_contract.id),
            "input_kind": "pasted_text",
            "title": "Tenant B only",
            "content": "B 租户的授权研报正文。",
        },
    )
    assert tenant_b.status_code == 201

    tenant_a_list = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-a-token"},
    )
    tenant_b_list = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-b-token"},
    )

    assert tenant_a_list.status_code == tenant_b_list.status_code == 200
    assert [
        (item["title"], item["intake_state"], item["updated_at"])
        for item in tenant_a_list.json()["items"]
    ] == [
        (
            "Tenant A only",
            "pending_candidate_extraction",
            tenant_a_list.json()["items"][0]["updated_at"],
        )
    ]
    assert [
        (item["title"], item["intake_state"], item["updated_at"])
        for item in tenant_b_list.json()["items"]
    ] == [
        (
            "Tenant B only",
            "pending_candidate_extraction",
            tenant_b_list.json()["items"][0]["updated_at"],
        )
    ]


def test_list_report_research_uses_case_initial_admission_when_tenants_freeze_identical_bytes(
    cmd_client, cmd_session, monkeypatch
) -> None:
    """A shared document hash must not make one tenant's Case visible to another."""
    from app.repositories.source_contracts import SourceContractRepository

    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS",
        json.dumps({"tenant-a-token": "tenant-a", "tenant-b-token": "tenant-b"}),
    )
    tenant_a = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "TENANT-A-CONFIDENTIAL-CASE",
            "content": "同一份受许可研报正文。",
        },
    )
    assert tenant_a.status_code == 201
    tenant_b_contract = SourceContractRepository(cmd_session).append_contract(
        provider_name="tenant-b identical-bytes contract",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cmd_session.commit()
    tenant_b = cmd_client.post(
        "/api/v1/report-research",
        json={
            "source_contract_id": str(tenant_b_contract.id),
            "input_kind": "pasted_text",
            "title": "Tenant B independent case",
            "content": "同一份受许可研报正文。",
        },
    )
    assert tenant_b.status_code == 201
    assert tenant_a.json()["document"]["id"] == tenant_b.json()["document"]["id"]

    initial_admissions = cmd_session.scalars(
        select(ReportCaseInitialAdmission).order_by(ReportCaseInitialAdmission.created_at)
    ).all()
    assert [admission.tenant_id for admission in initial_admissions] == [
        "tenant-a",
        "tenant-b",
    ]
    assert initial_admissions[0].document_source_record_id != initial_admissions[1].document_source_record_id

    tenant_a_list = cmd_client.get(
        "/api/v1/report-research", headers={"Authorization": "Bearer tenant-a-token"}
    )
    tenant_b_list = cmd_client.get(
        "/api/v1/report-research", headers={"Authorization": "Bearer tenant-b-token"}
    )

    assert [item["title"] for item in tenant_a_list.json()["items"]] == [
        "TENANT-A-CONFIDENTIAL-CASE"
    ]
    assert [item["title"] for item in tenant_b_list.json()["items"]] == [
        "Tenant B independent case"
    ]


def test_supplement_rejects_a_foreign_tenant_even_when_the_primary_bytes_are_shared(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    """A later admission of identical bytes cannot grant write access to Case A."""
    from app.repositories.source_contracts import SourceContractRepository

    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    tenant_a = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "Tenant A failed Case", "filename": "shared.pdf"},
        content=b"%PDF-shared-across-tenants",
        headers={"content-type": "application/pdf"},
    )
    assert tenant_a.status_code == 201
    tenant_b_contract = SourceContractRepository(cmd_session).append_contract(
        provider_name="tenant-b shared primary contract",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cmd_session.commit()
    tenant_b = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={
            "title": "Tenant B independent failed Case",
            "filename": "shared.pdf",
            "source_contract_id": str(tenant_b_contract.id),
        },
        content=b"%PDF-shared-across-tenants",
        headers={"content-type": "application/pdf"},
    )
    assert tenant_b.status_code == 201
    assert tenant_a.json()["document"]["id"] == tenant_b.json()["document"]["id"]

    foreign_supplement = cmd_client.post(
        "/api/v1/report-research/"
        f"{tenant_a.json()['case']['id']}/documents/{tenant_a.json()['document']['id']}/supplement",
        json={
            "content": "Tenant B 无权写入的补充正文。",
            "source_contract_id": str(tenant_b_contract.id),
        },
    )

    assert foreign_supplement.status_code == 422
    assert foreign_supplement.json()["error"]["code"] == "validation_failed"


def test_list_report_research_rejects_missing_or_unknown_tenant_credentials(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS", json.dumps({"tenant-a-token": "tenant-a"})
    )

    missing = cmd_client.get("/api/v1/report-research")
    unknown = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer unknown-token"},
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "permission_denied"


def test_list_report_research_does_not_treat_a_supplement_record_as_primary_access(
    cmd_client, cmd_session, monkeypatch
) -> None:
    """A tenant may list a Case only through its frozen primary document."""
    from app.repositories.source_contracts import SourceContractRepository

    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS", json.dumps({"tenant-b-token": "tenant-b"})
    )
    original = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "Tenant A primary",
            "content": "Tenant A 主资料。",
        },
    )
    assert original.status_code == 201
    original_body = original.json()
    supplemented = cmd_client.post(
        "/api/v1/report-research/"
        f"{original_body['case']['id']}/documents/{original_body['document']['id']}/supplement",
        json={"content": "Tenant A 补充资料。"},
    )
    assert supplemented.status_code == 201

    contracts = SourceContractRepository(cmd_session)
    tenant_b_contract = contracts.append_contract(
        provider_name="tenant-b supplemental record",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    contracts.add_document_source_record(
        document_version_id=uuid.UUID(supplemented.json()["document"]["id"]),
        source_contract_version_id=tenant_b_contract.id,
        admission_type="pasted_snapshot",
        tenant_id="tenant-b",
        source_actor="test",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"test": "supplement-only"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cmd_session.commit()

    response = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-b-token"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_list_report_research_authorizes_only_the_earliest_primary_document(
    cmd_client, cmd_session, monkeypatch
) -> None:
    """A later tenant-B input cannot replace an earlier tenant-A primary."""
    from app.repositories.source_contracts import SourceContractRepository

    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS", json.dumps({"tenant-b-token": "tenant-b"})
    )
    tenant_a = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "Tenant A primary Case",
            "content": "Tenant A 主资料。",
        },
    )
    assert tenant_a.status_code == 201
    tenant_a_body = tenant_a.json()

    contracts = SourceContractRepository(cmd_session)
    tenant_b_contract = contracts.append_contract(
        provider_name="tenant-b primary record",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cmd_session.commit()
    tenant_b = cmd_client.post(
        "/api/v1/report-research",
        json={
            "source_contract_id": str(tenant_b_contract.id),
            "input_kind": "pasted_text",
            "title": "Tenant B own Case",
            "content": "Tenant B 主资料。",
        },
    )
    assert tenant_b.status_code == 201
    tenant_b_body = tenant_b.json()
    tenant_b_document_id = uuid.UUID(tenant_b_body["document"]["id"])
    tenant_b_span = cmd_session.scalar(
        select(SourceSpan)
        .where(SourceSpan.document_version_id == tenant_b_document_id)
        .order_by(SourceSpan.id)
        .limit(1)
    )
    assert tenant_b_span is not None
    linked_at = datetime.now(timezone.utc)
    tenant_a_case_id = uuid.UUID(tenant_a_body["case"]["id"])
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=tenant_a_case_id,
            document_version_id=tenant_b_document_id,
            linked_at=linked_at,
        )
    )
    cmd_session.commit()
    cmd_session.add(
        ReportCaseSourceSpan(
            research_case_id=tenant_a_case_id,
            document_version_id=tenant_b_document_id,
            source_span_id=tenant_b_span.id,
            created_at=linked_at,
        )
    )
    cmd_session.commit()

    response = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-b-token"},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Tenant B own Case"]


def test_list_report_research_ignores_other_tenant_supplement_for_state_and_timestamp(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    """Tenant-B recovery documents cannot change tenant-A's dispatch summary."""
    from app.repositories.documents import DocumentRepository
    from app.repositories.source_contracts import SourceContractRepository
    from app.services.ingest import DocumentService

    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS",
        json.dumps({"tenant-a-token": "tenant-a", "tenant-b-token": "tenant-b"}),
    )
    original = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "Tenant A failed primary", "filename": "failed.pdf"},
        content=b"%PDF-tenant-a-failed",
        headers={"content-type": "application/pdf"},
    )
    assert original.status_code == 201
    original_body = original.json()
    before = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-a-token"},
    )
    assert before.status_code == 200
    before_item = before.json()["items"][0]
    assert before_item["intake_state"] == "needs_supplement"
    assert before_item["next_action"] == "supplement_text"

    contracts = SourceContractRepository(cmd_session)
    tenant_b_contract = contracts.append_contract(
        provider_name="tenant-b child input",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    documents = DocumentRepository(cmd_session)
    tenant_b_document = DocumentService(documents).freeze(
        raw=b"tenant-b-only-supplement",
        source_url="pasted://fixture/tenant-b-only-supplement",
        parser_version="test-fixture",
        title="Tenant B only supplement",
        parse_state="partial",
    )
    contracts.add_document_source_record(
        document_version_id=tenant_b_document.id,
        source_contract_version_id=tenant_b_contract.id,
        admission_type="pasted_snapshot",
        tenant_id="tenant-b",
        source_actor="test",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"test": "tenant-b-only"},
        created_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    future = datetime(2030, 1, 2, tzinfo=timezone.utc)
    original_case_id = uuid.UUID(original_body["case"]["id"])
    original_document_id = uuid.UUID(original_body["document"]["id"])
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=original_case_id,
            document_version_id=tenant_b_document.id,
            linked_at=future,
        )
    )
    cmd_session.commit()
    tenant_b_span = documents.insert_span(
        document_version_id=tenant_b_document.id,
        locator={"input_kind": "recovery_text", "parser": "test-fixture"},
        verbatim_text="tenant-b-only-supplement",
    )
    cmd_session.commit()
    cmd_session.add(
        ReportCaseSourceSpan(
            research_case_id=original_case_id,
            document_version_id=tenant_b_document.id,
            source_span_id=tenant_b_span.id,
            created_at=future,
        )
    )
    cmd_session.commit()
    contracts.add_supplement_link(
        original_document_version_id=original_document_id,
        supplement_document_version_id=tenant_b_document.id,
        claimed_page_reference="第 8 页",
        created_by="tenant-b",
        created_at=future,
    )
    cmd_session.commit()

    tenant_a = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-a-token"},
    )
    tenant_b = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-b-token"},
    )

    assert tenant_a.status_code == tenant_b.status_code == 200
    assert tenant_a.json()["items"] == [before_item]
    assert tenant_b.json()["items"] == []


def test_list_report_research_ignores_other_tenant_recovery_artifact_for_state_and_timestamp(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    """Tenant-B recovery artifacts cannot change tenant-A's dispatch summary."""
    from app.repositories.source_contracts import SourceContractRepository

    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    monkeypatch.setenv(
        "REPORT_RESEARCH_TENANT_TOKENS",
        json.dumps({"tenant-a-token": "tenant-a", "tenant-b-token": "tenant-b"}),
    )
    original = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "Tenant A failed artifact primary", "filename": "failed.pdf"},
        content=b"%PDF-tenant-a-artifact-failed",
        headers={"content-type": "application/pdf"},
    )
    assert original.status_code == 201
    original_body = original.json()
    before = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-a-token"},
    )
    assert before.status_code == 200
    before_item = before.json()["items"][0]
    assert before_item["intake_state"] == "needs_supplement"
    assert before_item["next_action"] == "supplement_text"

    tenant_b_contract = SourceContractRepository(cmd_session).append_contract(
        provider_name="tenant-b artifact input",
        tenant_id="tenant-b",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="test fixture",
        downstream_restrictions="internal research only",
        approved_by="test",
        reason="test fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    original_case_id = uuid.UUID(original_body["case"]["id"])
    original_document_id = uuid.UUID(original_body["document"]["id"])
    SourceContractRepository(cmd_session).add_recovery_supplement_snapshot(
        research_case_id=original_case_id,
        original_document_version_id=original_document_id,
        source_contract_version_id=tenant_b_contract.id,
        tenant_id="tenant-b",
        source_identity="pasted://fixture/tenant-b-only-artifact",
        content_sha256=hashlib.sha256(b"tenant-b-only-artifact").hexdigest(),
        verbatim_text="tenant-b-only-artifact",
        parser_version="test-fixture",
        claimed_page_reference="第 9 页",
        created_by="tenant-b",
        created_at=datetime(2030, 1, 2, tzinfo=timezone.utc),
    )
    cmd_session.commit()

    tenant_a = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-a-token"},
    )
    tenant_b = cmd_client.get(
        "/api/v1/report-research",
        headers={"Authorization": "Bearer tenant-b-token"},
    )

    assert tenant_a.status_code == tenant_b.status_code == 200
    assert tenant_a.json()["items"] == [before_item]
    assert tenant_b.json()["items"] == []


def test_list_report_research_uses_a_bounded_queue_limit(cmd_client) -> None:
    for title in ("第一份", "第二份", "第三份"):
        created = cmd_client.post(
            "/api/v1/report-research",
            json={
                "input_kind": "pasted_text",
                "title": title,
                "content": f"{title}研报正文。",
            },
        )
        assert created.status_code == 201

    limited = cmd_client.get(
        "/api/v1/report-research",
        params={"limit": 2},
        headers=cmd_client.report_tenant_headers,
    )
    too_large = cmd_client.get(
        "/api/v1/report-research",
        params={"limit": 101},
        headers=cmd_client.report_tenant_headers,
    )

    assert limited.status_code == 200
    assert len(limited.json()["items"]) == 2
    assert too_large.status_code == 422
    assert too_large.json()["error"]["code"] == "validation_failed"


def test_list_report_research_limits_case_selection_in_sql(cmd_client, cmd_session) -> None:
    for title in ("第一份", "第二份", "第三份"):
        created = cmd_client.post(
            "/api/v1/report-research",
            json={
                "input_kind": "pasted_text",
                "title": title,
                "content": f"{title}研报正文。",
            },
        )
        assert created.status_code == 201

    statements: list[tuple[str, object]] = []

    def record(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement.upper(), parameters))

    sqlalchemy_event.listen(cmd_session.bind, "before_cursor_execute", record)
    try:
        response = cmd_client.get(
            "/api/v1/report-research",
            params={"limit": 2},
            headers=cmd_client.report_tenant_headers,
        )
    finally:
        sqlalchemy_event.remove(cmd_session.bind, "before_cursor_execute", record)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert any("LIMIT" in statement for statement, _parameters in statements)


def test_list_report_research_openapi_declares_tenant_credentials_and_bounds() -> None:
    from app.main import app

    operation = app.openapi()["paths"]["/api/v1/report-research"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["authorization"]["in"] == "header"
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "default": 50,
        "title": "Limit",
    }
    assert set(operation["responses"]).issuperset({"200", "401", "403", "422"})


def test_list_report_research_uses_a_fixed_select_budget_as_queue_grows(
    cmd_client, cmd_session
) -> None:
    def create_reports(prefix: str, count: int) -> None:
        for index in range(count):
            created = cmd_client.post(
                "/api/v1/report-research",
                json={
                    "input_kind": "pasted_text",
                    "title": f"{prefix}-{index}",
                    "content": f"{prefix}-{index} 的授权研报正文。",
                },
            )
            assert created.status_code == 201

    def select_count() -> int:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sqlalchemy_event.listen(cmd_session.bind, "before_cursor_execute", record)
        try:
            response = cmd_client.get(
                "/api/v1/report-research",
                params={"limit": 100},
                headers=cmd_client.report_tenant_headers,
            )
            assert response.status_code == 200
        finally:
            sqlalchemy_event.remove(cmd_session.bind, "before_cursor_execute", record)
        return len(statements)

    create_reports("small", 3)
    three_cases = select_count()
    create_reports("large", 5)
    eight_cases = select_count()

    assert eight_cases == three_cases
    assert eight_cases <= 8


def test_list_report_research_orders_newest_first(cmd_client) -> None:
    older = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "较早",
            "content": "较早研报正文。",
        },
    )
    newer = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "较新",
            "content": "较新研报正文。",
        },
    )
    assert older.status_code == newer.status_code == 201

    response = cmd_client.get(
        "/api/v1/report-research", headers=cmd_client.report_tenant_headers
    )

    assert response.status_code == 200
    assert [item["case_id"] for item in response.json()["items"]][:2] == [
        newer.json()["case"]["id"],
        older.json()["case"]["id"],
    ]


def test_list_report_research_orders_by_latest_immutable_intake_input(
    cmd_client, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    older = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "较早但后补资料", "filename": "older.pdf"},
        content=b"%PDF-older-case",
        headers={"content-type": "application/pdf"},
    )
    newer = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "较新但未补资料",
            "content": "较新研报正文。",
        },
    )
    assert older.status_code == newer.status_code == 201
    older_body = older.json()
    supplemented = cmd_client.post(
        "/api/v1/report-research/"
        f"{older_body['case']['id']}/documents/{older_body['document']['id']}/supplement",
        json={"content": "后补的可读正文。", "page_reference": "第 3 页"},
    )
    assert supplemented.status_code == 201

    response = cmd_client.get(
        "/api/v1/report-research",
        params={"limit": 1},
        headers=cmd_client.report_tenant_headers,
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["case_id"] for item in items] == [older_body["case"]["id"]]


def test_list_report_research_counts_recovery_artifact_as_latest_intake_input(
    cmd_client, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    duplicate_content = "已由其他案例冻结的恢复正文。"
    assert cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "既有同字节资料",
            "content": duplicate_content,
        },
    ).status_code == 201
    older = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "较早但后有恢复快照", "filename": "older.pdf"},
        content=b"%PDF-older-artifact",
        headers={"content-type": "application/pdf"},
    )
    newer = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "较新但未补资料",
            "content": "较新研报正文。",
        },
    )
    assert older.status_code == newer.status_code == 201
    older_body = older.json()
    recovered = cmd_client.post(
        "/api/v1/report-research/"
        f"{older_body['case']['id']}/documents/{older_body['document']['id']}/supplement",
        json={"content": duplicate_content, "page_reference": "第 9 页"},
    )
    assert recovered.status_code == 201
    assert recovered.json()["recovery_snapshot"] is not None

    response = cmd_client.get(
        "/api/v1/report-research", headers=cmd_client.report_tenant_headers
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["case_id"] == older_body["case"]["id"]
    assert datetime.fromisoformat(items[0]["updated_at"]) > datetime.fromisoformat(
        next(item for item in items if item["case_id"] == newer.json()["case"]["id"])[
            "updated_at"
        ]
    )


def test_intake_read_keeps_recovery_artifact_and_its_only_action(
    cmd_client, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    content = "其他案例已经冻结的同字节正文"
    assert cmd_client.post(
        "/api/v1/report-research",
        json={"input_kind": "pasted_text", "title": "既有案例", "content": content},
    ).status_code == 201
    original = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "待恢复 PDF", "filename": "failed.pdf"},
        content=b"%PDF-collision",
        headers={"content-type": "application/pdf"},
    )
    assert original.status_code == 201
    original_body = original.json()
    recovered = cmd_client.post(
        "/api/v1/report-research/"
        f"{original_body['case']['id']}/documents/{original_body['document']['id']}/supplement",
        json={"content": content, "page_reference": "第 9 页"},
    )
    assert recovered.status_code == 201

    response = cmd_client.get(
        f"/api/v1/report-research/{original_body['case']['id']}/intake"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["primary_document"] == original_body["document"]
    assert body["supplement_documents"] == []
    assert len(body["supplement_artifacts"]) == 1
    artifact = body["supplement_artifacts"][0]
    assert artifact["input_kind"] == "recovery_text"
    assert artifact["claimed_page_reference"] == "第 9 页"
    assert artifact["original_document_id"] == original_body["document"]["id"]
    assert body["state"] == "artifact_extraction_unavailable"
    assert body["next_action"] == "view_saved_snapshot"
    assert "controlled candidate adapter" in body["blocking_reason"]


def test_intake_prefers_an_executable_supplement_document_over_an_artifact(
    cmd_client, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    duplicated_content = "其他案例已经冻结的同字节正文"
    assert cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "既有案例",
            "content": duplicated_content,
        },
    ).status_code == 201
    original = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "混合恢复 PDF", "filename": "failed.pdf"},
        content=b"%PDF-mixed-recovery",
        headers={"content-type": "application/pdf"},
    )
    assert original.status_code == 201
    original_body = original.json()
    recovery_url = (
        f"/api/v1/report-research/{original_body['case']['id']}/documents/"
        f"{original_body['document']['id']}/supplement"
    )
    artifact = cmd_client.post(recovery_url, json={"content": duplicated_content})
    executable = cmd_client.post(
        recovery_url,
        json={"content": "可抽取的补充正文：供应商甲订单增长。"},
    )
    assert artifact.status_code == executable.status_code == 201

    intake = cmd_client.get(
        f"/api/v1/report-research/{original_body['case']['id']}/intake"
    )

    assert intake.status_code == 200
    body = intake.json()
    assert len(body["supplement_artifacts"]) == 1
    assert len(body["supplement_documents"]) == 1
    assert body["state"] == "pending_candidate_extraction"
    assert body["blocking_reason"] is None
    assert body["next_action"] == "extract_candidates"


def test_intake_read_returns_not_found_for_unknown_case(cmd_client) -> None:
    response = cmd_client.get(f"/api/v1/report-research/{uuid.uuid4()}/intake")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_pending_intake_wiki_returns_typed_not_ready_conflict(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "未审核范围研报",
            "content": "研报观点：订单增长。",
        },
    )
    assert created.status_code == 201

    response = cmd_client.get(
        f"/api/v1/report-research/{created.json()['case']['id']}/wiki"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_intake_not_ready"


def test_wiki_remains_available_after_an_explicit_report_scope(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "已明确范围研报",
            "content": "研报观点：供应商甲是星海科技的供应商。",
        },
    )
    assert created.status_code == 201
    body = created.json()
    case_id = uuid.UUID(body["case"]["id"])
    document_id = uuid.UUID(body["document"]["id"])

    from app.services.report_research import ReportClaimExtractor, ReportResearchService

    claims = ReportClaimExtractor(cmd_session).extract(case_id)
    relation_ids = list(
        cmd_session.scalars(
            select(ReportRelation.id).where(
                ReportRelation.research_case_id == case_id
            )
        )
    )
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document_id,
        changed_by="reviewer-a",
        change_summary="已审核的研报范围",
        selected_claim_ids=[claim.id for claim in claims],
        selected_relation_ids=relation_ids,
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert response.status_code == 200
    assert response.json()["scope"]["document_id"] == str(document_id)


def test_report_research_rejects_blank_title_or_content(cmd_client) -> None:
    blank_title = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "   ",
            "content": "有内容",
        },
    )
    blank_content = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "有标题",
            "content": " \n ",
        },
    )
    blank_pdf_title = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "  "},
        content=b"%PDF-not-parsed-because-title-is-invalid",
        headers={"content-type": "application/pdf"},
    )

    assert blank_title.status_code == 422
    assert blank_content.status_code == 422
    assert blank_pdf_title.status_code == 422


def test_same_report_title_and_date_with_changed_pasted_content_appends_version(
    cmd_client, cmd_session
) -> None:
    payload = {
        "input_kind": "pasted_text",
        "title": "服务器产业链更新",
        "publisher": "某券商",
        "published_at": "2026-08-01T08:00:00Z",
    }
    first = cmd_client.post(
        "/api/v1/report-research", json={**payload, "content": "第一版订单增长。"}
    )
    second = cmd_client.post(
        "/api/v1/report-research", json={**payload, "content": "修订版订单增长放缓。"}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_document = cmd_session.get(
        DocumentVersion, uuid.UUID(first.json()["document"]["id"])
    )
    second_document = cmd_session.get(
        DocumentVersion, uuid.UUID(second.json()["document"]["id"])
    )
    assert first_document is not None
    assert second_document is not None
    assert first_document.id != second_document.id
    assert first_document.content_sha256 != second_document.content_sha256
    assert second_document.supersedes_id == first_document.id


def test_same_report_title_and_date_with_changed_pdf_bytes_appends_blob_version(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    def _parse_failure(self, raw: bytes, *, document_sha256: str):
        raise PdfParseError("fixture parser failure")

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_failure
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    params = {
        "title": "公司深度报告",
        "publisher": "某券商",
        "published_at": "2026-08-01T08:00:00Z",
        "filename": "company-report.pdf",
    }
    first_raw = b"%PDF-first-revision"
    second_raw = b"%PDF-second-revision"
    first = cmd_client.post(
        "/api/v1/report-research/pdf",
        params=params,
        content=first_raw,
        headers={"content-type": "application/pdf"},
    )
    second = cmd_client.post(
        "/api/v1/report-research/pdf",
        params=params,
        content=second_raw,
        headers={"content-type": "application/pdf"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_document_id = uuid.UUID(first.json()["document"]["id"])
    second_document_id = uuid.UUID(second.json()["document"]["id"])
    first_document = cmd_session.get(DocumentVersion, first_document_id)
    second_document = cmd_session.get(DocumentVersion, second_document_id)
    assert first_document is not None
    assert second_document is not None
    assert first_document.id != second_document.id
    assert second_document.supersedes_id == first_document.id
    first_blob = cmd_session.scalar(
        select(DocumentBlob).where(DocumentBlob.document_version_id == first_document_id)
    )
    second_blob = cmd_session.scalar(
        select(DocumentBlob).where(DocumentBlob.document_version_id == second_document_id)
    )
    assert first_blob is not None
    assert second_blob is not None
    assert first_blob.storage_key != second_blob.storage_key
    assert cmd_client.get(
        f"/api/v1/report-research/documents/{first_document_id}/original"
    ).content == first_raw
    assert cmd_client.get(
        f"/api/v1/report-research/documents/{second_document_id}/original"
    ).content == second_raw


@pytest.mark.parametrize(
    ("input_kind", "source_url"),
    [
        ("pasted_text", None),
        ("web_content", "https://research.example.com/original-whitespace"),
    ],
)
def test_text_report_preserves_original_whitespace_bytes(
    cmd_client, cmd_session, input_kind, source_url
) -> None:
    raw_content = "  原始研报正文\n保留前后空白。  \n"
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": input_kind,
            "title": "原文保真测试",
            "content": raw_content,
            "source_url": source_url,
        },
    )

    assert response.status_code == 201
    document = cmd_session.get(
        DocumentVersion, uuid.UUID(response.json()["document"]["id"])
    )
    assert document is not None
    assert document.content_sha256 == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    span = cmd_session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.verbatim_text == raw_content
    assert cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    ) is None


def test_successful_pdf_keeps_original_filename_in_source_locator(
    cmd_client, cmd_session, monkeypatch, tmp_path
) -> None:
    from app.datasources.docling import ParsedSpan

    def _parse_one_span(self, raw: bytes, *, document_sha256: str):
        locator = coerce_locator_v1(
            {"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
            document_sha256=document_sha256,
            parser_version="pypdf-v1",
        )
        return [
            ParsedSpan(
                locator=locator,
                verbatim_text="成功解析的原文",
                text_sha256=compute_text_sha256("成功解析的原文"),
                context_hash="fixture",
            )
        ]

    monkeypatch.setattr(
        "app.services.report_research.PypdfAdapter.extract_spans", _parse_one_span
    )
    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    response = cmd_client.post(
        "/api/v1/report-research/pdf",
        params={"title": "成功解析研报", "filename": "original-name.pdf"},
        content=b"%PDF-success",
        headers={"content-type": "application/pdf"},
    )

    assert response.status_code == 201
    document_id = uuid.UUID(response.json()["document"]["id"])
    span = cmd_session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document_id)
    )
    assert span is not None
    assert span.locator["filename"] == "original-name.pdf"


def test_blob_store_concurrent_writers_never_expose_partial_object(
    monkeypatch, tmp_path
) -> None:
    from app.services.document_blobs import LocalImmutableBlobStore
    import app.services.document_blobs as blob_module

    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    original_write = blob_module.os.write
    first_write_started = Event()
    allow_first_write = Event()
    first_fd: dict[str, int | None] = {"value": None}

    def _delayed_first_write(fd: int, data) -> int:
        if first_fd["value"] is None:
            first_fd["value"] = fd
            first_write_started.set()
            assert allow_first_write.wait(timeout=5)
        return original_write(fd, data)

    monkeypatch.setattr(blob_module.os, "write", _delayed_first_write)
    raw = b"complete PDF bytes" * 1024
    start = Barrier(2)
    results: list[tuple[str, str]] = []
    errors: list[BaseException] = []

    def _persist() -> None:
        try:
            start.wait()
            results.append(LocalImmutableBlobStore().persist(raw))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = Thread(target=_persist)
    second = Thread(target=_persist)
    first.start()
    second.start()
    assert first_write_started.wait(timeout=5)
    allow_first_write.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not errors
    assert len(results) == 2
    assert results[0] == results[1]
    key, _ = results[0]
    assert (tmp_path / "report-blobs" / key).read_bytes() == raw


@pytest.mark.pg_only
def test_postgres_concurrent_blob_reference_creation_rereads_existing_row(
    engine, monkeypatch, tmp_path
) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService
    from app.services.report_research import ReportResearchService

    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    raw = b"same uploaded PDF bytes"
    try:
        document = DocumentService(DocumentRepository(bootstrap)).freeze(
            raw=raw,
            source_url="report://pdf_upload/blob-race",
            title="并发 blob",
            natural_key=hashlib.sha256(raw).hexdigest()[:32],
        )
        bootstrap.commit()
        document_id = document.id
    finally:
        bootstrap.close()

    monkeypatch.setenv("DOCUMENT_BLOB_DIR", str(tmp_path / "report-blobs"))
    before_insert = Barrier(2)
    monkeypatch.setattr(
        "app.services.report_research._before_document_blob_insert",
        lambda: before_insert.wait(timeout=5),
    )
    errors: list[BaseException] = []

    def _persist_reference() -> None:
        db = SessionLocal()
        try:
            document = db.get(DocumentVersion, document_id)
            assert document is not None
            ReportResearchService(db)._persist_uploaded_blob(document, raw)
            db.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first = Thread(target=_persist_reference)
    second = Thread(target=_persist_reference)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    verify = SessionLocal()
    try:
        blobs = list(
            verify.scalars(
                select(DocumentBlob).where(
                    DocumentBlob.document_version_id == document_id
                )
            )
        )
        assert len(blobs) == 1
        assert (tmp_path / "report-blobs" / blobs[0].storage_key).read_bytes() == raw
    finally:
        verify.close()


def test_extracted_report_claim_keeps_span_statement_and_unresolved_relation_node(
    session, document_service, research_service
) -> None:
    """Extraction must preserve the report locator before it creates a claim.

    The relation deliberately names an unlisted supplier which is absent from
    ``companies``.  The report ledger needs to retain that named node rather
    than invent a listed or unlisted Company record simply to make a graph
    edge render.
    """
    from app.models.report_research import ReportClaim, ReportRelation
    from app.services.report_research import (
        ExtractedReportClaim,
        ExtractedReportRelation,
        ReportClaimExtractor,
    )

    report_case = research_service.add_case(
        title="服务器产业链深度", industry_topic="研报研究", created_by="tester"
    )
    report = document_service.freeze(
        raw="第一页：供应商订单增长。".encode(),
        source_url="report://fixture/server-chain",
        title="服务器产业链深度",
    )
    document_service.attach_to_case(
        research_case_id=report_case.id, document_version_id=report.id
    )
    report_span = document_service.add_span(
        document_version_id=report.id,
        locator={"page": 3, "paragraph": 2, "parser": "fixture"},
        verbatim_text="研报预计星海科技订单增长，并由未上市供应商甲提供关键部件。",
    )
    session.add(
        ReportCaseSourceSpan(
            research_case_id=report_case.id,
            document_version_id=report.id,
            source_span_id=report_span.id,
        )
    )
    session.flush()

    class FakeExtractor:
        def extract(self, *, span):
            assert span.id == report_span.id
            return [
                ExtractedReportClaim(
                    kind="report_forecast",
                    statement="预计星海科技订单增长。",
                    relations=(
                        ExtractedReportRelation(
                            subject_name="未上市供应商甲",
                            object_name="星海科技",
                            relation_kind="supplier",
                            mechanism="提供关键部件以支持订单增长",
                        ),
                    ),
                )
            ]

    claims = ReportClaimExtractor(session, FakeExtractor()).extract(report_case.id)
    session.commit()

    assert len(claims) == 1
    claim = claims[0]
    assert isinstance(claim, ReportClaim)
    assert claim.research_case_id == report_case.id
    assert claim.source_span_id == report_span.id
    assert claim.kind == "report_forecast"
    statement = session.get(SourceStatement, claim.source_statement_id)
    assert statement is not None
    assert statement.source_span_id == report_span.id
    assert statement.normalized_text == claim.statement
    assert len(claim.relations) == 1
    relation = claim.relations[0]
    assert isinstance(relation, ReportRelation)
    assert relation.status == "report_claim"
    assert relation.source_span_id == report_span.id
    assert relation.source_statement_id == claim.source_statement_id
    assert relation.subject_company_id is None
    assert relation.object_company_id is None
    assert relation.subject_name == "未上市供应商甲"
    assert relation.object_name == "星海科技"
    assert session.query(ReportRelation).count() == 1


def test_report_claim_extractor_appends_all_claim_kinds_and_rejects_mutation(
    session, document_service, research_service
) -> None:
    from app.models.report_research import ReportClaim
    from app.services.report_research import (
        ExtractedReportClaim,
        ReportClaimExtractor,
    )

    report_case = research_service.add_case(
        title="产业研究", industry_topic="研报研究", created_by="tester"
    )
    report = document_service.freeze(
        raw=b"report", source_url="report://fixture/kinds", title="产业研究"
    )
    document_service.attach_to_case(
        research_case_id=report_case.id, document_version_id=report.id
    )
    report_span = document_service.add_span(
        document_version_id=report.id,
        locator={"page": 6, "paragraph": 1},
        verbatim_text="观点、预测、假设与风险。",
    )
    session.add(
        ReportCaseSourceSpan(
            research_case_id=report_case.id,
            document_version_id=report.id,
            source_span_id=report_span.id,
        )
    )
    session.flush()

    class FourKindsExtractor:
        def extract(self, *, span):
            return [
                ExtractedReportClaim(kind=kind, statement=kind)
                for kind in (
                    "report_opinion",
                    "report_forecast",
                    "report_assumption",
                    "report_risk",
                )
            ]

    claims = ReportClaimExtractor(session, FourKindsExtractor()).extract(report_case.id)
    session.commit()

    assert {claim.kind for claim in claims} == {
        "report_opinion",
        "report_forecast",
        "report_assumption",
        "report_risk",
    }
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(ReportClaim)
            .where(ReportClaim.id == claims[0].id)
            .values(statement="cannot alter source claim")
        )


def test_report_claim_extractor_does_not_read_a_span_from_another_case(
    session, document_service, research_service
) -> None:
    from app.services.report_research import ReportClaimExtractor

    target_case = research_service.add_case(
        title="目标研报", industry_topic="研报研究", created_by="tester"
    )
    other_case = research_service.add_case(
        title="其他研报", industry_topic="研报研究", created_by="tester"
    )
    target = document_service.freeze(
        raw=b"target", source_url="report://fixture/target", title="目标研报"
    )
    other = document_service.freeze(
        raw=b"other", source_url="report://fixture/other", title="其他研报"
    )
    document_service.attach_to_case(
        research_case_id=target_case.id, document_version_id=target.id
    )
    document_service.attach_to_case(
        research_case_id=other_case.id, document_version_id=other.id
    )
    document_service.add_span(
        document_version_id=other.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="其他案例不可读取的观点。",
    )

    class FailsIfCalled:
        def extract(self, *, span):  # pragma: no cover - assertion proves it
            raise AssertionError(f"foreign span leaked into extractor: {span.id}")

    assert ReportClaimExtractor(session, FailsIfCalled()).extract(target_case.id) == []


def test_report_claim_and_relation_reject_cross_case_or_mismatched_provenance(
    session, document_service, research_service
) -> None:
    """Denormalized case/span keys must not be forgeable through ORM writes."""
    from app.models.report_research import ReportClaim, ReportRelation

    first_case = research_service.add_case(
        title="第一份研报", industry_topic="研报研究", created_by="tester"
    )
    second_case = research_service.add_case(
        title="第二份研报", industry_topic="研报研究", created_by="tester"
    )
    first_document = document_service.freeze(
        raw=b"first", source_url="report://fixture/first", title="第一份研报"
    )
    second_document = document_service.freeze(
        raw=b"second", source_url="report://fixture/second", title="第二份研报"
    )
    document_service.attach_to_case(
        research_case_id=first_case.id, document_version_id=first_document.id
    )
    document_service.attach_to_case(
        research_case_id=second_case.id, document_version_id=second_document.id
    )
    first_span = document_service.add_span(
        document_version_id=first_document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="第一份观点",
    )
    second_span = document_service.add_span(
        document_version_id=second_document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="第二份观点",
    )
    for case, document, span in (
        (first_case, first_document, first_span),
        (second_case, second_document, second_span),
    ):
        session.add(
            ReportCaseSourceSpan(
                research_case_id=case.id,
                document_version_id=document.id,
                source_span_id=span.id,
            )
        )
    session.flush()
    first_statement = research_service.add_statement(
        first_span.id, "第一份观点", kind="research_opinion"
    )
    second_statement = research_service.add_statement(
        second_span.id, "第二份观点", kind="research_opinion"
    )
    session.commit()

    forged = ReportClaim(
        research_case_id=first_case.id,
        source_span_id=second_span.id,
        source_statement_id=second_statement.id,
        kind="report_opinion",
        statement="不应跨案例引用。",
    )
    session.add(forged)
    with pytest.raises(ValueError, match="report claim source span is not selected"):
        session.flush()
    session.rollback()

    valid_claim = ReportClaim(
        research_case_id=first_case.id,
        source_span_id=first_span.id,
        source_statement_id=first_statement.id,
        kind="report_opinion",
        statement="第一份观点",
    )
    session.add(valid_claim)
    session.flush()
    session.add(
        ReportRelation(
            claim_id=valid_claim.id,
            research_case_id=second_case.id,
            source_span_id=first_span.id,
            source_statement_id=first_statement.id,
            subject_name="甲",
            object_name="乙",
            relation_kind="supplier",
            mechanism="不应篡改关系的案例归属",
            status="report_claim",
        )
    )
    with pytest.raises(ValueError, match="report relation provenance must match"):
        session.flush()
