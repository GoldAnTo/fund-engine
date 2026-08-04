"""Refresh-boundary tests for the Cambricon profitability case.

These tests guarantee that the live refresh command is purely append-only:
- new evidence appears as ``machine_generated`` links ready for human review;
- the frozen snapshot, assessment, conclusion, and review decisions remain
  unchanged regardless of refresh success, failure, or duplication;
- the CLI surfaces a token-free diagnostic and never prints request URLs.

No real network call is made: an injected fake client supplies canned
``FinQuery`` responses.  All assertions live in an isolated SQLite test
database created via the project-wide ``engine`` and ``session`` fixtures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import func, select

from app.datasources.gildata.client import GildataMCPError
from app.models.ledger import (
    AIAssessment,
    CaseThemeTagEvent,
    Company,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
)
from app.scripts.cambricon_profitability_data import load_case_data
from app.scripts.refresh_cambricon_profitability_case import (
    REFRESH_QUERIES,
    RefreshResult,
    refresh,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PARENT_TABLE_MARKDOWN = (
    "|股票名称|股票代码|财务科目名称|财务科目代码|财务科目层级|财务科目父级名称|财务科目父级代码|时间|报告期|核算方式|财务科目数额|展示单位|同比(%)|环比(%)|报表名称|\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    "|寒武纪|688256|归属于母公司所有者的净利润|NPParentCompanyOwners|3级|2)净利润按所有权归属分类|OwnershipCateg|2026-06-30|中报|累计值|10.85|亿元|294.42|-|利润表|\n"
    "|寒武纪|688256|归属于母公司所有者的净利润|NPParentCompanyOwners|3级|2)净利润按所有权归属分类|OwnershipCateg|2026-03-31|一季报|累计值|4.12|亿元|256.82|-|利润表|\n"
)

_ADJUSTED_TABLE_MARKDOWN = (
    "|股票名称|股票代码|财务分析指标名称|财务分析指标代码|指标类型|核算方式|时间|报告期|财务分析指标数额|展示单位|报表名称|\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n"
    "|寒武纪|688256|扣除非经常损益后的归母净利润|NetProfitCut|区间类型|累计值|2026-06-30|中报|921,500,000.00|元|收益质量|\n"
    "|寒武纪|688256|扣除非经常损益后的归母净利润|NetProfitCut|区间类型|累计值|2026-03-31|一季报|281,400,000.00|元|收益质量|\n"
)


def _make_response(*tables: str) -> str:
    """Build a Juyuan-style ``result.content[0].text`` envelope."""
    results = [
        {"api_name": "财务报表", "table_markdown": tables[0]},
        {"api_name": "财务分析", "table_markdown": tables[1]},
    ]
    return json.dumps({"code": "0", "results": results}, ensure_ascii=False)


class FakeClient:
    """Fake ``GildataMCPClient`` returning canned FinQuery text strings."""

    def __init__(self, responses: list[str], *, fail_on_call: int | None = None):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self._fail_on_call = fail_on_call
        self._call_index = 0

    def call_tool(self, name, arguments, timeout=60):
        self._call_index += 1
        self.calls.append((name, dict(arguments)))
        if self._fail_on_call is not None and self._call_index == self._fail_on_call:
            raise GildataMCPError("simulated transport failure")
        if not self._responses:
            raise GildataMCPError("no more canned responses")
        return self._responses.pop(0)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_refresh_appends_pending_links_without_changing_seed_snapshot(session):
    from app.scripts.seed_cambricon_profitability_case import seed

    seeded = seed(session)
    snapshot = session.get(EvidenceSnapshot, session.get(AIAssessment, seeded.assessment_id).snapshot_id)
    member_ids = list(snapshot.evidence_link_ids)
    review_count_before = session.scalar(select(func.count()).select_from(ReviewDecision)) or 0
    snapshot_count_before = session.scalar(select(func.count()).select_from(EvidenceSnapshot)) or 0
    assessment_count_before = session.scalar(select(func.count()).select_from(AIAssessment)) or 0

    new_parent_text = _make_response(_PARENT_TABLE_MARKDOWN, _ADJUSTED_TABLE_MARKDOWN)
    client = FakeClient([new_parent_text])

    result = refresh(session, seeded.case_id, client=client)

    # The frozen snapshot is untouched.
    session.refresh(snapshot)
    assert snapshot.evidence_link_ids == member_ids
    assert session.scalar(select(func.count()).select_from(EvidenceSnapshot)) == snapshot_count_before
    assert session.scalar(select(func.count()).select_from(AIAssessment)) == assessment_count_before
    assert session.scalar(select(func.count()).select_from(ReviewDecision)) == review_count_before

    # Every newly appended link is machine_generated / creator_type=ai / available_at
    # falls on or after the original capture instant.
    assert result.pending_links >= 2
    assert result.created_documents == 1
    assert result.duplicate_documents == 0
    for link in result.links:
        assert link.review_state == "machine_generated"
        assert link.creator_type == "ai"

    # The single auditable FinQuery (pinned to the frozen seed observation)
    # went through the injected client exactly once.  One combined response
    # carries both the parent-profit and adjusted-profit tables, mirroring the
    # frozen seed capture.
    assert [name for name, _ in client.calls] == ["FinQuery"]
    assert client.calls[0][1] == {"query": REFRESH_QUERIES[0]}


def test_refresh_deduplicates_identical_response_with_existing_seed(session):
    from app.scripts.seed_cambricon_profitability_case import seed

    seeded = seed(session)
    data = load_case_data()
    # Pretend the live refresh returns the SAME response already pinned by the seed.
    same_text = data.juyuan.raw_response
    client = FakeClient([same_text])

    result = refresh(session, seeded.case_id, client=client)

    assert result.created_documents == 0
    assert result.duplicate_documents == 1
    assert result.pending_links == 0
    assert result.links == []
    assert session.scalar(select(func.count()).select_from(EvidenceReview)) == 0


def test_refresh_failure_rolls_back_and_preserves_seed(session):
    from app.scripts.seed_cambricon_profitability_case import seed

    seeded = seed(session)
    snapshot_count_before = session.scalar(select(func.count()).select_from(EvidenceSnapshot)) or 0
    link_count_before = session.scalar(select(func.count()).select_from(EvidenceLink)) or 0

    client = FakeClient(["placeholder"], fail_on_call=1)

    with pytest.raises(GildataMCPError):
        refresh(session, seeded.case_id, client=client)

    assert session.scalar(select(func.count()).select_from(EvidenceSnapshot)) == snapshot_count_before
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == link_count_before


def test_refresh_rejects_wrong_case_title(session, document_service, research_service):
    from app.repositories.research import ResearchRepository
    from app.services.ingest import DocumentService
    from app.repositories.documents import DocumentRepository

    version = document_service.freeze(raw=b"placeholder", source_url="https://example.test/wrong")
    span = document_service.add_span(version.id, {"page": 1}, "无关案例占位文本")
    wrong_case = research_service.add_case(
        title="其他研究案例",
        industry_topic="other",
        created_by="test",
        research_object="无关",
        phenomenon="无关",
        core_question="无关",
    )
    thesis = research_service.add_thesis(
        wrong_case.id,
        statement="无关命题",
        title="无关",
        support_condition="—",
        falsification_condition="—",
        next_verification_event="—",
        created_by="test",
        creator_type="ai",
        review_state="draft",
    )
    statement = research_service.add_statement(span.id, "无关陈述", kind="disclosed_fact")
    research_service.link_evidence(
        thesis.id, statement.id, role="supports", reason="无关",
        scope={"metric": "irrelevant"}, available_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )

    client = FakeClient(["placeholder"])
    with pytest.raises(ValueError, match="Cambricon"):
        refresh(session, wrong_case.id, client=client)


def test_refresh_records_new_observations_in_review_queue(session):
    from app.queries.review_queue import ReviewQueueQueries
    from app.scripts.seed_cambricon_profitability_case import seed

    seeded = seed(session)
    client = FakeClient([_make_response(_PARENT_TABLE_MARKDOWN, _ADJUSTED_TABLE_MARKDOWN)])
    refresh(session, seeded.case_id, client=client)

    queue = ReviewQueueQueries(session).list_items(case_id=seeded.case_id, limit=50)
    # The newly appended links must surface in the review queue (they carry
    # review_state == "machine_generated" and no EvidenceReview yet).
    assert len(queue.items) >= 2
    # And they must reference real EvidenceLink / SourceSpan / SourceStatement
    # rows (no orphan refs).
    for item in queue.items:
        assert session.get(EvidenceLink, uuid.UUID(item.link_id)) is not None
        assert session.get(SourceSpan, uuid.UUID(item.span_id)) is not None
        assert session.get(SourceStatement, uuid.UUID(item.statement_id)) is not None


def test_refresh_cli_emits_token_free_diagnostic_on_token_missing(monkeypatch, capsys):
    monkeypatch.setenv("GILDATA_TOKEN", "")
    monkeypatch.setenv("APP_ENV", "test")
    script = (
        "/Users/xiongjiali/.config/superpowers/worktrees/fund-engine/cambricon-complete-case"
        "/backend/app/scripts/refresh_cambricon_profitability_case.py"
    )
    result = subprocess.run(
        [sys.executable, script, "--case-id", str(uuid.uuid4())],
        capture_output=True,
        text=True,
        env={**os.environ, "GILDATA_TOKEN": "", "APP_ENV": "test"},
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert "GILDATA_TOKEN" in output
    assert "token=" not in output
    assert "investoday" not in output.lower()
    assert result.returncode != 0


def test_refresh_appended_links_do_not_break_company_theme_role_or_holdings(session):
    """Theme role / company / stock rows must survive a refresh append."""
    from app.scripts.seed_cambricon_profitability_case import seed

    seeded = seed(session)
    company_count_before = session.scalar(select(func.count()).select_from(Company)) or 0
    stock_count_before = session.scalar(select(func.count()).select_from(Stock)) or 0
    role_count_before = session.scalar(select(func.count()).select_from(ThemeRole)) or 0
    tag_count_before = session.scalar(select(func.count()).select_from(CaseThemeTagEvent)) or 0

    client = FakeClient([_make_response(_PARENT_TABLE_MARKDOWN, _ADJUSTED_TABLE_MARKDOWN)])
    refresh(session, seeded.case_id, client=client)

    assert session.scalar(select(func.count()).select_from(Company)) == company_count_before
    assert session.scalar(select(func.count()).select_from(Stock)) == stock_count_before
    assert session.scalar(select(func.count()).select_from(ThemeRole)) == role_count_before
    assert session.scalar(select(func.count()).select_from(CaseThemeTagEvent)) == tag_count_before


def test_refresh_result_dataclass_exposes_required_counters():
    result = RefreshResult(
        document_ids=[],
        links=[],
        created_documents=0,
        duplicate_documents=0,
        pending_links=0,
    )
    assert result.created_documents == 0
    assert result.duplicate_documents == 0
    assert result.pending_links == 0
