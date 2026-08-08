"""Read contracts for the source-safe report Wiki graph."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select

from app.models.ledger import Company, DocumentVersion, SourceSpan, SourceStatement, Stock
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportConfounderAssessment,
    ReportFundExposure,
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportRelation,
    ReportResearchScopeVersion,
)
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.services.ingest import DocumentService
from app.services.research import ResearchService
from app.services.report_research import ReportResearchService


def _create_report(cmd_client, *, title: str, content: str) -> uuid.UUID:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": title,
            "publisher": "测试券商",
            "published_at": "2026-08-01T08:00:00Z",
            "content": content,
        },
    )
    assert created.status_code == 201
    return uuid.UUID(created.json()["case"]["id"])


def _append_report_claim(
    cmd_session,
    *,
    case_id: uuid.UUID,
    statement: str,
    source_url: str,
    subject_company_id: uuid.UUID | None = None,
    object_company_id: uuid.UUID | None = None,
    create_scope: bool = False,
):
    documents = DocumentService(DocumentRepository(cmd_session))
    research = ResearchService(ResearchRepository(cmd_session))
    document = documents.freeze(
        raw=statement.encode("utf-8"),
        source_url=source_url,
        title="研报修订版",
        published_at=datetime(2026, 8, 2, 8, tzinfo=timezone.utc),
    )
    documents.attach_to_case(research_case_id=case_id, document_version_id=document.id)
    span = documents.add_span(
        document_version_id=document.id,
        locator={"page": 2, "paragraph": 1},
        verbatim_text=statement,
    )
    source_statement = research.add_statement(
        span.id, statement, kind="research_opinion"
    )
    cmd_session.add(
        ReportCaseSourceSpan(
            research_case_id=case_id,
            document_version_id=document.id,
            source_span_id=span.id,
        )
    )
    cmd_session.flush()
    claim = ReportClaim(
        research_case_id=case_id,
        source_span_id=span.id,
        source_statement_id=source_statement.id,
        kind="report_opinion",
        statement=statement,
    )
    cmd_session.add(claim)
    cmd_session.flush()
    if subject_company_id is not None and object_company_id is not None:
        cmd_session.add(
            ReportRelation(
                claim_id=claim.id,
                research_case_id=case_id,
                source_span_id=span.id,
                source_statement_id=source_statement.id,
                subject_company_id=subject_company_id,
                object_company_id=object_company_id,
                subject_name=None,
                object_name=None,
                relation_kind="supplier",
                mechanism="研报明确供应商关系",
                status="report_claim",
            )
        )
        cmd_session.flush()
    if create_scope:
        relation_ids = list(
            cmd_session.scalars(
                select(ReportRelation.id).where(ReportRelation.claim_id == claim.id)
            )
        )
        ReportResearchService(cmd_session).append_scope(
            case_id,
            document.id,
            changed_by="tester",
            change_summary="切换至修订研报",
            selected_claim_ids=[claim.id],
            selected_relation_ids=relation_ids,
        )
    return document, span, source_statement, claim


def _append_independent_statement(
    cmd_session,
    *,
    case_id: uuid.UUID,
    text: str,
    source_url: str | None = None,
    kind: str = "disclosed_fact",
):
    documents = DocumentService(DocumentRepository(cmd_session))
    research = ResearchService(ResearchRepository(cmd_session))
    document = documents.freeze(
        raw=text.encode("utf-8"),
        source_url=source_url or f"https://disclosure.cninfo.com.cn/{uuid.uuid4()}",
        title="公司公告",
        published_at=datetime(2026, 8, 2, 9, tzinfo=timezone.utc),
    )
    documents.attach_to_case(research_case_id=case_id, document_version_id=document.id)
    span = documents.add_span(
        document_version_id=document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text=text,
    )
    return research.add_statement(span.id, text, kind=kind)


def _append_claim_to_report_document(
    cmd_session,
    *,
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    statement: str,
    subject_name: str,
    object_name: str,
):
    """Add a second extracted path to the same immutable report revision."""
    documents = DocumentService(DocumentRepository(cmd_session))
    research = ResearchService(ResearchRepository(cmd_session))
    span = documents.add_span(
        document_version_id=document_id,
        locator={"page": 2, "paragraph": 1},
        verbatim_text=statement,
    )
    source_statement = research.add_statement(
        span.id, statement, kind="research_opinion"
    )
    cmd_session.add(
        ReportCaseSourceSpan(
            research_case_id=case_id,
            document_version_id=document_id,
            source_span_id=span.id,
        )
    )
    cmd_session.flush()
    claim = ReportClaim(
        research_case_id=case_id,
        source_span_id=span.id,
        source_statement_id=source_statement.id,
        kind="report_opinion",
        statement=statement,
    )
    cmd_session.add(claim)
    cmd_session.flush()
    relation = ReportRelation(
        claim_id=claim.id,
        research_case_id=case_id,
        source_span_id=span.id,
        source_statement_id=source_statement.id,
        subject_company_id=None,
        object_company_id=None,
        subject_name=subject_name,
        object_name=object_name,
        relation_kind="supplier",
        mechanism="研报明确供应商关系",
        status="report_claim",
    )
    cmd_session.add(relation)
    cmd_session.flush()
    return claim, relation


def test_wiki_graph_returns_only_the_current_report_scope_and_source_locators(
    cmd_client,
) -> None:
    case_id = _create_report(
        cmd_client,
        title="供应链关系图",
        content="研报观点：未上市供应商甲是星海科技的供应商。",
    )

    response = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert response.status_code == 200
    graph = response.json()
    assert graph["scope_version"] == 1
    assert any(node["kind"] == "report_claim" for node in graph["nodes"])
    assert any(node["kind"] == "company" for node in graph["nodes"])
    assert all(node["source_locator"] for node in graph["nodes"] if node["kind"] == "report_claim")
    assert graph["edges"]
    assert all(edge["scope_version"] == graph["scope_version"] for edge in graph["edges"])
    assert all(edge["source_locator"] for edge in graph["edges"])


def test_report_factor_without_independent_verification_is_an_evidence_gap(
    cmd_client,
) -> None:
    case_id = _create_report(
        cmd_client,
        title="未验证观点",
        content="研报观点：未上市供应商甲是星海科技的供应商。",
    )

    response = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert response.status_code == 200
    factor = response.json()["factors"][0]
    assert factor["classification"] == "evidence_gap"
    assert factor["components"]["report_source"] is True
    assert factor["components"]["company_relation"] is False
    assert factor["components"]["operating"] is False
    assert factor["components"]["market"] is False
    assert factor["components"]["peer"] is False
    assert factor["components"]["confounder"] is False


def test_wiki_defaults_to_latest_report_document_but_can_read_one_history_scope(
    cmd_client, cmd_session
) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "版本研报",
            "content": "研报观点：旧版订单判断。",
        },
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case"]["id"])
    old_document_id = uuid.UUID(created.json()["document"]["id"])
    initial_scope = cmd_session.scalar(
        select(ReportResearchScopeVersion).where(
            ReportResearchScopeVersion.research_case_id == case_id
        )
    )
    assert initial_scope is not None
    assert initial_scope.version == 1
    assert initial_scope.document_version_id == old_document_id
    new_document, _span, _statement, _claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：新版订单判断。",
        source_url="report://fixture/version-revision",
        create_scope=True,
    )
    cmd_session.commit()

    current = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    history = cmd_client.get(
        f"/api/v1/report-research/{case_id}/wiki",
        params={"scope_version": 1},
    )

    assert current.status_code == history.status_code == 200
    assert current.json()["scope_version"] == 2
    assert "新版订单判断" in current.text
    assert "旧版订单判断" not in current.text
    assert history.json()["scope_version"] == 1
    assert "旧版订单判断" in history.text
    assert "新版订单判断" not in history.text


def test_same_report_document_can_have_multiple_question_scopes(cmd_client, cmd_session) -> None:
    created = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": "同一研报多问题",
            "content": "研报观点：订单增长。",
        },
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case"]["id"])
    document_id = uuid.UUID(created.json()["document"]["id"])
    claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == case_id)
    )
    assert claim is not None

    scope = ReportResearchService(cmd_session).append_scope(
        case_id,
        document_id,
        changed_by="tester",
        change_summary="改为验证订单增长能否传导至基金持仓",
        research_question="订单增长是否会传导至中国基金？",
        factor_selection=["订单", "基金暴露"],
        evidence_plan=["公司公告", "基金持仓", "发布后市场窗口"],
        selected_claim_ids=[claim.id],
        selected_relation_ids=[],
    )
    cmd_session.commit()

    current = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    history = cmd_client.get(
        f"/api/v1/report-research/{case_id}/wiki", params={"scope_version": 1}
    )

    assert scope.version == 2
    assert current.status_code == history.status_code == 200
    assert current.json()["scope_version"] == 2
    assert current.json()["scope"]["research_question"] == "订单增长是否会传导至中国基金？"
    assert current.json()["scope"]["factor_selection"] == ["订单", "基金暴露"]
    assert history.json()["scope_version"] == 1
    assert history.json()["scope"]["research_question"] != current.json()["scope"]["research_question"]


def test_same_document_scopes_freeze_distinct_claim_and_relation_paths(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="同一研报不同路径",
        content="研报观点：供应商甲是星海科技的供应商。",
    )
    document_id = cmd_session.scalar(
        select(ReportCaseSourceSpan.document_version_id)
        .where(ReportCaseSourceSpan.research_case_id == case_id)
        .limit(1)
    )
    first_claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == case_id)
    )
    assert document_id is not None and first_claim is not None
    first_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == first_claim.id)
    )
    assert first_relation is not None
    second_claim, second_relation = _append_claim_to_report_document(
        cmd_session,
        case_id=case_id,
        document_id=document_id,
        statement="研报观点：供应商乙是星海科技的供应商。",
        subject_name="供应商乙",
        object_name="星海科技",
    )
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document_id,
        changed_by="tester",
        change_summary="改为验证供应商乙路径",
        selected_claim_ids=[second_claim.id],
        selected_relation_ids=[second_relation.id],
    )
    cmd_session.commit()

    current = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    history = cmd_client.get(
        f"/api/v1/report-research/{case_id}/wiki", params={"scope_version": 1}
    )

    assert current.status_code == history.status_code == 200
    assert current.json()["scope"]["selected_claim_ids"] == [str(second_claim.id)]
    assert current.json()["scope"]["selected_relation_ids"] == [str(second_relation.id)]
    assert [row["relation_id"] for row in current.json()["factors"]] == [
        str(second_relation.id)
    ]
    assert [row["relation_id"] for row in history.json()["factors"]] == [
        str(first_relation.id)
    ]
    assert "供应商乙" in current.text
    assert "供应商甲" not in current.text
    assert "供应商甲" in history.text
    assert "供应商乙" not in history.text


def test_scope_selection_rejects_foreign_claim_before_appending_scope(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="范围归属校验",
        content="研报观点：供应商甲是星海科技的供应商。",
    )
    other_case_id = _create_report(
        cmd_client,
        title="其他研报",
        content="研报观点：供应商乙是远海科技的供应商。",
    )
    document_id = cmd_session.scalar(
        select(ReportCaseSourceSpan.document_version_id)
        .where(ReportCaseSourceSpan.research_case_id == case_id)
        .limit(1)
    )
    foreign_claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == other_case_id)
    )
    assert document_id is not None and foreign_claim is not None
    version_count = len(
        list(
            cmd_session.scalars(
                select(ReportResearchScopeVersion).where(
                    ReportResearchScopeVersion.research_case_id == case_id
                )
            )
        )
    )

    with pytest.raises(ValueError, match="selected claims must belong"):
        ReportResearchService(cmd_session).append_scope(
            case_id,
            document_id,
            changed_by="tester",
            change_summary="错误地选择了其他案例的观点",
            selected_claim_ids=[foreign_claim.id],
            selected_relation_ids=[],
        )

    assert len(
        list(
            cmd_session.scalars(
                select(ReportResearchScopeVersion).where(
                    ReportResearchScopeVersion.research_case_id == case_id
                )
            )
        )
    ) == version_count


def test_factor_becomes_key_only_with_independent_relation_operating_market_peer_and_confounder_evidence(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="关键因素门槛",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    supplier = Company(
        code="REPORT-SUP", name="供应商甲", type="listed", created_at=now
    )
    issuer = Company(
        code="REPORT-ISSUER", name="星海科技", type="listed", created_at=now
    )
    cmd_session.add_all((supplier, issuer))
    cmd_session.flush()
    document, _span, _source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：供应商甲是星海科技的供应商。",
        source_url="report://fixture/factor-gate",
        subject_company_id=supplier.id,
        object_company_id=issuer.id,
        create_scope=False,
    )
    independent = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="公告披露：供应商甲是星海科技的供应商，订单和收入增长。",
    )
    relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert relation is not None
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document.id,
        changed_by="tester",
        change_summary="以当时已可见公告验证供应链路径",
        selected_claim_ids=[claim.id],
        selected_relation_ids=[relation.id],
    )
    confounder = ReportMarketConfounder(
        research_case_id=case_id,
        report_claim_id=claim.id,
        source_statement_id=independent.id,
        window="1d",
        kind="announcement",
        as_of_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
        summary="同期公告已纳入混杂因素评估",
        collection_key="wiki-confounder",
    )
    cmd_session.add_all(
        (
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="target_market",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="EVENT_RETURN_1D",
                summary="目标股票发布后反应已验证",
                collection_key="wiki-target-market",
            ),
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="peer_control",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="PEER_RETURN_1D",
                summary="同业控制已验证",
                collection_key="wiki-peer-control",
            ),
            confounder,
        )
    )
    cmd_session.flush()
    unresolved = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    assert unresolved.status_code == 200
    unresolved_factor = next(
        row
        for row in unresolved.json()["factors"]
        if row["relation_id"] == str(relation.id)
    )
    assert unresolved_factor["classification"] == "evidence_gap"
    assert unresolved_factor["components"]["confounder"] is False
    cmd_session.add(
        ReportConfounderAssessment(
            research_case_id=case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id,
            report_confounder_id=confounder.id,
            outcome="not_material",
            rationale="该公告不涉及供应商订单链。",
        )
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    factor = next(row for row in graph.json()["factors"] if row["claim_id"] == str(claim.id))
    assert factor["classification"] == "key"
    assert all(factor["components"].values())

    # A later immutable assessment can surface the competing explanation; it
    # must displace the projection without rewriting the prior assessment.
    cmd_session.add(
        ReportConfounderAssessment(
            research_case_id=case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id,
            report_confounder_id=confounder.id,
            outcome="material",
            rationale="复核发现该公告足以独立解释价格反应。",
        )
    )
    cmd_session.commit()
    alternative = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    alternative_factor = next(
        row
        for row in alternative.json()["factors"]
        if row["relation_id"] == str(relation.id)
    )
    assert alternative_factor["classification"] == "alternative"


def test_unadmitted_confounder_cannot_satisfy_factor_gate_or_leak_into_wiki(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="混杂来源必须准入",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    supplier = Company(code="CONF-SUP", name="混杂供应商", type="listed", created_at=now)
    issuer = Company(code="CONF-ISSUER", name="混杂发行人", type="listed", created_at=now)
    cmd_session.add_all((supplier, issuer))
    cmd_session.flush()
    document, _span, _source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：混杂供应商是混杂发行人的供应商。",
        source_url="report://fixture/confounder-admission",
        subject_company_id=supplier.id,
        object_company_id=issuer.id,
    )
    relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert relation is not None
    later_statement = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="公告披露：混杂供应商是混杂发行人的供应商，订单和收入增长。",
    )
    rejected_source = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="不可准入混杂消息：同期消息。",
        source_url="https://example.test/unadmitted-confounder",
    )
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document.id,
        changed_by="tester",
        change_summary="验证来源准入后的混杂路径",
        selected_claim_ids=[claim.id],
        selected_relation_ids=[relation.id],
    )
    confounder = ReportMarketConfounder(
        research_case_id=case_id,
        report_claim_id=claim.id,
        source_statement_id=rejected_source.id,
        window="1d",
        kind="announcement",
        as_of_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
        summary="不可准入混杂消息",
        collection_key="unadmitted-confounder",
    )
    cmd_session.add_all(
        (
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="target_market",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="EVENT_RETURN_1D",
                summary="目标市场已验证",
                collection_key="unadmitted-confounder-market",
            ),
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="peer_control",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="PEER_RETURN_1D",
                summary="同业控制已验证",
                collection_key="unadmitted-confounder-peer",
            ),
            confounder,
        )
    )
    cmd_session.flush()
    cmd_session.add(
        ReportConfounderAssessment(
            research_case_id=case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id,
            report_confounder_id=confounder.id,
            outcome="not_material",
            rationale="即使写入评估，也不能绕过来源准入。",
        )
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    factor = next(row for row in graph.json()["factors"] if row["relation_id"] == str(relation.id))
    assert factor["classification"] == "evidence_gap"
    assert factor["components"]["confounder"] is False
    assert "不可准入混杂消息" not in graph.text


def test_later_disclosure_cannot_upgrade_an_older_scope_to_causal_key(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="旧范围不能使用后验材料",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    supplier = Company(code="ASOF-SUP", name="时点供应商", type="listed", created_at=now)
    issuer = Company(code="ASOF-ISSUER", name="时点发行人", type="listed", created_at=now)
    cmd_session.add_all((supplier, issuer))
    cmd_session.flush()
    document, _span, _source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：时点供应商是时点发行人的供应商。",
        source_url="report://fixture/visibility-cutoff",
        subject_company_id=supplier.id,
        object_company_id=issuer.id,
    )
    relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert relation is not None
    confounder_source = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="公告披露：同期没有改变市场判断的事项。",
    )
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document.id,
        changed_by="tester",
        change_summary="冻结初始可见证据范围",
        selected_claim_ids=[claim.id],
        selected_relation_ids=[relation.id],
    )
    later_statement = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="公告披露：时点供应商是时点发行人的供应商，订单和收入增长。",
        source_url="https://issuer.example.com/later-disclosure",
    )
    # Freeze the later document's visibility timestamp after the scope has
    # already been appended; otherwise a single final flush assigns both
    # append-only defaults at once and erases the test's temporal ordering.
    cmd_session.flush()
    scope = cmd_session.scalar(
        select(ReportResearchScopeVersion)
        .where(ReportResearchScopeVersion.research_case_id == case_id)
        .order_by(ReportResearchScopeVersion.version.desc())
        .limit(1)
    )
    later_span = cmd_session.get(SourceSpan, later_statement.source_span_id)
    assert scope is not None and later_span is not None
    later_document = cmd_session.get(DocumentVersion, later_span.document_version_id)
    assert later_document is not None
    assert later_document.available_at > scope.created_at
    confounder = ReportMarketConfounder(
        research_case_id=case_id,
        report_claim_id=claim.id,
        source_statement_id=confounder_source.id,
        window="1d",
        kind="announcement",
        as_of_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
        summary="同期事项已评估",
        collection_key="old-scope-confounder",
    )
    cmd_session.add_all(
        (
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="target_market",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="EVENT_RETURN_1D",
                summary="目标市场已验证",
                collection_key="old-scope-market",
            ),
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="peer_control",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="PEER_RETURN_1D",
                summary="同业控制已验证",
                collection_key="old-scope-peer",
            ),
            confounder,
        )
    )
    cmd_session.flush()
    cmd_session.add(
        ReportConfounderAssessment(
            research_case_id=case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id,
            report_confounder_id=confounder.id,
            outcome="not_material",
            rationale="没有实质替代解释。",
        )
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    factor = next(row for row in graph.json()["factors"] if row["relation_id"] == str(relation.id))
    assert factor["classification"] == "evidence_gap"
    assert factor["components"]["company_relation"] is False
    assert factor["components"]["operating"] is False
    assert "订单和收入增长" not in graph.text


def test_wiki_bulk_loads_confounder_sources(cmd_client, cmd_session) -> None:
    case_id = _create_report(
        cmd_client,
        title="批量混杂因素",
        content="研报观点：订单增长。",
    )
    claim = cmd_session.scalar(
        select(ReportClaim).where(ReportClaim.research_case_id == case_id)
    )
    assert claim is not None
    independent = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="公告披露：订单增长。",
    )
    for number in range(5):
        cmd_session.add(
            ReportMarketConfounder(
                research_case_id=case_id,
                report_claim_id=claim.id,
                source_statement_id=independent.id,
                window="1d",
                kind="announcement",
                as_of_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
                summary=f"同期公告 {number}",
                collection_key=f"wiki-bulk-confounder-{number}",
            )
        )
    cmd_session.commit()

    source_statement_queries: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "source_statements" in statement.lower():
            source_statement_queries.append(statement)

    event.listen(cmd_session.bind, "before_cursor_execute", capture)
    try:
        response = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", capture)

    assert response.status_code == 200
    # claim source + independent evidence + all confounder locators: the five
    # confounders must not become five more database reads.
    assert len(source_statement_queries) <= 3


def test_report_or_note_cannot_count_as_independent_relation_evidence(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="二手观点不准入",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    supplier = Company(code="NOTE-SUP", name="供应商乙", type="listed", created_at=now)
    issuer = Company(code="NOTE-ISSUER", name="海岳科技", type="listed", created_at=now)
    cmd_session.add_all((supplier, issuer))
    cmd_session.flush()
    _doc, _span, _source, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：供应商乙是海岳科技的供应商。",
        source_url="report://fixture/note-gate",
        subject_company_id=supplier.id,
        object_company_id=issuer.id,
        create_scope=True,
    )
    _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text="研究笔记：供应商乙是海岳科技的供应商，订单增长。",
        source_url="https://broker.example.test/note",
        kind="research_opinion",
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    factor = next(row for row in graph.json()["factors"] if row["claim_id"] == str(claim.id))
    assert factor["classification"] == "evidence_gap"
    assert factor["components"]["company_relation"] is False
    assert factor["components"]["operating"] is False


def test_factor_gate_never_unions_market_and_peer_evidence_across_relations(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="关系链不能拼接",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    companies = [
        Company(code=f"CHAIN-{index}", name=name, type="listed", created_at=now)
        for index, name in enumerate(("供应商一", "目标一", "供应商二", "目标二"), start=1)
    ]
    cmd_session.add_all(companies)
    cmd_session.flush()
    _doc, span, source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：供应商一和供应商二均受订单拉动。",
        source_url="report://fixture/per-relation-gate",
        subject_company_id=companies[0].id,
        object_company_id=companies[1].id,
        create_scope=True,
    )
    first_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert first_relation is not None
    second_relation = ReportRelation(
        claim_id=claim.id,
        research_case_id=case_id,
        source_span_id=span.id,
        source_statement_id=source_statement.id,
        subject_company_id=companies[2].id,
        object_company_id=companies[3].id,
        subject_name=None,
        object_name=None,
        relation_kind="supplier",
        mechanism="研报明确供应商关系",
        status="report_claim",
    )
    cmd_session.add(second_relation)
    cmd_session.flush()
    ReportResearchService(cmd_session).append_scope(
        case_id,
        _doc.id,
        changed_by="tester",
        change_summary="扩展为双供应商路径核验",
        selected_claim_ids=[claim.id],
        selected_relation_ids=[first_relation.id, second_relation.id],
    )
    independent = _append_independent_statement(
        cmd_session,
        case_id=case_id,
        text=(
            "公告披露：供应商一是目标一的供应商，订单增长；"
            "供应商二是目标二的供应商，订单增长。"
        ),
    )
    confounder = ReportMarketConfounder(
        research_case_id=case_id,
        report_claim_id=claim.id,
        source_statement_id=independent.id,
        window="1d",
        kind="announcement",
        as_of_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
        summary="同期公告已评估",
        collection_key="per-relation-confounder",
    )
    cmd_session.add_all(
        (
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=first_relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="target_market",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="EVENT_RETURN_1D",
                summary="仅关系一有目标市场观察",
                collection_key="per-relation-target",
            ),
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=second_relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="peer_control",
                status="verified",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name="PEER_RETURN_1D",
                summary="仅关系二有同业控制",
                collection_key="per-relation-peer",
            ),
            confounder,
        )
    )
    cmd_session.flush()
    cmd_session.add_all(
        (
            ReportConfounderAssessment(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=first_relation.id,
                report_confounder_id=confounder.id,
                outcome="not_material",
                rationale="不改变关系一订单判断。",
            ),
            ReportConfounderAssessment(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=second_relation.id,
                report_confounder_id=confounder.id,
                outcome="not_material",
                rationale="不改变关系二订单判断。",
            ),
        )
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    factors = {
        row["relation_id"]: row
        for row in graph.json()["factors"]
        if row["claim_id"] == str(claim.id)
    }
    assert factors[str(first_relation.id)]["classification"] == "evidence_gap"
    assert factors[str(first_relation.id)]["components"]["market"] is True
    assert factors[str(first_relation.id)]["components"]["peer"] is False
    assert factors[str(second_relation.id)]["classification"] == "evidence_gap"
    assert factors[str(second_relation.id)]["components"]["market"] is False
    assert factors[str(second_relation.id)]["components"]["peer"] is True

    focused = cmd_client.get(
        f"/api/v1/report-research/{case_id}/wiki",
        params={"relation_id": str(first_relation.id)},
    )
    assert focused.status_code == 200
    focused_body = focused.json()
    assert [row["relation_id"] for row in focused_body["factors"]] == [
        str(first_relation.id)
    ]
    assert all(
        edge["kind"] == "reported_by"
        or edge["relation_id"] == str(first_relation.id)
        for edge in focused_body["edges"]
    )
    assert any(edge["kind"] == "target_market" for edge in focused_body["edges"])
    assert not any(edge["kind"] == "peer_control" for edge in focused_body["edges"])


def test_scope_hides_unselected_relation_market_and_fund_edges_but_keeps_claim_gap(
    cmd_client, cmd_session
) -> None:
    case_id = _create_report(
        cmd_client,
        title="范围不泄露未选路径证据",
        content="研报观点：占位关系。",
    )
    now = datetime.now(timezone.utc)
    companies = [
        Company(code=f"FILTER-{index}", name=name, type="listed", created_at=now)
        for index, name in enumerate(("供应商甲", "目标甲", "供应商乙", "目标乙"), start=1)
    ]
    cmd_session.add_all(companies)
    cmd_session.flush()
    document, span, source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：供应商甲和供应商乙均受订单拉动。",
        source_url="report://fixture/scope-edge-filter",
        subject_company_id=companies[0].id,
        object_company_id=companies[1].id,
    )
    first_relation = cmd_session.scalar(
        select(ReportRelation).where(ReportRelation.claim_id == claim.id)
    )
    assert first_relation is not None
    second_relation = ReportRelation(
        claim_id=claim.id,
        research_case_id=case_id,
        source_span_id=span.id,
        source_statement_id=source_statement.id,
        subject_company_id=companies[2].id,
        object_company_id=companies[3].id,
        subject_name=None,
        object_name=None,
        relation_kind="supplier",
        mechanism="研报明确供应商关系",
        status="report_claim",
    )
    selected_stock = Stock(
        company_id=companies[2].id,
        code="600099",
        name="路径乙股票",
        market="CN",
        created_at=now,
    )
    cmd_session.add_all((second_relation, selected_stock))
    cmd_session.flush()
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document.id,
        changed_by="tester",
        change_summary="只研究供应商甲路径",
        selected_claim_ids=[claim.id],
        selected_relation_ids=[first_relation.id],
    )
    cmd_session.add_all(
        (
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=second_relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="1d",
                kind="target_market",
                status="insufficient",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                metric_name=None,
                summary="仅路径乙的市场窗口",
                collection_key="scope-filter-relation-b-market",
            ),
            ReportFundExposure(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=second_relation.id,
                stock_id=selected_stock.id,
                fund_id=None,
                holding_disclosure_id=None,
                window="1d",
                as_of_date=datetime(2026, 8, 3, tzinfo=timezone.utc).date(),
                status="insufficient",
                weight=None,
                summary="仅路径乙的基金暴露",
                collection_key="scope-filter-relation-b-fund",
            ),
            # A NULL relation_id is an actual claim-level collection gap, not
            # evidence borrowed from an unselected relationship path.
            ReportMarketObservation(
                research_case_id=case_id,
                report_claim_id=claim.id,
                report_relation_id=None,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window="5d",
                kind="target_market",
                status="insufficient",
                as_of_date=datetime(2026, 8, 7, tzinfo=timezone.utc).date(),
                metric_name=None,
                summary="观点级市场数据不足",
                collection_key="scope-filter-claim-gap",
            ),
        )
    )
    cmd_session.commit()

    graph = cmd_client.get(f"/api/v1/report-research/{case_id}/wiki")

    assert graph.status_code == 200
    body = graph.json()
    assert not any(
        edge["relation_id"] == str(second_relation.id) for edge in body["edges"]
    )
    assert "仅路径乙的市场窗口" not in graph.text
    assert "仅路径乙的基金暴露" not in graph.text
    assert any(
        edge["kind"] == "target_market" and edge["relation_id"] is None
        for edge in body["edges"]
    )
    assert "观点级市场数据不足" in graph.text
