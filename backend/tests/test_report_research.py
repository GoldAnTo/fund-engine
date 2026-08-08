"""Report-first research input contracts and immutable provenance."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from threading import Barrier, Event, Thread

import pytest
from sqlalchemy import select, update

from app.datasources.docling import PdfParseError
from app.documents.locators import coerce_locator_v1, compute_text_sha256
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentBlob,
    DocumentVersion,
    ImmutableLedgerError,
    ResearchCase,
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
    assert body["state"] == "ready_to_extract"
    assert body["initial_scope_version"] is None

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
    statement = cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    )
    assert statement is not None
    assert statement.normalized_text == "资料描述：订单增长。"


def test_web_report_preserves_origin_url_and_creates_auditable_statement(
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
    ) is not None


def test_created_report_automatically_extracts_claim_and_safe_named_relation(
    cmd_client, cmd_session
) -> None:
    """The report intake path must immediately populate the research ledger."""
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
    assert response.json()["initial_scope_version"] == 1
    case_id = uuid.UUID(response.json()["case"]["id"])
    claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == case_id)
    )
    assert claim is not None
    assert claim.kind == "report_opinion"
    span = cmd_session.get(SourceSpan, claim.source_span_id)
    assert span is not None
    assert span.locator["paragraph"] == 1
    statement = cmd_session.get(SourceStatement, claim.source_statement_id)
    assert statement is not None
    assert statement.source_span_id == span.id
    relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert relation is not None
    assert relation.relation_kind == "supplier"
    assert relation.subject_name == "未上市供应商甲"
    assert relation.object_name == "星海科技"
    assert relation.subject_company_id is None
    assert relation.object_company_id is None
    task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.research_case_id == case_id,
            ResearchTask.task_type == "report_market_impact",
        )
    )
    assert task is not None
    assert task.query == f"report_market_impact:{claim.id}"


def test_plain_relation_and_narrator_prefix_create_name_only_report_nodes(
    cmd_client, cmd_session
) -> None:
    """Explicit relations are report assertions even when no opinion cue exists."""
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
    plain_claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == plain_case)
    )
    narrated_claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == narrated_case)
    )
    competitor_claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == competitor_case)
    )
    assert plain_claim is not None
    assert narrated_claim is not None
    assert competitor_claim is not None
    plain_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == plain_claim.id)
    )
    narrated_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == narrated_claim.id)
    )
    competitor_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == competitor_claim.id)
    )
    assert plain_relation is not None
    assert (plain_relation.subject_name, plain_relation.object_name) == (
        "未上市客户乙",
        "星海科技",
    )
    assert narrated_relation is not None
    assert (narrated_relation.subject_name, narrated_relation.object_name) == (
        "未上市供应商甲",
        "星海科技",
    )
    assert competitor_relation is not None
    assert competitor_relation.relation_kind == "competitor"
    assert (competitor_relation.subject_name, competitor_relation.object_name) == (
        "甲公司",
        "乙公司",
    )
    assert all(
        company_id is None
        for company_id in (
            plain_relation.subject_company_id,
            plain_relation.object_company_id,
            narrated_relation.subject_company_id,
            narrated_relation.object_company_id,
            competitor_relation.subject_company_id,
            competitor_relation.object_company_id,
        )
    )
    assert cmd_session.scalars(
        select(Company).where(
            Company.name.in_(
                ("未上市客户乙", "未上市供应商甲", "星海科技", "甲公司", "乙公司")
            )
        )
    ).all() == []


def test_adversarial_narrator_prefixes_do_not_become_relation_entities(
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
    assert len(relations) == len(prefixes)
    assert {
        (relation.subject_name, relation.object_name)
        for relation in relations
    } == {("未上市供应商甲", "星海科技")}
    assert all(
        relation.subject_company_id is None and relation.object_company_id is None
        for relation in relations
    )


def test_same_report_bytes_are_case_owned_and_retry_does_not_duplicate_claims(
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
    first_claims = list(
        cmd_session.scalars(
            select(ReportClaim).where(ReportClaim.research_case_id == first_case)
        )
    )
    second_claims = list(
        cmd_session.scalars(
            select(ReportClaim).where(ReportClaim.research_case_id == second_case)
        )
    )
    assert len(first_claims) == len(second_claims) == 1
    assert first_claims[0].source_span_id != second_claims[0].source_span_id
    for case_id, claim in ((first_case, first_claims[0]), (second_case, second_claims[0])):
        bound_spans = list(
            cmd_session.scalars(
                select(ReportCaseSourceSpan.source_span_id).where(
                    ReportCaseSourceSpan.research_case_id == case_id
                )
            )
        )
        assert bound_spans == [claim.source_span_id]

    from app.services.report_research import ReportClaimExtractor

    assert ReportClaimExtractor(cmd_session).extract(first_case) == []
    assert cmd_session.scalar(
        select(ReportExtractionClaim).where(
            ReportExtractionClaim.research_case_id == first_case
        )
    ) is not None
    assert cmd_session.scalars(
        select(ReportClaim).where(ReportClaim.research_case_id == first_case)
    ).all() == first_claims


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
    assert body["state"] == "needs_text_or_pages"
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
    ) is not None
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
    statement = cmd_session.scalar(
        select(SourceStatement).where(SourceStatement.source_span_id == span.id)
    )
    assert statement is not None
    assert statement.normalized_text == raw_content


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
