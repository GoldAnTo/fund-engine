from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from app.models.ledger import CaseDocumentVersion, DocumentUploadArtifact, SourceSpan
from app.models.source_governance import SourceContract
from app.models.operational import EventResearchLifecycle, ResearchRun


def _create_event(cmd_client) -> uuid.UUID:
    response = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "公司披露新增经营数据，市场等待后续验证。",
            "event_title": "经营数据披露后的变动",
            "company_name": "示例公司",
            "ticker": "000001",
            "research_question": "新增经营数据是否改变收入与利润率预期？",
            "candidate_factors": ["订单", "收入", "利润率"],
            "research_protocol_required": False,
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["case_id"])


def _upload(
    cmd_client,
    case_id: uuid.UUID,
    *,
    name: str,
    raw: bytes,
    mime: str,
    source_metadata: dict[str, object] | None = None,
):
    return cmd_client.post(
        f"/api/v1/event-research/{case_id}/uploaded-materials",
        files={"file": (name, raw, mime)},
        data={
            "actor": "user:forged",
            "source_metadata": json.dumps(
                source_metadata
                or {
                    "authority_level": "primary_disclosure",
                    "permissions": {"ai_processing": True, "display": True},
                    "retention_policy": "case_retained",
                }
            ),
        },
    )


def _publish(case_id: uuid.UUID, cmd_session) -> None:
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "published"
    cmd_session.commit()


def test_uploaded_text_original_is_frozen_attached_and_readable_without_a_run(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    raw = "公司披露本季度订单金额增长。".encode()

    response = _upload(
        cmd_client, case_id, name="disclosure.txt", raw=raw, mime="text/plain"
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["parse_state"] == "partial"
    document_id = uuid.UUID(payload["document_version_id"])
    artifact = cmd_session.scalar(
        select(DocumentUploadArtifact).where(
            DocumentUploadArtifact.document_version_id == document_id
        )
    )
    assert artifact is not None
    assert artifact.raw_bytes == raw
    assert artifact.file_name == "disclosure.txt"
    assert artifact.mime_type == "text/plain"
    assert artifact.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert cmd_session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case_id,
            CaseDocumentVersion.document_version_id == document_id,
        )
    ) is not None
    spans = list(
        cmd_session.scalars(
            select(SourceSpan).where(SourceSpan.document_version_id == document_id)
        )
    )
    assert [span.verbatim_text for span in spans] == [raw.decode()]
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["original_file"] == {
        "file_name": "disclosure.txt",
        "mime_type": "text/plain",
        "byte_size": len(raw),
        "object_version": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "uploaded_by": "user:test-team",
        "retention_policy": "case_retained",
    }
    assert cmd_session.scalars(
        select(ResearchRun).where(ResearchRun.research_case_id == case_id)
    ).all() == []


def test_upload_company_disclosure_uses_the_declared_retrieval_reference(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    response = _upload(
        cmd_client,
        case_id,
        name="annual-report.txt",
        raw="公司正式披露年度经营数据。".encode(),
        mime="text/plain",
        source_metadata={
            "research_source_type": "company_disclosure",
            "retrieval_reference": "https://www.cninfo.com.cn/new/disclosure/detail?stockCode=601138",
            "permissions": {"ai_processing": True, "display": True},
        },
    )

    assert response.status_code == 201, response.text
    document_id = uuid.UUID(response.json()["document_version_id"])
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_id)
    )
    assert contract is not None
    assert contract.source_type == "uploaded_file"
    assert contract.research_source_type == "company_disclosure"


def test_deduplicated_upload_rejects_a_different_declared_retrieval_reference(
    cmd_client, cmd_session
) -> None:
    raw = "公司正式披露年度经营数据。".encode()
    first_case_id = _create_event(cmd_client)
    first_reference = "https://issuer-a.example.com/disclosures/annual-report"
    first = _upload(
        cmd_client,
        first_case_id,
        name="annual-report.txt",
        raw=raw,
        mime="text/plain",
        source_metadata={
            "research_source_type": "company_disclosure",
            "retrieval_reference": first_reference,
            "permissions": {"ai_processing": True, "display": True},
        },
    )
    second_case_id = _create_event(cmd_client)
    second = _upload(
        cmd_client,
        second_case_id,
        name="annual-report.txt",
        raw=raw,
        mime="text/plain",
        source_metadata={
            "research_source_type": "company_disclosure",
            "retrieval_reference": "https://issuer-b.example.com/disclosures/annual-report",
            "permissions": {"ai_processing": True, "display": True},
        },
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 422
    assert "deduplicated original has a different source contract" in second.json()[
        "error"
    ]["message"]
    first_document_id = uuid.UUID(first.json()["document_version_id"])
    contract = cmd_session.scalar(
        select(SourceContract).where(
            SourceContract.document_version_id == first_document_id
        )
    )
    assert contract is not None
    assert contract.intake_metadata["retrieval_reference"] == first_reference


@pytest.mark.parametrize("retrieval_reference", [None, "event://not-an-http-url"])
def test_upload_company_disclosure_rejects_a_missing_or_non_http_retrieval_reference(
    cmd_client, retrieval_reference
) -> None:
    case_id = _create_event(cmd_client)
    metadata: dict[str, object] = {
        "research_source_type": "company_disclosure",
        "permissions": {"ai_processing": True, "display": True},
    }
    if retrieval_reference is not None:
        metadata["retrieval_reference"] = retrieval_reference

    response = _upload(
        cmd_client,
        case_id,
        name="annual-report.txt",
        raw="公司正式披露年度经营数据。".encode(),
        mime="text/plain",
        source_metadata=metadata,
    )

    assert response.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in response.json()[
        "error"
    ]["message"]


def test_malformed_pdf_is_kept_as_an_original_and_returns_recovery_state(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    raw = b"%PDF-not-a-real-pdf"

    response = _upload(
        cmd_client, case_id, name="scanned.pdf", raw=raw, mime="application/pdf"
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["parse_state"] == "failed"
    assert payload["next_action"] == "supplement_original"
    document_id = uuid.UUID(payload["document_version_id"])
    assert cmd_session.scalar(
        select(DocumentUploadArtifact.raw_bytes).where(
            DocumentUploadArtifact.document_version_id == document_id
        )
    ) == raw
    assert cmd_session.scalars(
        select(SourceSpan).where(SourceSpan.document_version_id == document_id)
    ).all() == []
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["parse_state"] == "failed"


def test_upload_command_rejects_published_case_without_creating_an_artifact(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    _publish(case_id, cmd_session)

    response = _upload(
        cmd_client,
        case_id,
        name="late.txt",
        raw=b"later material",
        mime="text/plain",
    )

    assert response.status_code == 422
    assert cmd_session.scalars(select(DocumentUploadArtifact)).all() == []


def test_published_case_freezes_pdf_original_before_recording_no_change_decision(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    _publish(case_id, cmd_session)
    raw = b"%PDF-not-a-real-pdf"

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/published-uploaded-material-decisions",
        files={"file": ("late-report.pdf", raw, "application/pdf")},
        data={
            "actor": "user:forged",
            "decision": "no_change",
            "reason": "该报告未提供改变已发布判断的新证据。",
            "source_metadata": json.dumps(
                {
                    "authority_level": "licensed_research",
                    "permissions": {"ai_processing": True, "display": True},
                    "retention_policy": "case_retained",
                }
            ),
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["decision"] == "no_change"
    assert payload["run_id"] is None
    assert payload["lifecycle"]["status"] == "published"
    document_id = uuid.UUID(payload["document_version_id"])
    artifact = cmd_session.scalar(
        select(DocumentUploadArtifact).where(
            DocumentUploadArtifact.document_version_id == document_id
        )
    )
    assert artifact is not None
    assert artifact.raw_bytes == raw
    assert artifact.file_name == "late-report.pdf"
    assert artifact.mime_type == "application/pdf"
    detail = cmd_client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["document"]["parse_state"] == "failed"
    assert detail.json()["document"]["original_file"]["file_name"] == "late-report.pdf"
    assert detail.json()["document"]["original_file"]["uploaded_by"] == "user:test-team"


def test_published_case_keeps_unparseable_pdf_when_reopen_requires_recovery(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    _publish(case_id, cmd_session)
    raw = b"%PDF-not-a-real-pdf"

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/published-uploaded-material-decisions",
        files={"file": ("needs-recovery.pdf", raw, "application/pdf")},
        data={
            "actor": "human:lin",
            "decision": "reopen",
            "reason": "新报告可能改变结论，先进入补证流程。",
            "source_metadata": json.dumps(
                {"permissions": {"ai_processing": True, "display": True}}
            ),
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["decision"] == "reopen"
    assert payload["recovery_required"] is True
    assert payload["run_id"] is None
    assert payload["lifecycle"]["status"] == "published"
    document_id = uuid.UUID(payload["document_version_id"])
    assert cmd_session.scalar(
        select(DocumentUploadArtifact.raw_bytes).where(
            DocumentUploadArtifact.document_version_id == document_id
        )
    ) == raw
    assert cmd_session.scalars(
        select(ResearchRun).where(ResearchRun.research_case_id == case_id)
    ).all() == []


def test_published_material_rejects_deduplicated_original_with_restrictive_new_declaration(
    cmd_client, cmd_session
) -> None:
    case_id = _create_event(cmd_client)
    _publish(case_id, cmd_session)
    raw = b"%PDF-not-a-real-pdf"
    endpoint = f"/api/v1/event-research/{case_id}/published-uploaded-material-decisions"

    first = cmd_client.post(
        endpoint,
        files={"file": ("report.pdf", raw, "application/pdf")},
        data={
            "actor": "human:lin",
            "decision": "no_change",
            "reason": "先冻结以便人工比较。",
            "source_metadata": json.dumps(
                {"permissions": {"ai_processing": True, "display": True}}
            ),
        },
    )
    assert first.status_code == 201, first.text

    restricted = cmd_client.post(
        endpoint,
        files={"file": ("report.pdf", raw, "application/pdf")},
        data={
            "actor": "human:lin",
            "decision": "no_change",
            "reason": "不应借用首次上传的宽松许可。",
            "source_metadata": json.dumps(
                {"permissions": {"ai_processing": False, "display": False}}
            ),
        },
    )

    assert restricted.status_code == 422
    assert "deduplicated original has a different source contract" in restricted.json()["error"]["message"]
    assert len(cmd_session.scalars(select(DocumentUploadArtifact)).all()) == 1
