"""Wire projections are typed and never trust persisted artifact identifiers."""
import uuid
from datetime import UTC

import pytest
from pydantic import ValidationError

from app.models.research_gateway import ArtifactReference
from app.repositories.research_gateway import ResearchGatewayRepository
from tests.test_research_gateway_automatic_adapter import seed_spec


def test_snapshot_catches_up_and_exposes_only_public_fields(cmd_session):
    from app.services.research_gateway_projection import project_conversation

    spec, run = seed_spec(cmd_session)
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert view.conversation_id == str(spec.conversation_id)
    assert view.messages[0].text == "研究设备验证与收入兑现"
    assert view.runs[0].native_run_id == str(run.id)
    assert view.runs[0].parent_run_spec_id is None
    assert view.runs[0].scope is not None
    assert view.runs[0].scope.topic == "研究设备验证与收入兑现"
    assert view.runs[0].scope.subject_label is None
    assert view.runs[0].scope.evidence_cutoff_at == run.created_at.replace(tzinfo=UTC)
    assert view.runs[0].scope.case_evidence_cutoff_date is None
    assert view.runs[0].scope.source_policy.input_kind == "topic"
    assert view.runs[0].scope.source_policy.allowed_source_types == []
    assert view.runs[0].scope.source_policy.allowed_source_roles == [
        "company_disclosure",
        "licensed_provider",
    ]
    assert view.runs[0].scope.source_policy.has_intake_material is False
    assert set(view.runs[0].scope.model_dump()) == {
        "topic",
        "subject_label",
        "evidence_cutoff_at",
        "case_evidence_cutoff_date",
        "source_policy",
    }
    assert len(view.roles) == 4
    assert view.latest_sequence == view.events[-1].sequence
    assert set(view.events[0].model_dump()) == {
        "sequence", "run_spec_id", "role", "type", "status", "summary",
        "reason_code", "artifacts", "occurred_at",
    }
    wire = view.model_dump_json()
    assert "claim_token" not in wire
    assert "automatic_protocol" not in wire
    assert "factor_statements" not in wire
    again = project_conversation(cmd_session, conversation_id=spec.conversation_id,
                                 after_sequence=view.latest_sequence)
    assert again.events == []


def test_run_scope_uses_only_frozen_protocol_subject_and_legacy_null(cmd_session):
    from app.services.research_gateway_projection import _run_scope

    spec, _ = seed_spec(cmd_session)
    assert _run_scope(spec).subject_label is None
    protocol = dict(spec.frozen_scope["automatic_protocol"])
    protocol["subject"] = {"value": "中微公司"}
    spec.frozen_scope = {**spec.frozen_scope, "automatic_protocol": protocol}
    assert _run_scope(spec).subject_label == "中微公司"
    spec.frozen_scope = {**spec.frozen_scope, "automatic_protocol": {"generated_by": "system"}}
    assert _run_scope(spec) is None


def test_read_drops_forged_or_out_of_scope_artifact_references(cmd_session):
    from app.services.research_gateway_projection import project_conversation

    spec, _ = seed_spec(cmd_session)
    forged_id = uuid.uuid4()
    ResearchGatewayRepository(cmd_session).append_role_event(
        run_spec_id=spec.id, role_key="sources_evidence", event_type="evidence_available",
        source_key="forged-artifact", artifact_refs=[ArtifactReference(
            kind="evidence_link", id=forged_id, case_id=spec.native_case_id,
        )],
    )
    cmd_session.commit()
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert str(forged_id) not in view.model_dump_json()


def test_snapshot_shows_only_allowlisted_system_messages(cmd_session):
    from app.services.research_gateway_policy import UNSUPPORTED_FOLLOWUP_MESSAGE
    from app.services.research_gateway_projection import project_conversation

    spec, _ = seed_spec(cmd_session)
    repo = ResearchGatewayRepository(cmd_session)
    for text in (UNSUPPORTED_FOLLOWUP_MESSAGE, "PRIVATE raw model message"):
        repo.append_message(conversation_id=spec.conversation_id, tenant_id=spec.tenant_id,
                            subject_id=spec.subject_id, text=text, message_kind="system")
    cmd_session.commit()
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert view.messages[-1].kind == "system"
    assert view.messages[-1].text == UNSUPPORTED_FOLLOWUP_MESSAGE
    assert "PRIVATE" not in view.model_dump_json()


def test_snapshot_refreshes_conversation_cursor_cached_by_preauthorization(cmd_session):
    from sqlalchemy.orm import sessionmaker

    from app.api.v1.tenant_context import ResearchActor
    from app.services.research_gateway import ResearchGateway
    from app.services.research_gateway_projection import project_conversation

    spec, _ = seed_spec(cmd_session)
    cached = ResearchGateway(cmd_session).require_read_access(ResearchActor("team-a", frozenset(), "alice"), spec.conversation_id)
    assert cached.next_event_sequence == 5
    with sessionmaker(bind=cmd_session.get_bind())() as writer:
        newer = project_conversation(writer, conversation_id=spec.conversation_id)
    assert cached.next_event_sequence == 5
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert view.latest_sequence == newer.latest_sequence
    assert len(view.events) == len(newer.events)


def test_safe_event_schema_rejects_raw_fields_and_unbounded_summary():
    from app.schemas.v1.research_gateway import SafeRoleEventDTO

    payload = {"sequence": 1, "run_spec_id": str(uuid.uuid4()), "role": "sources_evidence",
               "type": "role_failed", "status": "failed", "summary": "Traceback SECRET",
               "reason_code": "native_execution_failed", "artifacts": [],
               "occurred_at": "2026-09-05T00:00:00Z"}
    with pytest.raises(ValidationError):
        SafeRoleEventDTO.model_validate(payload)
    payload["summary"] = None
    payload["prompt"] = "SECRET"
    with pytest.raises(ValidationError):
        SafeRoleEventDTO.model_validate(payload)


def complete_with_evidence(session, *, expires_at=None):
    from tests.test_research_gateway_artifact_authorization import (
        complete_authorized_evidence,
    )

    return complete_authorized_evidence(session, expires_at=expires_at)


def test_native_evidence_and_validated_draft_have_real_artifacts(cmd_session):
    from app.services.research_gateway_projection import project_conversation
    spec, _, links = complete_with_evidence(cmd_session)
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    evidence = [ref for event in view.events for ref in event.artifacts if ref.kind == "evidence_link"]
    assert {ref.id for ref in evidence} == {link.id for link in links}
    assert all(ref.case_id == spec.native_case_id for ref in evidence)
    assert any(event.type == "draft_ref" and event.artifacts for event in view.events)
    assert {role.status for role in view.roles} == {"completed"}


def test_historical_evidence_refs_are_rechecked_after_display_right_expires(cmd_session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services import research_gateway_artifacts as artifacts
    from app.services.research_gateway_projection import project_conversation
    from app.services.source_admission import source_contract_is_active

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    spec, _, links = complete_with_evidence(cmd_session, expires_at=expires_at)
    first = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert any(ref.kind == "evidence_link" for e in first.events for ref in e.artifacts)
    monkeypatch.setattr(artifacts, "source_contract_is_active",
                        lambda contract: source_contract_is_active(contract, at=expires_at + timedelta(seconds=1)))
    second = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert not any(ref.kind in {"evidence_link", "document_version"} for e in second.events for ref in e.artifacts)
    assert all(str(link.id) not in second.model_dump_json() for link in links)


def test_native_success_cannot_complete_gateway_after_bound_source_policy_changes(cmd_session):
    from sqlalchemy import select

    from app.models.acquisition import AcquisitionJob
    from app.services.research_gateway_projection import project_conversation

    spec, run, _ = complete_with_evidence(cmd_session)
    first = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert {role.status for role in first.roles} == {"completed"}
    job = cmd_session.scalar(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id))
    job.tenant_id = "foreign-tenant"
    cmd_session.commit()
    second = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert next(role.status for role in second.roles if role.role == "compilation_checks") == "blocked"
    assert not any(ref.kind == "draft" for event in second.events for ref in event.artifacts)
