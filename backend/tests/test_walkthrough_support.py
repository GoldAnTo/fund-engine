from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.scripts.walkthrough_support import (
    WalkthroughConfigurationError,
    assessment_review_payload,
    atomic_claim_review_payload,
    classify_historical_case_read,
    configured_research_headers,
    proposal_review_payload,
    walkthrough_database_path,
    walkthrough_paths,
)


def test_configured_research_headers_use_a_configured_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"walkthrough-token":{"tenant_id":"walkthrough","roles":[]}}',
    )

    assert configured_research_headers() == {
        "Authorization": "Bearer walkthrough-token"
    }


def test_configured_research_headers_fail_closed_without_a_token(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_TENANT_TOKENS", raising=False)

    with pytest.raises(WalkthroughConfigurationError, match="RESEARCH_TENANT_TOKENS"):
        configured_research_headers()


def test_walkthrough_paths_are_unique_per_run_and_resumable(tmp_path) -> None:
    first = walkthrough_paths(tmp_path, "first")
    resumed = walkthrough_paths(tmp_path, "first")
    second = walkthrough_paths(tmp_path, "second")

    assert first == resumed
    assert first != second
    assert first.state.name == "cambricon_walkthrough_first_state.json"


def test_walkthrough_database_path_is_unique_per_run(tmp_path) -> None:
    assert walkthrough_database_path(tmp_path, "first") == (
        tmp_path / "evidence_walkthrough_first.db"
    )


def test_historical_case_absence_is_classified_without_hiding_other_failures() -> None:
    assert classify_historical_case_read(
        404,
        {"error": {"code": "not_found", "message": "research case not found"}},
    ) == "case_not_created_at_cutoff"
    assert classify_historical_case_read(500, {"error": {"code": "internal_error"}}) is None


def test_walkthrough_review_payloads_keep_human_gates_explicit() -> None:
    assert atomic_claim_review_payload("claim-1") == {
        "outcome": "confirmed",
        "reviewer": "walkthrough-reviewer",
        "reason": "人工核对原文连续引文后确认发布",
        "idempotency_key": "walkthrough-atomic-claim-claim-1",
    }
    assert proposal_review_payload(3, can_accept=True) == {
        "outcome": "confirmed",
        "reason": "人工核对来源与命题关联后确认发布",
        "reviewer_id": "walkthrough-reviewer",
        "expected_version": 3,
    }
    assert proposal_review_payload(4, can_accept=False) == {
        "outcome": "rejected",
        "reason": "人工复核：该来源不满足正式证据准入条件，保留提案审计记录但不发布",
        "reviewer_id": "walkthrough-reviewer",
        "expected_version": 4,
    }


def test_walkthrough_assessment_review_does_not_override_a_zero_evidence_verdict() -> None:
    assert assessment_review_payload("insufficient_evidence", evidence_count=0) == {
        "outcome": "confirmed",
        "conclusion": None,
        "reason": "人工复核：当前冻结快照没有已发布证据，确认证据不足结论；历史交叉验证不改写该快照。",
        "reviewer": "walkthrough-reviewer",
    }


def test_walkthrough_cli_starts_without_running_any_stage() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ | {
        "RESEARCH_TENANT_TOKENS": '{"walkthrough-token":"walkthrough"}',
        "WALKTHROUGH_RUN_ID": "cli-startup-test",
    }

    result = subprocess.run(
        [sys.executable, "scripts/walkthrough_cambricon_case.py", "--help"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--stages" in result.stdout
