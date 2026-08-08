"""Read contracts for the source-safe report Wiki graph."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import event, select

from app.models.ledger import Company, SourceSpan, SourceStatement
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportConfounderAssessment,
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
        ReportResearchService(cmd_session).append_scope(
            case_id,
            document.id,
            changed_by="tester",
            change_summary="切换至修订研报",
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
    _document, _span, _source_statement, claim = _append_report_claim(
        cmd_session,
        case_id=case_id,
        statement="研报观点：供应商甲是星海科技的供应商。",
        source_url="report://fixture/factor-gate",
        subject_company_id=supplier.id,
        object_company_id=issuer.id,
        create_scope=True,
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
