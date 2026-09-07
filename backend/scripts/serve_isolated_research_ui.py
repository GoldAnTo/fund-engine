"""Disposable loopback API for browser integration tests; never uses local data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5187)
    parser.add_argument("--preparation-worker", action="store_true", help="run the test-only preparation provider alongside the API")
    parser.add_argument("--run-controls-fixture", action="store_true", help="seed a queued research run and its history in the disposable database")
    args = parser.parse_args()
    os.environ["APP_ENV"] = "test"
    for name in tuple(os.environ):
        if name.startswith(("LLM_", "GILDATA_", "OPENAI_")):
            os.environ.pop(name)
    os.environ["RESEARCH_TENANT_TOKENS"] = json.dumps({"browser-integration-token": "browser-integration-team"})
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="fund-engine-browser-") as directory:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(directory) / 'research.db'}"
        import uvicorn
        from app.main import app
        from app.db import engine
        from app.models.ledger import Base
        Base.metadata.create_all(engine)
        if args.run_controls_fixture:
            from app.db import SessionLocal
            from app.schemas.v1.event_research import CreateEventResearchRequest
            from app.services.event_research import EventResearchService
            from app.services.auto_research import AutoResearchService
            from app.services.case_monitor import ResearchRunEventRepository
            with SessionLocal() as session:
                created = EventResearchService(session).create(
                    CreateEventResearchRequest(
                        event_title="运行操作浏览器验收",
                        raw_input="隔离测试材料：企业订单与收入的关系需要核验。",
                        research_question="订单如何影响收入？",
                        candidate_factors=["订单数量", "产品价格", "交付节奏"],
                        research_protocol_required=False,
                        created_by="browser-fixture",
                    ), tenant_id="browser-integration-team", workflow_mode="reviewed",
                )
                run = AutoResearchService(session).start(uuid.UUID(created.case_id), budget=10)
                events = ResearchRunEventRepository(session)
                for index in range(55):
                    events.append(run.id, stage="planning", status="queued",
                                  message=f"隔离运行历史 {index + 1}")
                # Test-only frozen source; no website or model is contacted.
                created = EventResearchService(session).create(
                    CreateEventResearchRequest(
                        event_title="证据审核浏览器验收",
                        raw_input="隔离审核研究：订单数量变化需要证据支持。",
                        research_question="订单变化是否有可靠证据？",
                        candidate_factors=["订单变化", "产品价格", "交付节奏"],
                        research_protocol_required=False, created_by="browser-fixture",
                    ), tenant_id="browser-integration-team", workflow_mode="reviewed",
                )
                from datetime import datetime, timezone
                from sqlalchemy import select
                from app.models.ledger import CaseDocumentVersion, SourceStatement, Thesis
                from app.models.proposals import Proposal
                from app.repositories.documents import DocumentRepository
                from app.services.ingest import DocumentService
                documents = DocumentService(DocumentRepository(session))
                source_text = "隔离验收原文：订单数量增加百分之十，后续收入仍需验证。"
                document = documents.freeze(raw=source_text.encode(),
                    source_url="https://investor.tsmc.com/english/quarterly-results/browser-fixture",
                    title="隔离证据审核来源", parser_version="html-v1", parse_state="success")
                span = documents.add_span(document_version_id=document.id,
                                          locator={"paragraph": 1}, verbatim_text=source_text)
                now = datetime.now(timezone.utc)
                session.add(CaseDocumentVersion(research_case_id=uuid.UUID(created.case_id),
                            document_version_id=document.id, linked_at=now))
                statement = SourceStatement(source_span_id=span.id, kind="fact",
                            normalized_text="隔离材料记录订单数量增长。", created_at=now)
                session.add(statement)
                session.flush()
                thesis = session.scalar(select(Thesis).where(Thesis.research_case_id == uuid.UUID(created.case_id)).order_by(Thesis.created_at, Thesis.id))
                session.add(Proposal(kind="evidence_link", research_case_id=uuid.UUID(created.case_id),
                    payload={"source_statement_id": str(statement.id), "role": "supports", "reason": "订单增长为因素提供待审核支持。", "scope": {"period": "隔离验收期间"}},
                    target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
                    proposed_by_type="ai", proposed_by_ref="browser-fixture", proposed_at=now, status="pending"))
                extraction_text = source_text + " 此资料用于候选抽取验收。"
                extraction_document = documents.freeze(raw=extraction_text.encode(),
                    source_url="https://investor.tsmc.com/english/quarterly-results/extraction-fixture",
                    title="隔离候选抽取来源", parser_version="html-v1", parse_state="success")
                documents.add_span(document_version_id=extraction_document.id,
                                   locator={"paragraph": 1}, verbatim_text=extraction_text)
                session.add(CaseDocumentVersion(research_case_id=uuid.UUID(created.case_id),
                            document_version_id=extraction_document.id, linked_at=now))
                monitor_case = EventResearchService(session).create(
                    CreateEventResearchRequest(
                        event_title="监控计划浏览器验收",
                        raw_input="隔离监控材料：等待下一次订单公告。",
                        research_question="订单趋势是否持续？",
                        candidate_factors=["订单趋势", "产品价格", "交付节奏"],
                        research_protocol_required=False, created_by="browser-fixture",
                    ), tenant_id="browser-integration-team", workflow_mode="reviewed",
                )
                factor = session.scalar(select(Thesis).where(
                    Thesis.research_case_id == uuid.UUID(monitor_case.case_id)))
                factor.review_state = "confirmed"
                session.flush()
                from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
                CaseMonitorService(session).save(uuid.UUID(monitor_case.case_id), actor="browser-fixture",
                    config=CaseMonitorConfig(frequency="daily_20_00", factor_ids=[factor.id],
                        allowed_source_types=["pasted_snapshot"], next_verification_event="下一次订单公告",
                        budget=20, change_reason="隔离浏览器验收计划"))
                unconfigured_case = EventResearchService(session).create(
                    CreateEventResearchRequest(
                        event_title="监控新建浏览器验收",
                        raw_input="隔离首次监控材料：订单与现金流需持续核验。",
                        research_question="订单增长能否转化为现金流？",
                        candidate_factors=["现金流转化", "订单增长", "履约节奏"],
                        research_protocol_required=False, created_by="browser-fixture",
                    ), tenant_id="browser-integration-team", workflow_mode="reviewed",
                )
                confirmed = session.scalar(select(Thesis).where(
                    Thesis.research_case_id == uuid.UUID(unconfigured_case.case_id),
                    Thesis.statement == "现金流转化"))
                confirmed.review_state = "confirmed"
                blocked_case = EventResearchService(session).create(
                    CreateEventResearchRequest(
                        event_title="协议阻断浏览器验收",
                        raw_input="隔离协议材料：结果指标、基线与验证窗口尚未确认。",
                        research_question="资本开支能否转化为利润？",
                        candidate_factors=["利润变化", "资本开支", "订单交付"],
                        research_protocol_required=True, created_by="browser-fixture",
                    ), tenant_id="browser-integration-team", workflow_mode="reviewed",
                )
                blocked_factor = session.scalar(select(Thesis).where(
                    Thesis.research_case_id == uuid.UUID(blocked_case.case_id)))
                blocked_factor.review_state = "confirmed"
                session.flush()
                CaseMonitorService(session).save(uuid.UUID(blocked_case.case_id), actor="browser-fixture",
                    config=CaseMonitorConfig(frequency="daily_20_00", factor_ids=[blocked_factor.id],
                        allowed_source_types=["pasted_snapshot"], next_verification_event="核对协议",
                        budget=10, change_reason="隔离协议阻断验收"))
                session.commit()
        worker = None
        try:
            if args.preparation_worker:
                worker = subprocess.Popen(
                    [sys.executable, "-m", "app.scripts.run_research_preparation_worker", "--loop", "--poll-seconds", "0.2"],
                    cwd=Path(__file__).resolve().parents[1],
                    env=dict(os.environ),
                )
            uvicorn.run(app, host="127.0.0.1", port=args.port)
        finally:
            if worker is not None:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
            engine.dispose()


if __name__ == "__main__":
    main()
