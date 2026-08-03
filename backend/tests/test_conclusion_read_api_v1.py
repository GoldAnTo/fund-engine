"""「结论与关键因素」页面 v1 read API.

对应 prototype/设计原型11-结论与关键因素.png。

验证:
  - 端点响应 schema_version + basis 完整
  - header 包含 AI 临时标记 + 人工复核并列
  - key_factors 数量 = thesis 数量
  - comparison.columns 8 列
  - causal_path / source_groups 来自账本
  - cutoff 控制时点可回放（设计 10）
"""
import uuid
from datetime import UTC, datetime

import pytest

from app.models.ledger import (
    AIAssessment,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)


def _seed_minimal_case(session, sha256_suffix):
    """最小账本：1 case + 3 theses + 1 evidence + 1 assessment + 1 review."""
    case_id = uuid.uuid4()
    thesis_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    case = ResearchCase(
        id=case_id,
        title="结论测试案例",
        industry_topic="ai_compute",
        created_by="t",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    session.add(case)
    session.flush()

    for i, tid in enumerate(thesis_ids):
        session.add(
            Thesis(
                id=tid,
                research_case_id=case_id,
                title=f"命题{i}",
                statement=f"命题{i}陈述",
                support_condition="sc",
                falsification_condition="fc",
                observation_start=datetime(2025, 1, 1, tzinfo=UTC).date(),
                observation_end=datetime(2026, 12, 31, tzinfo=UTC).date(),
                created_by="t",
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        )
    session.flush()

    # Document → span → statement → reviewed link (T1)
    doc = DocumentVersion(
        content_sha256=f"{sha256_suffix}" * 64,
        source_url="u",
        published_at=datetime(2025, 2, 1, tzinfo=UTC),
        available_at=datetime(2025, 2, 1, tzinfo=UTC),
        acquired_at=datetime(2025, 2, 1, tzinfo=UTC),
        parser_version="1",
        title="测试文档",
    )
    session.add(doc)
    session.flush()
    span = SourceSpan(
        document_version_id=doc.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="测试引用",
    )
    session.add(span)
    session.flush()
    st = SourceStatement(
        source_span_id=span.id,
        normalized_text="测试陈述",
        kind="disclosed_fact",
        observed_period=datetime(2025, 1, 1, tzinfo=UTC),
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    session.add(st)
    session.flush()
    link = EvidenceLink(
        thesis_id=thesis_ids[0],
        source_statement_id=st.id,
        role="supports",
        reason="支持",
        scope={"segment": "AI 算力"},
        review_state="reviewed",
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
        available_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    session.add(link)
    session.flush()

    snap = EvidenceSnapshot(
        thesis_id=thesis_ids[0],
        cutoff=datetime(2025, 2, 1, tzinfo=UTC),
        evidence_link_ids=[str(link.id)],
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    session.add(snap)
    session.flush()
    assess = AIAssessment(
        snapshot_id=snap.id,
        conclusion="supported",
        rationale="支持理由",
        gaps=[],
        displayed_as_provisional=True,
        model_version="test-model-v1",
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    session.add(assess)
    session.flush()
    review = ReviewDecision(
        ai_assessment_id=assess.id,
        outcome="confirmed",
        conclusion="supported",
        reason="人工维持",
        reviewer="tester",
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    session.add(review)
    session.flush()

    # causal step (only for T1)
    session.add(
        CausalStep(
            thesis_id=thesis_ids[0],
            description="需求爆发",
            sequence=1,
            created_at=datetime(2025, 2, 1, tzinfo=UTC),
        )
    )
    session.flush()
    return case_id


@pytest.fixture
def seeded_case(session):
    """为每个测试产生独立的 sha256 后缀以避免 UNIQUE 约束。"""
    suffix = uuid.uuid4().hex[:16]
    case_id = _seed_minimal_case(session, suffix)
    return case_id


def test_conclusion_returns_envelope(api_client, seeded_case):
    response = api_client.get(f"/api/v1/research-cases/{seeded_case}/conclusion")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert "basis" in payload
    assert "header" in payload
    assert "key_factors" in payload
    assert "comparison" in payload
    assert "source_groups" in payload
    assert "reproduction_manifest" in payload
    assert "causal_path" in payload
    assert "gap_explanation" in payload


def test_conclusion_key_factor_count_matches_theses(api_client, seeded_case):
    response = api_client.get(f"/api/v1/research-cases/{seeded_case}/conclusion")
    payload = response.json()
    assert len(payload["key_factors"]) == 3


def test_conclusion_header_exposes_ai_and_human_boundary(api_client, seeded_case):
    response = api_client.get(f"/api/v1/research-cases/{seeded_case}/conclusion")
    header = response.json()["header"]
    # AI 临时标记显式可见
    assert header["ai_provisional"] is True
    # 人工复核并列（outcome=confirmed）
    assert header["review_state"] == "confirmed"
    assert header["reviewer"] == "tester"


def test_conclusion_comparison_has_eight_columns(api_client, seeded_case):
    response = api_client.get(f"/api/v1/research-cases/{seeded_case}/conclusion")
    cols = response.json()["comparison"]["columns"]
    assert len(cols) == 8
    assert "评审维度" in cols
    assert "直接证据" in cols
    assert "替代解释" in cols


def test_conclusion_404_for_unknown_case(api_client):
    response = api_client.get(f"/api/v1/research-cases/{uuid.uuid4()}/conclusion")
    assert response.status_code == 404


def test_conclusion_respects_cutoff(api_client, seeded_case):
    """cutoff 在 evidence 之后仍可访问 case，但 future evidence 被过滤掉。

    与 dossier 一致：case 的 created_at 在过去，cutoff=未来时刻时
    case 可见，但 evidence 的 available_at 在 cutoff 之前时仍可见。
    这里测试反向：cutoff=evidence 后一秒，evidence 应仍可见。
    """
    # 在 evidence 之后 1 天 → evidence 仍可见
    response = api_client.get(
        f"/api/v1/research-cases/{seeded_case}/conclusion",
        params={"cutoff": "2025-12-31T00:00:00+00:00"},
    )
    assert response.status_code == 200
    payload = response.json()
    # 至少 1 条 evidence 应该可见（T1 的支持证据）
    assert len(payload["comparison"]["rows"]) >= 1
    assert any(len(g["relations"]) > 0 for g in payload["source_groups"])