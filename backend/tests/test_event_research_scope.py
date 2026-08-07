from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun, ResearchTask
from app.services.event_research_scope import EventResearchScopeService


INITIAL_FACTORS = [
    "资本开支上调可能加剧自由现金流担忧",
    "盈利前景与市场预期可能存在分歧",
    "估值重定价可能放大盘后波动",
]


def _create_event(client) -> dict:
    response = client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "Alphabet 公布财报后上调全年资本开支指引，盘后股价下跌。",
            "source_url": "https://example.com/alphabet",
            "event_title": "Alphabet 财报后股价下跌",
            "company_name": "Alphabet",
            "ticker": "GOOGL",
            "market_reaction": "盘后下跌",
            "research_question": "资本开支上调是否是盘后下跌的主要因素？",
            "candidate_factors": INITIAL_FACTORS,
            "created_by": "tester",
        },
    )
    assert response.status_code == 201
    return response.json()


def _scope_statements(session, version_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(EventResearchScopeFactor.statement)
            .where(EventResearchScopeFactor.scope_version_id == version_id)
            .order_by(EventResearchScopeFactor.position)
        )
    )


def _reviewed_evidence(session, case_id: uuid.UUID, factor: str) -> EvidenceLink:
    now = datetime.now(timezone.utc)
    thesis = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == factor,
        )
    )
    assert thesis is not None
    document = DocumentVersion(
        content_sha256=str(uuid.uuid4()).replace("-", ""),
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified release",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="Reviewed evidence",
        locator={"kind": "fixture"},
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="Reviewed evidence statement",
        created_at=now,
    )
    session.add(statement)
    session.flush()
    link = EvidenceLink(
        thesis_id=thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="reviewed fixture",
        scope={"period": "event"},
        available_at=now,
        creator_type="human",
        review_state="reviewed",
        created_at=now,
    )
    session.add(link)
    session.commit()
    return link


def test_creating_event_persists_ordered_scope_version_one(cmd_client, cmd_session) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])

    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion).where(
                EventResearchScopeVersion.research_case_id == case_id
            )
        )
    )

    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].changed_by == "tester"
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS


def test_scope_update_appends_v2_without_rewriting_v1(cmd_client, cmd_session) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    updated_factors = [
        INITIAL_FACTORS[0],
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": updated_factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "version": 2,
        "factors": updated_factors,
        "reclassified_evidence_count": 0,
        "unmapped_evidence_count": 0,
    }
    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assert [version.version for version in versions] == [1, 2]
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS
    assert _scope_statements(cmd_session, versions[1].id) == updated_factors


def test_scope_service_backfills_legacy_drafts_before_appending_an_update(cmd_session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="Legacy event",
        industry_topic="事件研究",
        created_by="legacy-author",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    cmd_session.add(
        EventResearchBrief(
            research_case_id=case.id,
            raw_input="legacy event input",
            source_url=None,
            event_title="Legacy event",
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            research_question="What explains the event?",
            extraction_state="human_confirmed",
            created_at=now,
        )
    )
    for position, statement in enumerate(INITIAL_FACTORS, start=1):
        cmd_session.add(
            EventResearchFactorDraft(
                research_case_id=case.id,
                statement=statement,
                position=position,
                created_by="legacy-author",
                created_at=now,
            )
        )
    cmd_session.commit()

    updated = EventResearchScopeService(cmd_session).update(
        case.id,
        [INITIAL_FACTORS[0], INITIAL_FACTORS[2], "New legacy scope factor"],
        "reviewer",
    )
    cmd_session.commit()

    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case.id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assert updated.version == 2
    assert [version.version for version in versions] == [1, 2]
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS


def test_scope_update_keeps_removed_factor_evidence_and_reports_mapping_counts(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    retained_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    removed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[1])

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                INITIAL_FACTORS[2],
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )

    assert response.status_code == 200
    assert response.json()["reclassified_evidence_count"] == 1
    assert response.json()["unmapped_evidence_count"] == 1
    assert cmd_session.get(EvidenceLink, retained_link.id) is not None
    assert cmd_session.get(EvidenceLink, removed_link.id) is not None
    assert cmd_session.get(EvidenceLink, removed_link.id).review_state == "reviewed"


def test_scope_updates_append_auditable_evidence_assignments_and_refresh_lifecycle(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    retained_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    removed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[1])

    second = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                INITIAL_FACTORS[2],
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    third = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[1],
                INITIAL_FACTORS[2],
                "广告业务增长弱于市场预期",
            ],
            "changed_by": "reviewer",
        },
    )

    assert second.status_code == 200
    assert third.status_code == 200
    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assignments = list(
        cmd_session.scalars(
            select(EventResearchScopeEvidenceAssignment)
            .where(
                EventResearchScopeEvidenceAssignment.scope_version_id.in_(
                    [versions[1].id, versions[2].id]
                )
            )
            .order_by(
                EventResearchScopeEvidenceAssignment.scope_version_id,
                EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
        )
    )
    by_scope = {
        scope_id: {
            assignment.evidence_link_id: (
                assignment.factor_statement,
                assignment.disposition,
            )
            for assignment in assignments
            if assignment.scope_version_id == scope_id
        }
        for scope_id in [versions[1].id, versions[2].id]
    }
    assert by_scope[versions[1].id] == {
        retained_link.id: (INITIAL_FACTORS[0], "mapped"),
        removed_link.id: (None, "unmapped"),
    }
    assert by_scope[versions[2].id] == {
        retained_link.id: (None, "unmapped"),
        removed_link.id: (INITIAL_FACTORS[1], "mapped"),
    }
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle.status == "researching"
    assert lifecycle.status_summary == "已更新因素，正在重新归类证据"
    assert lifecycle.current_gap == "已更新因素，正在重新归类证据"
    assert lifecycle.next_human_action is None


@pytest.mark.parametrize("paused_status", ["awaiting_scope", "exhausted"])
def test_scope_update_resumes_research_with_current_scope_factors_only(
    cmd_client, cmd_session, paused_status: str
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    initial_run_id = uuid.UUID(created["lifecycle"]["active_run_id"])
    reviewed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = paused_status
    lifecycle.current_gap = "需要调整研究范围"
    lifecycle.next_human_action = "补充来源或调整研究范围"
    cmd_session.commit()
    active_factors = [
        INITIAL_FACTORS[0],
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": active_factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 200
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle.status == "continuing"
    assert lifecycle.next_human_action is None
    assert lifecycle.active_run_id is not None
    assert lifecycle.active_run_id != initial_run_id
    successor = cmd_session.get(ResearchRun, lifecycle.active_run_id)
    assert successor is not None
    assert successor.research_case_id == case_id
    task_statements = set(
        cmd_session.scalars(
            select(Thesis.statement)
            .join(ResearchTask, ResearchTask.thesis_id == Thesis.id)
            .where(ResearchTask.run_id == successor.id)
        )
    )
    assert task_statements == set(active_factors)
    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
    assert workbench.status_code == 200
    assert [item["statement"] for item in workbench.json()["factors"]] == active_factors
    assert cmd_session.get(EvidenceLink, reviewed_link.id) is not None
    assert cmd_session.scalar(
        select(EventResearchConclusion.id).where(
            EventResearchConclusion.research_case_id == case_id
        )
    ) is None
    assert workbench.json()["conclusion"]["state"] == "cannot_conclude"


@pytest.mark.parametrize(
    "factors",
    [
        INITIAL_FACTORS[:2],
        INITIAL_FACTORS + ["第四项", "第五项", "第六项"],
        [INITIAL_FACTORS[0], "   ", INITIAL_FACTORS[2]],
        [INITIAL_FACTORS[0], f" {INITIAL_FACTORS[0]} ", INITIAL_FACTORS[2]],
    ],
)
def test_scope_update_rejects_invalid_factor_sets(cmd_client, factors: list[str]) -> None:
    created = _create_event(cmd_client)

    response = cmd_client.put(
        f"/api/v1/event-research/{created['case_id']}/scope",
        json={"factors": factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 422


def test_scope_update_rejects_changed_by_longer_than_128_characters(cmd_client) -> None:
    created = _create_event(cmd_client)

    response = cmd_client.put(
        f"/api/v1/event-research/{created['case_id']}/scope",
        json={"factors": INITIAL_FACTORS, "changed_by": "x" * 129},
    )

    assert response.status_code == 422


def test_scope_update_returns_existing_not_found_response_for_unknown_case(cmd_client) -> None:
    response = cmd_client.put(
        f"/api/v1/event-research/{uuid.uuid4()}/scope",
        json={"factors": INITIAL_FACTORS, "changed_by": "reviewer"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
