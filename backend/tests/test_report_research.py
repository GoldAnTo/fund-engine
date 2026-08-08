"""Report-first research input contracts and immutable provenance."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.datasources.docling import PdfParseError
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentBlob,
    DocumentVersion,
    ImmutableLedgerError,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)


def test_pasted_report_is_frozen_and_creates_case(cmd_client, cmd_session) -> None:
    published_at = "2026-08-01T08:00:00Z"
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "服务器产业链更新",
            "publisher": "某券商",
            "published_at": published_at,
            "content": "核心观点：订单增长。",
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
    assert statement.normalized_text == "核心观点：订单增长。"


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

    assert blank_title.status_code == 422
    assert blank_content.status_code == 422


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
