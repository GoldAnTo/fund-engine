"""Run the AI-compute evidence chain end-to-end and dump results.

This script:
1. Creates a fresh SQLite database
2. Seeds the AI-compute case (寒武纪/工业富联/SK海力士)
3. Drives the real v1 HTTP API via TestClient
4. Dumps: overview, per-thesis dossier, graph, fund-exposure, documents, search, KPIs
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./evidence_demo.db"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import Base
from app.scripts.seed_ai_compute_case import seed as seed_ai_compute
from app.db import get_db
from app.main import app
from fastapi.testclient import TestClient


def main():
    # 1. Fresh DB
    db_path = BACKEND / "evidence_demo.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_engine("sqlite:///./evidence_demo.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # 2. Seed
    with SessionLocal() as session:
        seed_ai_compute(session)
        session.commit()
    print("=== AI算力链案例种子化完成 ===\n")

    # 3. HTTP client
    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 4. Find the case
    resp = client.get("/api/v1/research-cases")
    cases = resp.json()
    case_id = cases["items"][0]["id"]
    print(f"案例ID: {case_id}")
    print(f"案例标题: {cases['items'][0]['title']}\n")

    # 5. Get all theses
    resp = client.get(f"/api/v1/research-cases/{case_id}/dossier?research_mode=true")
    master_dossier = resp.json()
    theses = master_dossier["theses"]
    thesis_ids = [t["id"] for t in theses]

    results = {}

    # 5a. Per-thesis dossier (full evidence chain)
    results["per_thesis"] = []
    for tid in thesis_ids:
        resp = client.get(f"/api/v1/research-cases/{case_id}/dossier?thesis_id={tid}&research_mode=true")
        results["per_thesis"].append(resp.json())

    # 5b. Overview
    resp = client.get(f"/api/v1/overview?case_id={case_id}")
    results["overview"] = resp.json()

    # 5c. Graph
    resp = client.get(f"/api/v1/research-cases/{case_id}/graph?research_mode=true&depth=8&limit=500")
    results["graph"] = resp.json()

    # 5d. Fund exposure
    resp = client.get(f"/api/v1/research-cases/{case_id}/fund-exposure")
    results["fund_exposure"] = resp.json()

    # 5e. Documents
    resp = client.get("/api/v1/documents?limit=100")
    results["documents"] = resp.json()

    # 5f. KPIs
    resp = client.get(f"/api/v1/research-ops/kpis?case_id={case_id}")
    results["kpis"] = resp.json()

    # 5g. Search
    resp = client.get("/api/v1/search?q=寒武纪&limit=20")
    results["search"] = resp.json()

    # 6. Dump
    output_path = BACKEND / "evidence_chain_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"完整结果已写入: {output_path}\n")

    # 7. Print full report
    print_report(results, case_id)


def print_report(r: dict, case_id: str):
    print("=" * 90)
    print("       AI算力链 · 证据驱动研究系统 · 完整证据链穿透报告")
    print("=" * 90)

    # Case info
    ov = r["overview"]
    case = ov.get("case", {})
    print(f"\n■ 研究案例")
    print(f"  ID:    {case_id}")
    print(f"  标题:  {case.get('title','AI 算力链')}")
    print(f"  主题:  {case.get('topic','ai_compute')}")
    basis = ov.get("basis", {})
    print(f"  截止:  {basis.get('cutoff','当前')} (historical={basis.get('is_historical',False)})")

    # ---- Per-thesis ----
    for i, dos in enumerate(r["per_thesis"]):
        thesis = dos["theses"][0] if dos.get("theses") else {}
        # Find the matching thesis from the master list
        master_theses = r["per_thesis"][i]["theses"] if i == 0 else None
        # Actually, each per_thesis dossier has all theses but focuses on one
        # The assessment is for the focus thesis
        assessment = dos.get("assessment")
        evidence = dos.get("evidence", {})
        causal_chain = dos.get("causal_chain", [])

        # Find the focus thesis from the theses list (focus_thesis_id)
        focus_id = dos.get("focus_thesis_id")
        focus_thesis = None
        for t in dos.get("theses", []):
            if t["id"] == focus_id:
                focus_thesis = t
                break

        print(f"\n{'='*90}")
        thesis_label = f"T{i+1}"
        print(f"■ 命题 {thesis_label}: {focus_thesis.get('statement','') if focus_thesis else 'N/A'}")
        print(f"{'='*90}")

        if focus_thesis:
            print(f"  创建者: {focus_thesis.get('creator_type','human')}")
            print(f"  审核状态: {focus_thesis.get('review_state','confirmed')}")

        # AI Assessment
        if assessment:
            print(f"\n  ┌─ AI判断（临时标记: {assessment.get('provisional', True)}）─────────────────────")
            print(f"  │ 结论: {assessment['conclusion']}")
            print(f"  │ 理由: {assessment.get('rationale','')}")
            if assessment.get("gaps"):
                print(f"  │ 证据缺口:")
                for g in assessment["gaps"]:
                    print(f"  │   • {g}")
            # Human review
            review = assessment.get("review")
            if review:
                print(f"  │")
                print(f"  │ ┌─ 人工复核（AI原始结论不被覆盖）──────────────────")
                print(f"  │ │ 决定: {review['outcome']}")
                print(f"  │ │ 结论: {review.get('conclusion','')}")
                print(f"  │ │ 理由: {review.get('reason','')}")
                print(f"  │ │ 审核人: {review.get('reviewer','')}")
                print(f"  │ └──────────────────────────────────────────────────")
            print(f"  └──────────────────────────────────────────────────────")

        # Evidence chain
        supports = evidence.get("supports", [])
        contradicts = evidence.get("contradicts", [])
        contextualizes = evidence.get("contextualizes", [])
        print(f"\n  ┌─ 证据链: 支持({len(supports)}) / 反驳({len(contradicts)}) / 背景({len(contextualizes)})")

        for ev_item in supports:
            _print_evidence(ev_item, "支持")
        for ev_item in contradicts:
            _print_evidence(ev_item, "反驳")
        for ev_item in contextualizes:
            _print_evidence(ev_item, "背景")
        print(f"  └──────────────────────────────────────────────────────")

        # Causal chain
        if causal_chain:
            print(f"\n  ┌─ 因果链（人工编写，已审核）")
            for step in sorted(causal_chain, key=lambda x: x.get("sequence", 0)):
                print(f"  │ {step['sequence']}. {step['description']}")
            print(f"  └──────────────────────────────────────────────────────")

    # ---- Fund exposure ----
    print(f"\n{'='*90}")
    print(f"■ 基金穿透: 研究案例 -> 主题股票 -> 基金持仓")
    print(f"{'='*90}")
    fe = r["fund_exposure"]
    for fund in fe.get("funds", []):
        print(f"\n  ● {fund['fund_name']} ({fund['fund_code']})")
        print(f"    主题暴露度: {fund['theme_exposure']:.1%}")
        for pos in fund.get("positions", []):
            pe = pos.get("pe_ttm")
            pb = pos.get("pb")
            pe_str = f"PE_TTM={pe}" if pe else "PE=N/A"
            pb_str = f"PB={pb}" if pb else ""
            print(f"    → {pos['stock_name']} ({pos['stock_code']}): "
                  f"权重 {float(pos['weight']):.2%} | {pe_str} {pb_str} | "
                  f"报告期={pos.get('report_period','')}")

    # ---- Documents ----
    print(f"\n{'='*90}")
    print(f"■ 冻结文档库")
    print(f"{'='*90}")
    docs = r["documents"]
    for d in docs.get("items", []):
        url = d.get("source_url", "")
        # Extract filename from URL
        fname = url.split("/")[-1] if url else "N/A"
        print(f"  • {fname}")
        print(f"    发布日: {d.get('published_at','')[:10]}  |  解析: {d.get('parse_state','')}  |  哈希: {d.get('content_sha256','')[:16]}...")

    # ---- Graph stats ----
    print(f"\n{'='*90}")
    print(f"■ 关系图谱统计")
    print(f"{'='*90}")
    graph = r["graph"]
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    print(f"  节点数: {len(nodes)}")
    print(f"  边数:   {len(edges)}")
    # Count by type
    node_types = {}
    for n in nodes:
        k = n.get("kind", "unknown")
        node_types[k] = node_types.get(k, 0) + 1
    print(f"  节点类型分布:")
    for k, v in sorted(node_types.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")

    # ---- KPIs ----
    print(f"\n{'='*90}")
    print(f"■ 研究效能 KPI")
    print(f"{'='*90}")
    kpi = r["kpis"]
    totals = kpi.get("totals", kpi)
    if isinstance(totals, dict):
        print(f"  证据总数: {totals.get('evidence_total','N/A')}")
        print(f"  待审核:   {totals.get('pending_review','N/A')}")
        print(f"  重大缺口: {totals.get('major_gaps','N/A')}")

    # ---- Search ----
    print(f"\n{'='*90}")
    print(f"■ 全局搜索: '寒武纪'")
    print(f"{'='*90}")
    search = r["search"]
    for group_name, hits in search.items():
        if isinstance(hits, list):
            print(f"  [{group_name}] {len(hits)} 条结果")
            for h in hits[:3]:
                if isinstance(h, dict):
                    print(f"    • {h.get('title','') or h.get('statement','')[:60]}")

    print(f"\n{'='*90}")
    print(f"       证据链穿透完成 - 每个结论可回溯到冻结原文")
    print(f"{'='*90}")


def _print_evidence(ev: dict, label: str):
    """Print a single evidence record with its full source chain."""
    print(f"  │")
    print(f"  │ ▶ [{label}] {ev.get('statement_text','')[:80]}")
    print(f"  │   陈述类型: {ev.get('statement_kind','')}")
    print(f"  │   观测期:   {ev.get('observed_period','')}")
    print(f"  │   原因:     {ev.get('reason','')[:80]}")
    print(f"  │   范围:     {json.dumps(ev.get('scope',{}), ensure_ascii=False)}")
    print(f"  │   审核状态: {ev.get('review_state','')}")
    # Source chain
    print(f"  │   ┌─ 原文溯源")
    print(f"  │   │ 定位: 第{ev.get('locator',{}).get('page','?')}页 第{ev.get('locator',{}).get('paragraph','?')}段")
    verbatim = ev.get('verbatim_text', '')
    # Show first 120 chars of verbatim
    if len(verbatim) > 120:
        verbatim = verbatim[:120] + "..."
    print(f"  │   │ 原文: \"{verbatim}\"")
    print(f"  │   └──────────────────────────────────")


if __name__ == "__main__":
    main()
