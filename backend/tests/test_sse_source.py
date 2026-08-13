"""Mapping and lifecycle tests for the official SSE announcement source."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import (
    RejectedSearchItem,
    SourceAdapter,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.datasources.exchanges.http import ExchangeHttpTransport, SourceProtocolError
from app.datasources.exchanges.sse import SSEAnnouncementSource


FIXTURE = Path(__file__).parent / "fixtures/acquisition/sse-announcements.json"
SEARCH_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
MIRROR_PREFIX = "https://big5.sse.com.cn/site/cht/www.sse.com.cn"


def response_json(value: object) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        content=json.dumps(value, ensure_ascii=False).encode(),
    )


def make_source(handler, **kwargs: object) -> SSEAnnouncementSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset(
            {
                "query.sse.com.cn",
                "www.sse.com.cn",
                "static.sse.com.cn",
                "big5.sse.com.cn",
            }
        ),
    )
    return SSEAnnouncementSource(transport=transport, **kwargs)


def accepted(items):
    return tuple(item for item in items if isinstance(item, SourceReferenceValue))


def rejected(items):
    return tuple(item for item in items if isinstance(item, RejectedSearchItem))


def fallback_id(row: dict) -> str:
    material = json.dumps(
        [
            "sse",
            row["SECURITY_CODE"],
            row["TITLE"],
            row["SSEDATE"],
            row["URL"],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"sse:{hashlib.sha256(material).hexdigest()[:24]}"


def mirror_url(canonical_url: str) -> str:
    return f"{MIRROR_PREFIX}{urlsplit(canonical_url).path}"


def test_descriptor_and_contract_are_exact_and_do_not_widen_policy():
    source = make_source(lambda request: response_json({"pageHelp": {}, "result": []}))

    assert isinstance(source, SourceAdapter)
    assert source.descriptor.adapter_key == "sse"
    assert source.descriptor.provider_identity == "Shanghai Stock Exchange"
    assert source.descriptor.allowed_schemes == frozenset({"https"})
    assert source.descriptor.allowed_hosts == frozenset(
        {
            "query.sse.com.cn",
            "www.sse.com.cn",
            "static.sse.com.cn",
            "big5.sse.com.cn",
        }
    )
    assert source.descriptor.allowed_source_roles == frozenset(
        {"company_disclosure"}
    )
    assert source.descriptor.allowed_hosts <= B_SCOPE_POLICY.exact_hosts
    assert B_SCOPE_POLICY.suffix_hosts == frozenset()


def test_official_fixture_maps_exact_stable_ids_dates_and_pdf_urls():
    fixture = json.loads(FIXTURE.read_text())
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_json(fixture)

    source = make_source(handler)
    results = accepted(
        source.search("寒武纪 688256 年度报告", datetime(2025, 4, 20, tzinfo=UTC))
    )
    rows = fixture["result"]

    assert [item.external_record_id for item in results] == sorted(
        [fallback_id(row) for row in rows]
    )
    assert {item.published_at for item in results} == {
        datetime(2025, 4, 18, 16, 0, tzinfo=UTC)
    }
    assert {item.external_version for item in results} == {"published:2025-04-19"}
    assert all(item.source_role == "company_disclosure" for item in results)
    assert all(item.canonical_url.startswith("https://www.sse.com.cn/") for item in results)
    assert all(item.fetch_locator == {"canonical_pdf_url": item.canonical_url} for item in results)
    assert all(item.metadata["source_publication"] == "2025-04-19" for item in results)
    assert all(item.metadata["publication_timezone"] == "Asia/Shanghai" for item in results)
    for item in results:
        source.descriptor.validate_reference(item)
    assert calls[0].method == "GET"
    assert str(calls[0].url).startswith(SEARCH_URL)
    assert calls[0].headers["Referer"] == "https://www.sse.com.cn/disclosure/listedinfo/announcement/"
    assert calls[0].url.params["productId"] == "688256"
    assert calls[0].url.params["keyWord"] == ""
    assert int(calls[0].url.params["pageHelp.pageSize"]) <= 20


def test_provider_id_is_used_when_official_row_supplies_one():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"] = [{**fixture["result"][0], "BULLETIN_ID": "official-77"}]
    source = make_source(lambda request: response_json(fixture))

    item = accepted(source.search("688256", datetime(2025, 4, 20, tzinfo=UTC)))[0]

    assert item.external_record_id == "sse:official-77"


def test_date_only_cutoff_uses_source_calendar_date_deterministically():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    source = make_source(lambda request: response_json(fixture))

    next_source_day = source.search(
        "688256", datetime(2025, 4, 19, 16, 0, tzinfo=UTC)
    )
    same_source_day = source.search(
        "688256", datetime(2025, 4, 19, 12, 0, tzinfo=UTC)
    )
    prior_source_day = source.search(
        "688256", datetime(2025, 4, 18, 15, 59, 59, tzinfo=UTC)
    )

    assert len(accepted(next_source_day)) == 2
    assert accepted(same_source_day) == ()
    assert {item.reason for item in rejected(same_source_day)} == {
        "publication_time_unresolved"
    }
    assert {
        (
            item.metadata["source_publication"],
            item.metadata["publication_precision"],
            item.metadata["publication_time_resolved"],
        )
        for item in rejected(same_source_day)
    } == {("2025-04-19", "date", False)}
    assert accepted(prior_source_day) == ()
    assert {item.reason for item in rejected(prior_source_day)} == {"after_cutoff"}


def test_date_only_reference_metadata_does_not_claim_exact_availability():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    source = make_source(lambda request: response_json(fixture))

    item = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    assert item.metadata["source_publication"] == "2025-04-19"
    assert item.metadata["publication_precision"] == "date"
    assert item.metadata["publication_time_resolved"] is False


@pytest.mark.parametrize(
    "query",
    [
        "688256",
        "寒武纪 688256 营业收入 2025-01-01 至 2025-12-31 支持 增长 改善",
        "寒武纪 688256 与 688256 年度报告",
    ],
)
def test_search_uses_exactly_one_distinct_code_without_title_keyword(query: str):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_json(
            {
                "pageHelp": {
                    "pageNo": 1,
                    "pageSize": 20,
                    "pageCount": 0,
                    "total": 0,
                },
                "result": [],
            }
        )

    source = make_source(handler)

    assert source.search(query, datetime(2025, 12, 31, tzinfo=UTC)) == ()
    assert len(calls) == 1
    assert calls[0].url.params["productId"] == "688256"
    assert calls[0].url.params["keyWord"] == ""


def test_search_uses_unfiltered_announcement_type_and_maps_temporary_notice():
    calls: list[httpx.Request] = []
    payload = {
        "pageHelp": {"pageNo": 1, "pageSize": 20, "pageCount": 1, "total": 1},
        "result": [
            {
                "SECURITY_CODE": "688256",
                "TITLE": "关于召开临时股东大会的公告",
                "SSEDATE": "2025-04-18",
                "URL": "/disclosure/listedinfo/announcement/temporary.pdf",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_json(payload)

    source = make_source(handler)
    results = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert [item.title for item in accepted(results)] == [
        "关于召开临时股东大会的公告"
    ]
    assert calls[0].url.params["reportType"] == "ALL"
    assert calls[0].url.params["reportType2"] == ""
    assert calls[0].url.params["reportType2"] != "DQBG"


def test_search_rejects_row_outside_requested_security_scope():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"] = [{**fixture["result"][0], "SECURITY_CODE": "600000"}]
    source = make_source(lambda request: response_json(fixture))

    first = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    second = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert accepted(first) == ()
    assert [item.reason for item in rejected(first)] == [
        "security_scope_mismatch"
    ]
    assert (
        rejected(first)[0].external_record_id
        == rejected(second)[0].external_record_id
    )


@pytest.mark.parametrize("query", ["寒武纪 年度报告", "688256 600000 年度报告"])
def test_search_rejects_missing_or_multiple_security_codes_before_network(query: str):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response_json({})

    source = make_source(handler)

    with pytest.raises(ValueError, match="exactly one security code"):
        source.search(query, datetime(2025, 4, 20, tzinfo=UTC))

    assert calls == 0


def test_same_provider_id_with_distinct_publication_versions_returns_both():
    rows = [
        {
            "BULLETIN_ID": "official-77",
            "SECURITY_CODE": "688256",
            "TITLE": "第一版公告",
            "SSEDATE": "2025-04-18",
            "URL": "/disclosure/listedinfo/announcement/first.pdf",
        },
        {
            "BULLETIN_ID": "official-77",
            "SECURITY_CODE": "688256",
            "TITLE": "第二版公告",
            "SSEDATE": "2025-04-19",
            "URL": "/disclosure/listedinfo/announcement/second.pdf",
        },
    ]
    payload = {
        "pageHelp": {"pageNo": 1, "pageSize": 20, "pageCount": 1, "total": 2},
        "result": rows,
    }
    source = make_source(lambda request: response_json(payload))

    results = accepted(
        source.search("688256", datetime(2025, 4, 21, tzinfo=UTC))
    )

    assert len(results) == 2
    assert {item.external_record_id for item in results} == {"sse:official-77"}
    assert {item.external_version for item in results} == {
        "published:2025-04-18",
        "published:2025-04-19",
    }


def test_same_identity_and_version_with_divergent_url_is_conflict():
    base = {
        "BULLETIN_ID": "official-77",
        "SECURITY_CODE": "688256",
        "TITLE": "同一公告",
        "SSEDATE": "2025-04-19",
    }
    payload = {
        "pageHelp": {"pageNo": 1, "pageSize": 20, "pageCount": 1, "total": 2},
        "result": [
            {**base, "URL": "/disclosure/listedinfo/announcement/first.pdf"},
            {**base, "URL": "/disclosure/listedinfo/announcement/second.pdf"},
        ],
    }
    source = make_source(lambda request: response_json(payload))

    results = source.search("688256", datetime(2025, 4, 21, tzinfo=UTC))

    assert len(accepted(results)) == 1
    assert [item.reason for item in rejected(results)] == ["identity_conflict"]


def test_pagination_and_total_results_are_bounded_deterministically():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageHelp.pageNo"])
        calls.append(page)
        rows = [
            {
                "BULLETIN_ID": f"id-{page}-{index:02}",
                "SECURITY_CODE": "688256",
                "TITLE": f"公告 {page}-{index:02}",
                "SSEDATE": "2025-04-19",
                "URL": f"/disclosure/listedinfo/announcement/c/new/2025-04-19/file-{page}-{index:02}.pdf",
            }
            for index in range(20)
        ]
        return response_json(
            {
                "pageHelp": {
                    "pageNo": page,
                    "pageSize": 20,
                    "pageCount": 99,
                    "total": 999,
                },
                "result": rows,
            }
        )

    source = make_source(handler)
    first = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    second = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert calls == [1, 2, 3, 1, 2, 3]
    assert len(first) == 60
    assert [item.external_record_id for item in first] == [
        item.external_record_id for item in second
    ]
    assert tuple(item.external_record_id for item in first) == tuple(
        sorted(item.external_record_id for item in first)
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"pageHelp": {}, "result": {}},
        {"pageHelp": {"pageNo": "1", "pageSize": 2, "pageCount": 1, "total": 2}, "result": []},
        {"pageHelp": {"pageNo": 1, "pageSize": 2, "pageCount": 1, "total": 2}, "result": ["row"]},
    ],
)
def test_malformed_schema_fails_closed(payload: object):
    source = make_source(lambda request: response_json(payload))

    with pytest.raises(SourceProtocolError, match="SSE response schema"):
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("TITLE", "missing_title"),
        ("URL", "missing_pdf_path"),
        ("SSEDATE", "missing_publication_date"),
        ("SECURITY_CODE", "missing_identity"),
    ],
)
def test_partial_invalid_rows_are_recorded_as_safe_rejections(field: str, reason: str):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"][0][field] = ""
    source = make_source(lambda request: response_json(fixture))

    results = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert len(accepted(results)) == 1
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == reason
    assert failures[0].external_record_id.startswith("sse:rejected:")


def test_pdf_path_outside_exact_download_host_is_row_local_rejection():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"][0]["URL"] = "https://evil.sse.com.cn/file.pdf"
    source = make_source(lambda request: response_json(fixture))

    results = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert {item.reason for item in rejected(results)} == {"invalid_pdf_path"}
    assert len(accepted(results)) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/disclosure/%252e%252e/file.pdf",
        "/disclosure/parent%252fchild/file.pdf",
        "/disclosure/parent%255cchild/file.pdf",
    ],
)
def test_recursively_encoded_pdf_path_is_rejected_without_document_request(
    path: str,
):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"][0]["URL"] = path
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.host == "query.sse.com.cn"
        return response_json(fixture)

    source = make_source(handler)

    results = source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))

    assert {item.reason for item in rejected(results)} == {"invalid_pdf_path"}
    assert len(accepted(results)) == 1
    assert len(calls) == 1


def test_absolute_pdf_url_is_canonicalized_before_storage_and_fetch():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    fixture["result"][0]["URL"] = (
        "HTTPS://WWW.SSE.COM.CN:443/disclosure/listedinfo/公告.PDF"
    )
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\ncanonical-spelling",
        )

    source = make_source(handler)
    expected = (
        "https://www.sse.com.cn/disclosure/listedinfo/"
        "%E5%85%AC%E5%91%8A.PDF"
    )

    reference = next(
        item
        for item in accepted(
            source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
        )
        if item.title == fixture["result"][0]["TITLE"]
    )

    assert reference.canonical_url == expected
    assert reference.fetch_locator == {"canonical_pdf_url": expected}
    envelope = source.fetch(reference)
    assert pdf_calls == [expected]
    assert envelope.final_url == expected


def test_search_rejects_blank_query_or_naive_cutoff_before_network():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response_json({})

    source = make_source(handler)

    with pytest.raises(ValueError, match="query"):
        source.search(" ", datetime(2025, 4, 20, tzinfo=UTC))
    with pytest.raises(ValueError, match="cutoff"):
        source.search("688256", datetime(2025, 4, 20))
    assert calls == 0


def test_fetch_requires_exact_tracked_reference_and_caches_duplicate_identity():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "ETag": '"pdf-v1"',
                "X-Request-ID": "sse-request-1",
            },
            content=b"%PDF-1.7\nfixture",
        )

    source = make_source(handler)
    reference = accepted(source.search("688256", datetime(2025, 4, 20, tzinfo=UTC)))[0]
    unknown = SourceReferenceValue(
        adapter_key=reference.adapter_key,
        external_record_id=reference.external_record_id,
        external_version=reference.external_version,
        canonical_url=reference.canonical_url.replace(".pdf", "-other.pdf"),
        title=reference.title,
        published_at=reference.published_at,
        source_role=reference.source_role,
        fetch_locator={"canonical_pdf_url": reference.canonical_url.replace(".pdf", "-other.pdf")},
        metadata=dict(reference.metadata),
    )

    with pytest.raises(ValueError, match="unknown or mismatched"):
        source.fetch(unknown)
    first = source.fetch(reference)
    second = source.fetch(reference)

    assert pdf_calls == [reference.canonical_url]
    assert first is second
    assert first.content == b"%PDF-1.7\nfixture"
    assert first.final_url == reference.canonical_url
    assert first.mime_type == "application/pdf"
    assert first.etag == '"pdf-v1"'
    assert first.provider_request_id == "sse-request-1"
    assert first.metadata["external_record_id"] == reference.external_record_id


def test_fetch_falls_back_from_static_html_to_exact_official_mirror_once():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        requested_url = str(request.url)
        pdf_calls.append(requested_url)
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(
                302,
                headers={
                    "Location": requested_url.replace(
                        "https://www.sse.com.cn/", "https://static.sse.com.cn/"
                    )
                },
            )
        if request.url.host == "static.sse.com.cn":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html>not a PDF</html>",
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nmirror",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    expected_mirror = mirror_url(reference.canonical_url)

    envelope = source.fetch(reference)

    expected_static = reference.canonical_url.replace(
        "https://www.sse.com.cn/", "https://static.sse.com.cn/"
    )
    assert reference.canonical_url.startswith("https://www.sse.com.cn/")
    assert expected_mirror == (
        f"{MIRROR_PREFIX}{urlsplit(reference.canonical_url).path}"
    )
    assert urlsplit(expected_mirror).query == ""
    assert urlsplit(expected_mirror).fragment == ""
    assert pdf_calls == [reference.canonical_url, expected_static, expected_mirror]
    assert envelope.final_url == expected_mirror
    assert envelope.content == b"%PDF-1.7\nmirror"


def test_fetch_valid_canonical_pdf_does_not_invoke_mirror():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\ncanonical",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    envelope = source.fetch(reference)

    assert pdf_calls == [reference.canonical_url]
    assert envelope.final_url == reference.canonical_url
    assert envelope.content == b"%PDF-1.7\ncanonical"


@pytest.mark.parametrize("use_mirror", [False, True], ids=["canonical", "mirror"])
def test_fetch_cache_rejection_does_not_fence_reference(use_mirror: bool):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []
    valid_pdf_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal valid_pdf_calls
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        if use_mirror and request.url.host == "www.sse.com.cn":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html>not a PDF</html>",
            )
        valid_pdf_calls += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\ncache-too-large",
        )

    source = make_source(handler, max_cache_bytes=10)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    for _attempt in range(2):
        with pytest.raises(SourceProtocolError, match="adapter cache limit"):
            source.fetch(reference)

    expected_attempt = (
        [reference.canonical_url, mirror_url(reference.canonical_url)]
        if use_mirror
        else [reference.canonical_url]
    )
    assert pdf_calls == expected_attempt * 2
    assert valid_pdf_calls == 2


@pytest.mark.parametrize(
    ("failure", "expected_exception", "message"),
    [
        ("timeout", SourceUnavailable, "timed out"),
        ("http_status", SourceUnavailable, "unavailable"),
        ("http_4xx", SourceUnavailable, "rejected"),
        ("rate_limit", SourceUnavailable, "rate limited"),
        ("network_error", SourceUnavailable, "network"),
        ("connect_error", SourceUnavailable, "network"),
        ("unsafe_redirect", SourceProtocolError, "redirect"),
        ("unsupported_encoding", SourceProtocolError, "content encoding"),
        ("oversized_body", SourceProtocolError, "byte limit"),
        ("empty_body", SourceProtocolError, "empty body"),
    ],
)
def test_fetch_non_response_type_failures_do_not_invoke_mirror(
    failure: str, expected_exception: type[Exception], message: str
):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        if request.url.host == "big5.sse.com.cn":
            pytest.fail("official mirror must not be requested")
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "http_status":
            return httpx.Response(503)
        if failure == "http_4xx":
            return httpx.Response(404)
        if failure == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "1"})
        if failure == "network_error":
            raise httpx.NetworkError("network failed", request=request)
        if failure == "connect_error":
            raise httpx.ConnectError("connect failed", request=request)
        if failure == "unsafe_redirect":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://evil.static.sse.com.cn/disclosure/file.pdf"
                },
            )
        if failure == "unsupported_encoding":
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Encoding": "gzip",
                },
                stream=httpx.ByteStream(b"%PDF-1.7\nencoded"),
            )
        if failure == "empty_body":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/pdf"},
                content=b"",
            )
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(B_SCOPE_POLICY.max_response_bytes + 1),
            },
            content=b"%PDF-1.7\noversized",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    with pytest.raises(expected_exception, match=message):
        source.fetch(reference)

    assert pdf_calls == [reference.canonical_url]


def test_new_instance_restores_persisted_reference_without_search():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    original = make_source(lambda request: response_json(fixture))
    reference = accepted(
        original.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    calls: list[httpx.Request] = []

    def fetch_only(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host != "query.sse.com.cn"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nrestored",
        )

    recovered = make_source(fetch_only)
    recovered.restore_reference(reference)

    assert recovered.fetch(reference).content == b"%PDF-1.7\nrestored"
    assert [request.method for request in calls] == ["GET"]


def test_restored_reference_falls_back_to_official_mirror_without_search():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    original = make_source(lambda request: response_json(fixture))
    reference = accepted(
        original.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    calls: list[str] = []

    def fetch_only(request: httpx.Request) -> httpx.Response:
        requested_url = str(request.url)
        calls.append(requested_url)
        assert request.url.host != "query.sse.com.cn"
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html>not a PDF</html>",
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nrestored-mirror",
        )

    recovered = make_source(fetch_only)
    recovered.restore_reference(reference)

    envelope = recovered.fetch(reference)

    assert calls == [reference.canonical_url, mirror_url(reference.canonical_url)]
    assert envelope.final_url == mirror_url(reference.canonical_url)
    assert envelope.content == b"%PDF-1.7\nrestored-mirror"


def test_fetch_rejects_mirror_redirect_that_changes_final_path():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        requested_url = str(request.url)
        calls.append(requested_url)
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html>not a PDF</html>",
            )
        if requested_url == mirror_url(reference.canonical_url):
            return httpx.Response(302, headers={"Location": "/changed.pdf"})
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nwrong-path",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    with pytest.raises(SourceProtocolError, match="mirror final PDF URL"):
        source.fetch(reference)

    assert calls == [
        reference.canonical_url,
        mirror_url(reference.canonical_url),
        "https://big5.sse.com.cn/changed.pdf",
    ]


def test_fetch_propagates_invalid_mirror_response_without_further_endpoint():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<html>not a PDF</html>",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    with pytest.raises(SourceProtocolError, match="response type") as caught:
        source.fetch(reference)

    assert caught.value.diagnostics == {"error_type": "response_type"}
    assert calls == [reference.canonical_url, mirror_url(reference.canonical_url)]


@pytest.mark.parametrize(
    "changes",
    [
        {"source_role": "licensed_provider"},
        {"fetch_locator": {}},
        {"metadata": {"provider_identity": "Other Exchange"}},
    ],
)
def test_restore_rejects_unsafe_persisted_sse_reference(changes):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    source = make_source(lambda request: response_json(fixture))
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    values = {
        "adapter_key": reference.adapter_key,
        "external_record_id": reference.external_record_id,
        "external_version": reference.external_version,
        "canonical_url": reference.canonical_url,
        "title": reference.title,
        "published_at": reference.published_at,
        "source_role": reference.source_role,
        "fetch_locator": dict(reference.fetch_locator),
        "metadata": dict(reference.metadata),
    }
    values.update(changes)

    with pytest.raises(ValueError, match="persisted SSE"):
        make_source(lambda request: pytest.fail("network must not run")).restore_reference(
            SourceReferenceValue(**values)
        )


@pytest.mark.parametrize(
    "path",
    [
        "/disclosure/%252e%252e/file.pdf",
        "/disclosure/parent%252fchild/file.pdf",
        "/disclosure/parent%255cchild/file.pdf",
    ],
)
def test_restore_rejects_recursively_encoded_pdf_path_before_network(path: str):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    source = make_source(lambda request: response_json(fixture))
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    malicious_url = f"https://www.sse.com.cn{path}"
    restored = SourceReferenceValue(
        adapter_key=reference.adapter_key,
        external_record_id=reference.external_record_id,
        external_version=reference.external_version,
        canonical_url=malicious_url,
        title=reference.title,
        published_at=reference.published_at,
        source_role=reference.source_role,
        fetch_locator={"canonical_pdf_url": malicious_url},
        metadata=dict(reference.metadata),
    )
    calls = 0

    def fail_network(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return pytest.fail("network must not run")

    recovered = make_source(fail_network)

    with pytest.raises(ValueError, match="persisted SSE"):
        recovered.restore_reference(restored)

    assert calls == 0


def test_fetch_records_exact_official_static_cdn_final_url_and_caches_identity():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(
                301,
                headers={
                    "Location": str(request.url).replace(
                        "https://www.sse.com.cn/", "https://static.sse.com.cn/"
                    )
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nofficial-cdn",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    expected_final = reference.canonical_url.replace(
        "https://www.sse.com.cn/", "https://static.sse.com.cn/"
    )

    first = source.fetch(reference)
    second = source.fetch(reference)

    assert reference.canonical_url.startswith("https://www.sse.com.cn/")
    assert first.final_url == expected_final
    assert first is second
    assert pdf_calls == [reference.canonical_url, expected_final]


@pytest.mark.parametrize("mismatch", ["path", "query"])
def test_fetch_rejects_static_cdn_final_document_identity_mismatch(mismatch: str):
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []
    target = ""

    def handler(request: httpx.Request) -> httpx.Response:
        pdf_calls.append(str(request.url))
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nwrong-static-document",
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]
    static_url = reference.canonical_url.replace(
        "https://www.sse.com.cn/", "https://static.sse.com.cn/"
    )
    target = (
        "https://static.sse.com.cn/unrelated.pdf"
        if mismatch == "path"
        else f"{static_url}?download=1"
    )

    with pytest.raises(SourceProtocolError, match="final PDF URL"):
        source.fetch(reference)

    assert len(pdf_calls) == 3
    assert pdf_calls[-2:] == [reference.canonical_url, target]


def test_fetch_rejects_sse_static_sibling_redirect_without_requesting_it():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            301,
            headers={
                "Location": "https://evil.static.sse.com.cn/disclosure/file.pdf"
            },
        )

    source = make_source(handler)
    reference = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )[0]

    with pytest.raises(SourceProtocolError, match="redirect"):
        source.fetch(reference)

    assert pdf_calls == [reference.canonical_url]


def test_fetch_rejects_redirected_final_pdf_identity_mismatch():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        if request.url.path.endswith("11FJ.pdf"):
            return httpx.Response(307, headers={"Location": "/other.pdf"})
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-x")

    source = make_source(handler)
    reference = next(
        item for item in accepted(source.search("688256", datetime(2025, 4, 20, tzinfo=UTC)))
        if item.canonical_url.endswith("11FJ.pdf")
    )

    with pytest.raises(SourceProtocolError, match="final PDF URL"):
        source.fetch(reference)


def test_evicted_download_is_never_fetched_twice_in_same_lifecycle():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-x",
        )

    source = make_source(handler, max_cache_bytes=10)
    references = accepted(
        source.search("688256", datetime(2025, 4, 20, tzinfo=UTC))
    )
    source.fetch(references[0])
    source.fetch(references[1])

    with pytest.raises(SourceProtocolError, match="evicted"):
        source.fetch(references[0])

    assert pdf_calls.count(references[0].canonical_url) == 1


def test_close_closes_transport_and_clears_reference_and_download_caches():
    fixture = json.loads(FIXTURE.read_text())
    fixture["pageHelp"]["pageCount"] = 1
    source = make_source(lambda request: response_json(fixture))
    reference = accepted(source.search("688256", datetime(2025, 4, 20, tzinfo=UTC)))[0]

    source.close()

    with pytest.raises(ValueError, match="unknown or mismatched"):
        source.fetch(reference)
