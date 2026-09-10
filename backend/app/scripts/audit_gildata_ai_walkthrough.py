"""Read-only acceptance audit for the live Gildata/AI walkthrough."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import and_, create_engine, distinct, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.prompts import EXTRACT_PROMPT_VERSION
from app.models.ledger import (
    AIAssessment,
    AIRun,
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.proposals import Proposal, ProposalReviewDecision
from app.models.source_governance import ProviderRecord, SourceContract
from app.models.versions import EvidenceLinkVersion
from app.queries.extraction_runs import extraction_state
from app.scripts.walkthrough_support import (
    WalkthroughConfigurationError,
    WalkthroughResponseError,
    validate_persisted_json,
    validate_walkthrough_run_id,
)
from app.services.content_quality import assess_span_texts
from app.services.source_admission import source_contract_is_active

_GILDATA_REFERENCE = re.compile(
    r"gildata://(research-report|announcement|news|macro-industry)/([0-9a-f]{64})"
)
_C0_CONTROL_AND_SPACE = "".join(chr(codepoint) for codepoint in range(0x21))
_ASCII_TAB_OR_NEWLINE = str.maketrans("", "", "\t\n\r")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXTRACT_SUCCESS_SUMMARY = re.compile(
    r"candidate extraction completed; unique candidates returned (?P<unique>[0-9]+); "
    r"rule-based accepted items (?P<rule>[0-9]+); "
    r"llm returned (?P<returned>[0-9]+), accepted (?P<accepted>[0-9]+), "
    r"rejected (?P<rejected>[0-9]+)"
    r"(?:, offsets repaired (?P<repaired>[0-9]+)"
    r"(?:, llm attempts (?P<attempts>[0-9]+), "
    r"malformed retries (?P<malformed_retries>[0-9]+))?)?; "
    r"source spans (?P<spans>[0-9]+)"
)
_NO_SPANS_SUCCESS_SUMMARY = (
    "skipped: no source spans attached to this version; "
    "llm returned 0, accepted 0, rejected 0"
)
_EXTRACT_PROMPT_VERSIONS_WITHOUT_OFFSET_DIAGNOSTICS = frozenset(
    {"extract-v1", "extract-v2"}
)
_EXTRACT_PROMPT_VERSIONS_WITH_OFFSET_DIAGNOSTICS = frozenset({"extract-v3"})
_EXTRACT_PROMPT_VERSIONS_WITH_RETRY_DIAGNOSTICS = frozenset(
    {"extract-v4", "extract-v5"}
)
_SUPPORTED_EXTRACT_PROMPT_VERSIONS = (
    _EXTRACT_PROMPT_VERSIONS_WITHOUT_OFFSET_DIAGNOSTICS
    | _EXTRACT_PROMPT_VERSIONS_WITH_OFFSET_DIAGNOSTICS
    | _EXTRACT_PROMPT_VERSIONS_WITH_RETRY_DIAGNOSTICS
    | {EXTRACT_PROMPT_VERSION}
)
_FULL_BODY_LOCATOR_KIND = {
    "research-report": "research_report",
    "announcement": "announcement",
    "news": "news",
}
_REPLAY_CREATED_FIELDS = (
    "research_reports",
    "announcements",
    "news",
    "macro_series",
    "spans",
    "valuations_written",
)
_REPLAY_REUSED_FIELDS = (
    "research_reports_reused",
    "announcements_reused",
    "news_reused",
    "macro_series_reused",
    "spans_reused",
)
_GILDATA_CONTRACT_VERSION = "gildata-local-rights-v1"
_GILDATA_RESTRICTIONS = ["仅限当前 Case 研究与人工审核"]
_FORBIDDEN_SUMMARY_KEYS = frozenset(
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
_SUMMARY_ROOT_KEYS = frozenset(
    {"elapsed_seconds", "facts", "issues", "phases", "run_id"}
)
_SUMMARY_PHASE_KEYS = frozenset(
    {
        "P0_preflight",
        "P1_create_case",
        "P2_ingest",
        "P3_extract",
        "P3_5_atomic_claim_review",
        "P4_propose",
        "P4_5_proposal_review",
        "P5_pre_review_assessment_T1",
        "P6_review",
        "P7_assessments",
        "P8_assessment_reviews",
        "P9_enrichment",
        "P10_reads",
        "P11_time_travel",
        "P12_fact_check",
    }
)
_SUMMARY_FACT_KEYS = frozenset({"checks", "gildata_ai_acceptance", "quote_2026_07_31"})
_SUMMARY_ISSUE_KEYS = frozenset({"code", "error_code", "http_status", "source"})
_SUMMARY_P1_KEYS = frozenset({"case_id", "theses"})
_SUMMARY_THESIS_KEYS = frozenset({"T1", "T2", "T3"})
_SUMMARY_P2_KEYS = frozenset(
    {
        "announcements",
        "announcements_reused",
        "case_id",
        "error_code",
        "http_status",
        "macro_series",
        "macro_series_reused",
        "news",
        "news_reused",
        "quote_identity_verified",
        "research_reports",
        "research_reports_reused",
        "round",
        "spans",
        "spans_reused",
        "stock_id",
        "valuations_skipped",
        "valuations_written",
    }
)
_SUMMARY_P3_KEYS = frozenset(
    {
        "attempted_this_run",
        "candidates",
        "claim_types",
        "documents_total",
        "extracted_this_run",
        "pending_after",
        "pending_before",
        "per_document",
    }
)
_SUMMARY_P3_DOCUMENT_KEYS = frozenset(
    {"candidates", "claim_types", "reason", "version_id"}
)
_SUMMARY_ACCEPTANCE_KEYS = frozenset({"issue_codes", "metrics", "ok"})
_SUMMARY_PHASE_OBJECT_KEYS = {
    "P0_preflight": frozenset(
        {
            "annual_2025",
            "fund_holders_rows",
            "quote_metrics",
            "smart_fund_selection",
            "tools",
        }
    ),
    "P1_create_case": _SUMMARY_P1_KEYS,
    "P3_extract": _SUMMARY_P3_KEYS,
    "P3_5_atomic_claim_review": frozenset({"confirmed", "failed", "queued"}),
    "P4_5_proposal_review": frozenset(
        {
            "confirmed",
            "failed",
            "published_evidence_links",
            "queued",
            "rejected_for_source",
        }
    ),
    "P5_pre_review_assessment_T1": frozenset(
        {
            "assessment_id",
            "conclusion",
            "error_code",
            "gap_count",
            "http_status",
            "mode",
            "snapshot_id",
        }
    ),
    "P6_review": frozenset(
        {
            "by_thesis",
            "confirmed",
            "needs_more_evidence",
            "queued",
            "rejected",
            "remaining_after_review",
        }
    ),
    "P9_enrichment": frozenset(
        {
            "causal_edges",
            "causal_steps",
            "funds",
            "holding_disclosures",
            "notes",
            "theme_roles",
        }
    ),
    "P10_reads": frozenset(
        {
            "dossier",
            "fund_exposure",
            "gaps",
            "graph",
            "knowledge",
            "kpis",
            "metric_catalog",
            "overview",
            "search",
            "snapshots",
        }
    ),
    "P11_time_travel": frozenset(
        {
            "compare",
            "cutoff_2024_12_31",
            "cutoff_2025_04_01",
            "cutoff_now",
            "docs_2024_12_31",
            "docs_2025_04_01",
            "docs_2025_05_01",
        }
    ),
}
_SUMMARY_THESIS_PHASE_KEYS = frozenset(
    {"P4_propose", "P7_assessments", "P8_assessment_reviews"}
)
_SUMMARY_THESIS_ITEM_KEYS = {
    "P4_propose": frozenset(
        {"error_code", "http_status", "link_count", "mode", "roles"}
    ),
    "P7_assessments": frozenset(
        {
            "assessment_id",
            "compliance_refused",
            "conclusion",
            "error_code",
            "gap_count",
            "http_status",
            "mode",
            "snapshot_id",
        }
    ),
    "P8_assessment_reviews": frozenset(
        {
            "ai_conclusion",
            "error_code",
            "http_status",
            "human_conclusion",
            "outcome",
            "skipped",
        }
    ),
}
_SUMMARY_READ_ENTRY_KEYS = frozenset(
    {
        "count",
        "edges",
        "error_code",
        "field_count",
        "funds",
        "groups",
        "http_status",
        "metrics",
        "nodes",
        "paths",
    }
)
_SUMMARY_TIME_TRAVEL_ENTRY_KEYS = frozenset(
    {
        "contextualizes",
        "contradicts",
        "count",
        "error_code",
        "field_count",
        "http_status",
        "observation",
        "supports",
    }
)
_SUMMARY_CHECK_KEYS = frozenset({"check", "ledger_fact", "source", "verified"})
_SUMMARY_QUOTE_KEYS = frozenset({"latest_price", "pb", "pe_lyr", "pe_ttm", "total_mv"})
_SUMMARY_COUNTER_MAP_KEYS = frozenset(
    {
        "confirmed",
        "contextualizes",
        "contradicts",
        "needs_more_evidence",
        "rejected",
        "supports",
    }
)
_SAFE_SUMMARY_ATOM = re.compile(r"[A-Za-z0-9_.:/+-]{1,160}")
_SAFE_SUMMARY_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
_ASSESSMENT_CONCLUSIONS = frozenset(
    {"contradicted", "insufficient_evidence", "supported"}
)
_SAFE_EXTRACT_REASONS = frozenset(
    {
        "llm_returned_no_candidates",
        "no_source_spans",
        "reason_reported_with_candidates",
        "zero_candidates_reported",
        "zero_candidates_unspecified",
    }
)
_SAFE_ENRICHMENT_NOTES = frozenset(
    {
        "enrichment already applied — skipped (no dedupe key)",
        "寒武纪公司未由 ingest 自动创建，穿透链断裂",
        (
            "Gildata 未返回带权重的基金持仓数据；穿透链路只能以空持仓演示，"
            "记为数据可得性缺口"
        ),
    }
)
_SAFE_FACT_CHECK_NAMES = frozenset(
    {"2025年度扭亏（隐含）", "2025年报营业收入（Gildata 探针）", "T3 估值水平证据"}
)
_SAFE_FACT_SOURCES = frozenset(
    {"gildata FinQuery 2026-07-31", "gildata FinQuery probe (2025年报)"}
)
MAX_SUMMARY_BYTES = 1_000_000


class WalkthroughAuditInputError(ValueError):
    """A fixed, content-free failure raised for malformed audit input."""


class _RunArtifactMismatchError(WalkthroughAuditInputError):
    """The two named artifacts cannot belong to the declared walkthrough run."""


class _DuplicateJSONKeyError(ValueError):
    pass


@dataclass(frozen=True)
class _SummaryFacts:
    case_id: uuid.UUID
    gildata_rounds: int
    per_document_candidates: tuple[tuple[uuid.UUID, int], ...]
    aggregate_candidates_match: bool
    unresolved_issue_count: int
    replay_created_count: int
    replay_reused_count: int
    quote_identity_verified_rounds: int
    scope_valid: bool


@dataclass(frozen=True)
class _LLMAuditCounts:
    unique: int
    rule: int
    returned: int
    accepted: int
    rejected: int
    spans: int
    repaired: int = 0
    attempts: int | None = None
    malformed_retries: int | None = None


@dataclass(frozen=True)
class _ScopedExtractRuns:
    latest: dict[uuid.UUID, AIRun]
    invalid_document_ids: frozenset[uuid.UUID]
    invalid_unscoped_reference_count: int
    ambiguous_latest: bool


@dataclass(frozen=True)
class WalkthroughAuditFacts:
    """Safe integer facts collected from one run-scoped walkthrough database."""

    gildata_rounds: int
    pending_extractions: int
    summary_candidate_count: int
    persisted_candidate_count: int
    gildata_supersession_count: int
    duplicate_full_body_span_count: int
    invalid_full_body_span_count: int
    gildata_document_count: int
    gildata_contract_count: int
    gildata_provider_record_count: int
    published_gildata_evidence_count: int
    # Defaults preserve the original eleven-field public constructor from the
    # implementation plan. The live collector always supplies every extension.
    llm_accepted_item_count: int = 1
    nonempty_gildata_evidence_assessment_count: int = 1
    unresolved_summary_issue_count: int = 0
    replay_created_count: int = 0
    replay_reused_count: int = 1
    quote_identity_verified_rounds: int = 2
    per_document_candidate_mismatch_count: int = 0
    invalid_extract_run_scope_count: int = 0
    run_scope_valid: bool = True


@dataclass(frozen=True)
class WalkthroughAuditResult:
    """A content-free verdict suitable for durable audit artifacts."""

    ok: bool
    issue_codes: tuple[str, ...]
    metrics: dict[str, int]


def _object(value: object, *, field: str) -> dict:
    if not isinstance(value, dict):
        raise WalkthroughAuditInputError(f"{field} must be an object")
    return value


def _list(value: object, *, field: str) -> list:
    if not isinstance(value, list):
        raise WalkthroughAuditInputError(f"{field} must be a list")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WalkthroughAuditInputError(f"{field} must be a non-negative integer")
    return value


def _uuid(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise WalkthroughAuditInputError(f"{field} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise WalkthroughAuditInputError(f"{field} must be a UUID") from None
    if str(parsed) != value:
        raise WalkthroughAuditInputError(f"{field} must be a canonical UUID")
    return parsed


def _schema_object(value: object, allowed_keys: frozenset[str]) -> dict:
    if type(value) is not dict or not set(value).issubset(allowed_keys):
        raise WalkthroughAuditInputError("summary schema is invalid")
    return value


def _schema_list(value: object) -> list:
    if type(value) is not list:
        raise WalkthroughAuditInputError("summary schema is invalid")
    return value


def _invalid_summary_schema() -> None:
    raise WalkthroughAuditInputError("summary schema is invalid")


def _validate_nonnegative(value: object, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid_summary_schema()


def _validate_http_status(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        _invalid_summary_schema()


def _validate_safe_atom(value: object, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or _SAFE_SUMMARY_ATOM.fullmatch(value) is None:
        _invalid_summary_schema()


def _validate_safe_code(value: object) -> None:
    if not isinstance(value, str) or _SAFE_SUMMARY_CODE.fullmatch(value) is None:
        _invalid_summary_schema()


def _validate_canonical_uuid(value: object, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str):
        _invalid_summary_schema()
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        _invalid_summary_schema()
        return
    if str(parsed) != value:
        _invalid_summary_schema()


def _validate_numeric_string(value: object, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][-+]?[0-9]+)?",
            value,
        )
        is None
    ):
        _invalid_summary_schema()


def _validate_fields(
    value: dict,
    fields: frozenset[str],
    validator,
) -> None:
    for field in fields & value.keys():
        validator(value[field])


def _validate_counter_map(value: object) -> None:
    mapping = _schema_object(value, _SUMMARY_COUNTER_MAP_KEYS)
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in mapping.values()
    ):
        raise WalkthroughAuditInputError("summary schema is invalid")


def _validate_named_counter_map(value: object) -> None:
    if type(value) is not dict or any(
        type(key) is not str
        or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) is None
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        for key, count in value.items()
    ):
        raise WalkthroughAuditInputError("summary schema is invalid")


def _validate_dynamic_projection(value: object) -> None:
    """Accept only the atom/counter projection emitted by ``safe_audit_data``."""

    value_type = type(value)
    if value is None or value_type is bool or value_type is int:
        return
    if value_type is float:
        return  # ``validate_persisted_json`` already rejects non-finite values.
    if value_type is str:
        if re.fullmatch(r"[A-Za-z0-9_.:/+-]{1,160}", value) is None:
            raise WalkthroughAuditInputError("summary schema is invalid")
        return
    if value_type is list:
        for item in value:
            _validate_dynamic_projection(item)
        return
    if value_type is not dict:
        raise WalkthroughAuditInputError("summary schema is invalid")
    for key, item in value.items():
        if (
            type(key) is not str
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", key) is None
        ):
            raise WalkthroughAuditInputError("summary schema is invalid")
        _validate_dynamic_projection(item)


def _validate_check_list(value: object) -> None:
    for item in _schema_list(value):
        check = _schema_object(item, _SUMMARY_CHECK_KEYS)
        if set(check) != _SUMMARY_CHECK_KEYS:
            _invalid_summary_schema()
        if check["check"] not in _SAFE_FACT_CHECK_NAMES:
            _invalid_summary_schema()
        if check["source"] not in _SAFE_FACT_SOURCES:
            _invalid_summary_schema()
        ledger_fact = check["ledger_fact"]
        if not isinstance(ledger_fact, str) or not 1 <= len(ledger_fact) <= 500:
            _invalid_summary_schema()
        if type(check["verified"]) is not bool:
            _invalid_summary_schema()


def _validate_summary_schema(summary: object) -> None:
    """Validate the runner's closed summary projection before using it.

    The live file contains a few intentionally constructed human-readable fact
    checks.  Those prose-bearing keys are allowed only at their exact P12/facts
    paths; provider-controlled or newly added fields fail closed.
    """

    root = _schema_object(summary, _SUMMARY_ROOT_KEYS)
    try:
        validate_walkthrough_run_id(root.get("run_id"))
    except WalkthroughConfigurationError:
        _invalid_summary_schema()
    if "elapsed_seconds" in root:
        elapsed = root["elapsed_seconds"]
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            _invalid_summary_schema()
    phases = _schema_object(root.get("phases"), _SUMMARY_PHASE_KEYS)
    issues = _schema_list(root.get("issues"))
    for issue in issues:
        item = _schema_object(issue, _SUMMARY_ISSUE_KEYS)
        if "code" not in item:
            _invalid_summary_schema()
        _validate_safe_code(item["code"])
        _validate_fields(item, frozenset({"error_code", "source"}), _validate_safe_code)
        if "http_status" in item:
            _validate_http_status(item["http_status"])

    if "facts" in root:
        facts_value = root["facts"]
        facts = _schema_object(facts_value, _SUMMARY_FACT_KEYS)
        if "checks" in facts:
            _validate_check_list(facts["checks"])
        if "quote_2026_07_31" in facts:
            _schema_object(facts["quote_2026_07_31"], _SUMMARY_QUOTE_KEYS)
        if "gildata_ai_acceptance" in facts:
            acceptance = _schema_object(
                facts["gildata_ai_acceptance"], _SUMMARY_ACCEPTANCE_KEYS
            )
            if set(acceptance) != _SUMMARY_ACCEPTANCE_KEYS:
                _invalid_summary_schema()
            issue_codes = _schema_list(acceptance.get("issue_codes"))
            if any(
                type(code) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) is None
                for code in issue_codes
            ):
                raise WalkthroughAuditInputError("summary schema is invalid")
            metrics = acceptance.get("metrics")
            if type(metrics) is not dict or any(
                type(key) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) is None
                or type(value) is not int
                for key, value in metrics.items()
            ):
                raise WalkthroughAuditInputError("summary schema is invalid")
            if type(acceptance.get("ok")) is not bool:
                raise WalkthroughAuditInputError("summary schema is invalid")
        if "quote_2026_07_31" in facts:
            for value in facts["quote_2026_07_31"].values():
                _validate_numeric_string(value, allow_none=True)

    for phase_name, phase_value in phases.items():
        if phase_name == "P2_ingest":
            for item in _schema_list(phase_value):
                ingest = _schema_object(item, _SUMMARY_P2_KEYS)
                _validate_fields(
                    ingest,
                    frozenset(
                        {
                            "announcements",
                            "announcements_reused",
                            "macro_series",
                            "macro_series_reused",
                            "news",
                            "news_reused",
                            "research_reports",
                            "research_reports_reused",
                            "round",
                            "spans",
                            "spans_reused",
                            "valuations_skipped",
                            "valuations_written",
                        }
                    ),
                    _validate_nonnegative,
                )
                if "http_status" in ingest:
                    _validate_http_status(ingest["http_status"])
                if "error_code" in ingest:
                    _validate_safe_code(ingest["error_code"])
                _validate_fields(
                    ingest,
                    frozenset({"case_id", "stock_id"}),
                    lambda value: _validate_canonical_uuid(value, allow_none=True),
                )
                if (
                    "quote_identity_verified" in ingest
                    and type(ingest["quote_identity_verified"]) is not bool
                ):
                    _invalid_summary_schema()
            continue
        if phase_name == "P12_fact_check":
            _validate_check_list(phase_value)
            continue
        if phase_name in _SUMMARY_THESIS_PHASE_KEYS:
            theses = _schema_object(phase_value, _SUMMARY_THESIS_KEYS)
            for item in theses.values():
                thesis_item = _schema_object(
                    item, _SUMMARY_THESIS_ITEM_KEYS[phase_name]
                )
                if "roles" in thesis_item:
                    _validate_named_counter_map(thesis_item["roles"])
                _validate_fields(
                    thesis_item,
                    frozenset({"gap_count", "link_count"}),
                    _validate_nonnegative,
                )
                if "http_status" in thesis_item:
                    _validate_http_status(thesis_item["http_status"])
                if "error_code" in thesis_item:
                    _validate_safe_code(thesis_item["error_code"])
                _validate_fields(
                    thesis_item,
                    frozenset({"assessment_id", "snapshot_id"}),
                    _validate_canonical_uuid,
                )
                if "mode" in thesis_item:
                    _validate_safe_atom(thesis_item["mode"])
                _validate_fields(
                    thesis_item,
                    frozenset({"ai_conclusion", "conclusion"}),
                    lambda value: (
                        None
                        if value in _ASSESSMENT_CONCLUSIONS
                        else _invalid_summary_schema()
                    ),
                )
                if "human_conclusion" in thesis_item and (
                    thesis_item["human_conclusion"] is not None
                    and thesis_item["human_conclusion"] not in _ASSESSMENT_CONCLUSIONS
                ):
                    _invalid_summary_schema()
                if "outcome" in thesis_item and thesis_item["outcome"] not in {
                    "confirmed",
                    "modified",
                    "rejected",
                }:
                    _invalid_summary_schema()
                if (
                    "compliance_refused" in thesis_item
                    and type(thesis_item["compliance_refused"]) is not bool
                ):
                    _invalid_summary_schema()
                if "skipped" in thesis_item and thesis_item["skipped"] != (
                    "no assessment (refused or failed)"
                ):
                    _invalid_summary_schema()
            continue

        phase = _schema_object(phase_value, _SUMMARY_PHASE_OBJECT_KEYS[phase_name])
        if phase_name == "P0_preflight":
            if "tools" in phase:
                for tool in _schema_list(phase["tools"]):
                    _validate_safe_atom(tool)
            if "quote_metrics" in phase:
                quote_metrics = _schema_object(
                    phase["quote_metrics"], _SUMMARY_QUOTE_KEYS
                )
                for value in quote_metrics.values():
                    _validate_numeric_string(value)
            if "annual_2025" in phase:
                annual = _schema_object(
                    phase["annual_2025"],
                    frozenset({"revenue_amount", "revenue_yoy_percent"}),
                )
                for value in annual.values():
                    _validate_numeric_string(value)
            if "fund_holders_rows" in phase:
                _validate_nonnegative(phase["fund_holders_rows"])
            if "smart_fund_selection" in phase:
                smart = _schema_object(
                    phase["smart_fund_selection"],
                    frozenset({"response_chars", "status"}),
                )
                if "response_chars" in smart:
                    _validate_nonnegative(smart["response_chars"])
                if "status" in smart and smart["status"] not in {
                    "failed",
                    "succeeded",
                }:
                    _invalid_summary_schema()
        elif phase_name == "P1_create_case" and "theses" in phase:
            if "case_id" in phase:
                _validate_canonical_uuid(phase["case_id"])
            theses = _schema_object(phase["theses"], _SUMMARY_THESIS_KEYS)
            for item in theses.values():
                thesis = _schema_object(item, frozenset({"id"}))
                if set(thesis) != {"id"}:
                    _invalid_summary_schema()
                _validate_canonical_uuid(thesis["id"])
        elif phase_name == "P1_create_case":
            if "case_id" in phase:
                _validate_canonical_uuid(phase["case_id"])
        elif phase_name == "P3_extract":
            _validate_fields(
                phase,
                frozenset(
                    {
                        "attempted_this_run",
                        "candidates",
                        "documents_total",
                        "extracted_this_run",
                        "pending_before",
                    }
                ),
                _validate_nonnegative,
            )
            if "pending_after" in phase:
                _validate_nonnegative(phase["pending_after"], allow_none=True)
            if "claim_types" in phase:
                _validate_named_counter_map(phase["claim_types"])
            for item in _schema_list(phase.get("per_document")):
                document = _schema_object(item, _SUMMARY_P3_DOCUMENT_KEYS)
                if "version_id" in document:
                    _validate_canonical_uuid(document["version_id"])
                if "candidates" in document:
                    _validate_nonnegative(document["candidates"])
                if "claim_types" in document:
                    _validate_named_counter_map(document["claim_types"])
                if "reason" in document and (
                    document["reason"] is not None
                    and document["reason"] not in _SAFE_EXTRACT_REASONS
                ):
                    _invalid_summary_schema()
        elif phase_name in {
            "P3_5_atomic_claim_review",
            "P4_5_proposal_review",
        }:
            for value in phase.values():
                _validate_nonnegative(value)
        elif phase_name == "P5_pre_review_assessment_T1":
            _validate_fields(
                phase,
                frozenset({"assessment_id", "snapshot_id"}),
                _validate_canonical_uuid,
            )
            if "conclusion" in phase and phase["conclusion"] not in (
                _ASSESSMENT_CONCLUSIONS
            ):
                _invalid_summary_schema()
            if "gap_count" in phase:
                _validate_nonnegative(phase["gap_count"])
            if "mode" in phase:
                _validate_safe_atom(phase["mode"])
            if "http_status" in phase:
                _validate_http_status(phase["http_status"])
            if "error_code" in phase:
                _validate_safe_code(phase["error_code"])
        elif phase_name == "P6_review" and "by_thesis" in phase:
            _validate_fields(
                phase,
                frozenset(
                    {
                        "confirmed",
                        "needs_more_evidence",
                        "queued",
                        "rejected",
                        "remaining_after_review",
                    }
                ),
                _validate_nonnegative,
            )
            by_thesis = _schema_object(
                phase["by_thesis"], _SUMMARY_THESIS_KEYS | {"unknown"}
            )
            for counts in by_thesis.values():
                _validate_counter_map(counts)
        elif phase_name == "P6_review":
            for value in phase.values():
                _validate_nonnegative(value)
        elif phase_name == "P9_enrichment":
            _validate_fields(
                phase,
                frozenset(
                    {
                        "causal_edges",
                        "causal_steps",
                        "funds",
                        "holding_disclosures",
                        "theme_roles",
                    }
                ),
                _validate_nonnegative,
            )
            if "notes" in phase:
                notes = _schema_list(phase["notes"])
                if any(note not in _SAFE_ENRICHMENT_NOTES for note in notes):
                    _invalid_summary_schema()
        elif phase_name == "P10_reads":
            for entry in phase.values():
                read_entry = _schema_object(entry, _SUMMARY_READ_ENTRY_KEYS)
                _validate_fields(
                    read_entry,
                    frozenset(
                        {
                            "count",
                            "edges",
                            "field_count",
                            "funds",
                            "nodes",
                            "paths",
                        }
                    ),
                    _validate_nonnegative,
                )
                if "http_status" in read_entry:
                    _validate_http_status(read_entry["http_status"])
                if "error_code" in read_entry:
                    _validate_safe_code(read_entry["error_code"])
                if "groups" in read_entry:
                    _validate_named_counter_map(read_entry["groups"])
                if "metrics" in read_entry:
                    _validate_dynamic_projection(read_entry["metrics"])
        elif phase_name == "P11_time_travel":
            for entry in phase.values():
                time_entry = _schema_object(entry, _SUMMARY_TIME_TRAVEL_ENTRY_KEYS)
                _validate_fields(
                    time_entry,
                    frozenset(
                        {
                            "contextualizes",
                            "contradicts",
                            "count",
                            "field_count",
                            "supports",
                        }
                    ),
                    _validate_nonnegative,
                )
                if "http_status" in time_entry:
                    _validate_http_status(time_entry["http_status"])
                if "error_code" in time_entry:
                    _validate_safe_code(time_entry["error_code"])
                if (
                    "observation" in time_entry
                    and time_entry["observation"] != "case_not_created_at_cutoff"
                ):
                    _invalid_summary_schema()


def _collect_summary_facts(summary: object) -> _SummaryFacts:
    _validate_summary_schema(summary)
    try:
        validate_persisted_json(
            summary,
            forbidden_keys=_FORBIDDEN_SUMMARY_KEYS,
            forbidden_list_keys=frozenset({"candidates"}),
        )
    except WalkthroughResponseError:
        raise WalkthroughAuditInputError("summary contains unsafe data") from None
    root = _object(summary, field="summary")
    phases = _object(root.get("phases"), field="summary phases")
    phase1 = _object(phases.get("P1_create_case"), field="P1 summary")
    case_id = _uuid(phase1.get("case_id"), field="P1 case_id")
    issues = _list(root.get("issues"), field="summary issues")

    rounds = _list(phases.get("P2_ingest"), field="P2 summary")
    round_numbers: list[int] = []
    successful_rounds = 0
    replay_created = 0
    replay_reused = 0
    quote_verified = 0
    scope_valid = True
    for item in rounds:
        item = _object(item, field="P2 item")
        round_number = _nonnegative_int(item.get("round"), field="P2 round")
        round_numbers.append(round_number)
        status = _nonnegative_int(item.get("http_status"), field="P2 status")
        if status == 201:
            successful_rounds += 1
        item_case_id = _uuid(item.get("case_id"), field="P2 case_id")
        if item_case_id != case_id:
            scope_valid = False
        for field in _REPLAY_CREATED_FIELDS:
            replay_created += _nonnegative_int(item.get(field), field=f"P2 {field}")
        for field in _REPLAY_REUSED_FIELDS:
            replay_reused += _nonnegative_int(item.get(field), field=f"P2 {field}")
        quote_attestation = item.get("quote_identity_verified")
        if not isinstance(quote_attestation, bool):
            raise WalkthroughAuditInputError(
                "P2 quote identity attestation must be boolean"
            )
        quote_verified += int(quote_attestation)
    if round_numbers != [1, 2]:
        scope_valid = False

    phase3 = _object(phases.get("P3_extract"), field="P3 summary")
    aggregate_candidates = _nonnegative_int(
        phase3.get("candidates"), field="P3 candidates"
    )
    per_document = _list(phase3.get("per_document"), field="P3 per_document")
    parsed_per_document: list[tuple[uuid.UUID, int]] = []
    seen_ids: set[uuid.UUID] = set()
    per_document_total = 0
    for item in per_document:
        item = _object(item, field="P3 per_document item")
        version_id = _uuid(item.get("version_id"), field="P3 version_id")
        count = _nonnegative_int(item.get("candidates"), field="P3 candidate count")
        if version_id in seen_ids:
            scope_valid = False
        seen_ids.add(version_id)
        parsed_per_document.append((version_id, count))
        per_document_total += count

    return _SummaryFacts(
        case_id=case_id,
        gildata_rounds=successful_rounds,
        per_document_candidates=tuple(parsed_per_document),
        aggregate_candidates_match=aggregate_candidates == per_document_total,
        unresolved_issue_count=len(issues),
        replay_created_count=replay_created,
        replay_reused_count=replay_reused,
        quote_identity_verified_rounds=quote_verified,
        scope_valid=scope_valid,
    )


def _looks_like_gildata(document: DocumentVersion) -> bool:
    source_url = document.source_url
    parser_version = document.parser_version
    has_gildata_scheme = False
    if isinstance(source_url, str):
        candidate = source_url.strip(_C0_CONTROL_AND_SPACE)
        prefix_start = 0
        while prefix_start < len(candidate):
            character = candidate[prefix_start]
            if not (
                character == "\\"
                or character.isspace()
                or ord(character) < 32
                or 127 <= ord(character) <= 159
            ):
                break
            prefix_start += 1
        scheme, separator, _ = candidate[prefix_start:].partition(":")
        has_gildata_scheme = bool(
            separator
            and scheme.translate(_ASCII_TAB_OR_NEWLINE).casefold() == "gildata"
        )
    return has_gildata_scheme or (
        isinstance(parser_version, str)
        and parser_version.casefold().startswith("gildata-mcp-")
    )


def _strict_gildata_identity(
    document: DocumentVersion,
) -> tuple[str, str] | None:
    source_url = document.source_url
    match = (
        _GILDATA_REFERENCE.fullmatch(source_url)
        if isinstance(source_url, str)
        else None
    )
    if match is None:
        return None
    source_kind, digest = match.groups()
    if not (
        document.content_sha256 == digest
        and document.parser_version == "gildata-mcp-1"
        and document.natural_key == f"gildata:{digest[:23]}"
        and document.parse_state == "success"
        and document.source_authority == "licensed_research"
        and document.supplements_document_version_id is None
        and document.claimed_page_reference is None
    ):
        return None
    return source_kind, digest


def _valid_contract(
    contract: SourceContract,
    *,
    document: DocumentVersion,
    source_kind: str,
    digest: str,
    tenant_id: str,
    at: datetime,
) -> bool:
    metadata = contract.intake_metadata
    if not isinstance(metadata, dict):
        return False
    request_scope = metadata.get("request_scope")
    if not isinstance(request_scope, dict):
        return False
    query_sha256 = request_scope.get("query_sha256")
    expected_record_id = f"{source_kind}:{digest}"
    return bool(
        contract.document_version_id == document.id
        and contract.source_type == "licensed_provider"
        and contract.research_source_type == "licensed_provider"
        and isinstance(contract.provider_or_tenant, str)
        and contract.provider_or_tenant.casefold() == "gildata"
        and contract.allow_ai_processing is True
        and contract.allow_display is True
        and contract.allow_export is False
        and contract.allow_api is False
        and source_contract_is_active(contract, at=at)
        and contract.contract_version == _GILDATA_CONTRACT_VERSION
        and contract.downstream_restrictions == _GILDATA_RESTRICTIONS
        and contract.declared_by == f"tenant:{tenant_id}"
        and metadata.get("research_source_type") == "licensed_provider"
        and isinstance(metadata.get("provider_name"), str)
        and metadata["provider_name"].casefold() == "gildata"
        and metadata.get("provider_record_id") == expected_record_id
        and metadata.get("retrieval_reference") == document.source_url
        and request_scope.get("source_kind") == source_kind
        and isinstance(query_sha256, str)
        and _HEX_SHA256.fullmatch(query_sha256) is not None
        and metadata.get("contract_version") == _GILDATA_CONTRACT_VERSION
        and metadata.get("_source_contract_declared_url") == document.source_url
        and metadata.get("_source_contract_declared_url_is_explicit") is True
        and metadata.get("downstream_restrictions") == _GILDATA_RESTRICTIONS
        and metadata.get("permissions")
        == {
            "ai_processing": True,
            "display": True,
            "export": False,
            "api": False,
        }
    )


def _valid_provider_record(
    record: ProviderRecord,
    *,
    contract: SourceContract | None,
    document: DocumentVersion,
    source_kind: str,
    digest: str,
) -> bool:
    scope = record.request_scope
    if not isinstance(scope, dict):
        return False
    query_sha256 = scope.get("query_sha256")
    contract_scope = (
        contract.intake_metadata.get("request_scope")
        if contract is not None and isinstance(contract.intake_metadata, dict)
        else None
    )
    return bool(
        record.document_version_id == document.id
        and isinstance(record.provider_name, str)
        and record.provider_name.casefold() == "gildata"
        and record.provider_record_id == f"{source_kind}:{digest}"
        and record.retrieval_reference == document.source_url
        and record.content_sha256 == digest
        and record.contract_version == _GILDATA_CONTRACT_VERSION
        and scope.get("source_kind") == source_kind
        and isinstance(query_sha256, str)
        and _HEX_SHA256.fullmatch(query_sha256) is not None
        and contract_scope == scope
    )


def _formal_gildata_evidence_ids_by_thesis(
    session: Session,
    *,
    case_id: uuid.UUID,
    document_ids: set[uuid.UUID],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    if not document_ids:
        return {}
    statement = (
        select(
            EvidenceLinkVersion.thesis_id,
            EvidenceLinkVersion.evidence_link_id,
        )
        .distinct()
        .join(
            EvidenceLink,
            and_(
                EvidenceLink.id == EvidenceLinkVersion.evidence_link_id,
                EvidenceLink.thesis_id == EvidenceLinkVersion.thesis_id,
                EvidenceLink.source_statement_id
                == EvidenceLinkVersion.source_statement_id,
            ),
        )
        .join(Proposal, Proposal.id == EvidenceLinkVersion.proposal_id)
        .join(
            ProposalReviewDecision,
            and_(
                ProposalReviewDecision.id == EvidenceLinkVersion.review_decision_id,
                ProposalReviewDecision.proposal_id == Proposal.id,
            ),
        )
        .join(Thesis, Thesis.id == EvidenceLinkVersion.thesis_id)
        .join(
            SourceStatement,
            SourceStatement.id == EvidenceLinkVersion.source_statement_id,
        )
        .join(
            AtomicClaimCandidate,
            AtomicClaimCandidate.id == SourceStatement.atomic_claim_candidate_id,
        )
        .join(
            AtomicClaimReview,
            and_(
                AtomicClaimReview.atomic_claim_candidate_id == AtomicClaimCandidate.id,
                AtomicClaimReview.published_source_statement_id == SourceStatement.id,
            ),
        )
        .join(
            SourceSpan,
            and_(
                SourceSpan.id == SourceStatement.source_span_id,
                SourceSpan.id == AtomicClaimCandidate.source_span_id,
            ),
        )
        .where(
            Thesis.research_case_id == case_id,
            Proposal.research_case_id == case_id,
            Proposal.kind == "evidence_link",
            Proposal.status == "decided",
            Proposal.proposed_by_type == "ai",
            ProposalReviewDecision.outcome.in_(("confirmed", "modified")),
            AtomicClaimReview.outcome.in_(("confirmed", "modified")),
            EvidenceLink.creator_type == "human",
            EvidenceLink.review_state == "reviewed",
            SourceSpan.document_version_id.in_(document_ids),
        )
    )
    evidence_by_thesis: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for thesis_id, evidence_link_id in session.execute(statement):
        evidence_by_thesis[thesis_id].add(evidence_link_id)
    return dict(evidence_by_thesis)


def _latest_scoped_extract_runs(
    session: Session,
    document_ids: set[uuid.UUID],
) -> _ScopedExtractRuns:
    """Return deterministic latest runs and flag a tied latest timestamp.

    ``latest_extract_runs`` intentionally models the application watermark and
    only orders by ``started_at``.  An acceptance audit must be stricter: when
    two runs for one document share the latest timestamp, choosing either row
    would depend on database row order.  We still choose deterministically by
    UUID so the remaining metrics are stable, while returning an ambiguity flag
    that makes the overall run-scope gate fail closed.
    """

    # Do not pre-filter JSON in SQL: malformed, non-canonical, and out-of-case
    # document references are themselves audit evidence and must fail closed.
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    by_document: dict[uuid.UUID, list[AIRun]] = defaultdict(list)
    invalid_document_ids: set[uuid.UUID] = set()
    invalid_unscoped_reference_count = 0
    for run in runs:
        input_ref = run.input_ref
        version_id = (
            input_ref.get("document_version_id")
            if isinstance(input_ref, dict)
            else None
        )
        if not isinstance(version_id, str):
            invalid_unscoped_reference_count += 1
            continue
        try:
            parsed_version_id = uuid.UUID(version_id)
        except ValueError:
            invalid_unscoped_reference_count += 1
            continue
        if parsed_version_id not in document_ids:
            invalid_unscoped_reference_count += 1
            continue
        if str(parsed_version_id) != version_id or set(input_ref) != {
            "document_version_id",
            "span_ids",
        }:
            invalid_document_ids.add(parsed_version_id)
        # A non-canonical equivalent remains in the latest-run competition so
        # an older canonical row cannot hide a later polluted update.
        by_document[parsed_version_id].append(run)

    latest: dict[uuid.UUID, AIRun] = {}
    ambiguous = False
    for document_id, document_runs in by_document.items():
        normalized_times = {
            run.id: (
                run.started_at.replace(tzinfo=UTC)
                if run.started_at.tzinfo is None
                else run.started_at.astimezone(UTC)
            )
            for run in document_runs
        }
        latest_started_at = max(normalized_times.values())
        latest_at_timestamp = [
            run
            for run in document_runs
            if normalized_times[run.id] == latest_started_at
        ]
        if len(latest_at_timestamp) > 1:
            ambiguous = True
        latest[document_id] = max(latest_at_timestamp, key=lambda run: str(run.id))
    return _ScopedExtractRuns(
        latest=latest,
        invalid_document_ids=frozenset(invalid_document_ids),
        invalid_unscoped_reference_count=invalid_unscoped_reference_count,
        ambiguous_latest=ambiguous,
    )


def _parse_extract_success_summary(
    output_summary: object,
    *,
    expected_span_count: int,
    prompt_version: object | None = None,
) -> _LLMAuditCounts | None:
    """Parse version-compatible durable success summaries from the extractor."""

    if (
        prompt_version is not None
        and prompt_version not in _SUPPORTED_EXTRACT_PROMPT_VERSIONS
    ):
        return None
    if output_summary == _NO_SPANS_SUCCESS_SUMMARY:
        return _LLMAuditCounts(0, 0, 0, 0, 0, 0) if expected_span_count == 0 else None
    if not isinstance(output_summary, str) or len(output_summary) > 1_000:
        return None
    match = _EXTRACT_SUCCESS_SUMMARY.fullmatch(output_summary)
    if match is None:
        return None
    try:
        has_repaired_diagnostics = match.group("repaired") is not None
        counts = _LLMAuditCounts(
            unique=int(match.group("unique")),
            rule=int(match.group("rule")),
            returned=int(match.group("returned")),
            accepted=int(match.group("accepted")),
            rejected=int(match.group("rejected")),
            spans=int(match.group("spans")),
            repaired=int(match.group("repaired") or 0),
            attempts=(
                int(match.group("attempts"))
                if match.group("attempts") is not None
                else None
            ),
            malformed_retries=(
                int(match.group("malformed_retries"))
                if match.group("malformed_retries") is not None
                else None
            ),
        )
    except (ValueError, OverflowError):
        return None
    version_diagnostics_valid = True
    if prompt_version in _EXTRACT_PROMPT_VERSIONS_WITHOUT_OFFSET_DIAGNOSTICS:
        version_diagnostics_valid = (
            not has_repaired_diagnostics and counts.attempts is None
        )
    elif prompt_version in _EXTRACT_PROMPT_VERSIONS_WITH_OFFSET_DIAGNOSTICS:
        version_diagnostics_valid = (
            has_repaired_diagnostics and counts.attempts is None
        )
    elif prompt_version in (
        _EXTRACT_PROMPT_VERSIONS_WITH_RETRY_DIAGNOSTICS | {EXTRACT_PROMPT_VERSION}
    ):
        version_diagnostics_valid = (
            has_repaired_diagnostics and counts.attempts is not None
        )
    retry_diagnostics_valid = (
        counts.attempts is None
        and counts.malformed_retries is None
    ) or (
        counts.attempts is not None
        and counts.malformed_retries is not None
        and counts.malformed_retries <= max(counts.attempts - 1, 0)
        and (
            counts.attempts > 0
            or (
                counts.returned == 0
                and counts.accepted == 0
                and counts.rejected == 0
                and counts.repaired == 0
                and counts.malformed_retries == 0
                and counts.rule >= counts.spans
            )
        )
    )
    if not (
        counts.returned == counts.accepted + counts.rejected
        and counts.unique <= counts.rule + counts.accepted
        and (counts.unique == 0) == (counts.rule + counts.accepted == 0)
        and counts.repaired <= counts.accepted
        and counts.spans == expected_span_count
        and retry_diagnostics_valid
        and version_diagnostics_valid
    ):
        return None
    return counts


def _extract_run_matches_document_spans(
    run: AIRun,
    *,
    document_id: uuid.UUID,
    expected_span_ids: set[uuid.UUID],
) -> bool:
    input_ref = run.input_ref
    if not isinstance(input_ref, dict):
        return False
    if set(input_ref) != {"document_version_id", "span_ids"}:
        return False
    if input_ref.get("document_version_id") != str(document_id):
        return False
    span_ids = input_ref.get("span_ids")
    if not isinstance(span_ids, list):
        return False
    parsed_span_ids: list[uuid.UUID] = []
    for value in span_ids:
        if not isinstance(value, str):
            return False
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            return False
        if str(parsed) != value:
            return False
        parsed_span_ids.append(parsed)
    return (
        len(parsed_span_ids) == len(set(parsed_span_ids))
        and set(parsed_span_ids) == expected_span_ids
    )


def collect_walkthrough_facts(
    session: Session, summary: object
) -> WalkthroughAuditFacts:
    """Collect facts from one run-scoped database and sanitized summary."""

    summary_facts = _collect_summary_facts(summary)
    case_id = summary_facts.case_id
    run_scope_valid = summary_facts.scope_valid

    all_case_ids = list(session.scalars(select(ResearchCase.id)))
    if len(all_case_ids) != 1 or all_case_ids[0] != case_id:
        run_scope_valid = False
    admissions = list(
        session.scalars(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == case_id
            )
        )
    )
    tenant_id = admissions[0].tenant_id if len(admissions) == 1 else None
    if not isinstance(tenant_id, str) or not tenant_id:
        tenant_id = None
        run_scope_valid = False

    case_links = list(
        session.scalars(
            select(CaseDocumentVersion).where(
                CaseDocumentVersion.research_case_id == case_id
            )
        )
    )
    case_document_ids = {link.document_version_id for link in case_links}
    if len(case_links) != len(case_document_ids):
        run_scope_valid = False
    if (
        len(admissions) == 1
        and admissions[0].initial_document_version_id not in case_document_ids
    ):
        run_scope_valid = False

    all_documents = list(session.scalars(select(DocumentVersion)))
    documents_by_id = {document.id: document for document in all_documents}
    if not case_document_ids.issubset(documents_by_id):
        run_scope_valid = False

    gildata_documents: dict[uuid.UUID, tuple[DocumentVersion, str, str]] = {}
    for document in all_documents:
        identity = _strict_gildata_identity(document)
        suspicious = _looks_like_gildata(document)
        if identity is None:
            if suspicious:
                run_scope_valid = False
            continue
        if document.id not in case_document_ids:
            run_scope_valid = False
            continue
        source_kind, digest = identity
        gildata_documents[document.id] = (document, source_kind, digest)

    gildata_document_ids = set(gildata_documents)
    per_document_ids = {
        version_id for version_id, _ in summary_facts.per_document_candidates
    }
    if not per_document_ids.issubset(case_document_ids):
        run_scope_valid = False
    if not summary_facts.aggregate_candidates_match:
        run_scope_valid = False
    summary_candidate_count = sum(
        count
        for version_id, count in summary_facts.per_document_candidates
        if version_id in gildata_document_ids
    )

    spans_by_document: dict[uuid.UUID, list[SourceSpan]] = defaultdict(list)
    if case_document_ids:
        for span in session.scalars(
            select(SourceSpan).where(
                SourceSpan.document_version_id.in_(case_document_ids)
            )
        ):
            spans_by_document[span.document_version_id].append(span)

    quality_by_document = {
        document_id: assess_span_texts(
            [span.verbatim_text for span in spans_by_document[document_id]]
        )[0]
        for document_id in case_document_ids
    }
    eligible_document_ids = {
        document_id
        for document_id, quality in quality_by_document.items()
        if quality != "degenerate"
    }

    case_candidates_by_document: dict[uuid.UUID, int] = {}
    if case_document_ids:
        case_candidates_by_document = {
            document_id: int(count)
            for document_id, count in session.execute(
                select(
                    SourceSpan.document_version_id,
                    func.count(distinct(AtomicClaimCandidate.id)),
                )
                .join(
                    SourceSpan,
                    SourceSpan.id == AtomicClaimCandidate.source_span_id,
                )
                .where(SourceSpan.document_version_id.in_(case_document_ids))
                .group_by(SourceSpan.document_version_id)
            )
        }
    persisted_candidate_count = sum(
        count
        for document_id, count in case_candidates_by_document.items()
        if document_id in gildata_document_ids
    )
    summary_candidates_by_document: dict[uuid.UUID, int] = defaultdict(int)
    for document_id, count in summary_facts.per_document_candidates:
        if document_id in case_document_ids:
            summary_candidates_by_document[document_id] += count
    per_document_candidate_mismatches = sum(
        summary_candidates_by_document.get(document_id, 0)
        != case_candidates_by_document.get(document_id, 0)
        for document_id in eligible_document_ids
        if document_id in per_document_ids
        or case_candidates_by_document.get(document_id, 0) > 0
    )

    statement_counts: dict[uuid.UUID, int] = {}
    if case_document_ids:
        statement_counts = {
            document_id: int(count)
            for document_id, count in session.execute(
                select(
                    SourceSpan.document_version_id,
                    func.count(SourceStatement.id),
                )
                .join(
                    SourceStatement,
                    SourceStatement.source_span_id == SourceSpan.id,
                )
                .where(SourceSpan.document_version_id.in_(case_document_ids))
                .group_by(SourceSpan.document_version_id)
            )
        }
    scoped_runs = _latest_scoped_extract_runs(
        session,
        case_document_ids,
    )
    latest_runs = scoped_runs.latest
    if scoped_runs.ambiguous_latest:
        run_scope_valid = False
    invalid_run_document_ids = set(scoped_runs.invalid_document_ids)
    trusted_latest_runs: dict[uuid.UUID, AIRun] = {}
    for document_id, run in latest_runs.items():
        expected_span_ids = {span.id for span in spans_by_document.get(document_id, [])}
        if (
            _extract_run_matches_document_spans(
                run,
                document_id=document_id,
                expected_span_ids=expected_span_ids,
            )
            and run.status == "success"
        ):
            counts = _parse_extract_success_summary(
                run.output_summary,
                expected_span_count=len(expected_span_ids),
                prompt_version=run.prompt_version,
            )
            if counts is not None:
                trusted_latest_runs[document_id] = run
                continue
        invalid_run_document_ids.add(document_id)

    # A statement can make the operational watermark look extracted even if
    # its durable AIRun is missing. Acceptance requires a trusted latest run
    # for every non-degenerate case document independently of that watermark.
    invalid_run_document_ids.update(eligible_document_ids - trusted_latest_runs.keys())
    invalid_extract_run_scope_count = (
        scoped_runs.invalid_unscoped_reference_count + len(invalid_run_document_ids)
    )
    if invalid_extract_run_scope_count:
        run_scope_valid = False
    pending_extractions = 0
    for document_id in eligible_document_ids:
        state = extraction_state(
            statement_count=statement_counts.get(document_id, 0),
            latest_run=latest_runs.get(document_id),
        )
        if state in {"failed", "not_attempted"}:
            pending_extractions += 1
    if not eligible_document_ids.issubset(per_document_ids):
        run_scope_valid = False

    llm_accepted_item_count = 0
    for document_id, run in trusted_latest_runs.items():
        if document_id not in gildata_document_ids:
            continue
        # An accepted model item may legitimately reuse an existing candidate,
        # so candidate.run_ref cannot provide a one-to-one attestation.  The
        # immutable AIRun is trusted only after its document and complete span
        # set have matched the ledger above.
        counts = _parse_extract_success_summary(
            run.output_summary,
            expected_span_count=len(spans_by_document[document_id]),
            prompt_version=run.prompt_version,
        )
        if counts is not None:  # guaranteed by ``trusted_latest_runs`` above
            llm_accepted_item_count += counts.accepted

    supersession_count = sum(
        document.supersedes_id is not None
        for document, _, _ in gildata_documents.values()
    )
    duplicate_full_body_spans = 0
    invalid_full_body_spans = 0
    for document, source_kind, digest in gildata_documents.values():
        if source_kind not in _FULL_BODY_LOCATOR_KIND:
            continue
        spans = spans_by_document[document.id]
        duplicate_full_body_spans += max(len(spans) - 1, 0)
        if not spans:
            invalid_full_body_spans += 1
            continue
        for span in spans:
            text_digest = hashlib.sha256(span.verbatim_text.encode("utf-8")).hexdigest()
            locator = span.locator
            if not (
                text_digest == digest
                and span.text_sha256 == digest
                and document.byte_size == len(span.verbatim_text.encode("utf-8"))
                and isinstance(locator, dict)
                and locator.get("kind") == _FULL_BODY_LOCATOR_KIND[source_kind]
                and locator.get("parser") == "gildata-mcp-1"
            ):
                invalid_full_body_spans += 1

    contracts_by_document: dict[uuid.UUID, list[SourceContract]] = defaultdict(list)
    records_by_document: dict[uuid.UUID, list[ProviderRecord]] = defaultdict(list)
    if gildata_document_ids:
        for contract in session.scalars(
            select(SourceContract).where(
                SourceContract.document_version_id.in_(gildata_document_ids)
            )
        ):
            contracts_by_document[contract.document_version_id].append(contract)
        for record in session.scalars(
            select(ProviderRecord).where(
                ProviderRecord.document_version_id.in_(gildata_document_ids)
            )
        ):
            records_by_document[record.document_version_id].append(record)

    now = datetime.now(UTC)
    contract_count = 0
    provider_record_count = 0
    for document, source_kind, digest in gildata_documents.values():
        contracts = contracts_by_document[document.id]
        contract = contracts[0] if len(contracts) == 1 else None
        if (
            tenant_id is not None
            and contract is not None
            and _valid_contract(
                contract,
                document=document,
                source_kind=source_kind,
                digest=digest,
                tenant_id=tenant_id,
                at=now,
            )
        ):
            contract_count += 1
        records = records_by_document[document.id]
        if len(records) == 1 and _valid_provider_record(
            records[0],
            contract=contract,
            document=document,
            source_kind=source_kind,
            digest=digest,
        ):
            provider_record_count += 1

    formal_evidence_ids_by_thesis = _formal_gildata_evidence_ids_by_thesis(
        session,
        case_id=case_id,
        document_ids=gildata_document_ids,
    )
    formal_evidence_ids = {
        evidence_link_id
        for evidence_ids in formal_evidence_ids_by_thesis.values()
        for evidence_link_id in evidence_ids
    }
    all_case_evidence_ids_by_thesis: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for thesis_id, evidence_link_id in session.execute(
        select(EvidenceLink.thesis_id, EvidenceLink.id)
        .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
        .where(Thesis.research_case_id == case_id)
    ):
        all_case_evidence_ids_by_thesis[thesis_id].add(evidence_link_id)
    nonempty_assessments = 0
    for snapshot_thesis_id, evidence_link_ids in session.execute(
        select(EvidenceSnapshot.thesis_id, EvidenceSnapshot.evidence_link_ids)
        .join(AIAssessment, AIAssessment.snapshot_id == EvidenceSnapshot.id)
        .join(Thesis, Thesis.id == EvidenceSnapshot.thesis_id)
        .where(Thesis.research_case_id == case_id)
    ):
        if not isinstance(evidence_link_ids, list):
            continue
        parsed_ids: set[uuid.UUID] = set()
        valid_snapshot_ids = True
        for item in evidence_link_ids:
            if not isinstance(item, str):
                valid_snapshot_ids = False
                break
            try:
                parsed = uuid.UUID(item)
            except ValueError:
                valid_snapshot_ids = False
                break
            if str(parsed) != item or parsed in parsed_ids:
                valid_snapshot_ids = False
                break
            parsed_ids.add(parsed)
        same_thesis_ids = all_case_evidence_ids_by_thesis.get(snapshot_thesis_id, set())
        if not valid_snapshot_ids or not parsed_ids.issubset(same_thesis_ids):
            continue
        if parsed_ids & formal_evidence_ids_by_thesis.get(snapshot_thesis_id, set()):
            nonempty_assessments += 1

    return WalkthroughAuditFacts(
        gildata_rounds=summary_facts.gildata_rounds,
        pending_extractions=pending_extractions,
        summary_candidate_count=summary_candidate_count,
        persisted_candidate_count=persisted_candidate_count,
        gildata_supersession_count=int(supersession_count),
        duplicate_full_body_span_count=duplicate_full_body_spans,
        invalid_full_body_span_count=invalid_full_body_spans,
        gildata_document_count=len(gildata_document_ids),
        gildata_contract_count=contract_count,
        gildata_provider_record_count=provider_record_count,
        published_gildata_evidence_count=len(formal_evidence_ids),
        llm_accepted_item_count=llm_accepted_item_count,
        nonempty_gildata_evidence_assessment_count=nonempty_assessments,
        unresolved_summary_issue_count=summary_facts.unresolved_issue_count,
        replay_created_count=summary_facts.replay_created_count,
        replay_reused_count=summary_facts.replay_reused_count,
        quote_identity_verified_rounds=(summary_facts.quote_identity_verified_rounds),
        per_document_candidate_mismatch_count=per_document_candidate_mismatches,
        invalid_extract_run_scope_count=invalid_extract_run_scope_count,
        run_scope_valid=run_scope_valid,
    )


def evaluate_walkthrough_facts(facts: WalkthroughAuditFacts) -> WalkthroughAuditResult:
    """Apply every live-acceptance gate without exposing licensed content."""

    checks = {
        "two_ingest_rounds": facts.gildata_rounds == 2,
        "no_pending_extractions": facts.pending_extractions == 0,
        "candidate_totals_match": (
            facts.persisted_candidate_count == facts.summary_candidate_count
        ),
        "no_false_supersession": facts.gildata_supersession_count == 0,
        "duplicate_full_body_span": facts.duplicate_full_body_span_count == 0,
        "full_body_span_integrity": facts.invalid_full_body_span_count == 0,
        "gildata_documents_present": facts.gildata_document_count > 0,
        "one_contract_per_document": (
            facts.gildata_contract_count == facts.gildata_document_count
        ),
        "one_provider_record_per_document": (
            facts.gildata_provider_record_count == facts.gildata_document_count
        ),
        "formal_evidence_published": facts.published_gildata_evidence_count > 0,
        "llm_candidate_accepted": facts.llm_accepted_item_count > 0,
        "nonempty_evidence_assessment": (
            facts.nonempty_gildata_evidence_assessment_count > 0
        ),
        "no_unresolved_summary_issues": facts.unresolved_summary_issue_count == 0,
        "ingest_replay_created_nothing": facts.replay_created_count == 0,
        "ingest_replay_reused_records": facts.replay_reused_count > 0,
        "quote_identity_verified": facts.quote_identity_verified_rounds == 2,
        "per_document_candidate_totals_match": (
            facts.per_document_candidate_mismatch_count == 0
        ),
        "extract_runs_match_document_spans": (
            facts.invalid_extract_run_scope_count == 0
        ),
        "run_scope_valid": facts.run_scope_valid,
    }
    issue_codes = tuple(code for code, passed in checks.items() if not passed)
    metrics = {
        "gildata_rounds": facts.gildata_rounds,
        "pending_extractions": facts.pending_extractions,
        "summary_candidate_count": facts.summary_candidate_count,
        "persisted_candidate_count": facts.persisted_candidate_count,
        "candidate_count": facts.persisted_candidate_count,
        "gildata_supersessions": facts.gildata_supersession_count,
        "duplicate_full_body_spans": facts.duplicate_full_body_span_count,
        "invalid_full_body_spans": facts.invalid_full_body_span_count,
        "gildata_documents": facts.gildata_document_count,
        "gildata_contracts": facts.gildata_contract_count,
        "gildata_provider_records": facts.gildata_provider_record_count,
        "published_evidence_links": facts.published_gildata_evidence_count,
        "llm_accepted_items": facts.llm_accepted_item_count,
        "nonempty_evidence_assessments": (
            facts.nonempty_gildata_evidence_assessment_count
        ),
        "unresolved_summary_issues": facts.unresolved_summary_issue_count,
        "replay_created": facts.replay_created_count,
        "replay_reused": facts.replay_reused_count,
        "quote_identity_verified_rounds": facts.quote_identity_verified_rounds,
        "per_document_candidate_mismatches": (
            facts.per_document_candidate_mismatch_count
        ),
        "invalid_extract_run_scopes": facts.invalid_extract_run_scope_count,
        "run_scope_valid": int(facts.run_scope_valid),
    }
    return WalkthroughAuditResult(
        ok=not issue_codes,
        issue_codes=issue_codes,
        metrics=metrics,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJSONKeyError
        value[key] = item
    return value


def _load_summary(path: Path) -> object:
    if path.stat().st_size > MAX_SUMMARY_BYTES:
        raise WalkthroughAuditInputError("summary exceeds the size limit")
    raw = path.read_bytes()
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKeyError):
        raise WalkthroughAuditInputError("summary JSON is invalid") from None


def _validated_artifact_paths(
    *, database: str, summary: str
) -> tuple[Path, Path, object]:
    try:
        database_path = Path(database).resolve(strict=True)
        summary_path = Path(summary).resolve(strict=True)
    except OSError:
        raise FileNotFoundError from None
    if not database_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError
    summary_data = _load_summary(summary_path)
    root = _object(summary_data, field="summary")
    try:
        run_id = validate_walkthrough_run_id(root.get("run_id"))
    except WalkthroughConfigurationError:
        raise WalkthroughAuditInputError("summary run_id is invalid") from None
    if (
        database_path.name != f"evidence_walkthrough_{run_id}.db"
        or summary_path.name != f"cambricon_walkthrough_{run_id}_summary.json"
    ):
        raise _RunArtifactMismatchError
    return database_path, summary_path, summary_data


def _read_only_engine(database_path: Path):
    database_uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            check_same_thread=False,
        )
        connection.execute("PRAGMA query_only=ON")
        return connection

    return create_engine("sqlite+pysqlite://", creator=connect, future=True)


def _print_result(
    *, ok: bool, issue_codes: Sequence[str], metrics: dict[str, int]
) -> None:
    print(
        json.dumps(
            {
                "ok": ok,
                "issue_codes": list(issue_codes),
                "metrics": metrics,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit against explicitly paired, read-only local artifacts."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args(argv)

    try:
        database_path, summary_path, summary_data = _validated_artifact_paths(
            database=args.database,
            summary=args.summary,
        )
        del summary_path
    except FileNotFoundError:
        _print_result(
            ok=False,
            issue_codes=("audit_artifact_unavailable",),
            metrics={},
        )
        return 1
    except _RunArtifactMismatchError:
        _print_result(
            ok=False,
            issue_codes=("run_artifact_mismatch",),
            metrics={},
        )
        return 1
    except WalkthroughAuditInputError:
        _print_result(ok=False, issue_codes=("invalid_summary",), metrics={})
        return 1

    engine = _read_only_engine(database_path)
    try:
        with Session(engine, autoflush=False) as session:
            facts = collect_walkthrough_facts(session, summary_data)
        result = evaluate_walkthrough_facts(facts)
    except WalkthroughAuditInputError:
        _print_result(ok=False, issue_codes=("invalid_summary",), metrics={})
        return 1
    except (SQLAlchemyError, sqlite3.DatabaseError):
        _print_result(ok=False, issue_codes=("database_audit_failed",), metrics={})
        return 1
    except Exception:  # noqa: BLE001 -- CLI boundary must never echo raw data
        _print_result(ok=False, issue_codes=("audit_failed",), metrics={})
        return 1
    finally:
        engine.dispose()

    _print_result(
        ok=result.ok,
        issue_codes=result.issue_codes,
        metrics=result.metrics,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised via ``main`` tests
    sys.exit(main())
