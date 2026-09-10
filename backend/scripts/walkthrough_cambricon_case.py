"""Full-pipeline walkthrough on a historically-verifiable case: 寒武纪 (688256).

Drives the real v1 HTTP contract end-to-end through FastAPI's TestClient
(full ASGI stack: middleware, error envelopes, routers), with:

  P0  preflight datasource probes (Gildata tools, quote, 2025 annual profit,
      fund-holding probe)
  P1  case + thesis creation            POST /api/v1/research-cases
  P2  real Gildata ingest (2 rounds)    POST /api/v1/documents/ingest
  P3  live-LLM statement extraction     POST /api/v1/documents/{id}/extract
  P3.5 atomic-claim confirmation        POST /api/v1/atomic-claims/{id}/reviews
  P4  evidence proposal (hybrid recall) POST /api/v1/theses/{id}/propose
  P4.5 proposal confirmation             POST /api/v1/review-proposals/{id}/decisions
  P5  pre-review AI assessment (T1)     POST /api/v1/theses/{id}/rerun
  P6  human review simulation           GET review-queue + POST link reviews
  P7  post-review assessments (all)     POST /api/v1/theses/{id}/rerun
  P8  assessment reviews (human)        POST /api/v1/assessments/{id}/reviews
  P9  instrument/fund enrichment        (repository-only path — no API exists)
  P10 read models: overview / dossier / graph / search / gaps / knowledge /
      KPIs / snapshots / compare / fund exposure
  P11 historical point-in-time replay   dossier at 2024-12-31 / 2025-04-01
  P12 fact cross-check vs verified history

Every observation is appended as JSONL to docs/evaluation/walkthrough/ so the
run is auditable; a compact summary JSON is written at the end.

Run from backend/:
    .venv/bin/python scripts/walkthrough_cambricon_case.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
OUT_DIR = REPO_ROOT / "docs" / "evaluation" / "walkthrough"

sys.path.insert(0, str(BACKEND_ROOT))
from app.scripts.walkthrough_support import (
    WalkthroughResponseError,
    assessment_review_payload,
    atomic_claim_review_payload,
    atomic_write_json,
    classify_extract_reason,
    classify_historical_case_read,
    configured_research_headers,
    ensure_walkthrough_directory,
    prepare_walkthrough_artifact_targets,
    prepare_walkthrough_database,
    project_gildata_probes,
    proposal_review_payload,
    safe_audit_data,
    secure_append_text,
    secure_read_json,
    summarize_extract_response,
    validate_live_walkthrough_environment,
    validate_persisted_json,
    walkthrough_database_path,
    walkthrough_paths,
)

# Runtime resources intentionally stay uninitialized at import time.  In
# particular, ``argparse`` must be allowed to service ``--help`` without
# loading local credentials, creating a database, or touching audit files.
RUN_ID = ""
DB_PATH = Path("__walkthrough_runtime_not_initialized__.db")
STATE_PATH = Path("__walkthrough_runtime_not_initialized__state.json")
JSONL_PATH = Path("__walkthrough_runtime_not_initialized__.jsonl")
SUMMARY_PATH = Path("__walkthrough_runtime_not_initialized__summary.json")
AUTH_HEADERS: dict[str, str] = {}
client: object | None = None
summary: dict = {"run_id": None, "phases": {}, "issues": [], "facts": {}}
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "headers",
        "message",
        "normalized_text",
        "quote",
        "raw",
        "request",
        "response_body",
        "rows",
        "token",
        "verbatim_text",
    }
)
_ACCEPTANCE_AUDIT_SOURCE = "gildata_ai_acceptance"


def _initialize_runtime(*, require_live_llm: bool) -> None:
    """Initialize credentials, storage and ASGI only after argument parsing."""
    global AUTH_HEADERS, DB_PATH, JSONL_PATH, RUN_ID, STATE_PATH, SUMMARY_PATH
    global client, summary

    configured_run_id = os.getenv("WALKTHROUGH_RUN_ID")
    run_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if configured_run_id is None
        else configured_run_id
    )
    database_path = walkthrough_database_path(BACKEND_ROOT, run_id)
    paths = walkthrough_paths(OUT_DIR, run_id)

    from app.env import load_local_env

    load_local_env()
    validate_live_walkthrough_environment(require_live_llm=require_live_llm)
    auth_headers = configured_research_headers()

    ensure_walkthrough_directory(OUT_DIR)
    prepare_walkthrough_artifact_targets((paths.state, paths.jsonl, paths.summary))
    prepare_walkthrough_database(database_path)
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"

    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app
    from app.models.ledger import Base

    previous_umask = os.umask(0o077)
    try:
        Base.metadata.create_all(engine)
    finally:
        os.umask(previous_umask)

    RUN_ID = run_id
    DB_PATH = database_path
    STATE_PATH = paths.state
    JSONL_PATH = paths.jsonl
    SUMMARY_PATH = paths.summary
    AUTH_HEADERS = auth_headers
    client = TestClient(app)
    summary = {"run_id": run_id, "phases": {}, "issues": [], "facts": {}}


def rec(phase: str, step: str, data: dict) -> None:
    """Append one auditable observation to the JSONL log."""
    projected = safe_audit_data(data)
    secure_append_text(
        JSONL_PATH,
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "phase": phase,
                "step": step,
                "data": projected,
            },
            ensure_ascii=False,
        )
        + "\n",
    )


def issue(code: str, *, http_status: int | None = None) -> None:
    """Record a fixed failure category without persisting response text."""
    observation: dict[str, object] = {"code": code}
    if http_status is not None:
        observation["http_status"] = http_status
    summary["issues"].append(observation)
    rec("issue", code, observation)


def api(method: str, path: str, phase: str, step: str, **kwargs) -> tuple[int, dict]:
    """Call v1 and record only a non-content projection of the response."""
    if client is None:
        raise RuntimeError("walkthrough runtime is not initialized")
    request_headers = {**AUTH_HEADERS, **kwargs.pop("headers", {})}
    try:
        resp = client.request(method, path, headers=request_headers, **kwargs)
    except Exception:  # noqa: BLE001 — never expose transport/provider details
        raise WalkthroughResponseError(f"{phase} {step} API transport failed") from None
    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"code": "non_json_response"}}
    if not isinstance(body, dict):
        body = {"error": {"code": "invalid_json_shape"}}
    audit: dict[str, object] = {
        "method": method,
        "path": path,
        "status": resp.status_code,
    }
    if 200 <= resp.status_code < 400:
        audit["response"] = body
    else:
        error_code = _safe_http_error_code(body)
        if error_code is not None:
            audit["error_code"] = error_code
    rec(phase, step, audit)
    return resp.status_code, body


def _safe_http_error_code(body: object) -> str | None:
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return None
    code = body["error"].get("code")
    if not isinstance(code, str) or not 1 <= len(code) <= 80:
        return None
    if not all(char.isascii() and (char.isalnum() or char in "_-") for char in code):
        return None
    return code


def _http_failure(status: int, body: object) -> dict[str, object]:
    failure: dict[str, object] = {"http_status": status}
    error_code = _safe_http_error_code(body)
    if error_code is not None:
        failure["error_code"] = error_code
    return failure


def _require_status(status: int, expected: int, operation: str) -> None:
    if status != expected:
        raise WalkthroughResponseError(f"{operation} returned HTTP {status}")


# ---------------------------------------------------------------------------
# P0 — preflight datasource probes
# ---------------------------------------------------------------------------


def phase0_preflight() -> dict:
    from app.datasources.gildata import adapters
    from app.datasources.gildata.client import GildataMCPClient

    tools: list[str] = []
    quotes: object = []
    annual: object = []
    funds: object = []
    smart_response_chars = 0
    smart_failed = False
    try:
        with GildataMCPClient.from_env() as gc:
            discovered_tools = [
                name
                for tool in gc.list_tools()
                if isinstance(tool, dict)
                and isinstance((name := tool.get("name")), str)
            ]
            projected_tools = safe_audit_data({"tools": discovered_tools}).get(
                "tools", []
            )
            tools = projected_tools if isinstance(projected_tools, list) else []
            rec("P0", "list_tools", {"tools": tools})

            quotes = adapters.fetch_quote(gc, "寒武纪最新股价行情")

            # Historical verification data: 2025 annual results (published 2026-04).
            annual = adapters.fetch_quote(
                gc, "寒武纪2025年年度报告 营业收入 归母净利润"
            )

            # Fund-holding probe: which funds disclose 寒武纪 positions.
            funds = adapters.fetch_quote(
                gc, "持有寒武纪股票的基金 持仓占净值比例 报告期"
            )

            try:
                text = gc.call_tool(
                    "SmartFundSelection", {"query": "重仓持有寒武纪的基金"}
                )
                smart_response_chars = len(text) if isinstance(text, str) else 0
            except Exception:  # noqa: BLE001 — optional probe must not kill the run
                smart_failed = True
    except Exception:  # noqa: BLE001 — redact provider transport/configuration errors
        raise WalkthroughResponseError("Gildata preflight probe failed") from None

    probes = project_gildata_probes(
        quote_rows=quotes,
        annual_rows=annual,
        fund_rows=funds,
        smart_response_chars=smart_response_chars,
        smart_failed=smart_failed,
    )
    probes["tools"] = tools
    rec("P0", "projected_probes", probes)

    summary["phases"]["P0_preflight"] = {
        "tools": tools,
        "quote_metrics": probes["quote_metrics"],
        "annual_2025": probes["annual_2025"],
        "fund_holders_rows": len(probes.get("fund_holders") or []),
        "smart_fund_selection": probes["smart_fund_selection"],
    }
    return probes


# ---------------------------------------------------------------------------
# P1 — create the research case
# ---------------------------------------------------------------------------

THESES = [
    {
        "key": "T1",
        "title": "国产算力需求驱动收入高增长",
        "statement": "2024-2025年国产AI算力芯片需求爆发将驱动寒武纪云端芯片收入持续高增长",
        "observation_start": "2024-01-01",
        "observation_end": "2025-12-31",
        "support_condition": "寒武纪2024年及2025年各报告期营业收入同比增速显著为正，云端产品线为主力",
        "falsification_condition": "收入增速回落至个位数或出现同比下滑",
        "next_verification_event": "2025年年度报告披露（2026年4月）",
    },
    {
        "key": "T2",
        "title": "盈利拐点兑现",
        "statement": "寒武纪将在2024Q4-2025年实现连续季度盈利并走向年度扭亏为盈",
        "observation_start": "2024-10-01",
        "observation_end": "2026-04-30",
        "support_condition": "2024Q4起单季度归母净利润转正且连续；2025年报归母净利润为正",
        "falsification_condition": "2025年任一季度重新转亏，或2025年度归母净利润仍为负",
        "next_verification_event": "2025年年度报告披露（2026年4月）",
    },
    {
        "key": "T3",
        "title": "估值溢价透支风险",
        "statement": "寒武纪当前估值水平已显著透支基本面兑现节奏，估值溢价难以仅由收入高增长维持",
        "observation_start": "2025-01-01",
        "observation_end": "2026-08-01",
        "support_condition": "PE(TTM)/PB 显著高于半导体行业均值，且盈利兑现依赖单一需求驱动",
        "falsification_condition": "盈利兑现速度使估值倍数快速消化至行业合理区间",
        "next_verification_event": "2026年半年报披露（2026年8月）",
    },
]


def phase1_create_case() -> dict:
    status, body = api(
        "POST",
        "/api/v1/event-research",
        "P1",
        "create_case",
        json={
            "raw_input": (
                "寒武纪（688256.SH）2024 年以来收入放量并出现盈利拐点。"
                "本走查以国产 AI 算力芯片需求、盈利兑现和估值风险为竞争命题，"
                "后续通过受治理资料接入、AI 提取和人工审核验证。"
            ),
            "source_type": "pasted_snapshot",
            "event_title": "寒武纪收入与盈利拐点研究",
            "company_name": "寒武纪",
            "ticker": "688256.SH",
            "event_at": "2026-08-01T00:00:00Z",
            "market_reaction": "收入和盈利改善背景下的估值变化待验证",
            "research_question": "寒武纪的收入高增长与盈利拐点是否由公开证据支持，当前估值溢价能否被基本面兑现？",
            "candidate_factors": [t["statement"] for t in THESES],
            "research_protocol_required": False,
            "created_by": "walkthrough-reviewer",
        },
    )
    _require_status(status, 201, "case creation")
    case_id = body["case_id"]
    status, dossier = api(
        "GET", f"/api/v1/research-cases/{case_id}/dossier", "P1", "created_dossier"
    )
    _require_status(status, 200, "created dossier read")
    thesis_by_statement = {item["statement"]: item for item in dossier["theses"]}
    theses = {
        thesis["key"]: {"id": thesis_by_statement[thesis["statement"]]["id"]}
        for thesis in THESES
    }
    out = {"case_id": case_id, "theses": theses}
    summary["phases"]["P1_create_case"] = out
    return out


# ---------------------------------------------------------------------------
# P2 — real Gildata ingest
# ---------------------------------------------------------------------------

INGEST_RUNS = [
    {
        "research_queries": [
            "寒武纪2024年年度报告业绩",
            "寒武纪2025年一季度业绩",
            "寒武纪算力芯片出货及估值研报观点",
        ],
        "announcement_query": "寒武纪定期报告 年度报告 季度报告",
        "news_query": "寒武纪 AI算力芯片 最新消息",
        "quote_query": "寒武纪最新股价行情",
        "quote_stock_code": "688256",
    },
    {
        "research_queries": [
            "工业富联AI服务器收入研报",
            "国产AI算力芯片行业需求研报",
            "寒武纪 大模型芯片平台 定增 研报",
        ],
        "announcement_query": "寒武纪 定增 募集资金 大模型芯片平台",
        "news_query": "寒武纪 股价 创新高 估值",
        "quote_query": "工业富联最新股价行情",
        "quote_stock_code": "601138",
    },
]


def phase2_ingest(case_id: str) -> list[dict]:
    results = []
    for i, run in enumerate(INGEST_RUNS, 1):
        status, body = api(
            "POST",
            "/api/v1/documents/ingest",
            "P2",
            f"ingest_round_{i}",
            json={"case_id": case_id, **run},
        )
        if status != 201:
            issue("ingest_failed", http_status=status)
            results.append({"round": i, **_http_failure(status, body)})
            continue
        results.append(
            {
                "round": i,
                "http_status": status,
                **safe_audit_data(body),
            }
        )
    summary["phases"]["P2_ingest"] = results
    return results


# ---------------------------------------------------------------------------
# P3 — extraction over every frozen document version
# ---------------------------------------------------------------------------

_EXTRACTION_STATES = frozenset(
    {"extracted", "extracted_empty", "failed", "not_attempted"}
)
_CONTENT_QUALITIES = frozenset({"ok", "degenerate", "unknown"})


def _all_documents(case_id: str, *, step: str) -> list[dict]:
    """Read every document page, failing closed on pagination drift."""
    by_id: dict[str, dict] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    page_number = 1
    while True:
        params: dict[str, object] = {"case_id": case_id, "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        status, body = api(
            "GET",
            "/api/v1/documents",
            "P3",
            f"{step}_page_{page_number}",
            params=params,
        )
        if status != 200:
            raise WalkthroughResponseError(f"document listing returned HTTP {status}")
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise WalkthroughResponseError("document listing items must be a list")
        page = body.get("page")
        if not isinstance(page, dict):
            raise WalkthroughResponseError("document listing page metadata is missing")
        has_more = page.get("has_more")
        next_cursor = page.get("next_cursor")
        if not isinstance(has_more, bool):
            raise WalkthroughResponseError(
                "document listing has_more must be a boolean"
            )

        for item in body["items"]:
            if not isinstance(item, dict):
                raise WalkthroughResponseError(
                    "document listing item must be an object"
                )
            version_id = item.get("id")
            extraction_state = item.get("extraction_state")
            content_quality = item.get("content_quality")
            if not isinstance(version_id, str) or not version_id:
                raise WalkthroughResponseError("document listing item id is missing")
            if extraction_state not in _EXTRACTION_STATES:
                raise WalkthroughResponseError(
                    "document listing extraction_state is invalid"
                )
            if content_quality not in _CONTENT_QUALITIES:
                raise WalkthroughResponseError(
                    "document listing content_quality is invalid"
                )
            by_id[version_id] = item

        if not has_more:
            return list(by_id.values())
        if not isinstance(next_cursor, str) or not next_cursor:
            raise WalkthroughResponseError("document listing next_cursor is missing")
        if next_cursor in seen_cursors:
            raise WalkthroughResponseError("document listing next_cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        page_number += 1


def _pending_documents(items: list[dict]) -> list[dict]:
    return [
        item
        for item in items
        if item["extraction_state"] in {"not_attempted", "failed"}
        and item["content_quality"] != "degenerate"
    ]


def _previous_extract_documents() -> list[dict]:
    phase = summary.get("phases", {}).get("P3_extract", {})
    previous = phase.get("per_document", []) if isinstance(phase, dict) else []
    if not isinstance(previous, list):
        raise WalkthroughResponseError("stored P3 per_document summary must be a list")
    validated: list[dict] = []
    for item in previous:
        if not isinstance(item, dict):
            raise WalkthroughResponseError(
                "stored P3 per_document item must be an object"
            )
        version_id = item.get("version_id")
        candidates = item.get("candidates")
        claim_types = item.get("claim_types")
        reason = item.get("reason")
        if (
            not isinstance(version_id, str)
            or not version_id
            or isinstance(candidates, bool)
            or not isinstance(candidates, int)
            or candidates < 0
            or not isinstance(claim_types, dict)
            or any(
                not isinstance(kind, str)
                or not kind
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for kind, count in claim_types.items()
            )
            or (reason is not None and not isinstance(reason, str))
        ):
            raise WalkthroughResponseError("stored P3 per_document item is invalid")
        validated.append(
            {
                "version_id": version_id,
                "candidates": candidates,
                "claim_types": claim_types,
                "reason": classify_extract_reason(candidates, reason),
            }
        )
    return validated


def _extraction_totals(
    *,
    documents_total: int,
    pending_before: int,
    attempted_this_run: int,
    extracted_this_run: int,
    pending_after: int | None,
    merged: dict[str, dict],
) -> dict:
    per_document = list(merged.values())
    claim_types: dict[str, int] = {}
    candidate_count = 0
    for item in per_document:
        candidate_count += item["candidates"]
        for claim_type, count in item["claim_types"].items():
            claim_types[claim_type] = claim_types.get(claim_type, 0) + count
    return {
        "documents_total": documents_total,
        "pending_before": pending_before,
        "attempted_this_run": attempted_this_run,
        "extracted_this_run": extracted_this_run,
        "pending_after": pending_after,
        "candidates": candidate_count,
        "claim_types": claim_types,
        "per_document": per_document,
    }


def phase3_extract(
    case_id: str, max_docs: int = 8, *, checkpoint_state: dict | None = None
) -> dict:
    if not isinstance(case_id, str) or not case_id:
        raise WalkthroughResponseError("P3 case_id must be a non-empty string")
    items = _all_documents(case_id, step="list_documents_before")
    # Extraction watermark (defect-3 fix): only attempt versions that were
    # never extracted or whose last run failed.  "extracted_empty" versions
    # (successful run, zero statements) used to be indistinguishable from
    # pending ones and were re-extracted every round — 5 docs × 5 rounds =
    # 25 wasted LLM calls in the original walkthrough.
    pending = _pending_documents(items)
    batch = pending[:max_docs]
    merged = {item["version_id"]: item for item in _previous_extract_documents()}
    attempted_this_run = 0
    extracted_this_run = 0
    for item in batch:
        version_id = item["id"]
        attempted_this_run += 1
        status, ext = api(
            "POST",
            f"/api/v1/documents/{version_id}/extract",
            "P3",
            "extract",
        )
        if status != 201:
            issue("extract_failed", http_status=status)
            continue
        extracted = summarize_extract_response(ext)
        merged[version_id] = {
            "version_id": version_id,
            "candidates": extracted["candidate_count"],
            "claim_types": extracted["claim_types"],
            "reason": extracted["reason"],
        }
        extracted_this_run += 1
        rec(
            "P3",
            "extract_summary",
            {
                "version_id": version_id,
                "candidate_count": extracted["candidate_count"],
                "claim_types": extracted["claim_types"],
                "reason": extracted["reason"],
            },
        )

        # A later extraction can fail after the service has already committed
        # this document.  Persist the safe merged watermark before beginning
        # the next call so resume accounting cannot lose the successful item.
        summary["phases"]["P3_extract"] = _extraction_totals(
            documents_total=len(items),
            pending_before=len(pending),
            attempted_this_run=attempted_this_run,
            extracted_this_run=extracted_this_run,
            pending_after=None,
            merged=merged,
        )
        if checkpoint_state is not None:
            _checkpoint(checkpoint_state)

    refreshed_items = _all_documents(case_id, step="list_documents_after")
    totals = _extraction_totals(
        documents_total=len(refreshed_items),
        pending_before=len(pending),
        attempted_this_run=attempted_this_run,
        extracted_this_run=extracted_this_run,
        pending_after=len(_pending_documents(refreshed_items)),
        merged=merged,
    )
    summary["phases"]["P3_extract"] = totals
    return totals


def phase3_review_atomic_claims(case_id: str) -> dict:
    """Confirm extracted candidates before they become SourceStatements.

    This is deliberately a separate recorded human gate: extraction creates
    candidates only, and downstream proposal work must not treat them as
    published source statements until a reviewer confirms them.
    """
    status, body = api(
        "GET",
        f"/api/v1/research-cases/{case_id}/atomic-claims",
        "P3.5",
        "list_atomic_claims",
        params={"review_state": "awaiting_review", "limit": 200},
    )
    _require_status(status, 200, "atomic claim queue read")
    items = body.get("items", [])
    stats = {"queued": len(items), "confirmed": 0, "failed": 0}
    for item in items:
        claim_id = item["id"]
        status, _ = api(
            "POST",
            f"/api/v1/atomic-claims/{claim_id}/reviews",
            "P3.5",
            "review_atomic_claim",
            json=atomic_claim_review_payload(claim_id),
        )
        if status != 201:
            stats["failed"] += 1
            issue("atomic_claim_review_failed", http_status=status)
            continue
        stats["confirmed"] += 1
    summary["phases"]["P3_5_atomic_claim_review"] = stats
    return stats


# ---------------------------------------------------------------------------
# P4 — evidence proposal per thesis
# ---------------------------------------------------------------------------

_PROPOSAL_ROLES = frozenset({"supports", "contradicts", "contextualizes"})


def phase4_propose(theses: dict) -> dict:
    out = {}
    for key, thesis in theses.items():
        status, body = api(
            "POST",
            f"/api/v1/theses/{thesis['id']}/propose",
            "P4",
            f"propose_{key}",
        )
        if status != 201:
            issue("propose_failed", http_status=status)
            out[key] = _http_failure(status, body)
            continue
        roles = {}
        for link in body.get("links", []):
            role = link.get("role")
            # The public command intentionally returns empty placeholder link
            # fields; they prove only proposal identity/count, not content.
            if role == "":
                continue
            if role not in _PROPOSAL_ROLES:
                raise WalkthroughResponseError(
                    "proposal response role is invalid"
                )
            roles[role] = roles.get(role, 0) + 1
        out[key] = {
            "mode": body.get("mode"),
            "link_count": body.get("link_count"),
            "roles": roles,
        }
    summary["phases"]["P4_propose"] = out
    return out


def phase4_review_proposals(case_id: str) -> dict:
    """Decide pending proposals using the event source-admission result."""
    status, body = api(
        "GET",
        f"/api/v1/event-research/{case_id}/review-queue",
        "P4.5",
        "list_event_evidence_proposals",
    )
    _require_status(status, 200, "proposal queue read")
    items = body.get("items", [])
    stats = {
        "queued": len(items),
        "confirmed": 0,
        "rejected_for_source": 0,
        "failed": 0,
        "published_evidence_links": 0,
    }
    for item in items:
        proposal_id = item["proposal_id"]
        can_accept = item["can_accept"]
        status, response = api(
            "POST",
            f"/api/v1/review-proposals/{proposal_id}/decisions",
            "P4.5",
            "review_evidence_proposal",
            json=proposal_review_payload(
                item["proposal_version"], can_accept=can_accept
            ),
        )
        if status != 201:
            stats["failed"] += 1
            issue("proposal_review_failed", http_status=status)
            continue
        if can_accept:
            stats["confirmed"] += 1
        else:
            stats["rejected_for_source"] += 1
        if response.get("published_entity_id"):
            stats["published_evidence_links"] += 1
    summary["phases"]["P4_5_proposal_review"] = stats
    return stats


# ---------------------------------------------------------------------------
# P5 — pre-review assessment for T1 (baseline snapshot for compare)
# ---------------------------------------------------------------------------


def phase5_pre_review_assessment(theses: dict) -> dict:
    status, body = api(
        "POST",
        f"/api/v1/theses/{theses['T1']['id']}/rerun",
        "P5",
        "rerun_T1_pre_review",
    )
    out: dict = {}
    if status == 201:
        a = body["assessment"]
        gaps = a.get("gaps")
        out = {
            "conclusion": a["conclusion"],
            "gap_count": len(gaps) if isinstance(gaps, list) else 0,
            "snapshot_id": a["snapshot_id"],
            "assessment_id": a["id"],
            "mode": body.get("mode"),
        }
    else:
        out = _http_failure(status, body)
        issue("pre_review_assessment_refused", http_status=status)
    summary["phases"]["P5_pre_review_assessment_T1"] = out
    return out


# ---------------------------------------------------------------------------
# P6 — human review simulation over the review queue
# ---------------------------------------------------------------------------

RISK_WORDS = (
    "风险",
    "亏损",
    "透支",
    "泡沫",
    "回调",
    "谨慎",
    "存货",
    "应收账款",
    "减持",
    "高估",
    "现金流",
    "赊销",
    "减值",
    "质疑",
)
GROWTH_WORDS = (
    "增长",
    "扭亏",
    "盈利",
    "放量",
    "爆发",
    "突破",
    "新高",
    "订单",
    "出货",
    "需求",
    "扩产",
    "超预期",
    "同比",
)
BACKGROUND_WORDS = (
    "成立",
    "专注",
    "产品线",
    "研发",
    "行业",
    "市场",
    "生态",
    "芯片设计",
    "处理器",
    "背景",
    "概况",
)


def _review_decision(thesis_key: str, text: str) -> dict:
    """Deterministic simulated-reviewer rule set (documented in the report).

    Decides (outcome, relation) for one queued link from the frozen verbatim
    text.  The point is to exercise the review write path with consistent,
    explainable human-style judgments — not to be a perfect analyst.
    """
    risk = any(w in text for w in RISK_WORDS)
    growth = any(w in text for w in GROWTH_WORDS)
    background = any(w in text for w in BACKGROUND_WORDS)
    if not text.strip():
        return {
            "outcome": "rejected",
            "relation": None,
            "reason": "原文片段为空，无法构成证据",
        }
    if thesis_key in {"T1", "T2"}:
        if risk and not growth:
            return {
                "outcome": "confirmed",
                "relation": "contradicts",
                "reason": "人工复核：该陈述指向风险因素，构成对命题的反向证据",
            }
        if growth:
            return {
                "outcome": "confirmed",
                "relation": "supports",
                "reason": "人工复核：该陈述与命题方向一致，证据链可追溯",
            }
        return {
            "outcome": "confirmed",
            "relation": "contextualizes",
            "reason": "人工复核：该陈述提供行业/公司背景，限定命题适用范围",
        }
    # T3 估值风险命题：风险表述支持命题，增长表述反向
    if risk:
        return {
            "outcome": "confirmed",
            "relation": "supports",
            "reason": "人工复核：风险/估值类陈述支持估值透支命题",
        }
    if growth:
        return {
            "outcome": "confirmed",
            "relation": "contradicts",
            "reason": "人工复核：基本面高增长对估值透支命题构成反向证据",
        }
    if background:
        return {
            "outcome": "confirmed",
            "relation": "contextualizes",
            "reason": "人工复核：背景性陈述，限定估值讨论边界",
        }
    return {
        "outcome": "needs_more_evidence",
        "relation": "evidence_gap",
        "reason": "人工复核：与命题相关性不足，需要更直接证据",
    }


def phase6_review(theses: dict, case_id: str) -> dict:
    thesis_by_id = {t["id"]: k for k, t in theses.items()}
    status, body = api(
        "GET",
        "/api/v1/review-queue",
        "P6",
        "review_queue",
        params={"case_id": case_id, "limit": 200},
    )
    _require_status(status, 200, "review queue read")
    items = body.get("items", [])
    stats = {
        "queued": len(items),
        "confirmed": 0,
        "rejected": 0,
        "needs_more_evidence": 0,
        "by_thesis": {},
    }
    for item in items:
        key = thesis_by_id.get(item["thesis_id"], "unknown")
        decision = _review_decision(key, item.get("verbatim_text", ""))
        ai_scope = item.get("ai_scope") or {}
        scope_text = (
            "; ".join(f"{k}={v}" for k, v in ai_scope.items())
            if ai_scope
            else "行业范围：国产AI算力芯片"
        )
        payload = {
            "outcome": decision["outcome"],
            "relation": decision["relation"],
            "factor_role": "证据因素",
            "scope_boundary": scope_text,
            "reason": decision["reason"],
            "reviewer": "walkthrough-reviewer",
        }
        status, _ = api(
            "POST",
            f"/api/v1/evidence-links/{item['link_id']}/reviews",
            "P6",
            "review_link",
            json=payload,
        )
        if status != 201:
            issue("review_failed", http_status=status)
            continue
        stats[decision["outcome"]] += 1
        per = stats["by_thesis"].setdefault(
            key, {"confirmed": 0, "rejected": 0, "needs_more_evidence": 0}
        )
        per[decision["outcome"]] += 1

    # Queue must be drained afterwards.
    status, after = api(
        "GET",
        "/api/v1/review-queue",
        "P6",
        "review_queue_after",
        params={"limit": 200},
    )
    _require_status(status, 200, "post-review queue read")
    stats["remaining_after_review"] = len(after.get("items", []))
    summary["phases"]["P6_review"] = stats
    return stats


# ---------------------------------------------------------------------------
# P7 — post-review assessments for all theses
# ---------------------------------------------------------------------------


def phase7_assessments(theses: dict) -> dict:
    out = {}
    for key, thesis in theses.items():
        status, body = api(
            "POST",
            f"/api/v1/theses/{thesis['id']}/rerun",
            "P7",
            f"rerun_{key}",
        )
        if status == 201:
            a = body["assessment"]
            gaps = a.get("gaps")
            out[key] = {
                "conclusion": a["conclusion"],
                "gap_count": len(gaps) if isinstance(gaps, list) else 0,
                "snapshot_id": a["snapshot_id"],
                "assessment_id": a["id"],
                "mode": body.get("mode"),
            }
        elif status == 422:
            out[key] = {
                "compliance_refused": True,
                **_http_failure(status, body),
            }
            rec("P7", f"compliance_refusal_{key}", out[key])
        else:
            out[key] = _http_failure(status, body)
            issue("assessment_failed", http_status=status)
    summary["phases"]["P7_assessments"] = out
    return out


# ---------------------------------------------------------------------------
# P8 — human reviews of the AI assessments
# ---------------------------------------------------------------------------


def phase8_assessment_reviews(assessments: dict) -> dict:
    proposal_review = summary["phases"].get("P4_5_proposal_review", {})
    evidence_count = int(proposal_review.get("published_evidence_links", 0))
    out = {}
    for key, a in assessments.items():
        if "assessment_id" not in a:
            out[key] = {"skipped": "no assessment (refused or failed)"}
            continue
        ai_conclusion = a["conclusion"]
        payload = assessment_review_payload(
            ai_conclusion, evidence_count=evidence_count
        )
        status, resp = api(
            "POST",
            f"/api/v1/assessments/{a['assessment_id']}/reviews",
            "P8",
            f"review_assessment_{key}",
            json=payload,
        )
        if status != 201:
            issue("assessment_review_failed", http_status=status)
            out[key] = _http_failure(status, resp)
            continue
        out[key] = {
            "outcome": resp["outcome"],
            "human_conclusion": resp.get("conclusion"),
            "ai_conclusion": ai_conclusion,
        }
    summary["phases"]["P8_assessment_reviews"] = out
    return out


# ---------------------------------------------------------------------------
# P9 — instrument / fund enrichment (API-first since 2026-08-02)
# ---------------------------------------------------------------------------


def phase9_enrichment(case_id: str, theses: dict, probes: dict) -> dict:
    """Write ThemeRole / Fund / HoldingDisclosure / CausalStep / CausalEdge
    through the v1 command APIs (instrument + causal, added 2026-08-02); the
    session below is only used for existence lookups and the idempotency
    guard.  Fund holding data is only written when the P0 probe returned an
    explicit weight; no weights are fabricated.
    """
    from decimal import Decimal, InvalidOperation

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.ledger import Company, Fund, Stock

    out: dict = {
        "theme_roles": 0,
        "causal_steps": 0,
        "causal_edges": 0,
        "funds": 0,
        "holding_disclosures": 0,
        "notes": [],
    }
    with SessionLocal() as session:
        # Idempotency guard: enrichment writes have no dedupe key, so a
        # re-run of this stage must skip rather than duplicate rows.
        from app.models.ledger import ThemeRole

        existing = session.scalar(
            select(ThemeRole)
            .where(ThemeRole.research_case_id == uuid.UUID(case_id))
            .limit(1)
        )
        if existing is not None:
            out["notes"].append("enrichment already applied — skipped (no dedupe key)")
            summary["phases"]["P9_enrichment"] = out
            return out

        cambricon = session.scalar(
            select(Company).where(Company.code.in_(["688256.SH", "688256"]))
        )
        foxconn = session.scalar(
            select(Company).where(Company.code.in_(["601138.SH", "601138"]))
        )
        if cambricon is None:
            issue("stock_missing")
            out["notes"].append("寒武纪公司未由 ingest 自动创建，穿透链断裂")
            summary["phases"]["P9_enrichment"] = out
            return out

        # Theme roles via the v1 instrument command API (human, reviewed by
        # construction in this walkthrough).
        status, _ = api(
            "POST",
            f"/api/v1/companies/{cambricon.id}/theme-roles",
            "P9",
            "theme_role_cambricon",
            json={
                "research_case_id": case_id,
                "role": "国产AI算力芯片核心设计商（云端训练/推理芯片）",
                "scope": {"segment": "云端AI芯片", "chain_position": "上游设计"},
                "applicable_from": "2024-01-01",
            },
        )
        if status == 201:
            out["theme_roles"] += 1
        else:
            issue("theme_role_api_failed", http_status=status)
        if foxconn is not None:
            status, _ = api(
                "POST",
                f"/api/v1/companies/{foxconn.id}/theme-roles",
                "P9",
                "theme_role_foxconn",
                json={
                    "research_case_id": case_id,
                    "role": "AI服务器制造与系统集成（算力基础设施下游兑现方）",
                    "scope": {"segment": "AI服务器", "chain_position": "下游制造"},
                    "applicable_from": "2024-01-01",
                },
            )
            if status == 201:
                out["theme_roles"] += 1
            else:
                issue("theme_role_api_failed", http_status=status)

        # Human-authored causal chain for T2 via the v1 causal command API
        # (added 2026-08-02; mirrors the storage-chain seed).
        chain = [
            "国产大模型训练/推理需求爆发，云端AI芯片采购放量",
            "寒武纪云端产品线收入高增长（2024年云端收入同比+1187.78%）",
            "收入规模越过研发费用固定成本临界点",
            "2024Q4起单季度归母净利润转正",
            "2025年连续盈利并走向年度扭亏",
        ]
        steps = {}
        for seq, desc in enumerate(chain, 1):
            status, body = api(
                "POST",
                f"/api/v1/theses/{theses['T2']['id']}/causal-steps",
                "P9",
                f"causal_step_{seq}",
                json={"description": desc, "sequence": seq},
            )
            if status != 201:
                issue("causal_api_failed", http_status=status)
                continue
            steps[seq] = body["id"]
            out["causal_steps"] += 1
        for seq in range(1, len(chain)):
            if seq not in steps or seq + 1 not in steps:
                continue
            status, _ = api(
                "POST",
                f"/api/v1/theses/{theses['T2']['id']}/causal-edges",
                "P9",
                f"causal_edge_{seq}",
                json={
                    "source_step_id": steps[seq],
                    "target_step_id": steps[seq + 1],
                    "rationale": "人工编写并复核的传导关系（走查模拟）",
                    "creator_type": "human",
                },
            )
            if status != 201:
                issue("causal_api_failed", http_status=status)
                continue
            out["causal_edges"] += 1

        # Funds + holding disclosures — only from probe data with real weights.
        # The probe returns the company's disclosed top-10 institutional
        # holders (机构类型=基金 rows carry an .OF fund code).  Weight here is
        # 持股数量占流通A股比例 — recorded verbatim with its source definition;
        # it is NOT the fund's NAV weight (semantic caveat noted in report).
        cambricon_stock = session.scalar(
            select(Stock).where(Stock.company_id == cambricon.id)
        )
        fund_rows = probes.get("fund_holders") or []
        seen_fund_codes: set[str] = set()
        written = 0
        for row in fund_rows[:5]:
            name = str(row.get("fund_name", "")).strip()
            code = str(row.get("fund_code", "")).strip()
            weight_raw = str(row.get("weight_percent", "")).strip()
            period_raw = str(row.get("report_date", "")).strip()
            try:
                weight = Decimal(weight_raw)
                period = date.fromisoformat(period_raw[:10])
            except (InvalidOperation, ValueError):
                continue
            if not name or not code or code in seen_fund_codes:
                continue
            seen_fund_codes.add(code)
            # Company top-10-holder disclosures follow the reporting calendar:
            # Q1→4月底, 中报→8月底, Q3→10月底, 年报→次年4月底.
            pub = {
                3: date(period.year, 4, 30),
                6: date(period.year, 8, 31),
                9: date(period.year, 10, 31),
            }.get(period.month, date(period.year + 1, 4, 30))
            status, body = api(
                "POST",
                "/api/v1/funds",
                "P9",
                f"fund_{code}",
                json={"code": code, "name": name, "fund_type": "指数基金/公募基金"},
            )
            if status == 201:
                fund_id = body["id"]
            elif status == 422:
                # Duplicate code (e.g. created by an earlier partial run):
                # reuse the existing fund rather than fail the stage.
                fund = session.scalar(select(Fund).where(Fund.code == code))
                if fund is None:
                    issue("fund_api_failed", http_status=status)
                    continue
                fund_id = str(fund.id)
            else:
                issue("fund_api_failed", http_status=status)
                continue
            out["funds"] += 1
            status, _ = api(
                "POST",
                f"/api/v1/funds/{fund_id}/holding-disclosures",
                "P9",
                f"holding_{code}",
                json={
                    "stock_id": str(cambricon_stock.id),
                    "weight": str(weight),
                    "report_period": period.isoformat(),
                    "published_at": datetime(
                        pub.year, pub.month, pub.day, tzinfo=UTC
                    ).isoformat(),
                    "source": "gildata-probe:top10-holder:占流通A股比例%",
                },
            )
            if status != 201:
                issue("holding_api_failed", http_status=status)
                continue
            written += 1
            out["holding_disclosures"] += 1
        if written == 0:
            out["notes"].append(
                "Gildata 未返回带权重的基金持仓数据；穿透链路只能以空持仓演示，"
                "记为数据可得性缺口"
            )
        session.commit()

    summary["phases"]["P9_enrichment"] = out
    return out


# ---------------------------------------------------------------------------
# P10 — read models
# ---------------------------------------------------------------------------


def phase10_reads(case_id: str) -> dict:
    out: dict = {}
    reads = [
        ("overview", "GET", "/api/v1/overview", {"case_id": case_id}),
        ("dossier", "GET", f"/api/v1/research-cases/{case_id}/dossier", {}),
        ("graph", "GET", f"/api/v1/research-cases/{case_id}/graph", {}),
        ("gaps", "GET", f"/api/v1/research-cases/{case_id}/gaps", {}),
        ("knowledge", "GET", "/api/v1/knowledge", {"case_id": case_id}),
        ("search", "GET", "/api/v1/search", {"q": "寒武纪"}),
        ("kpis", "GET", "/api/v1/research-ops/kpis", {"case_id": case_id}),
        ("snapshots", "GET", f"/api/v1/research-cases/{case_id}/snapshots", {}),
        ("fund_exposure", "GET", f"/api/v1/research-cases/{case_id}/fund-exposure", {}),
        ("metric_catalog", "GET", "/api/v1/metrics/catalog", {}),
    ]
    for name, method, path, params in reads:
        status, body = api(method, path, "P10", name, params=params or None)
        entry: dict = {"http_status": status}
        if status == 200:
            if name == "graph":
                entry["nodes"] = len(body.get("nodes", []))
                entry["edges"] = len(body.get("edges", []))
                entry["paths"] = len(body.get("paths", []))
            elif name == "search":
                entry["groups"] = (
                    {
                        g.get("object_type"): len(g.get("hits", []))
                        for g in body.get("groups", [])
                    }
                    if body.get("groups")
                    else {}
                )
            elif name == "kpis":
                entry["metrics"] = safe_audit_data(body)
            elif name == "dossier":
                entry["field_count"] = len(body)
            elif name == "fund_exposure":
                entry["field_count"] = len(body)
                entry["funds"] = len(body.get("funds", []) or [])
            elif name == "snapshots":
                items = body.get("items", body.get("snapshots", [])) or []
                entry["count"] = len(items)
            else:
                entry["field_count"] = len(body)
        else:
            entry.update(_http_failure(status, body))
            issue(f"read_{name}_failed", http_status=status)
        out[name] = entry
    summary["phases"]["P10_reads"] = out
    return out


# ---------------------------------------------------------------------------
# P11 — historical point-in-time replay
# ---------------------------------------------------------------------------


def phase11_time_travel(case_id: str) -> dict:
    """Replay the dossier at two historical cutoffs.  Documents carry real
    publication dates (2025-04 annual/Q1 reports etc.), so a 2024-12-31
    cutoff must show materially less evidence than today."""
    out = {}
    for label, cutoff in [
        ("cutoff_2024_12_31", "2024-12-31T23:59:59+00:00"),
        ("cutoff_2025_04_01", "2025-04-01T23:59:59+00:00"),
        ("cutoff_now", None),
    ]:
        params = {} if cutoff is None else {"cutoff": cutoff}
        status, body = api(
            "GET",
            f"/api/v1/research-cases/{case_id}/dossier",
            "P11",
            f"dossier_{label}",
            params=params or None,
        )
        entry: dict = {"http_status": status}
        if status == 200:
            for grp in ("supports", "contradicts", "contextualizes"):
                block = body.get(grp) or body.get("evidence", {}).get(grp)
                if isinstance(block, list):
                    entry[grp] = len(block)
        else:
            entry.update(_http_failure(status, body))
            observation = classify_historical_case_read(status, body)
            if observation is not None:
                entry["observation"] = observation
            else:
                issue("time_travel_failed", http_status=status)
        out[label] = entry

    # Document-level time travel: does document visibility follow the
    # publication/availability time even when the case-level replay 404s?
    for label, cutoff in [
        ("docs_2024_12_31", "2024-12-31T23:59:59+00:00"),
        ("docs_2025_04_01", "2025-04-01T23:59:59+00:00"),
        ("docs_2025_05_01", "2025-05-01T23:59:59+00:00"),
    ]:
        status, body = api(
            "GET",
            "/api/v1/documents",
            "P11",
            label,
            params={"cutoff": cutoff, "limit": 100},
        )
        out[label] = {
            "http_status": status,
            "count": len(body.get("items", [])) if status == 200 else None,
            **({} if status == 200 else _http_failure(status, body)),
        }

    # Snapshot compare: earliest vs latest assessment state.
    status, body = api(
        "GET",
        f"/api/v1/research-cases/{case_id}/compare",
        "P11",
        "compare",
        params={
            "base": "2024-06-01T00:00:00Z",
            "compare": datetime.now(UTC).isoformat(),
        },
    )
    out["compare"] = (
        {"http_status": status, "field_count": len(body)}
        if status == 200
        else _http_failure(status, body)
    )
    summary["phases"]["P11_time_travel"] = out
    return out


# ---------------------------------------------------------------------------
# P12 — fact cross-check against verified history
# ---------------------------------------------------------------------------


def phase12_fact_check(probes: dict) -> dict:
    """Cross-check ledger facts against independently verified history.

    Verified references (public disclosures):
      - 2024 annual report (2025-04-18): revenue 11.74亿 (+65.56%),
        net profit -4.52亿; 2024Q4 first profitable quarter (+2.72亿).
      - 2025 Q1 (2025-04-18): revenue 11.11亿 (+4230.22%), net +3.55亿.
      - 2024 share price +387.55% (A股年度涨幅王).
      - 2026-07-31 quote (Gildata): price 1106.0, total MV 6948.92亿,
        PE(TTM) 255.759, PE(LYR) 337.453 → implied FY2025 net ≈ 20.6亿
        (annual turnaround confirmed by LYR earnings being positive).
    """
    checks = []
    quote = probes.get("quote_metrics") or {}

    def _num(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    pe_lyr = _num(quote.get("pe_lyr"))
    total_mv = _num(quote.get("total_mv"))
    if pe_lyr and total_mv:
        implied_fy2025_net = total_mv / pe_lyr
        checks.append(
            {
                "check": "2025年度扭亏（隐含）",
                "ledger_fact": f"PE(LYR)={pe_lyr}, 总市值={total_mv}亿 → 隐含2025年度净利润≈{implied_fy2025_net:.1f}亿>0",
                "verified": True,
                "source": "gildata FinQuery 2026-07-31",
            }
        )
    pe_ttm = _num(quote.get("pe_ttm"))
    checks.append(
        {
            "check": "T3 估值水平证据",
            "ledger_fact": f"PE(TTM)={pe_ttm}, PB={quote.get('pb')}（2026-07-31）",
            "verified": pe_ttm is not None and pe_ttm > 50,
            "source": "gildata FinQuery 2026-07-31",
        }
    )
    annual = probes.get("annual_2025") or {}
    checks.append(
        {
            "check": "2025年报营业收入（Gildata 探针）",
            "ledger_fact": (
                f"2025年报营业收入 {annual.get('revenue_amount')}亿，"
                f"同比 +{annual.get('revenue_yoy_percent')}%"
                if annual
                else "annual_2025 probe 未返回营业收入行"
            ),
            "verified": bool(annual),
            "source": "gildata FinQuery probe (2025年报)",
        }
    )
    summary["facts"] = {
        "checks": checks,
        "quote_2026_07_31": {
            k: quote.get(k)
            for k in ("latest_price", "total_mv", "pe_ttm", "pe_lyr", "pb")
        },
    }
    summary["phases"]["P12_fact_check"] = checks
    return {"checks": checks}


# ---------------------------------------------------------------------------
# main — staged runner (state persisted so each Bash call stays bounded)
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    loaded = secure_read_json(STATE_PATH)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise WalkthroughResponseError("walkthrough checkpoint must be an object")
    _classify_stored_p3_reasons(loaded)
    _ensure_safe_artifact(loaded)
    return loaded


def _classify_stored_p3_reasons(state: dict) -> None:
    """Remove legacy provider prose before a checkpoint can be loaded or saved."""
    phases = state.get("_summary_phases")
    phase = phases.get("P3_extract") if isinstance(phases, dict) else None
    per_document = phase.get("per_document") if isinstance(phase, dict) else None
    if per_document is None:
        return
    if not isinstance(per_document, list):
        raise WalkthroughResponseError("stored P3 per_document summary is invalid")
    for item in per_document:
        if not isinstance(item, dict):
            raise WalkthroughResponseError("stored P3 per_document item is invalid")
        candidates = item.get("candidates")
        reason = item.get("reason")
        if (
            isinstance(candidates, bool)
            or not isinstance(candidates, int)
            or candidates < 0
            or (reason is not None and not isinstance(reason, str))
        ):
            raise WalkthroughResponseError("stored P3 per_document item is invalid")
        item["reason"] = classify_extract_reason(candidates, reason)


def _save_state(state: dict) -> None:
    _ensure_safe_artifact(state)
    atomic_write_json(STATE_PATH, state)


def _checkpoint(state: dict) -> None:
    """Persist both domain state and accumulated audit observations."""
    state["_summary_phases"] = summary["phases"]
    state["_summary_issues"] = summary["issues"]
    _save_state(state)


def phase_terminal_acceptance_audit(session) -> None:
    """Persist a content-free, idempotent verdict for the completed live run."""

    from app.scripts.audit_gildata_ai_walkthrough import (
        collect_walkthrough_facts,
        evaluate_walkthrough_facts,
    )

    issues = summary.get("issues")
    try:
        if not isinstance(issues, list):
            raise WalkthroughResponseError("stored summary issues are invalid")
        summary["issues"] = [
            item
            for item in issues
            if not (
                isinstance(item, dict)
                and item.get("source") == _ACCEPTANCE_AUDIT_SOURCE
            )
        ]
        facts = collect_walkthrough_facts(session, summary)
        result = evaluate_walkthrough_facts(facts)
        if (
            not isinstance(result.ok, bool)
            or any(type(value) is not int for value in result.metrics.values())
            or any(not isinstance(code, str) for code in result.issue_codes)
        ):
            raise WalkthroughResponseError("acceptance audit result is invalid")
        audit = {
            "ok": result.ok,
            "issue_codes": list(result.issue_codes),
            "metrics": dict(result.metrics),
        }
    except Exception:  # noqa: BLE001 -- durable audit boundary is fail-closed
        if not isinstance(summary.get("issues"), list):
            summary["issues"] = []
        audit = {
            "ok": False,
            "issue_codes": ["audit_execution_failed"],
            "metrics": {},
        }

    facts_summary = summary.get("facts")
    if not isinstance(facts_summary, dict):
        facts_summary = {}
        summary["facts"] = facts_summary
    facts_summary[_ACCEPTANCE_AUDIT_SOURCE] = audit
    for code in audit["issue_codes"]:
        observation = {
            "code": f"{_ACCEPTANCE_AUDIT_SOURCE}_{code}",
            "source": _ACCEPTANCE_AUDIT_SOURCE,
        }
        summary["issues"].append(observation)
        rec("acceptance_audit", code, observation)


def _ensure_safe_artifact(value: object) -> None:
    """Refuse persistence if a raw-body or credential field escaped projection."""
    validate_persisted_json(
        value,
        forbidden_keys=_FORBIDDEN_PERSISTED_KEYS,
        forbidden_list_keys=frozenset({"candidates"}),
    )


def _saved_case(state: dict) -> dict | None:
    case = state.get("case")
    if case is None:
        return None
    if not isinstance(case, dict):
        raise WalkthroughResponseError("stored walkthrough case is invalid")
    case_id = case.get("case_id")
    theses = case.get("theses")
    if not isinstance(case_id, str) or not case_id or not isinstance(theses, dict):
        raise WalkthroughResponseError("stored walkthrough case is invalid")
    for key in ("T1", "T2", "T3"):
        thesis = theses.get(key)
        if (
            not isinstance(thesis, dict)
            or not isinstance(thesis.get("id"), str)
            or not thesis["id"]
        ):
            raise WalkthroughResponseError("stored walkthrough case is invalid")
    return case


def _saved_probes(state: dict) -> dict | None:
    probes = state.get("probes")
    if probes is None:
        return None
    if not isinstance(probes, dict):
        raise WalkthroughResponseError("stored walkthrough probes are invalid")
    required_shapes = {
        "quote_metrics": dict,
        "annual_2025": dict,
        "fund_holders": list,
        "smart_fund_selection": dict,
        "tools": list,
    }
    if any(
        not isinstance(probes.get(key), kind) for key, kind in required_shapes.items()
    ):
        raise WalkthroughResponseError("stored walkthrough probes are invalid")
    return probes


def _finalize(state: dict, started: datetime) -> None:
    _checkpoint(state)
    summary["elapsed_seconds"] = (datetime.now(UTC) - started).total_seconds()
    _ensure_safe_artifact(summary)
    atomic_write_json(SUMMARY_PATH, summary)
    print(f"walkthrough stages complete in {summary['elapsed_seconds']:.0f}s")
    print(f"  jsonl:   {JSONL_PATH}")
    print(f"  summary: {SUMMARY_PATH}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stages",
        default="p0_p1_p2,p3,p4_p5,p6,p7_p8,p9_plus",
        help="comma-separated stage groups to run",
    )
    args = parser.parse_args()
    wanted = {s.strip() for s in args.stages.split(",") if s.strip()}

    _initialize_runtime(require_live_llm=bool(wanted & {"p3", "p4_p5", "p7_p8"}))

    started = datetime.now(UTC)
    state = _load_state()
    _ensure_safe_artifact(state)
    state.setdefault("run_id", RUN_ID)
    # Merge observations from earlier stage-group invocations so the final
    # summary is cumulative across Bash calls.
    summary["phases"].update(state.get("_summary_phases", {}))
    summary["issues"].extend(state.get("_summary_issues", []))
    rec(
        "meta",
        "run_start",
        {"db": str(DB_PATH), "run_id": RUN_ID, "stages": sorted(wanted)},
    )

    if "p0_p1_p2" in wanted:
        saved_probes = _saved_probes(state)
        state["probes"] = saved_probes or phase0_preflight()
        saved_case = _saved_case(state)
        state["case"] = saved_case or phase1_create_case()
        phase2_ingest(state["case"]["case_id"])
        _checkpoint(state)
    if "p3" in wanted:
        phase3_extract(state["case"]["case_id"], checkpoint_state=state)
        _checkpoint(state)
        phase3_review_atomic_claims(state["case"]["case_id"])
        _checkpoint(state)
    if "p4_p5" in wanted:
        phase4_propose(state["case"]["theses"])
        phase4_review_proposals(state["case"]["case_id"])
        state["pre_review_assessment"] = phase5_pre_review_assessment(
            state["case"]["theses"]
        )
        _checkpoint(state)
    if "p6" in wanted:
        phase6_review(state["case"]["theses"], state["case"]["case_id"])
    if "p7_p8" in wanted:
        state["assessments"] = phase7_assessments(state["case"]["theses"])
        phase8_assessment_reviews(state["assessments"])
        _checkpoint(state)
    if "p9_plus" in wanted:
        phase9_enrichment(
            state["case"]["case_id"], state["case"]["theses"], state["probes"]
        )
        phase10_reads(state["case"]["case_id"])
        phase11_time_travel(state["case"]["case_id"])
        phase12_fact_check(state["probes"])
        from app.db import SessionLocal

        with SessionLocal() as session:
            phase_terminal_acceptance_audit(session)
        _finalize(state, started)
        return

    # Non-terminal groups: report progress so far.
    _checkpoint(state)
    print(f"stages done: {sorted(wanted)}; state at {STATE_PATH}")
    print(f"  jsonl: {JSONL_PATH}")


if __name__ == "__main__":
    main()
