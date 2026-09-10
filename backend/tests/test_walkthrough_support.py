from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.scripts import walkthrough_support
from app.scripts.walkthrough_support import (
    WalkthroughConfigurationError,
    WalkthroughResponseError,
    assessment_review_payload,
    atomic_claim_review_payload,
    classify_historical_case_read,
    configured_research_headers,
    project_gildata_probes,
    proposal_review_payload,
    safe_audit_data,
    summarize_extract_response,
    validate_live_walkthrough_environment,
    walkthrough_database_path,
    walkthrough_paths,
)
from scripts import walkthrough_cambricon_case as walkthrough


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


def test_summarize_extract_response_uses_current_candidate_contract() -> None:
    result = summarize_extract_response(
        {
            "candidate_count": 2,
            "candidates": [
                {
                    "claim_type": "reported_claim",
                    "quote": "licensed body must not enter the summary",
                },
                {
                    "claim_type": "research_opinion",
                    "normalized_text": "licensed body must not enter the summary",
                },
            ],
            "reason": None,
        }
    )

    assert result == {
        "candidate_count": 2,
        "claim_types": {"reported_claim": 1, "research_opinion": 1},
        "reason": None,
    }
    assert "licensed body" not in repr(result)


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"candidate_count": True, "candidates": [], "reason": None},
        {"candidate_count": -1, "candidates": [], "reason": None},
        {"candidate_count": 0.0, "candidates": [], "reason": None},
        {"candidate_count": 0, "candidates": {}, "reason": None},
        {"candidate_count": 1, "candidates": [], "reason": None},
        {"candidate_count": 1, "candidates": ["bad"], "reason": None},
        {
            "candidate_count": 1,
            "candidates": [{"claim_type": ""}],
            "reason": None,
        },
        {
            "candidate_count": 1,
            "candidates": [{"claim_type": "   "}],
            "reason": None,
        },
        {
            "candidate_count": 1,
            "candidates": [{"claim_type": 1}],
            "reason": None,
        },
        {"candidate_count": 0, "candidates": [], "reason": []},
    ],
)
def test_summarize_extract_response_rejects_protocol_drift(response: object) -> None:
    with pytest.raises(WalkthroughResponseError):
        summarize_extract_response(response)


def test_summarize_extract_response_classifies_honest_zero_reason() -> None:
    assert summarize_extract_response(
        {
            "candidate_count": 0,
            "candidates": [],
            "reason": "LLM completed without supported candidates",
        }
    ) == {
        "candidate_count": 0,
        "claim_types": {},
        "reason": "zero_candidates_reported",
    }


def test_summarize_extract_response_never_returns_untrusted_reason_prose() -> None:
    malicious_reason = (
        "sentinel licensed response https://provider.example/private?"
        "access_token=sentinel-token"
    )

    result = summarize_extract_response(
        {
            "candidate_count": 0,
            "candidates": [],
            "reason": malicious_reason,
        }
    )

    assert result["reason"] == "zero_candidates_reported"
    assert "sentinel" not in repr(result)
    assert "provider.example" not in repr(result)


@pytest.mark.parametrize(
    ("candidate_count", "reason", "expected_category"),
    [
        (0, None, "zero_candidates_unspecified"),
        (0, "该版本没有附加来源片段，无法抽取陈述", "no_source_spans"),
        (
            0,
            "LLM 抽取调用完成但未返回任何陈述（可能为纯结构化或合规受限）",
            "llm_returned_no_candidates",
        ),
        (1, "provider supplied unexpected prose", "reason_reported_with_candidates"),
        (1, "zero_candidates_reported", "reason_reported_with_candidates"),
    ],
)
def test_summarize_extract_response_uses_closed_reason_categories(
    candidate_count: int,
    reason: str | None,
    expected_category: str,
) -> None:
    candidates = [{"claim_type": "reported_claim"}] * candidate_count

    result = summarize_extract_response(
        {
            "candidate_count": candidate_count,
            "candidates": candidates,
            "reason": reason,
        }
    )

    assert result["reason"] == expected_category


def test_safe_audit_data_removes_credentials_bodies_and_candidate_content() -> None:
    unsafe = {
        "method": "POST",
        "path": "/api/v1/documents/version-1/extract",
        "status": 201,
        "headers": {"Authorization": "Bearer sentinel-token"},
        "request": {"json": {"query": "sentinel-licensed-query"}},
        "response": {
            "candidate_count": 1,
            "candidates": [
                {
                    "claim_type": "reported_claim",
                    "quote": "sentinel-licensed-body",
                    "normalized_text": "sentinel-normalized-body",
                }
            ],
            "error": {
                "code": "upstream_unavailable",
                "message": (
                    "sentinel-provider-error "
                    "https://provider.example/path?access_token=sentinel-token"
                ),
            },
        },
    }

    safe = safe_audit_data(unsafe)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert safe["method"] == "POST"
    assert safe["status"] == 201
    assert safe["response"]["candidate_count"] == 1
    assert safe["response"]["error"]["code"] == "upstream_unavailable"
    for forbidden in (
        "sentinel-token",
        "sentinel-licensed-query",
        "sentinel-licensed-body",
        "sentinel-normalized-body",
        "sentinel-provider-error",
        "provider.example",
        '"headers"',
        '"request"',
        '"candidates"',
        '"quote"',
        '"normalized_text"',
        '"message"',
    ):
        assert forbidden not in serialized


def test_safe_audit_data_drops_untrusted_strings_even_under_friendly_keys() -> None:
    projected = safe_audit_data(
        {
            "tools": ["FinQuery", "SmartFundSelection"],
            "response": {
                "sentinel licensed key": 17,
                "reason": "sentinel licensed body",
                "title": "sentinel provider title",
                "conclusion": "sentinel generated prose",
                "scope": "sentinel provider scope",
                "fund_name": "sentinel disguised provider error",
            },
        }
    )

    assert projected["tools"] == ["FinQuery", "SmartFundSelection"]
    assert "sentinel" not in json.dumps(projected, ensure_ascii=False)


def test_project_gildata_probes_keeps_only_minimal_numeric_inputs() -> None:
    projected = project_gildata_probes(
        quote_rows=[
            {
                "stock_name": "sentinel-raw-stock-name",
                "latest_price": "1,106.0",
                "total_mv": "6948.92",
                "pe_ttm": "255.759",
                "pe_lyr": "337.453",
                "pb": "secret-not-numeric",
                "unknown": "sentinel-licensed-body",
            }
        ],
        annual_rows=[
            {
                "财务科目名称": "营业收入",
                "财务科目数额": "12.34",
                "同比(%)": "56.7",
                "报表名称": "sentinel-provider-row",
            }
        ],
        fund_rows=[
            {
                "机构类型": "基金",
                "机构股东名称": "Safe Fund",
                "交易代码": "123456.OF",
                "持股数量占流通A股比例(%)": "1.25",
                "报告日期": "2026-06-30T00:00:00",
                "原文": "sentinel-fund-body",
            }
        ],
        smart_response_chars=987,
        smart_failed=False,
    )

    assert projected == {
        "quote_metrics": {
            "latest_price": "1106.0",
            "total_mv": "6948.92",
            "pe_ttm": "255.759",
            "pe_lyr": "337.453",
        },
        "annual_2025": {
            "revenue_amount": "12.34",
            "revenue_yoy_percent": "56.7",
        },
        "fund_holders": [
            {
                "fund_name": "Safe Fund",
                "fund_code": "123456",
                "weight_percent": "1.25",
                "report_date": "2026-06-30",
            }
        ],
        "smart_fund_selection": {
            "status": "succeeded",
            "response_chars": 987,
        },
    }
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "sentinel" not in serialized
    assert "stock_name" not in serialized
    assert "报表名称" not in serialized


def test_api_audit_log_never_records_request_or_extraction_content(
    monkeypatch, tmp_path
) -> None:
    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {
                "candidate_count": 1,
                "candidates": [
                    {
                        "claim_type": "reported_claim",
                        "quote": "sentinel-licensed-quote",
                        "normalized_text": "sentinel-normalized-text",
                    }
                ],
                "reason": None,
                "mode": "live-model",
            }

    class FakeClient:
        @staticmethod
        def request(*_args, **_kwargs):
            return FakeResponse()

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(walkthrough, "client", FakeClient())
    monkeypatch.setattr(
        walkthrough,
        "AUTH_HEADERS",
        {"Authorization": "Bearer sentinel-tenant-token"},
    )
    monkeypatch.setattr(walkthrough, "JSONL_PATH", audit_path)

    status, body = walkthrough.api(
        "POST",
        "/api/v1/documents/v1/extract",
        "P3",
        "extract",
        json={"query": "sentinel-request-body"},
    )

    assert status == 201
    assert body["candidates"][0]["quote"] == "sentinel-licensed-quote"
    persisted = audit_path.read_text(encoding="utf-8")
    assert '"candidate_count": 1' in persisted
    for forbidden in (
        "sentinel-tenant-token",
        "sentinel-request-body",
        "sentinel-licensed-quote",
        "sentinel-normalized-text",
        '"request"',
        '"candidates"',
        '"quote"',
        '"normalized_text"',
    ):
        assert forbidden not in persisted


def test_api_transport_failure_does_not_expose_exception_text(monkeypatch) -> None:
    class FailingClient:
        @staticmethod
        def request(*_args, **_kwargs):
            raise RuntimeError(
                "sentinel-provider-error "
                "https://provider.example/path?access_token=sentinel-token"
            )

    monkeypatch.setattr(walkthrough, "client", FailingClient())

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough.api("GET", "/api/v1/documents", "P3", "list")

    rendered = repr(exc_info.value) + repr(exc_info.value.__cause__)
    assert "sentinel" not in rendered
    assert "provider.example" not in rendered


def test_api_error_audit_keeps_only_status_and_fixed_category(
    monkeypatch, tmp_path
) -> None:
    class FailedResponse:
        status_code = 503

        @staticmethod
        def json():
            return {
                "error": {
                    "code": "upstream_unavailable",
                    "message": "sentinel provider failure",
                    "licensed_metric": 1729,
                },
                "candidate_count": 99,
            }

    class FakeClient:
        @staticmethod
        def request(*_args, **_kwargs):
            return FailedResponse()

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(walkthrough, "client", FakeClient())
    monkeypatch.setattr(walkthrough, "JSONL_PATH", audit_path)

    walkthrough.api("POST", "/api/v1/documents/v1/extract", "P3", "extract")

    record = json.loads(audit_path.read_text(encoding="utf-8"))["data"]
    assert record == {
        "method": "POST",
        "path": "/api/v1/documents/v1/extract",
        "status": 503,
        "error_code": "upstream_unavailable",
    }


def test_state_persistence_fails_closed_on_raw_or_credential_content(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError, match="raw") as exc_info:
        walkthrough._save_state(
            {"probes": {"raw": "https://provider.example/?access_token=sentinel-token"}}
        )

    assert "sentinel-token" not in str(exc_info.value)
    assert not state_path.exists()


class _LeakyStringValue:
    def __str__(self) -> str:
        return "Bearer sentinel-object-token"


@pytest.mark.parametrize(
    "unsafe_value",
    [
        _LeakyStringValue(),
        date(2026, 9, 3),
        Decimal("1.25"),
        float("nan"),
        float("inf"),
        float("-inf"),
        ("tuple-is-not-json",),
    ],
)
def test_state_persistence_rejects_non_json_or_non_finite_values(
    monkeypatch, tmp_path, unsafe_value: object
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._save_state({"safe_key": unsafe_value})

    assert "sentinel" not in str(exc_info.value)
    assert not state_path.exists()


def test_atomic_json_writer_does_not_stringify_unknown_objects(tmp_path) -> None:
    target = tmp_path / "summary.json"

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough_support.atomic_write_json(target, {"safe_key": _LeakyStringValue()})

    assert "sentinel" not in str(exc_info.value)
    assert not target.exists()
    assert list(tmp_path.glob(".summary.json.*.tmp")) == []


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "GILDATA_TOKEN",
        "refreshToken",
        "password",
        "db_password",
        "client-secret",
        "credential",
        "service.credentials",
        "api-key",
        "api_key",
        "apikey",
        "Authorization",
        "auth",
        "auth_header",
        "access_key",
        "accesskey",
        "private_key",
        "pwd",
        "db_pwd",
        "passwd",
        "passphrase",
        "jwt",
        "signature",
    ],
)
def test_state_persistence_rejects_credential_keys_without_echoing_them(
    monkeypatch, tmp_path, unsafe_key: str
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._save_state({unsafe_key: 1729})

    assert unsafe_key not in str(exc_info.value)
    assert not state_path.exists()


_HIGH_CONFIDENCE_SECRET_VALUES = (
    "sk-sentinel-secret",
    "sk_proj_sentinel_secret",
    "ghp_sentinel_secret",
    "github_pat_11AA0_sentinelCredentialValue0123456789",
    "glpat-sentinelCredentialValue0123456789",
    "hf_sentinelCredentialValue0123456789",
    "xoxb-sentinel-secret",
    "AKIAIOSFODNN7EXAMPLE",
    "AIzaSySentinelCredentialValue0123456789",
    "npm_sentinelCredentialValue0123456789",
    "pypi-sentinelCredentialValue0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZW50aW5lbCJ9.c2VudGluZWxfc2lnbmF0dXJl",
)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "GILDATA_TOKEN=sentinel-token",
        '"GILDATA_TOKEN_VALUE" = "sentinel-token"',
        "refreshToken: sentinel-token",
        "tokens=sentinel-token",
        '"credentials" : "sentinel-credential"',
        "password=sentinel-password",
        "PASSWORD: sentinel-password",
        "passwords = sentinel-password",
        "client-secret = sentinel-secret",
        "secrets=sentinel-secret",
        "secret_key: sentinel-secret",
        "credential: sentinel-credential",
        "credential_value = sentinel-credential",
        "api key: sentinel-api-key",
        "api-key=sentinel-api-key",
        "api_keys = sentinel-api-key",
        "authorization = sentinel-authorization",
        "authorization_header: sentinel-authorization",
        "token_value = sentinel-token",
        "Bearer sentinel-bearer-token",
        "Bearer:sentinel-bearer-token",
        "Bearer=sentinel-bearer-token",
        '"Bearer": "sentinel-bearer-token"',
        "auth=sentinel-authorization",
        "auth_header: sentinel-authorization",
        "access_key=sentinel-access-key",
        "accesskey: sentinel-access-key",
        "private_key=sentinel-private-key",
        "pwd: sentinel-password",
        "db_pwd=sentinel-password",
        "passwd: sentinel-password",
        "passphrase=sentinel-password",
        "jwt: sentinel-token",
        "signature=sentinel-signature",
        "access key:sentinel-access-key",
        '"private key":sentinel-private-key',
        "auth header=sentinel-authorization",
        "authentication header:sentinel-authorization",
        "api key value:sentinel-api-key",
        "authorization header value:sentinel-authorization",
        "client secret value:sentinel-secret",
        "secret key value:sentinel-secret",
        "token\n=sentinel-token",
        '"token"\n:sentinel-token',
        "access key\r\n:sentinel-access-key",
        "AWS access key id:sentinel-access-key",
        "private key material:sentinel-private-key",
        "https:\t//provider.example/sentinel-private",
        "https:\n//provider.example/sentinel-private",
        *_HIGH_CONFIDENCE_SECRET_VALUES,
    ],
)
def test_state_persistence_rejects_credential_values_without_echoing_them(
    monkeypatch, tmp_path, unsafe_value: str
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._save_state({"safe_key": unsafe_value})

    assert "sentinel" not in str(exc_info.value)
    assert not state_path.exists()


@pytest.mark.parametrize(
    "safe_text",
    [
        "password policy is enabled",
        "secret research conclusion",
        "token count remained zero",
        "credential review completed",
        "authorization was not requested",
        "api key rotation policy",
        "review note: password policy is enabled",
        "The password policy is enabled: no action is required",
        "Token count: 0",
        "Password policy: enabled",
        "No credentials were stored: verified",
        "Authorization was not requested: confirmed",
        "Bearer instrument matured",
        "Bearer instrument: matured",
    ],
)
def test_state_persistence_allows_credential_words_without_assignment(
    monkeypatch, tmp_path, safe_text: str
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    walkthrough._save_state({"safe_key": safe_text})

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"safe_key": safe_text}


def test_safe_audit_data_uses_the_same_credential_denylist() -> None:
    projected = safe_audit_data(
        {
            "status": 201,
            "GILDATA_TOKEN": 1729,
            "password": False,
            "response": {
                "code": "password:sentinel-password",
                "path": "credential:sentinel-credential",
                "mode": "tokens:sentinel-token",
                "error_code": "safe_error_code",
            },
            "code": "Bearer:sentinel-bearer-token",
        }
    )

    assert projected == {
        "status": 201,
        "response": {"error_code": "safe_error_code"},
    }


@pytest.mark.parametrize(
    "secret_value",
    _HIGH_CONFIDENCE_SECRET_VALUES,
)
def test_safe_audit_data_rejects_high_confidence_secret_values(
    secret_value: str,
) -> None:
    assert safe_audit_data({"code": secret_value}) == {}


def test_state_persistence_accepts_existing_json_primitive_shapes(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)
    legal_state = {
        "run_id": "safe-run",
        "values": [None, True, False, 0, 1, 1.25, "合法状态"],
        "nested": {"status": "complete", "count": 2},
    }

    walkthrough._save_state(legal_state)

    assert json.loads(state_path.read_text(encoding="utf-8")) == legal_state
    assert walkthrough._load_state() == legal_state


def test_state_load_rejects_existing_credential_content_without_echo(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        '{"GILDATA_TOKEN":"sentinel-existing-token"}', encoding="utf-8"
    )
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._load_state()

    assert "sentinel" not in str(exc_info.value)


def test_state_atomic_write_failure_preserves_previous_checkpoint(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text('{"checkpoint":"old"}', encoding="utf-8")
    state_path.chmod(0o600)
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    def fail_replace(*_args, **_kwargs):
        raise OSError("sentinel filesystem detail")

    monkeypatch.setattr(walkthrough_support.os, "replace", fail_replace)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._save_state({"checkpoint": "new"})

    assert state_path.read_text(encoding="utf-8") == '{"checkpoint":"old"}'
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
    assert "sentinel" not in str(exc_info.value)


def test_state_and_jsonl_reject_symlink_targets(monkeypatch, tmp_path) -> None:
    state_victim = tmp_path / "state-victim.json"
    state_victim.write_text("old-state", encoding="utf-8")
    state_link = tmp_path / "state.json"
    state_link.symlink_to(state_victim)
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_link)

    with pytest.raises(WalkthroughResponseError):
        walkthrough._save_state({"checkpoint": "new"})

    jsonl_victim = tmp_path / "jsonl-victim.jsonl"
    jsonl_victim.write_text("old-jsonl\n", encoding="utf-8")
    jsonl_link = tmp_path / "audit.jsonl"
    jsonl_link.symlink_to(jsonl_victim)
    monkeypatch.setattr(walkthrough, "JSONL_PATH", jsonl_link)

    with pytest.raises(WalkthroughResponseError):
        walkthrough.rec("P3", "extract", {"status": 201})

    assert state_victim.read_text(encoding="utf-8") == "old-state"
    assert jsonl_victim.read_text(encoding="utf-8") == "old-jsonl\n"


def test_state_load_rejects_symlink_target(monkeypatch, tmp_path) -> None:
    state_victim = tmp_path / "state-victim.json"
    state_victim.write_text('{"run_id":"unsafe-indirect-read"}', encoding="utf-8")
    state_link = tmp_path / "state.json"
    state_link.symlink_to(state_victim)
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_link)

    with pytest.raises(WalkthroughResponseError):
        walkthrough._load_state()


def test_state_load_reclassifies_legacy_p3_reason(monkeypatch, tmp_path) -> None:
    malicious_reason = "sentinel licensed provider response"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "_summary_phases": {
                    "P3_extract": {
                        "per_document": [
                            {
                                "version_id": "v1",
                                "candidates": 0,
                                "claim_types": {},
                                "reason": malicious_reason,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    loaded = walkthrough._load_state()

    assert (
        loaded["_summary_phases"]["P3_extract"]["per_document"][0]["reason"]
        == "zero_candidates_reported"
    )
    assert "sentinel" not in repr(loaded)


def test_state_load_rejects_malformed_nested_p3_reason_without_leaking_it(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "_summary_phases": {
                    "P3_extract": {
                        "per_document": [
                            {
                                "version_id": "v1",
                                "candidates": 0,
                                "claim_types": {},
                                "reason": ["sentinel licensed provider response"],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)

    with pytest.raises(WalkthroughResponseError) as exc_info:
        walkthrough._load_state()

    assert "sentinel" not in str(exc_info.value)


def test_summary_rejects_symlink_target(monkeypatch, tmp_path) -> None:
    summary_victim = tmp_path / "summary-victim.json"
    summary_victim.write_text("old-summary", encoding="utf-8")
    summary_link = tmp_path / "summary.json"
    summary_link.symlink_to(summary_victim)
    monkeypatch.setattr(walkthrough, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(walkthrough, "SUMMARY_PATH", summary_link)
    monkeypatch.setattr(
        walkthrough,
        "summary",
        {"run_id": "safe", "phases": {}, "issues": [], "facts": {}},
    )

    with pytest.raises(WalkthroughResponseError):
        walkthrough._finalize(
            {"run_id": "safe"},
            walkthrough.datetime.now(walkthrough.UTC),
        )

    assert summary_victim.read_text(encoding="utf-8") == "old-summary"


def test_jsonl_rejects_non_regular_target(monkeypatch, tmp_path) -> None:
    directory_target = tmp_path / "audit.jsonl"
    directory_target.mkdir()
    monkeypatch.setattr(walkthrough, "JSONL_PATH", directory_target)

    with pytest.raises(WalkthroughResponseError):
        walkthrough.rec("P3", "extract", {"status": 201})


def test_walkthrough_artifacts_are_created_with_owner_only_permissions(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "state.json"
    jsonl_path = tmp_path / "audit.jsonl"
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(walkthrough, "STATE_PATH", state_path)
    monkeypatch.setattr(walkthrough, "JSONL_PATH", jsonl_path)
    monkeypatch.setattr(walkthrough, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(
        walkthrough,
        "summary",
        {"run_id": "safe", "phases": {}, "issues": [], "facts": {}},
    )

    old_umask = os.umask(0o022)
    try:
        walkthrough.rec("meta", "run_start", {"run_id": "safe"})
        walkthrough._finalize(
            {"run_id": "safe"},
            walkthrough.datetime.now(walkthrough.UTC),
        )
    finally:
        os.umask(old_umask)

    for path in (state_path, jsonl_path, summary_path):
        assert path.stat().st_mode & 0o777 == 0o600


def test_database_artifact_is_prepared_with_owner_only_permissions(
    tmp_path,
) -> None:
    database_path = tmp_path / "walkthrough.db"

    old_umask = os.umask(0o022)
    try:
        walkthrough_support.prepare_walkthrough_database(database_path)
    finally:
        os.umask(old_umask)

    assert database_path.stat().st_mode & 0o777 == 0o600


def test_existing_walkthrough_artifact_permissions_are_tightened(tmp_path) -> None:
    artifact_paths = tuple(tmp_path / name for name in ("state", "audit", "summary"))
    database_path = tmp_path / "walkthrough.db"
    for path in (*artifact_paths, database_path):
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o644)

    walkthrough_support.prepare_walkthrough_artifact_targets(artifact_paths)
    walkthrough_support.prepare_walkthrough_database(database_path)

    for path in (*artifact_paths, database_path):
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("smart_fails", [False, True])
def test_phase0_persists_only_projected_probes(
    monkeypatch, tmp_path, smart_fails: bool
) -> None:
    from app.datasources.gildata import adapters
    from app.datasources.gildata.client import GildataMCPClient

    class FakeGildata:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def list_tools():
            return [{"name": "FinQuery"}, {"name": "SmartFundSelection"}]

        @staticmethod
        def call_tool(*_args, **_kwargs):
            if smart_fails:
                raise RuntimeError(
                    "sentinel-provider-error "
                    "https://provider.example/path?access_token=sentinel-token"
                )
            return "sentinel-smart-fund-selection-body"

    responses = iter(
        [
            [
                {
                    "latest_price": "1106.0",
                    "total_mv": "6948.92",
                    "pe_ttm": "255.759",
                    "stock_name": "sentinel-quote-row",
                }
            ],
            [
                {
                    "财务科目名称": "营业收入",
                    "财务科目数额": "12.34",
                    "同比(%)": "56.7",
                    "raw": "sentinel-annual-row",
                }
            ],
            [
                {
                    "机构类型": "基金",
                    "机构股东名称": "Safe Fund",
                    "交易代码": "123456.OF",
                    "持股数量占流通A股比例(%)": "1.25",
                    "报告日期": "2026-06-30T00:00:00",
                    "raw": "sentinel-fund-row",
                }
            ],
        ]
    )
    monkeypatch.setattr(
        GildataMCPClient,
        "from_env",
        classmethod(lambda _cls: FakeGildata()),
    )
    monkeypatch.setattr(adapters, "fetch_quote", lambda *_args: next(responses))
    monkeypatch.setattr(walkthrough, "JSONL_PATH", tmp_path / "audit.jsonl")
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }

    probes = walkthrough.phase0_preflight()
    persisted = json.dumps(
        {"probes": probes, "summary": walkthrough.summary},
        ensure_ascii=False,
    ) + (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

    assert probes["quote_metrics"]["latest_price"] == "1106.0"
    assert probes["fund_holders"][0]["fund_code"] == "123456"
    assert probes["smart_fund_selection"]["status"] == (
        "failed" if smart_fails else "succeeded"
    )
    for forbidden in (
        "sentinel-quote-row",
        "sentinel-annual-row",
        "sentinel-fund-row",
        "sentinel-smart-fund-selection-body",
        "sentinel-provider-error",
        "provider.example",
        "sentinel-token",
        "quote_cambricon",
        "smart_fund_selection_raw",
        "smart_fund_selection_error",
    ):
        assert forbidden not in persisted


def _configure_valid_live_walkthrough_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"walkthrough-token":{"tenant_id":"walkthrough","roles":[]}}',
    )
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "TRUE")
    monkeypatch.setenv("LLM_API_KEY", "sentinel-never-persisted")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")


def test_live_walkthrough_preflight_accepts_strict_runtime(monkeypatch) -> None:
    _configure_valid_live_walkthrough_environment(monkeypatch)

    validate_live_walkthrough_environment(require_live_llm=True)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_MAX_ATTEMPTS", "1"),
        ("LLM_MAX_ATTEMPTS", "secret-invalid"),
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_TIMEOUT_SECONDS", "nan"),
        ("LLM_TIMEOUT_SECONDS", "secret-invalid"),
        ("LLM_MAX_OUTPUT_TOKENS", "4095"),
        ("LLM_MAX_OUTPUT_TOKENS", "secret-invalid"),
    ],
)
def test_live_walkthrough_preflight_rejects_unsafe_llm_bounds_without_values(
    monkeypatch, name: str, value: str
) -> None:
    _configure_valid_live_walkthrough_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(WalkthroughConfigurationError) as exc_info:
        validate_live_walkthrough_environment(require_live_llm=True)

    assert name in str(exc_info.value)
    assert value not in str(exc_info.value)


def test_live_walkthrough_preflight_requires_both_gildata_rights(monkeypatch) -> None:
    _configure_valid_live_walkthrough_environment(monkeypatch)
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "1")

    with pytest.raises(WalkthroughConfigurationError) as exc_info:
        validate_live_walkthrough_environment(require_live_llm=True)

    assert "GILDATA_ALLOW_AI_PROCESSING" in str(exc_info.value)
    assert "GILDATA_ALLOW_DISPLAY" in str(exc_info.value)
    assert "sentinel-never-persisted" not in str(exc_info.value)


def test_live_walkthrough_preflight_rejects_mock_llm(monkeypatch) -> None:
    _configure_valid_live_walkthrough_environment(monkeypatch)
    monkeypatch.delenv("LLM_API_KEY")

    with pytest.raises(WalkthroughConfigurationError, match="LLM_API_KEY"):
        validate_live_walkthrough_environment(require_live_llm=True)


def test_live_walkthrough_preflight_requires_tenant_credentials(monkeypatch) -> None:
    _configure_valid_live_walkthrough_environment(monkeypatch)
    monkeypatch.delenv("RESEARCH_TENANT_TOKENS")

    with pytest.raises(WalkthroughConfigurationError, match="RESEARCH_TENANT_TOKENS"):
        validate_live_walkthrough_environment(require_live_llm=True)


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


@pytest.mark.parametrize(
    "unsafe_run_id",
    [
        "",
        "..",
        "../escape",
        "/absolute",
        "nested/path",
        ".hidden",
        "white space",
        "unicode-路径",
        "a" * 65,
    ],
)
def test_walkthrough_run_id_rejects_unsafe_slugs(tmp_path, unsafe_run_id: str) -> None:
    for builder in (walkthrough_paths, walkthrough_database_path):
        with pytest.raises(WalkthroughConfigurationError, match="WALKTHROUGH_RUN_ID"):
            builder(tmp_path, unsafe_run_id)

    assert list(tmp_path.iterdir()) == []


def test_walkthrough_path_builders_reject_symlink_directories(tmp_path) -> None:
    real_directory = tmp_path / "outside"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(WalkthroughConfigurationError):
        walkthrough_paths(linked_directory, "safe-run")
    with pytest.raises(WalkthroughConfigurationError):
        walkthrough_database_path(linked_directory, "safe-run")


def test_runtime_rejects_database_symlink_before_schema_initialization(
    monkeypatch, tmp_path
) -> None:
    backend_root = tmp_path / "backend"
    backend_root.mkdir()
    output_dir = tmp_path / "walkthrough"
    run_id = "safe-run"
    victim = tmp_path / "database-victim.db"
    victim.write_bytes(b"sentinel-old-database")
    database_path = backend_root / f"evidence_walkthrough_{run_id}.db"
    database_path.symlink_to(victim)

    from app import env as app_env
    from app.models.ledger import Base

    schema_initializations: list[object] = []
    monkeypatch.setenv("WALKTHROUGH_RUN_ID", run_id)
    monkeypatch.setattr(walkthrough, "BACKEND_ROOT", backend_root)
    monkeypatch.setattr(walkthrough, "OUT_DIR", output_dir)
    monkeypatch.setattr(app_env, "load_local_env", lambda: None)
    monkeypatch.setattr(
        walkthrough,
        "validate_live_walkthrough_environment",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        walkthrough,
        "configured_research_headers",
        lambda: {"Authorization": "Bearer safe"},
    )
    monkeypatch.setattr(
        Base.metadata,
        "create_all",
        lambda *_args, **_kwargs: schema_initializations.append(object()),
    )

    with pytest.raises(WalkthroughConfigurationError):
        walkthrough._initialize_runtime(require_live_llm=False)

    assert schema_initializations == []
    assert victim.read_bytes() == b"sentinel-old-database"


def test_runtime_rejects_explicit_empty_run_id_before_loading_environment(
    monkeypatch, tmp_path
) -> None:
    from app import env as app_env

    monkeypatch.setenv("WALKTHROUGH_RUN_ID", "")
    monkeypatch.setattr(walkthrough, "BACKEND_ROOT", tmp_path / "backend")
    monkeypatch.setattr(walkthrough, "OUT_DIR", tmp_path / "walkthrough")
    monkeypatch.setattr(
        app_env,
        "load_local_env",
        lambda: (_ for _ in ()).throw(AssertionError("must not load local env")),
    )

    with pytest.raises(WalkthroughConfigurationError, match="WALKTHROUGH_RUN_ID"):
        walkthrough._initialize_runtime(require_live_llm=False)

    assert list(tmp_path.iterdir()) == []


def test_historical_case_absence_is_classified_without_hiding_other_failures() -> None:
    assert (
        classify_historical_case_read(
            404,
            {"error": {"code": "not_found", "message": "research case not found"}},
        )
        == "case_not_created_at_cutoff"
    )
    assert (
        classify_historical_case_read(500, {"error": {"code": "internal_error"}})
        is None
    )


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


def test_walkthrough_assessment_review_does_not_override_a_zero_evidence_verdict() -> (
    None
):
    assert assessment_review_payload("insufficient_evidence", evidence_count=0) == {
        "outcome": "confirmed",
        "conclusion": None,
        "reason": "人工复核：当前冻结快照没有已发布证据，确认证据不足结论；历史交叉验证不改写该快照。",
        "reviewer": "walkthrough-reviewer",
    }


def test_walkthrough_help_needs_no_credentials_and_creates_no_run_artifacts() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    run_id = f"help-{uuid.uuid4().hex}"
    environment = dict(os.environ)
    for name in (
        "RESEARCH_TENANT_TOKENS",
        "GILDATA_TOKEN",
        "GILDATA_ALLOW_AI_PROCESSING",
        "GILDATA_ALLOW_DISPLAY",
        "LLM_API_KEY",
    ):
        environment.pop(name, None)
    environment["WALKTHROUGH_RUN_ID"] = run_id
    database_path = walkthrough_database_path(backend_dir, run_id)
    paths = walkthrough_paths(
        backend_dir.parent / "docs" / "evaluation" / "walkthrough",
        run_id,
    )

    try:
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
        assert not database_path.exists()
        assert all(
            not path.exists() for path in (paths.state, paths.jsonl, paths.summary)
        )
    finally:
        database_path.unlink(missing_ok=True)
        for path in (paths.state, paths.jsonl, paths.summary):
            path.unlink(missing_ok=True)


def test_walkthrough_p3_preflight_fails_before_creating_run_artifacts() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    run_id = f"preflight-{uuid.uuid4().hex}"
    environment = dict(os.environ)
    environment.update(
        {
            "APP_ENV": "test",
            "WALKTHROUGH_RUN_ID": run_id,
            "RESEARCH_TENANT_TOKENS": (
                '{"walkthrough-token":{"tenant_id":"walkthrough","roles":[]}}'
            ),
            "GILDATA_ALLOW_AI_PROCESSING": "true",
            "GILDATA_ALLOW_DISPLAY": "true",
            "LLM_API_KEY": "sentinel-live-key",
            "LLM_MAX_ATTEMPTS": "sentinel-invalid-attempts",
        }
    )
    database_path = walkthrough_database_path(backend_dir, run_id)
    paths = walkthrough_paths(
        backend_dir.parent / "docs" / "evaluation" / "walkthrough",
        run_id,
    )

    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/walkthrough_cambricon_case.py",
                "--stages",
                "p3",
            ],
            cwd=backend_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "LLM_MAX_ATTEMPTS" in output
        assert "sentinel-invalid-attempts" not in output
        assert "sentinel-live-key" not in output
        assert not database_path.exists()
        assert all(
            not path.exists() for path in (paths.state, paths.jsonl, paths.summary)
        )
    finally:
        database_path.unlink(missing_ok=True)
        for path in (paths.state, paths.jsonl, paths.summary):
            path.unlink(missing_ok=True)


def test_phase2_records_two_safe_round_summaries(monkeypatch) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    responses = iter(
        [
            (201, {"research_reports": 2, "raw": "sentinel-first-body"}),
            (201, {"research_reports": 3, "raw": "sentinel-second-body"}),
        ]
    )
    monkeypatch.setattr(walkthrough, "api", lambda *_args, **_kwargs: next(responses))

    result = walkthrough.phase2_ingest("case-1")

    assert result == [
        {"round": 1, "http_status": 201, "research_reports": 2},
        {"round": 2, "http_status": 201, "research_reports": 3},
    ]
    assert "sentinel" not in json.dumps(walkthrough.summary)


def _document(
    version_id: str,
    extraction_state: str,
    *,
    content_quality: str = "ok",
) -> dict[str, object]:
    return {
        "id": version_id,
        "extraction_state": extraction_state,
        "content_quality": content_quality,
        "title": f"title-{version_id}",
        "doc_kind": "research-report",
    }


def _document_page(
    items: list[dict[str, object]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "items": items,
        "page": {"has_more": has_more, "next_cursor": next_cursor},
    }


def test_phase3_paginates_rechecks_and_merges_resume_history(monkeypatch) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {
            "P3_extract": {
                "per_document": [
                    {
                        "version_id": "v-old",
                        "mode": "live-model",
                        "title": "old",
                        "doc_kind": "research-report",
                        "candidates": 2,
                        "claim_types": {"research_opinion": 2},
                        "reason": None,
                    },
                    {
                        "version_id": "v-replaced",
                        "mode": "live-model",
                        "title": "stale",
                        "doc_kind": "research-report",
                        "candidates": 4,
                        "claim_types": {"forecast": 4},
                        "reason": None,
                    },
                ]
            }
        },
    }
    list_responses = iter(
        [
            _document_page(
                [
                    _document("v-new", "not_attempted"),
                    _document("v-replaced", "failed"),
                    _document("v-done", "extracted"),
                ],
                has_more=True,
                next_cursor="before-page-2",
            ),
            _document_page([_document("v-later", "not_attempted")]),
            _document_page(
                [
                    _document("v-new", "extracted"),
                    _document("v-replaced", "extracted_empty"),
                ],
                has_more=True,
                next_cursor="after-page-2",
            ),
            _document_page(
                [
                    _document("v-done", "extracted"),
                    _document("v-later", "failed"),
                    _document("v-old", "extracted"),
                ]
            ),
        ]
    )
    list_cursors: list[object] = []
    list_case_ids: list[object] = []

    def fake_api(method, path, phase, step, **kwargs):
        assert phase == "P3"
        if method == "GET":
            list_cursors.append((kwargs.get("params") or {}).get("cursor"))
            list_case_ids.append((kwargs.get("params") or {}).get("case_id"))
            return 200, next(list_responses)
        version_id = path.split("/")[-2]
        if version_id == "v-new":
            return 201, {
                "candidate_count": 1,
                "candidates": [
                    {
                        "claim_type": "reported_claim",
                        "quote": "must not persist",
                    }
                ],
                "reason": None,
                "mode": "live-model",
            }
        assert version_id == "v-replaced"
        return 201, {
            "candidate_count": 0,
            "candidates": [],
            "reason": "honest empty result",
            "mode": "live-model",
        }

    monkeypatch.setattr(walkthrough, "api", fake_api)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    result = walkthrough.phase3_extract("case-1", max_docs=2)

    assert list_cursors == [None, "before-page-2", None, "after-page-2"]
    assert list_case_ids == ["case-1"] * 4
    assert result["documents_total"] == 5
    assert result["pending_before"] == 3
    assert result["extracted_this_run"] == 2
    assert result["pending_after"] == 1
    assert result["candidates"] == 3
    assert result["claim_types"] == {
        "reported_claim": 1,
        "research_opinion": 2,
    }
    by_id = {item["version_id"]: item for item in result["per_document"]}
    assert len(by_id) == len(result["per_document"]) == 3
    assert by_id["v-replaced"]["candidates"] == 0
    assert by_id["v-replaced"]["reason"] == "zero_candidates_reported"
    assert all("title" not in item for item in result["per_document"])
    assert "quote" not in repr(result)
    assert "statements" not in result
    assert "kinds" not in result


def test_phase3_pending_after_comes_from_refetched_state(monkeypatch) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    list_responses = iter(
        [
            _document_page([_document("v1", "not_attempted")]),
            _document_page([_document("v1", "failed")]),
        ]
    )

    def fake_api(method, path, phase, step, **kwargs):
        if method == "GET":
            return 200, next(list_responses)
        return 201, {
            "candidate_count": 0,
            "candidates": [],
            "reason": "honest empty result",
            "mode": "live-model",
        }

    monkeypatch.setattr(walkthrough, "api", fake_api)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    result = walkthrough.phase3_extract("case-1", max_docs=1)

    assert result["extracted_this_run"] == 1
    assert result["pending_after"] == 1


def test_phase4_propose_omits_placeholder_roles_from_safe_summary(
    monkeypatch,
) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    monkeypatch.setattr(
        walkthrough,
        "api",
        lambda *_args, **_kwargs: (
            201,
            {
                "mode": "live-model",
                "link_count": 2,
                "links": [{"role": ""}, {"role": "supports"}],
            },
        ),
    )

    result = walkthrough.phase4_propose({"T1": {"id": "thesis-1"}})

    assert result["T1"]["roles"] == {"supports": 1}
    walkthrough._ensure_safe_artifact(
        {"_summary_phases": walkthrough.summary["phases"]}
    )


def test_phase4_propose_rejects_unknown_nonempty_role(monkeypatch) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    monkeypatch.setattr(
        walkthrough,
        "api",
        lambda *_args, **_kwargs: (
            201,
            {
                "mode": "live-model",
                "link_count": 1,
                "links": [{"role": "sentinel-unknown-role"}],
            },
        ),
    )

    with pytest.raises(WalkthroughResponseError, match="role is invalid"):
        walkthrough.phase4_propose({"T1": {"id": "thesis-1"}})


def test_phase3_checkpoint_never_persists_provider_reason_prose(monkeypatch) -> None:
    malicious_reason = (
        "sentinel licensed body https://provider.example/private?"
        "access_token=sentinel-token"
    )
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    responses = iter(
        [
            (200, _document_page([_document("v1", "not_attempted")])),
            (
                201,
                {
                    "candidate_count": 0,
                    "candidates": [],
                    "reason": malicious_reason,
                },
            ),
            (200, _document_page([_document("v1", "extracted_empty")])),
        ]
    )
    checkpoints: list[str] = []
    monkeypatch.setattr(walkthrough, "api", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        walkthrough,
        "_checkpoint",
        lambda state: checkpoints.append(
            json.dumps(
                {"state": state, "summary": walkthrough.summary},
                ensure_ascii=False,
            )
        ),
    )

    result = walkthrough.phase3_extract(
        "case-1", max_docs=1, checkpoint_state={"run_id": "test"}
    )

    assert result["per_document"][0]["reason"] == "zero_candidates_reported"
    persisted = json.dumps(result, ensure_ascii=False) + "".join(checkpoints)
    assert "sentinel" not in persisted
    assert "provider.example" not in persisted


def test_phase3_resume_reclassifies_legacy_untrusted_reason() -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {
            "P3_extract": {
                "per_document": [
                    {
                        "version_id": "v1",
                        "candidates": 0,
                        "claim_types": {},
                        "reason": (
                            "sentinel upstream body https://provider.example/private"
                        ),
                    }
                ]
            }
        },
    }

    resumed = walkthrough._previous_extract_documents()

    assert resumed == [
        {
            "version_id": "v1",
            "candidates": 0,
            "claim_types": {},
            "reason": "zero_candidates_reported",
        }
    ]
    assert "sentinel" not in repr(resumed)


def test_phase3_checkpoints_each_success_and_resume_keeps_prior_batch_work(
    monkeypatch,
) -> None:
    state: dict[str, object] = {"case": {"case_id": "case-1"}}
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    first_run = iter(
        [
            (
                200,
                _document_page(
                    [
                        _document("v1", "not_attempted"),
                        _document("v2", "not_attempted"),
                    ]
                ),
            ),
            (
                201,
                {
                    "candidate_count": 1,
                    "candidates": [{"claim_type": "reported_claim", "quote": "secret"}],
                    "reason": None,
                    "mode": "live-model",
                },
            ),
            (201, {"statement_count": 1, "statements": [{"kind": "forecast"}]}),
        ]
    )
    checkpoints: list[dict[str, object]] = []

    def checkpoint(current: dict[str, object]) -> None:
        current["_summary_phases"] = json.loads(
            json.dumps(walkthrough.summary["phases"])
        )
        checkpoints.append(json.loads(json.dumps(current)))

    monkeypatch.setattr(walkthrough, "api", lambda *_args, **_kwargs: next(first_run))
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_checkpoint", checkpoint)

    with pytest.raises(WalkthroughResponseError, match="candidate_count"):
        walkthrough.phase3_extract("case-1", max_docs=2, checkpoint_state=state)

    assert len(checkpoints) == 1
    persisted = checkpoints[0]["_summary_phases"]["P3_extract"]
    assert persisted["candidates"] == 1
    assert [item["version_id"] for item in persisted["per_document"]] == ["v1"]
    assert "secret" not in json.dumps(checkpoints[0])

    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": json.loads(json.dumps(state["_summary_phases"])),
    }
    second_run = iter(
        [
            (
                200,
                _document_page(
                    [
                        _document("v1", "extracted"),
                        _document("v2", "failed"),
                    ]
                ),
            ),
            (
                201,
                {
                    "candidate_count": 2,
                    "candidates": [
                        {"claim_type": "forecast"},
                        {"claim_type": "forecast"},
                    ],
                    "reason": None,
                    "mode": "live-model",
                },
            ),
            (
                200,
                _document_page(
                    [
                        _document("v1", "extracted"),
                        _document("v2", "extracted"),
                    ]
                ),
            ),
        ]
    )
    monkeypatch.setattr(walkthrough, "api", lambda *_args, **_kwargs: next(second_run))

    result = walkthrough.phase3_extract("case-1", max_docs=2, checkpoint_state=state)

    assert result["candidates"] == 3
    assert result["claim_types"] == {"reported_claim": 1, "forecast": 2}
    assert {item["version_id"] for item in result["per_document"]} == {"v1", "v2"}
    assert result["pending_after"] == 0


def test_phase3_rejects_retired_extraction_response_fields(monkeypatch) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }

    def fake_api(method, path, phase, step, **kwargs):
        if method == "GET":
            return 200, _document_page([_document("v1", "not_attempted")])
        return 201, {
            "statement_count": 2,
            "statements": [{"kind": "reported_claim"}] * 2,
            "mode": "live-model",
        }

    monkeypatch.setattr(walkthrough, "api", fake_api)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    with pytest.raises(WalkthroughResponseError, match="candidate_count"):
        walkthrough.phase3_extract("case-1", max_docs=1)


@pytest.mark.parametrize("malformed_after", [False, True])
def test_phase3_pagination_fails_closed_without_next_cursor(
    monkeypatch, malformed_after: bool
) -> None:
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    valid_page = _document_page([_document("v1", "not_attempted")])
    invalid_page = _document_page(
        [_document("v1", "not_attempted")], has_more=True, next_cursor=None
    )
    list_responses = iter(
        [valid_page, invalid_page] if malformed_after else [invalid_page]
    )

    def fake_api(method, path, phase, step, **kwargs):
        if method == "GET":
            return 200, next(list_responses)
        return 201, {
            "candidate_count": 0,
            "candidates": [],
            "reason": "honest empty result",
            "mode": "live-model",
        }

    monkeypatch.setattr(walkthrough, "api", fake_api)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    with pytest.raises(WalkthroughResponseError, match="next_cursor"):
        walkthrough.phase3_extract("case-1", max_docs=1)


def test_main_checkpoints_completed_p3_before_atomic_review(monkeypatch) -> None:
    state = {"case": {"case_id": "case-1"}}
    checkpoints: list[dict] = []
    monkeypatch.setattr(sys, "argv", ["walkthrough", "--stages", "p3"])
    monkeypatch.setattr(walkthrough, "_initialize_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_load_state", lambda: state)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        walkthrough,
        "phase3_extract",
        lambda *_args, **_kwargs: {"candidates": 1},
    )
    monkeypatch.setattr(
        walkthrough,
        "phase3_review_atomic_claims",
        lambda _case_id: (_ for _ in ()).throw(RuntimeError("review failed")),
    )
    monkeypatch.setattr(
        walkthrough, "_checkpoint", lambda current: checkpoints.append(dict(current))
    )

    with pytest.raises(RuntimeError, match="review failed"):
        walkthrough.main()

    assert len(checkpoints) == 1


def test_main_checkpoints_completed_atomic_reviews(monkeypatch) -> None:
    state = {"case": {"case_id": "case-1"}}
    checkpoints: list[dict] = []
    walkthrough.summary = {
        "run_id": "test",
        "issues": [],
        "facts": {},
        "phases": {},
    }
    monkeypatch.setattr(sys, "argv", ["walkthrough", "--stages", "p3"])
    monkeypatch.setattr(walkthrough, "_initialize_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_load_state", lambda: state)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        walkthrough,
        "phase3_extract",
        lambda *_args, **_kwargs: {"candidates": 1},
    )

    def review(_case_id):
        walkthrough.summary["phases"]["P3_5_atomic_claim_review"] = {"confirmed": 1}
        return {"confirmed": 1}

    monkeypatch.setattr(walkthrough, "phase3_review_atomic_claims", review)
    monkeypatch.setattr(
        walkthrough,
        "_checkpoint",
        lambda current: checkpoints.append(
            json.loads(
                json.dumps(
                    {
                        "state": current,
                        "phases": walkthrough.summary["phases"],
                    }
                )
            )
        ),
    )

    walkthrough.main()

    assert len(checkpoints) == 3
    assert "P3_5_atomic_claim_review" not in checkpoints[0]["phases"]
    assert checkpoints[1]["phases"]["P3_5_atomic_claim_review"] == {"confirmed": 1}


def test_main_replays_two_ingest_rounds_against_saved_case_without_recreating_it(
    monkeypatch,
) -> None:
    saved_case = {
        "case_id": "case-1",
        "theses": {
            "T1": {"id": "thesis-1"},
            "T2": {"id": "thesis-2"},
            "T3": {"id": "thesis-3"},
        },
    }
    state = {
        "case": saved_case,
        "probes": {
            "quote_metrics": {},
            "annual_2025": {},
            "fund_holders": [],
            "smart_fund_selection": {"status": "succeeded", "response_chars": 0},
            "tools": ["FinQuery"],
        },
    }
    ingested_case_ids: list[str] = []
    monkeypatch.setattr(sys, "argv", ["walkthrough", "--stages", "p0_p1_p2"])
    monkeypatch.setattr(walkthrough, "_initialize_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_load_state", lambda: state)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_checkpoint", lambda _state: None)
    monkeypatch.setattr(
        walkthrough,
        "phase0_preflight",
        lambda: (_ for _ in ()).throw(AssertionError("P0 must be reused")),
    )
    monkeypatch.setattr(
        walkthrough,
        "phase1_create_case",
        lambda: (_ for _ in ()).throw(AssertionError("P1 must not repeat")),
    )
    monkeypatch.setattr(
        walkthrough,
        "phase2_ingest",
        lambda case_id: (
            ingested_case_ids.append(case_id) or [{"round": 1}, {"round": 2}]
        ),
    )

    walkthrough.main()

    assert state["case"] is saved_case
    assert ingested_case_ids == ["case-1"]
