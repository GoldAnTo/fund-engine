from __future__ import annotations

from datetime import date, datetime, timezone
from threading import Event, Thread, get_ident
import uuid

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.orm import sessionmaker

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.models.ledger import AIAssessment, AIRun, CaseDocumentVersion, DocumentVersion, EvidenceSnapshot, ImmutableLedgerError, ResearchCase, Thesis, ValidationError
from app.models.research_protocol import CaseMechanismSelectionVersion, MechanismEdgeVersion, MechanismNodeVersion, MechanismTemplateVersion, MetricDefinitionVersion, OutcomeBindingVersion, VerificationRuleVersion
from app.models.source_governance import SourceContract
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import (
    MetricDefinitionInput,
    OutcomeBindingInput,
    ResearchProtocolService,
    VerificationRuleInput,
)


def test_mechanism_template_versions_are_append_only(session) -> None:
    template = MechanismTemplateVersion(
        template_key="overseas_ai_capex_to_china_hardware",
        version=1,
        display_name="海外 AI CapEx 到中国硬件",
        industry_scope="ai_hardware",
        approved_by="human:owner",
        reason="初始模板",
        created_at=datetime.now(timezone.utc),
    )
    session.add(template)
    session.flush()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(MechanismTemplateVersion)
            .where(MechanismTemplateVersion.id == template.id)
            .values(display_name="被改写")
        )


def test_seeded_ai_capex_template_has_required_and_alternative_nodes(session) -> None:
    template = seed_ai_capex_template(session)
    roles = set(session.scalars(
        select(MechanismNodeVersion.role)
        .where(MechanismNodeVersion.template_version_id == template.id)
    ))

    assert {"required_for_outcome", "required_for_attribution", "alternative_explanation", "scope_guard"}.issubset(roles)
    assert seed_ai_capex_template(session).id == template.id


def test_case_template_selection_is_append_only(session) -> None:
    case = ResearchCase(
        title="机制协议 Case", industry_topic="ai", created_by="human", created_at=datetime.now(timezone.utc)
    )
    session.add(case)
    session.flush()
    template = seed_ai_capex_template(session)

    selection = ResearchProtocolService(session).select_template(
        case.id, template.id, reviewer="human:reviewer", reason="适用范围已核对"
    )

    assert selection.research_case_id == case.id
    assert selection.template_version_id == template.id


def test_template_upgrade_requires_a_new_case_selection_version(session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="模板升级 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    first_template = seed_ai_capex_template(session)
    second_template = MechanismTemplateVersion(
        template_key=first_template.template_key,
        version=2,
        display_name="海外 AI CapEx 到中国硬件（范围澄清）",
        industry_scope=first_template.industry_scope,
        supersedes_id=first_template.id,
        approved_by="human:industry-owner",
        reason="新增范围保护，已有 Case 必须重新复核后才能采用。",
        created_at=now,
    )
    session.add(second_template)
    session.flush()

    service = ResearchProtocolService(session)
    first_selection = service.select_template(case.id, first_template.id, reviewer="human", reason="首次适用")
    upgraded_selection = service.select_template(case.id, second_template.id, reviewer="human", reason="复核模板范围变化")

    assert upgraded_selection.supersedes_id == first_selection.id
    assert service._repo.effective_case_template(case.id).template_version_id == second_template.id


def test_gate_requires_rules_and_independent_metrics_after_template_selection(session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="门槛 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="业务线收入增长", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.flush()
    baseline_available_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    baseline_document = DocumentVersion(content_sha256=uuid.uuid4().hex + uuid.uuid4().hex, source_url="https://disclosure.example.org/baseline", available_at=baseline_available_at, acquired_at=baseline_available_at, parser_version="fixture-v1")
    session.add(baseline_document)
    session.flush()
    session.add_all([
        CaseDocumentVersion(research_case_id=case.id, document_version_id=baseline_document.id, linked_at=baseline_available_at),
        SourceContract(document_version_id=baseline_document.id, source_type="uploaded_file", provider_or_tenant="research-team", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="cn", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="fixture-v1", intake_metadata={}, declared_by="human", created_at=baseline_available_at),
    ])
    session.flush()
    service = ResearchProtocolService(session)
    metric = service.add_metric_version(MetricDefinitionInput(
        metric_id="business_line_revenue", display_name="业务线收入", canonical_definition="指定业务线季度收入",
        entity_scope="business_line", unit="yuan", frequency="quarterly", period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"], role_eligibility=["outcome"],
    ), approved_by="human:owner", reason="结果指标")
    binding = service.create_outcome_binding(thesis.id, OutcomeBindingInput(
        metric_definition_id=metric.id, entity_scope={"company_id": "company-a", "business_line": "光模块"},
        direction="increase", baseline={"source_ref": f"document:{baseline_document.id}", "value": "1", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
        horizon_start=date(2026, 4, 1), horizon_end=date(2026, 12, 31), reviewer="human", reason="固定结果",
    ))
    service.approve_outcome_binding(binding.id, reviewer="human", reason="审核基线")
    service.select_template(case.id, seed_ai_capex_template(session).id, reviewer="human", reason="适用范围")

    result = service.check_researchability(thesis.id)

    assert result.reason_codes == ["missing_verification_rule", "insufficient_primary_metrics", "missing_counter_hypothesis"]

    selection = service._repo.effective_case_template(case.id)
    assert selection is not None
    for edge in session.scalars(select(MechanismEdgeVersion).where(MechanismEdgeVersion.template_version_id == selection.template_version_id)):
        session.add(VerificationRuleVersion(
            research_case_id=case.id, mechanism_edge_id=edge.id, metric_definition_id=metric.id,
            expected_direction="increase", support_predicate="同口径增长", contradiction_predicate="同口径下滑",
            allowed_source_roles=["primary_disclosure"], observed_period_start=date(2026, 4, 1),
            observed_period_end=date(2026, 6, 30), available_at_deadline=date(2026, 8, 31),
            next_verification_event="半年报", reviewer="human", reason="单指标监测", created_at=now,
        ))
    session.flush()
    monitoring = service.check_researchability(thesis.id)
    assert monitoring.status == "single_metric_monitoring"
    assert monitoring.reason_codes == ["insufficient_primary_metrics"]
    assert monitoring.mechanism_template_version_id == selection.template_version_id
    assert monitoring.verification_rule_ids == tuple(
        sorted(
            session.scalars(
                select(VerificationRuleVersion.id).where(
                    VerificationRuleVersion.research_case_id == case.id
                )
            ),
            key=str,
        )
    )


def test_template_and_verification_rule_mutations_take_the_shared_case_lock(
    session, monkeypatch
) -> None:
    now = datetime.now(timezone.utc)
    first_case = ResearchCase(title="第一个事件", industry_topic="ai", created_by="human", created_at=now)
    second_case = ResearchCase(title="第二个事件", industry_topic="ai", created_by="human", created_at=now)
    session.add_all([first_case, second_case])
    session.flush()
    template = seed_ai_capex_template(session)
    locked_case_ids: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.services.research_protocol.lock_event_scope_case",
        lambda _session, case_id: locked_case_ids.append(case_id),
    )
    service = ResearchProtocolService(session)
    service.select_template(first_case.id, template.id, reviewer="human", reason="第一个事件适用")
    service.select_template(second_case.id, template.id, reviewer="human", reason="第二个事件适用")
    metric = service.add_metric_version(MetricDefinitionInput(
        metric_id="case_scoped_capex", display_name="Case 范围 CapEx", canonical_definition="Case 内客户 CapEx",
        entity_scope="company", unit="yuan", frequency="quarterly", period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"], role_eligibility=["driver"],
    ), approved_by="human", reason="用于隔离性测试")
    edge = session.scalar(select(MechanismEdgeVersion).where(MechanismEdgeVersion.template_version_id == template.id))
    assert edge is not None

    first_rule = service.add_verification_rule(first_case.id, edge.id, VerificationRuleInput(
        metric_definition_id=metric.id, expected_direction="increase", support_predicate="CapEx 增长",
        contradiction_predicate="CapEx 下调", allowed_source_roles=["primary_disclosure"],
        observed_period_start=date(2026, 1, 1), observed_period_end=date(2026, 3, 31),
        available_at_deadline=date(2026, 5, 31), next_verification_event="一季报",
        reviewer="human", reason="只用于第一个事件",
    ))

    assert first_rule.research_case_id == first_case.id
    assert service._repo.effective_rule(first_case.id, edge.id).id == first_rule.id
    assert service._repo.effective_rule(second_case.id, edge.id) is None
    assert locked_case_ids == [first_case.id, second_case.id, first_case.id]


@pytest.mark.pg_only
def test_postgres_protocol_mutation_wins_before_final_assessment_write(engine) -> None:
    """A protocol mutation holding the Case lock cannot be assessed stale."""
    SessionLocal = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as setup:
        case = ResearchCase(
            title="protocol assessment race",
            industry_topic="ai",
            created_by="human",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="A directional result must match the locked protocol footprint",
            research_protocol_required=True,
            created_by="human",
            created_at=now,
        )
        metric = MetricDefinitionVersion(
            metric_id=f"race_metric_{uuid.uuid4().hex}",
            version=1,
            display_name="Race metric",
            canonical_definition="One primary metric for coordinated locking",
            entity_scope="business_line",
            unit="yuan",
            frequency="quarterly",
            period_semantics="period_end",
            allowed_source_roles=["primary_disclosure"],
            role_eligibility=["outcome"],
            approved_by="human",
            reason="race fixture",
            created_at=now,
        )
        setup.add_all([thesis, metric])
        setup.flush()
        setup.add(
            OutcomeBindingVersion(
                thesis_id=thesis.id,
                metric_definition_id=metric.id,
                entity_scope={
                    "company_id": "company-a",
                    "business_line": "optics",
                },
                direction="increase",
                baseline={
                    "source_ref": "fixture",
                    "value": "1",
                    "unit": "yuan",
                    "observed_period": "2025-12-31",
                    "available_at": "2026-03-01T00:00:00Z",
                },
                horizon_start=date(2026, 4, 1),
                horizon_end=date(2026, 12, 31),
                state="approved",
                reviewer="human",
                reason="race fixture",
                created_at=now,
            )
        )
        ready_template = seed_ai_capex_template(setup)
        setup.add(
            CaseMechanismSelectionVersion(
                research_case_id=case.id,
                template_version_id=ready_template.id,
                reviewer="human",
                reason="initial monitoring protocol",
                created_at=now,
            )
        )
        setup.flush()
        edges = list(
            setup.scalars(
                select(MechanismEdgeVersion).where(
                    MechanismEdgeVersion.template_version_id == ready_template.id
                )
            )
        )
        setup.add_all(
            VerificationRuleVersion(
                research_case_id=case.id,
                mechanism_edge_id=edge.id,
                metric_definition_id=metric.id,
                expected_direction="increase",
                support_predicate="metric rises",
                contradiction_predicate="metric falls",
                allowed_source_roles=["primary_disclosure"],
                observed_period_start=date(2026, 4, 1),
                observed_period_end=date(2026, 6, 30),
                available_at_deadline=date(2026, 8, 31),
                next_verification_event="interim report",
                reviewer="human",
                reason="single-metric monitoring",
                created_at=now,
            )
            for edge in edges
        )
        blocked_template = MechanismTemplateVersion(
            template_key=f"blocked_race_{uuid.uuid4().hex}",
            version=1,
            display_name="Incomplete replacement template",
            industry_scope="ai",
            approved_by="human",
            reason="coordinated race mutation",
            created_at=now,
        )
        setup.add(blocked_template)
        setup.flush()
        case_id = case.id
        thesis_id = thesis.id
        blocked_template_id = blocked_template.id
        from tests.tenant_admission import admit_case

        admit_case(setup, case.id)

    # Hold the shared Case lock with an uncommitted mutation. The assessment
    # initial read sees the prior monitoring protocol, calls the provider, and
    # then waits on the mutation before its final protocol read.
    mutating = SessionLocal()
    ResearchProtocolService(mutating).select_template(
        case_id,
        blocked_template_id,
        reviewer="human",
        reason="replace with incomplete protocol",
    )
    provider_returned = Event()
    case_lock_attempted = Event()
    assessment_finished = Event()
    errors: list[BaseException] = []
    assessment_thread_id: list[int] = []

    def observe_case_lock(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            assessment_thread_id
            and get_ident() == assessment_thread_id[0]
            and "research_cases" in statement.lower()
            and "for update" in statement.lower()
        ):
            case_lock_attempted.set()

    event.listen(engine, "before_cursor_execute", observe_case_lock)

    def assess() -> None:
        from unittest.mock import patch

        from app.api.v1.commands.engine import rerun_assessment
        from app.errors import ValidationFailedError

        assessing = SessionLocal()
        assessment_thread_id.append(get_ident())
        client = LLMClient(model_version="mock-test", mock=True)

        def provider(*_args, **_kwargs):
            provider_returned.set()
            return {
                "conclusion": "supported",
                "rationale": "This directional output was generated against stale inputs.",
                "gaps": [],
            }

        try:
            client.chat_json = provider
            with patch(
                "app.api.v1.commands.engine.LLMClient.from_env",
                return_value=client,
            ):
                rerun_assessment(thesis_id, tenant_id="test-team", db=assessing)
        except BaseException as exc:
            errors.append(exc)
            assert isinstance(exc, ValidationFailedError)
        finally:
            assessing.close()
            assessment_finished.set()

    thread = Thread(target=assess)
    thread.start()
    assert provider_returned.wait(timeout=5)
    assert case_lock_attempted.wait(timeout=5)
    assert not assessment_finished.wait(timeout=0.1)
    mutating.commit()
    mutating.close()
    thread.join(timeout=5)
    event.remove(engine, "before_cursor_execute", observe_case_lock)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert "researchability gate blocked" in str(errors[0])
    with SessionLocal() as check:
        assert check.scalar(
            select(AIAssessment.id)
            .join(
                EvidenceSnapshot,
                AIAssessment.snapshot_id == EvidenceSnapshot.id,
            )
            .where(EvidenceSnapshot.thesis_id == thesis_id)
        ) is None
        failed_run = check.scalar(
            select(AIRun)
            .where(AIRun.kind == "assess", AIRun.status == "failed")
            .where(AIRun.input_ref["thesis_id"].as_string() == str(thesis_id))
        )
        assert failed_run is not None
        assert failed_run.input_ref["final_protocol_status"] == "blocked"


def test_blocked_protocol_thesis_never_freezes_an_assessment_snapshot(session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="拦截 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="未定义结果变量", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.flush()

    with pytest.raises(ValidationError, match="researchability gate blocked: missing_outcome_binding"):
        AssessmentGenerator(LLMClient(model_version="mock-test", mock=True)).generate(
            thesis.id, now, session
        )

    assert list(session.scalars(select(EvidenceSnapshot))) == []
