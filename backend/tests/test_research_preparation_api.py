from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.services.research_preparation import ResearchPreparationService


def _prepared_case(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="preparation API", industry_topic="test", created_by="tester", created_at=now)
    session.add(case); session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex * 2, source_url="https://example.test/preparation", available_at=now, acquired_at=now, parser_version="test")
    session.add(document); session.flush()
    session.add_all([CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now), CaseTenantAdmission(research_case_id=case.id, tenant_id="test-team", initial_document_version_id=document.id, admitted_by="tester", admitted_at=now)])
    preparation = ResearchPreparationService(session).create_for_case(case.id, input_fingerprint="a" * 64, actor="tester")
    session.commit()
    return case, preparation


def test_preparation_summary_is_tenant_scoped_and_hides_secret_event_detail(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    preparation.last_error_code = "preparation_provider_unavailable"
    ResearchPreparationService(cmd_session)._repo.append_event(preparation, research_case_id=case.id, type="unsafe", step=None, message="Bearer sk-secret", detail={"token": "sk-secret", "safe": "ok"})
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "preparing"
    assert body["research_run_id"] is None
    assert "sk-secret" not in response.text
    assert "Bearer" not in response.text
