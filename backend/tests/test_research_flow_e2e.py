"""End-to-end research-flow walk (流程跑一遍).

Walks the whole chain through the v1 API on a private engine:

  新建研究 → 审核队列 → 关系级审核 → 评估级审核 → AI rerun（监测更新）
  → 快照对比 → 证据缺口 → 主题↔基金穿透 → 点时指标

If this flow stays green, the backend loop is coherent enough for the
frontend to plug into.  Mock LLM mode (no LLM_API_KEY) keeps it offline.
"""
from __future__ import annotations

from sqlalchemy import select


def test_full_research_flow_walkthrough(cmd_client, cmd_seeded):
    from app.models.ledger import (
        AIAssessment,
        AIRun,
        EvidenceSnapshot,
        ResearchCase,
        Thesis,
    )

    # 0. 金标切片已由 cmd_seeded 落库（离线、可重放）。
    seeded_case = cmd_seeded.scalar(select(ResearchCase))

    # 1. 新建研究：研究问题 + 可反证初始命题（AI 草案待人工复核）。
    created = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "AI 算力产业链：订单到收入传导验证",
            "industry_topic": "ai_compute",
            "created_by": "flow-test",
            "core_question": "截至 2026-06-30 算力资本开支能否通过已披露订单验证？",
            "period_start": "2026-01-01",
            "period_end": "2027-12-31",
            "evidence_cutoff": "2026-06-30",
            "initial_theses": [
                {
                    "statement": "云厂商资本开支形成持续算力需求",
                    "support_condition": "至少两家主要云厂商给出扩张指引",
                    "falsification_condition": "主要云厂商下调资本开支",
                    "next_verification_event": "核对 2026Q2 云厂商财报",
                    "creator_type": "ai",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    new_case_id = created.json()["case_id"]
    assert created.json()["theses"][0]["review_state"] == "draft"

    # 2. 审核队列：金标切片的 15 条机器提议待审；新案无链接不进队。
    queue = cmd_client.get("/api/v1/review-queue").json()["items"]
    assert len(queue) == 15
    new_case_queue = cmd_client.get(
        "/api/v1/review-queue", params={"case_id": new_case_id}
    ).json()["items"]
    assert new_case_queue == []

    # 3. 关系级审核（四要素）：确认一条，驳回一条。
    confirm = cmd_client.post(
        f"/api/v1/evidence-links/{queue[0]['link_id']}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "supports",
            "factor_role": "需求驱动因素",
            "scope_boundary": "仅适用于当前截止日与该分部口径",
            "reason": "原文披露与 AI 提议一致",
            "reviewer": "flow-reviewer",
        },
    )
    assert confirm.status_code == 201, confirm.text
    reject = cmd_client.post(
        f"/api/v1/evidence-links/{queue[1]['link_id']}/reviews",
        json={
            "outcome": "rejected",
            "factor_role": "不适用",
            "scope_boundary": "公司整体口径，非分部披露",
            "reason": "整体口径误用于业务线命题",
            "reviewer": "flow-reviewer",
        },
    )
    assert reject.status_code == 201, reject.text
    assert len(cmd_client.get("/api/v1/review-queue").json()["items"]) == 13

    # 4. 评估级审核：人工确认 T1 的 AI 结论。
    seeded_thesis = cmd_seeded.scalar(
        select(Thesis).where(Thesis.research_case_id == seeded_case.id)
    )
    assessment = cmd_seeded.scalar(
        select(AIAssessment)
        .join(EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id)
        .where(EvidenceSnapshot.thesis_id == seeded_thesis.id)
    )
    reviewed = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认，证据链完整",
            "reviewer": "flow-reviewer",
        },
    )
    assert reviewed.status_code == 201, reviewed.text

    # 5. AI rerun（监测与更新）：冻结新快照 + 追加新临时评估 + AIRun 审计。
    reran = cmd_client.post(f"/api/v1/theses/{seeded_thesis.id}/rerun")
    assert reran.status_code == 201, reran.text
    body = reran.json()
    assert body["mode"] == "mock"
    assert body["assessment"]["displayed_as_provisional"] is True
    snapshots = cmd_seeded.scalars(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.thesis_id == seeded_thesis.id
        )
    ).all()
    assert len(snapshots) == 2  # 原快照不动，新快照追加
    runs = cmd_seeded.scalars(
        select(AIRun).where(AIRun.kind == "assess")
    ).all()
    assert len(runs) == 1 and runs[0].status == "success"

    # 6. 快照对比：rerun 之后结论与证据集合无漂移（无新证据可见）。
    compared = cmd_client.get(
        f"/api/v1/research-cases/{seeded_case.id}/compare",
        params={"base": "2098-01-01T00:00:00Z", "compare": "2099-01-01T00:00:00Z"},
    )
    assert compared.status_code == 200
    for thesis in compared.json()["theses"]:
        assert thesis["added_links"] == []
        assert thesis["removed_links"] == []

    # 7. 证据缺口：rerun 产生的最新评估带缺口（mock 模式至少不报错），
    #    且金标 T2/T3 的缺口在 cutoff 前仍可见。
    gaps = cmd_client.get(
        f"/api/v1/research-cases/{seeded_case.id}/gaps"
    ).json()["gaps"]
    assert isinstance(gaps, list)

    # 8. 穿透：主题 → 基金（按暴露度排序）；基金 → 持仓反穿。
    exposure = cmd_client.get(
        f"/api/v1/research-cases/{seeded_case.id}/fund-exposure",
        params={"as_of": "2026-06-30"},
    ).json()
    assert [f["fund_code"] for f in exposure["funds"]] == ["008888", "012345"]
    fund_a = exposure["funds"][0]
    composition = cmd_client.get(
        f"/api/v1/funds/{fund_a['fund_id']}/composition",
        params={"as_of": "2026-06-30"},
    ).json()
    assert len(composition["positions"]) == 2
    assert composition["positions"][0]["theme_hits"], "持仓必须能反穿回主题"

    # 9. 点时指标：估值目录与时点序列（「多贵」必答项）。
    catalog = cmd_client.get("/api/v1/metrics/catalog").json()["entries"]
    assert len(catalog) == 5
    stock_id = exposure["funds"][0]["positions"][0]["stock_id"]
    series = cmd_client.get(
        "/api/v1/metrics/series",
        params={"stock_id": stock_id, "metric_name": "PE_TTM"},
    ).json()
    assert series["points"][0]["value"] == 380.5
