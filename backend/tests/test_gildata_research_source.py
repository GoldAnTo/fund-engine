"""Tests for the governed Gildata research/announcement source adapter."""
from __future__ import annotations

import re
import json
import inspect
from datetime import UTC, datetime

import pytest

from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedSearchResult,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.datasources.gildata.client import GildataMCPError
from app.datasources.gildata.research_source import GildataResearchSource
from app.acquisition.policy import B_SCOPE_POLICY


class FakeGildataClient:
    def __init__(
        self,
        *,
        reports: list[dict] | None = None,
        announcements: list[dict] | None = None,
        error: GildataMCPError | None = None,
        errors: dict[str, GildataMCPError] | None = None,
        raw_responses: dict[str, str] | None = None,
    ) -> None:
        self.reports = reports or []
        self.announcements = announcements or []
        self.error = error
        self.errors = errors or {}
        self.raw_responses = raw_responses or {}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def call_tool(self, name: str, arguments: dict, timeout: float = 60) -> str:
        self.calls.append((name, dict(arguments)))
        if self.error is not None:
            raise self.error
        if name in self.errors:
            raise self.errors[name]
        if name in self.raw_responses:
            return self.raw_responses[name]
        if name == "FinancialResearchReport":
            results = self.reports
        elif name == "AnnouncementData":
            results = self.announcements
        else:
            raise AssertionError(f"unexpected tool {name!r}")
        wrapped = [
            {"table_markdown": json.dumps(item, ensure_ascii=False)}
            for item in results
        ]
        return json.dumps({"code": "0", "results": wrapped}, ensure_ascii=False)

    def close(self) -> None:
        self.closed = True


REPORT = {
    "报告标题": " 示例公司  收入跟踪 ",
    "发布时间": "2026-08-01",
    "撰写机构": "示例机构",
    "作者": "研究员甲",
    "证券代码": "600000",
    "原文": "示例公司收入同比增长。",
}

ANNOUNCEMENT = {
    "公告标题": "示例公司关于收入情况的公告",
    "公告日期": "2026-07-31",
    "股票代码": "600000",
    "证券简称": "示例公司",
    "公告内容": "示例公司公告收入情况。",
}


def make_source(**kwargs: object) -> tuple[GildataResearchSource, FakeGildataClient]:
    client = FakeGildataClient(**kwargs)  # type: ignore[arg-type]
    return GildataResearchSource(client), client


def retrieved(items):
    return tuple(item for item in items if isinstance(item, RetrievedSearchResult))


def accepted(items):
    return tuple(item.reference for item in retrieved(items))


def rejected(items: tuple[SourceReferenceValue | RejectedSearchItem, ...]):
    return tuple(item for item in items if isinstance(item, RejectedSearchItem))


def raw_inner(
    results: list[dict] | None = None,
    *,
    code: str = "0",
    include_results: bool = True,
) -> str:
    value: dict[str, object] = {"code": code}
    if include_results:
        value["results"] = results or []
    return json.dumps(value, ensure_ascii=False)


def test_descriptor_declares_internal_gildata_url_boundary():
    source, _client = make_source()

    assert source.descriptor.adapter_key == "gildata"
    assert source.descriptor.provider_identity == "Gildata"
    assert source.descriptor.allowed_schemes == frozenset({"gildata"})
    assert source.descriptor.allowed_hosts == frozenset(
        {"research-report", "announcement"}
    )


def test_search_maps_reports_and_announcements_to_governed_references():
    source, client = make_source(reports=[REPORT], announcements=[ANNOUNCEMENT])

    results = source.search("示例公司 收入", datetime(2026, 8, 1, tzinfo=UTC))

    references = accepted(results)
    assert len(references) == 2
    report = next(
        item
        for item in references
        if item.metadata["source_type"] == "research_report"
    )
    announcement = next(
        item
        for item in references
        if item.metadata["source_type"] == "announcement"
    )
    assert report.title == "示例公司 收入跟踪"
    assert report.published_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert report.external_record_id.startswith("report:600000:2026-08-01:")
    assert report.external_version == "published:2026-08-01"
    assert report.canonical_url == (
        f"gildata://research-report/{report.external_record_id}"
    )
    assert report.metadata == {
        "source_type": "research_report",
        "security_code": "600000",
        "publisher": "示例机构",
        "author": "研究员甲",
    }
    assert announcement.external_record_id.startswith(
        "announcement:600000:2026-07-31:"
    )
    assert announcement.canonical_url == (
        f"gildata://announcement/{announcement.external_record_id}"
    )
    assert announcement.metadata == {
        "source_type": "announcement",
        "security_code": "600000",
        "security_name": "示例公司",
        "issuer_identity": "示例公司",
        "provider_identity": "Gildata",
    }
    assert [call[0] for call in client.calls] == [
        "FinancialResearchReport",
        "AnnouncementData",
    ]


def test_search_returns_transient_envelopes_without_caching_body_in_reference():
    source, _client = make_source(reports=[REPORT])

    result = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))[0]

    assert type(result).__name__ == "RetrievedSearchResult"
    assert result.reference.metadata["source_type"] == "research_report"
    assert result.envelope.content == REPORT["原文"].encode("utf-8")
    assert REPORT["原文"] not in repr(dict(result.reference.metadata))
    assert not hasattr(source, "_payloads")


def test_stable_identity_ignores_result_order_and_normalizes_title():
    report_variant = {**REPORT, "报告标题": "示例公司\u3000收入跟踪"}
    other = {
        **REPORT,
        "报告标题": "示例公司利润跟踪",
        "原文": "利润增长。",
    }
    first, _ = make_source(reports=[REPORT, other])
    second, _ = make_source(reports=[other, report_variant])

    first_ids = {
        item.title: item.external_record_id
        for item in accepted(
            first.search("query", datetime(2026, 8, 2, tzinfo=UTC))
        )
    }
    second_ids = {
        item.title: item.external_record_id
        for item in accepted(
            second.search("query", datetime(2026, 8, 2, tzinfo=UTC))
        )
    }

    assert first_ids == second_ids


def test_within_search_divergent_payloads_reject_ambiguous_identity():
    variant = {**REPORT, "原文": "同一身份的不同正文。"}
    source, _client = make_source(reports=[REPORT, variant])

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert accepted(results) == ()
    conflicts = rejected(results)
    assert len(conflicts) == 1
    assert conflicts[0].reason == "variant_conflict"


def test_cross_search_payload_conflict_keeps_old_reference_and_bytes():
    source, client = make_source(reports=[REPORT])
    original = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    original_bytes = original.envelope.content
    client.reports = [{**REPORT, "原文": "发生漂移的新正文。"}]

    later = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert accepted(later) == ()
    assert [item.reason for item in rejected(later)] == ["variant_conflict"]
    assert original.envelope.content == original_bytes


def test_variant_conflict_tombstone_survives_later_single_variant():
    source, client = make_source(reports=[REPORT])
    original_result = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    original = original_result.reference
    original_bytes = original_result.envelope.content
    client.reports = [{**REPORT, "原文": "冲突正文。"}]
    assert [
        item.reason
        for item in rejected(
            source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
        )
    ] == ["variant_conflict"]
    client.reports = [REPORT]

    after_conflict = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert accepted(after_conflict) == ()
    assert [item.reason for item in rejected(after_conflict)] == [
        "variant_conflict"
    ]
    assert original_result.envelope.content == original_bytes


def test_close_clears_variant_conflict_tombstones_for_new_lifecycle():
    variant = {**REPORT, "原文": "冲突正文。"}
    source, client = make_source(reports=[REPORT, variant])
    assert [
        item.reason
        for item in rejected(
            source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
        )
    ] == ["variant_conflict"]
    source.close()
    client.reports = [REPORT]

    restarted = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(restarted)) == 1
    assert rejected(restarted) == ()


def test_same_payload_metadata_drift_reuses_original_cached_reference():
    source, client = make_source(reports=[REPORT])
    original = accepted(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    client.reports = [{**REPORT, "作者": "漂移后的作者"}]

    repeated_result = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    repeated = repeated_result.reference

    assert repeated == original
    assert repeated.metadata["author"] == "研究员甲"
    assert repeated_result.envelope.content == REPORT["原文"].encode("utf-8")


@pytest.mark.parametrize(
    ("kind", "item", "reason"),
    [
        ("reports", {**REPORT, "发布时间": ""}, "missing_publication_date"),
        ("reports", {**REPORT, "原文": ""}, "missing_body"),
        ("reports", {**REPORT, "撰写机构": ""}, "missing_provider_identity"),
        (
            "announcements",
            {**ANNOUNCEMENT, "公告日期": ""},
            "missing_publication_date",
        ),
        ("announcements", {**ANNOUNCEMENT, "公告内容": ""}, "missing_body"),
    ],
)
def test_search_returns_rejected_items_for_unusable_provider_rows(
    kind: str, item: dict, reason: str
):
    source, _client = make_source(**{kind: [item]})

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(results) == 1
    assert results[0].reason == reason
    if reason == "missing_publication_date":
        assert results[0].published_at is None


def test_search_rejects_after_cutoff_deterministically():
    source, _client = make_source(reports=[REPORT])

    results = source.search("query", datetime(2026, 7, 31, 23, 59, tzinfo=UTC))

    assert accepted(results) == ()
    assert len(rejected(results)) == 1
    assert rejected(results)[0].reason == "after_cutoff"
    assert rejected(results)[0].published_at == datetime(2026, 8, 1, tzinfo=UTC)


def test_invalid_date_and_security_code_use_only_safe_fixed_id_markers():
    malicious = {
        **REPORT,
        "发布时间": "2026-13-40/../../bad-date",
        "证券代码": "600000/../../bad-code",
    }
    source, _client = make_source(reports=[malicious])

    result = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))[0]

    assert isinstance(result, RejectedSearchItem)
    assert result.reason == "invalid_publication_date"
    assert re.fullmatch(r"report:unknown:invalid-date:[0-9a-f]{16}", result.external_record_id)
    assert result.metadata["security_code"] == "unknown"
    assert ".." not in repr(result)


def test_credential_or_control_provider_fields_become_generic_safe_rejection():
    malicious = {
        **REPORT,
        "报告标题": "Authorization: Basic top-secret",
        "发布时间": "2026-08-01\nTOKEN=top-secret",
        "证券代码": "600000\x00bad",
    }
    source, _client = make_source(reports=[malicious])

    result = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))[0]

    assert isinstance(result, RejectedSearchItem)
    assert result.reason == "unsafe_provider_data"
    assert result.title == "Rejected provider item"
    assert re.fullmatch(r"report:unknown:invalid-date:[0-9a-f]{16}", result.external_record_id)
    rendered = repr(result)
    assert "top-secret" not in rendered
    assert "TOKEN" not in rendered
    assert "Authorization" not in rendered


def test_distinct_unsafe_rows_have_distinct_opaque_safe_rejections():
    unsafe_a = {
        **REPORT,
        "报告标题": "Authorization: Basic first-secret",
    }
    unsafe_b = {
        **REPORT,
        "报告标题": "Cookie: session=second-secret",
    }
    source, _client = make_source(reports=[unsafe_a, unsafe_b])

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert accepted(results) == ()
    unsafe_results = rejected(results)
    assert len(unsafe_results) == 2
    assert {item.reason for item in unsafe_results} == {"unsafe_provider_data"}
    assert len({item.external_record_id for item in unsafe_results}) == 2
    for item in unsafe_results:
        assert re.fullmatch(
            r"report:unknown:invalid-date:[0-9a-f]{16}",
            item.external_record_id,
        )
        rendered = repr(item)
        assert "first-secret" not in rendered
        assert "second-secret" not in rendered
        assert "Authorization" not in rendered
        assert "Cookie" not in rendered


def test_search_rejects_naive_cutoff_without_provider_calls():
    source, client = make_source(reports=[REPORT])

    with pytest.raises(ValueError, match="cutoff"):
        source.search("query", datetime(2026, 8, 2))

    assert client.calls == []


def test_report_success_survives_sanitized_announcement_failure():
    error = GildataMCPError("https://provider.test?token=top-secret")
    source, _client = make_source(
        reports=[REPORT], errors={"AnnouncementData": error}
    )

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(results)) == 1
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == "source_unavailable"
    assert failures[0].published_at is None
    assert failures[0].metadata == {
        "error_class": "provider_operation_failure",
        "operation": "announcement",
        "provider_identity": "Gildata",
        "retryable": True,
        "source_type": "announcement",
    }
    assert "top-secret" not in repr(failures[0])


def test_announcement_success_survives_sanitized_report_failure():
    error = GildataMCPError("Authorization: Basic top-secret")
    source, _client = make_source(
        announcements=[ANNOUNCEMENT], errors={"FinancialResearchReport": error}
    )

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(results)) == 1
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == "source_unavailable"
    assert failures[0].metadata["operation"] == "research_report"
    assert failures[0].metadata["error_class"] == "provider_operation_failure"
    assert failures[0].metadata["retryable"] is True
    assert "top-secret" not in repr(failures[0])


def test_both_operation_failures_raise_context_free_source_unavailable():
    source, _client = make_source(
        errors={
            "FinancialResearchReport": GildataMCPError("token=report-secret"),
            "AnnouncementData": GildataMCPError("Cookie: announcement-secret"),
        }
    )

    with pytest.raises(SourceUnavailable) as caught:
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret" not in str(caught.value).casefold()


@pytest.mark.parametrize(
    "raw_response",
    [
        "not-json token=top-secret",
        raw_inner(include_results=False),
        raw_inner([], code="provider-error"),
        json.dumps({"code": "0", "results": {}}),
        json.dumps({"code": False, "results": []}),
    ],
)
def test_strict_inner_response_validation_never_false_succeeds(
    raw_response: str,
):
    source, _client = make_source(
        raw_responses={
            "FinancialResearchReport": raw_response,
            "AnnouncementData": raw_response,
        }
    )

    with pytest.raises(SourceUnavailable) as caught:
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "top-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"table_markdown": 123},
        {"table_markdown": "   "},
        {"table_markdown": "not a usable row"},
        {
            "table_markdown": json.dumps(
                {"报告标题": 123, "原文": "otherwise usable"}
            )
        },
    ],
)
def test_strict_inner_result_schema_rejects_malformed_listed_items(result: dict):
    response = raw_inner([result])
    source, _client = make_source(
        raw_responses={
            "FinancialResearchReport": response,
            "AnnouncementData": response,
        }
    )

    with pytest.raises(SourceUnavailable) as caught:
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_empty_results_remain_a_legitimate_empty_search():
    empty = raw_inner([])
    source, _client = make_source(
        raw_responses={
            "FinancialResearchReport": empty,
            "AnnouncementData": empty,
        }
    )

    assert source.search("query", datetime(2026, 8, 2, tzinfo=UTC)) == ()


def test_malformed_nested_report_is_isolated_from_valid_announcement():
    malformed = raw_inner([{"table_markdown": "not a usable row"}])
    source, _client = make_source(
        announcements=[ANNOUNCEMENT],
        raw_responses={"FinancialResearchReport": malformed},
    )

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(results)) == 1
    assert accepted(results)[0].metadata["source_type"] == "announcement"
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == "source_unavailable"
    assert failures[0].metadata["operation"] == "research_report"


def test_mixed_json_list_fails_closed_without_blocking_other_operation():
    mixed = raw_inner(
        [
            {
                "table_markdown": json.dumps(
                    [REPORT, 42], ensure_ascii=False
                )
            }
        ]
    )
    source, _client = make_source(
        announcements=[ANNOUNCEMENT],
        raw_responses={"FinancialResearchReport": mixed},
    )

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(results)) == 1
    assert accepted(results)[0].metadata["source_type"] == "announcement"
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == "source_unavailable"
    assert failures[0].metadata["operation"] == "research_report"
    assert failures[0].metadata["retryable"] is True


@pytest.mark.parametrize(
    ("bad_kind", "good_kind", "failed_operation", "accepted_source_type"),
    [
        (
            "reports",
            "announcements",
            "research_report",
            "announcement",
        ),
        (
            "announcements",
            "reports",
            "announcement",
            "research_report",
        ),
    ],
)
def test_provider_unicode_mapping_failure_is_isolated_per_operation(
    bad_kind: str,
    good_kind: str,
    failed_operation: str,
    accepted_source_type: str,
):
    bad_row = (
        {**REPORT, "报告标题": "bad\ud800title"}
        if bad_kind == "reports"
        else {**ANNOUNCEMENT, "公告标题": "bad\ud800title"}
    )
    good_row = REPORT if good_kind == "reports" else ANNOUNCEMENT
    source, _client = make_source(**{bad_kind: [bad_row], good_kind: [good_row]})

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(results)) == 1
    assert accepted(results)[0].metadata["source_type"] == accepted_source_type
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == "source_unavailable"
    assert failures[0].metadata == {
        "error_class": "provider_operation_failure",
        "operation": failed_operation,
        "provider_identity": "Gildata",
        "retryable": True,
        "source_type": failed_operation,
    }
    assert "bad" not in repr(failures[0])


def test_code_less_announcements_use_distinct_issuer_identity():
    issuer_a = {**ANNOUNCEMENT, "股票代码": "", "证券简称": "甲公司"}
    issuer_b = {**ANNOUNCEMENT, "股票代码": "", "证券简称": "乙公司"}
    source, _client = make_source(announcements=[issuer_a, issuer_b])

    announcements = accepted(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )

    assert len(announcements) == 2
    assert len({item.external_record_id for item in announcements}) == 2
    assert {item.metadata["issuer_identity"] for item in announcements} == {
        "甲公司",
        "乙公司",
    }
    assert {item.metadata["provider_identity"] for item in announcements} == {
        "Gildata"
    }
    assert all("publisher" not in item.metadata for item in announcements)


def test_announcement_without_issuer_discriminator_is_rejected():
    source, _client = make_source(
        announcements=[{**ANNOUNCEMENT, "股票代码": "", "证券简称": ""}]
    )

    result = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))[0]

    assert isinstance(result, RejectedSearchItem)
    assert result.reason == "missing_provider_identity"
    assert result.metadata["provider_identity"] == "Gildata"
    assert result.metadata["issuer_identity"] == "unknown"


def test_offset_publication_keeps_source_calendar_identity_date():
    offset_report = {**REPORT, "发布时间": "2026-08-01T00:30:00+08:00"}
    source, _client = make_source(reports=[offset_report])

    report = accepted(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]

    assert ":2026-08-01:" in report.external_record_id
    assert report.external_version == "published:2026-08-01"
    assert report.published_at == datetime(2026, 7, 31, 16, 30, tzinfo=UTC)


def test_fetch_returns_exact_cached_provider_text_without_another_call():
    source, client = make_source(reports=[REPORT])
    result = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    reference = result.reference
    calls_after_search = tuple(client.calls)

    envelope = result.envelope

    assert envelope.content == REPORT["原文"].encode("utf-8")
    assert envelope.mime_type == "text/plain; charset=utf-8"
    assert envelope.final_url == reference.canonical_url
    assert envelope.etag is None
    assert envelope.last_modified is None
    assert envelope.provider_request_id is None
    assert envelope.metadata == {
        "adapter_key": "gildata",
        "external_record_id": reference.external_record_id,
        "source_type": "research_report",
        "provider_identity": "示例机构",
    }
    assert tuple(client.calls) == calls_after_search


def test_fetch_preserves_provider_payload_whitespace_byte_for_byte():
    exact_body = "  第一行。\n第二行。\n"
    source, _client = make_source(reports=[{**REPORT, "原文": exact_body}])
    result = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]

    envelope = result.envelope

    assert envelope.content == exact_body.encode("utf-8")


def test_cache_limit_default_is_positive_and_within_b_scope_limit():
    default = inspect.signature(GildataResearchSource).parameters[
        "max_cache_bytes"
    ].default

    assert 0 < default <= B_SCOPE_POLICY.max_response_bytes


def test_cache_limit_must_be_positive():
    client = FakeGildataClient()

    with pytest.raises(ValueError, match="max_cache_bytes"):
        GildataResearchSource(client, max_cache_bytes=0)


def test_oversized_payload_is_rejected_without_entering_cache():
    body = "12345"
    client = FakeGildataClient(reports=[{**REPORT, "原文": body}])
    source = GildataResearchSource(client, max_cache_bytes=4)

    results = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert accepted(results) == ()
    assert [item.reason for item in rejected(results)] == ["payload_too_large"]


def test_payload_limit_applies_per_transient_result_without_body_cache():
    first_row = {**REPORT, "原文": "1234"}
    second_row = {
        **REPORT,
        "报告标题": "示例公司利润跟踪",
        "原文": "5678",
    }
    client = FakeGildataClient(reports=[first_row])
    source = GildataResearchSource(client, max_cache_bytes=4)
    first = retrieved(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    client.reports = [second_row]

    later = source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert len(accepted(later)) == 1
    assert rejected(later) == ()
    assert first.envelope.content == b"1234"


def test_fetch_rejects_unknown_or_mismatched_reference_without_provider_call():
    source, client = make_source(reports=[REPORT])
    reference = accepted(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]
    calls_after_search = tuple(client.calls)
    unknown = type(reference)(
        adapter_key=reference.adapter_key,
        external_record_id=reference.external_record_id + "x",
        external_version=reference.external_version,
        canonical_url=reference.canonical_url + "x",
        title=reference.title,
        published_at=reference.published_at,
        source_role=reference.source_role,
        fetch_locator={"record_id": reference.external_record_id + "x"},
        metadata=reference.metadata,
    )

    with pytest.raises(SourceUnavailable) as caught:
        source.fetch(unknown)

    assert tuple(client.calls) == calls_after_search
    assert caught.value.retryable is False
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_gildata_error_becomes_retryable_sanitized_source_unavailable():
    source, _client = make_source(
        error=GildataMCPError(
            "HTTP error at https://api.gildata.test/path?token=top-secret&x=1 "
            "Authorization: Bearer also-secret"
        )
    )

    with pytest.raises(SourceUnavailable) as caught:
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))

    assert caught.value.retryable is True
    rendered = f"{caught.value} {dict(caught.value.diagnostics)}".lower()
    assert "top-secret" not in rendered
    assert "also-secret" not in rendered
    assert "token=" not in rendered
    assert "authorization" not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_close_delegates_to_client():
    source, client = make_source()

    source.close()

    assert client.closed is True


def test_close_clears_identity_state_and_persisted_fetch_fails_closed():
    source, _client = make_source(reports=[REPORT])
    reference = accepted(
        source.search("query", datetime(2026, 8, 2, tzinfo=UTC))
    )[0]

    source.close()

    with pytest.raises(SourceUnavailable) as caught:
        source.fetch(reference)
    assert caught.value.retryable is False
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
