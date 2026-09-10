from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import String, select

from app.errors import PermissionDeniedError
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentUploadArtifact,
    DocumentVersion,
    ResearchCase,
    Thesis,
)
from app.models.operational import Job, ResearchRun
from app.models.source_governance import SourceContract
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction
from app.services.event_research import EventResearchService


class _FakeExtractor:
    def extract(self, raw_input: str, source_url: str | None) -> EventExtraction:
        return EventExtraction(
            event_title=raw_input,
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


def _invalid_source_interval_request() -> CreateEventResearchRequest:
    return CreateEventResearchRequest(
        raw_input="具有无效来源有效期的研究主题",
        source_type="pasted_snapshot",
        source_metadata={
            "authority_level": "user_supplied",
            "effective_from": "2026-12-31",
            "effective_until": "2026-01-01",
        },
        event_title="无效有效期研究",
        research_question="无效有效期是否会回滚已创建的研究？",
        candidate_factors=["需求变化", "供给约束", "替代解释"],
        created_by="human:alice",
    )


def _assert_early_intake_rows_absent(session) -> None:
    assert list(session.scalars(select(ResearchCase))) == []
    assert list(session.scalars(select(DocumentVersion))) == []
    assert list(session.scalars(select(CaseDocumentVersion))) == []


def test_gateway_actor_requires_host_configured_subject(monkeypatch) -> None:
    from app.api.v1.tenant_context import (
        require_gateway_actor,
        require_research_actor,
    )

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"legacy":"team-a"}')
    with pytest.raises(PermissionDeniedError):
        require_gateway_actor(require_research_actor("Bearer legacy"))

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"alice":{"tenant_id":"team-a","subject_id":"alice","roles":[]}}',
    )
    actor = require_gateway_actor(require_research_actor("Bearer alice"))

    assert (actor.tenant_id, actor.subject_id) == ("team-a", "alice")


@pytest.mark.parametrize(
    "subject_id",
    [None, "", "  \t", "a" * 129, "alice\x00", "alice\u0085", "alice\n"],
)
def test_invalid_configured_gateway_subject_is_not_accepted(
    monkeypatch, subject_id: str | None
) -> None:
    from app.api.v1.tenant_context import require_research_actor

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps(
            {
                "alice": {
                    "tenant_id": "team-a",
                    "subject_id": subject_id,
                    "roles": [],
                }
            }
        ),
    )

    with pytest.raises(PermissionDeniedError):
        require_research_actor("Bearer alice")


@pytest.mark.parametrize(
    ("configuration", "expected_subject_id"),
    [
        ("  padded-team \n", None),
        (
            {
                "tenant_id": "  padded-team \n",
                "subject_id": "alice",
                "roles": [],
            },
            "alice",
        ),
    ],
)
def test_host_configuration_normalizes_outer_tenant_whitespace(
    monkeypatch, configuration: object, expected_subject_id: str | None
) -> None:
    from app.api.v1.tenant_context import require_research_actor

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps({"alice": configuration}),
    )

    actor = require_research_actor("Bearer alice")

    assert (actor.tenant_id, actor.subject_id) == (
        "padded-team",
        expected_subject_id,
    )


@pytest.mark.parametrize(
    "configuration",
    [
        "team-a\x00",
        {
            "tenant_id": "team-a\x1fmember",
            "subject_id": "alice",
            "roles": [],
        },
        {
            "tenant_id": "  padded-\x00team \n",
            "subject_id": "alice",
            "roles": [],
        },
    ],
)
def test_host_configuration_rejects_control_characters_in_audit_tenant_values(
    monkeypatch, configuration: object
) -> None:
    from app.api.v1.tenant_context import require_research_actor

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps({"alice": configuration}),
    )

    with pytest.raises(PermissionDeniedError):
        require_research_actor("Bearer alice")


@pytest.mark.parametrize(
    ("model", "column_name"),
    [
        (ResearchCase, "created_by"),
        (CaseTenantAdmission, "admitted_by"),
        (Thesis, "created_by"),
        (EventResearchScopeVersion, "changed_by"),
        (SourceContract, "declared_by"),
        (DocumentUploadArtifact, "uploaded_by"),
    ],
)
def test_event_intake_audit_columns_fit_max_gateway_identity(
    model, column_name: str
) -> None:
    column = model.__table__.c[column_name]
    assert isinstance(column.type, String)
    assert column.type.length >= len("human:" + ("s" * 128))


@pytest.mark.parametrize(
    ("tenant_id", "actor_subject_id", "audit_identity"),
    [
        ("team-a", " host-alice ", "human: host-alice "),
        ("team-a", "s" * 128, "human:" + ("s" * 128)),
        ("t" * 121, None, "tenant:" + ("t" * 121)),
    ],
)
def test_native_intake_derives_exact_gateway_or_legacy_audit_identity(
    cmd_session,
    tenant_id: str,
    actor_subject_id: str | None,
    audit_identity: str,
) -> None:
    started = AutomaticResearchIntakeService(
        cmd_session, extractor=_FakeExtractor()
    ).start(
        "最大长度身份测试",
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
    )
    case_id = uuid.UUID(started.case_id)
    case = cmd_session.get(ResearchCase, case_id)
    admission = cmd_session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    contract = cmd_session.scalar(select(SourceContract))

    assert case is not None and case.created_by == audit_identity
    assert admission is not None and admission.admitted_by == audit_identity
    assert contract is not None and contract.declared_by == audit_identity


@pytest.mark.parametrize(
    ("actor_subject_id", "message"),
    [
        (" \t", "automatic research actor_subject_id must not be blank"),
        (
            "s" * 129,
            "automatic research actor_subject_id must not exceed 128 characters",
        ),
        (
            "alice\x00",
            "automatic research actor_subject_id must not contain control characters",
        ),
        (
            "alice\u0085",
            "automatic research actor_subject_id must not contain control characters",
        ),
        (
            "alice\n",
            "automatic research actor_subject_id must not contain control characters",
        ),
    ],
)
def test_native_intake_rejects_invalid_actor_subject_before_writes(
    cmd_session, actor_subject_id: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        AutomaticResearchIntakeService(
            cmd_session, extractor=_FakeExtractor()
        ).start(
            "无效主体测试",
            tenant_id="team-a",
            actor_subject_id=actor_subject_id,
        )

    assert cmd_session.scalar(select(ResearchCase)) is None


def test_native_intake_rejects_control_character_tenant_before_writes(
    cmd_session,
) -> None:
    with pytest.raises(
        ValueError,
        match="automatic research tenant_id must not contain control characters",
    ):
        AutomaticResearchIntakeService(
            cmd_session, extractor=_FakeExtractor()
        ).start(
            "无效租户测试",
            tenant_id="team-a\x00",
            actor_subject_id=None,
        )

    assert cmd_session.scalar(select(ResearchCase)) is None


def test_commit_true_rolls_back_early_source_governance_failure(
    cmd_session,
) -> None:
    with pytest.raises(
        ValueError, match="effective_until must not be before effective_from"
    ):
        EventResearchService(cmd_session).create(
            _invalid_source_interval_request(),
            tenant_id="team-a",
            workflow_mode="automatic",
        )

    _assert_early_intake_rows_absent(cmd_session)


def test_commit_false_leaves_early_source_governance_failure_to_caller(
    cmd_session,
) -> None:
    with pytest.raises(
        ValueError, match="effective_until must not be before effective_from"
    ):
        EventResearchService(cmd_session).create(
            _invalid_source_interval_request(),
            tenant_id="team-a",
            workflow_mode="automatic",
            commit=False,
        )

    assert cmd_session.in_transaction()
    assert cmd_session.scalar(select(ResearchCase)) is not None
    assert cmd_session.scalar(select(DocumentVersion)) is not None
    assert cmd_session.scalar(select(CaseDocumentVersion)) is not None

    cmd_session.rollback()
    cmd_session.expire_all()

    _assert_early_intake_rows_absent(cmd_session)


def test_native_intake_can_flush_without_committing(cmd_session) -> None:
    intake = AutomaticResearchIntakeService(
        cmd_session, extractor=_FakeExtractor()
    )

    started = intake.start(
        "测试主题",
        tenant_id="team-a",
        actor_subject_id="alice",
        commit=False,
    )

    case_id = uuid.UUID(started.case_id)
    run_id = uuid.UUID(started.run_id)
    assert cmd_session.in_transaction()
    assert cmd_session.get(ResearchCase, case_id) is not None
    assert cmd_session.get(ResearchRun, run_id) is not None
    assert cmd_session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case_id
        )
    ) is not None
    assert cmd_session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    ) is not None
    assert cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    ) is not None
    assert cmd_session.scalar(
        select(Job).where(Job.research_case_id == case_id)
    ) is not None

    cmd_session.rollback()
    cmd_session.expire_all()

    assert cmd_session.get(ResearchCase, case_id) is None


def test_native_intake_leaves_caller_transaction_open_when_create_fails(
    cmd_session, monkeypatch
) -> None:
    from app.services import event_research

    def fail_to_enqueue(self, *args, **kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(event_research.AutoResearchService, "start", fail_to_enqueue)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        AutomaticResearchIntakeService(
            cmd_session, extractor=_FakeExtractor()
        ).start(
            "测试主题",
            tenant_id="team-a",
            actor_subject_id="alice",
            commit=False,
        )

    assert cmd_session.in_transaction()
    assert cmd_session.scalar(select(ResearchCase)) is not None

    cmd_session.rollback()
    cmd_session.expire_all()

    assert cmd_session.scalar(select(ResearchCase)) is None
