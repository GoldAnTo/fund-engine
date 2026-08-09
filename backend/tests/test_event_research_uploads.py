from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select

from app.models.ledger import CaseDocumentVersion, DocumentUploadArtifact, SourceSpan
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
            "created_by": "human:lin",
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["case_id"])


def _upload(cmd_client, case_id: uuid.UUID, *, name: str, raw: bytes, mime: str):
    return cmd_client.post(
        f"/api/v1/event-research/{case_id}/uploaded-materials",
        files={"file": (name, raw, mime)},
        data={
            "actor": "human:lin",
            "source_metadata": json.dumps(
                {
                    "authority_level": "primary_disclosure",
                    "permissions": {"ai_processing": True, "display": True},
                    "retention_policy": "case_retained",
                }
            ),
        },
    )


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
        "uploaded_by": "human:lin",
        "retention_policy": "case_retained",
    }
    assert cmd_session.scalars(
        select(ResearchRun).where(ResearchRun.research_case_id == case_id)
    ).all() == []


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
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "published"
    cmd_session.commit()

    response = _upload(
        cmd_client,
        case_id,
        name="late.txt",
        raw=b"later material",
        mime="text/plain",
    )

    assert response.status_code == 422
    assert cmd_session.scalars(select(DocumentUploadArtifact)).all() == []
