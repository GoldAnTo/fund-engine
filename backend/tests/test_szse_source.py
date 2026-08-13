"""Mapping and lifecycle tests for the official SZSE announcement source."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import RejectedSearchItem, SourceAdapter, SourceReferenceValue
from app.datasources.exchanges.http import ExchangeHttpTransport, SourceProtocolError
from app.datasources.exchanges.szse import SZSEAnnouncementSource


FIXTURE = Path(__file__).parent / "fixtures/acquisition/szse-announcements.json"
SEARCH_URL = "https://www.szse.cn/api/disc/announcement/annList"


def response_json(value: object) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        content=json.dumps(value, ensure_ascii=False).encode(),
    )


def make_source(handler, **kwargs: object) -> SZSEAnnouncementSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset({"www.szse.cn", "disc.static.szse.cn"}),
    )
    return SZSEAnnouncementSource(transport=transport, **kwargs)


def accepted(items):
    return tuple(item for item in items if isinstance(item, SourceReferenceValue))


def rejected(items):
    return tuple(item for item in items if isinstance(item, RejectedSearchItem))


def fallback_id(row: dict) -> str:
    material = json.dumps(
        [
            "szse",
            row["secCode"][0],
            " ".join(unicodedata.normalize("NFKC", row["title"]).split()),
            row["publishTime"],
            row["attachPath"],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"szse:{hashlib.sha256(material).hexdigest()[:24]}"


def test_descriptor_and_contract_are_exact_and_do_not_widen_policy():
    source = make_source(lambda request: response_json({"announceCount": 0, "data": []}))

    assert isinstance(source, SourceAdapter)
    assert source.descriptor.adapter_key == "szse"
    assert source.descriptor.provider_identity == "Shenzhen Stock Exchange"
    assert source.descriptor.allowed_schemes == frozenset({"https"})
    assert source.descriptor.allowed_hosts == frozenset(
        {"www.szse.cn", "disc.static.szse.cn"}
    )
    assert source.descriptor.allowed_source_roles == frozenset(
        {"company_disclosure"}
    )
    assert source.descriptor.allowed_hosts <= B_SCOPE_POLICY.exact_hosts
    assert B_SCOPE_POLICY.suffix_hosts == frozenset()


def test_official_fixture_maps_provider_ids_timestamp_and_pdf_urls():
    fixture = json.loads(FIXTURE.read_text())
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_json(fixture)

    source = make_source(handler)
    results = accepted(
        source.search("宁德时代 300750 回购", datetime(2025, 4, 30, tzinfo=UTC))
    )

    assert [item.external_record_id for item in results] == [
        "szse:2953ce30-a77f-4d06-be87-977e255845fe",
        "szse:55b15183-e43e-4ad5-b47d-2b6b46c6356e",
    ]
    by_id = {item.external_record_id: item for item in results}
    assert by_id["szse:55b15183-e43e-4ad5-b47d-2b6b46c6356e"].published_at == datetime(
        2025, 4, 25, 11, 56, 13, tzinfo=UTC
    )
    assert all(item.published_at.tzinfo is UTC for item in results)
    assert all(item.canonical_url.startswith("https://disc.static.szse.cn/") for item in results)
    assert all(item.fetch_locator == {"canonical_pdf_url": item.canonical_url} for item in results)
    assert all(item.metadata["publication_timezone"] == "Asia/Shanghai" for item in results)
    for item in results:
        source.descriptor.validate_reference(item)
    assert calls[0].method == "POST"
    assert str(calls[0].url) == SEARCH_URL
    assert calls[0].headers["Referer"] == "https://www.szse.cn/disclosure/listed/notice/index.html"
    body = json.loads(calls[0].content)
    assert body["stock"] == ["300750"]
    assert body["channelCode"] == ["listedNotice_disc"]
    assert body["pageSize"] <= 20
    assert body["pageNum"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "300750",
        "宁德时代 300750 营业收入 2025-01-01 至 2025-12-31 反证 下滑 风险",
        "宁德时代 300750 与 300750 公告",
    ],
)
def test_search_uses_exactly_one_distinct_stock_code(query: str):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_json({"announceCount": 0, "data": []})

    source = make_source(handler)

    assert source.search(query, datetime(2025, 12, 31, tzinfo=UTC)) == ()
    assert len(calls) == 1
    assert json.loads(calls[0].content)["stock"] == ["300750"]


def test_search_rejects_row_outside_requested_security_scope():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 1
    fixture["data"] = [{**fixture["data"][0], "secCode": ["000001"]}]
    source = make_source(lambda request: response_json(fixture))

    first = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))
    second = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))

    assert accepted(first) == ()
    assert [item.reason for item in rejected(first)] == [
        "security_scope_mismatch"
    ]
    assert (
        rejected(first)[0].external_record_id
        == rejected(second)[0].external_record_id
    )


@pytest.mark.parametrize("query", ["宁德时代 年度报告", "300750 000001 年度报告"])
def test_search_rejects_missing_or_multiple_security_codes_before_network(query: str):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response_json({})

    source = make_source(handler)

    with pytest.raises(ValueError, match="exactly one security code"):
        source.search(query, datetime(2025, 4, 30, tzinfo=UTC))

    assert calls == 0


def test_ann_id_is_used_when_string_id_is_absent():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 1
    fixture["data"] = [{**fixture["data"][0], "id": None}]
    source = make_source(lambda request: response_json(fixture))

    item = accepted(source.search("300750", datetime(2025, 4, 30, tzinfo=UTC)))[0]

    assert item.external_record_id == "szse:1223318248"


def test_deterministic_fallback_is_used_when_official_provider_ids_are_absent():
    fixture = json.loads(FIXTURE.read_text())
    row = {**fixture["data"][0], "id": None, "annId": None}
    fixture["announceCount"] = 1
    fixture["data"] = [row]
    source = make_source(lambda request: response_json(fixture))

    item = accepted(source.search("300750", datetime(2025, 4, 30, tzinfo=UTC)))[0]

    assert item.external_record_id == fallback_id(row)


def test_naive_official_timestamp_is_interpreted_as_asia_shanghai_for_cutoff():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 1
    fixture["data"] = [fixture["data"][0]]
    source = make_source(lambda request: response_json(fixture))

    exact = source.search("300750", datetime(2025, 4, 25, 11, 56, 13, tzinfo=UTC))
    before = source.search("300750", datetime(2025, 4, 25, 11, 56, 12, tzinfo=UTC))

    assert len(accepted(exact)) == 1
    assert accepted(before) == ()
    assert rejected(before)[0].reason == "after_cutoff"


@pytest.mark.parametrize(
    "publication",
    [
        "2025-04-25",
        "2025-04-25T19:56:13",
        "2025-04-25 19:56:13Z",
        "2025-04-25 19:56:13+08:00",
        "2025-04-25 19:56:13.123",
        "2025-4-25 19:56:13",
        "not-a-date",
    ],
)
def test_noncanonical_official_timestamp_is_rejected(publication: str):
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 1
    fixture["data"] = [{**fixture["data"][0], "publishTime": publication}]
    source = make_source(lambda request: response_json(fixture))

    result = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))[0]

    assert isinstance(result, RejectedSearchItem)
    assert result.reason == "invalid_publication_date"


def test_same_provider_id_with_distinct_publication_versions_returns_both():
    rows = [
        {
            "id": "official-77",
            "annId": 77,
            "title": "第一版公告",
            "publishTime": "2025-04-24 12:00:00",
            "secCode": ["300750"],
            "attachPath": "/disc/final/first.PDF",
        },
        {
            "id": "official-77",
            "annId": 77,
            "title": "第二版公告",
            "publishTime": "2025-04-25 12:00:00",
            "secCode": ["300750"],
            "attachPath": "/disc/final/second.PDF",
        },
    ]
    source = make_source(
        lambda request: response_json({"announceCount": 2, "data": rows})
    )

    results = accepted(
        source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))
    )

    assert len(results) == 2
    assert {item.external_record_id for item in results} == {"szse:official-77"}
    assert {item.external_version for item in results} == {
        "published:2025-04-24 12:00:00",
        "published:2025-04-25 12:00:00",
    }


def test_same_identity_and_version_with_divergent_url_is_conflict():
    base = {
        "id": "official-77",
        "annId": 77,
        "title": "同一公告",
        "publishTime": "2025-04-25 12:00:00",
        "secCode": ["300750"],
    }
    rows = [
        {**base, "attachPath": "/disc/final/first.PDF"},
        {**base, "attachPath": "/disc/final/second.PDF"},
    ]
    source = make_source(
        lambda request: response_json({"announceCount": 2, "data": rows})
    )

    results = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))

    assert len(accepted(results)) == 1
    assert [item.reason for item in rejected(results)] == ["identity_conflict"]


def test_pagination_and_result_count_stop_at_policy_bound():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        page = body["pageNum"]
        calls.append(page)
        rows = [
            {
                "id": f"official-{page}-{index:02}",
                "annId": page * 100 + index,
                "title": f"公告 {page}-{index:02}",
                "publishTime": "2025-04-25 12:00:00",
                "secCode": ["300750"],
                "attachPath": f"/disc/final/file-{page}-{index:02}.PDF",
            }
            for index in range(20)
        ]
        return response_json({"announceCount": 999, "data": rows})

    source = make_source(handler)
    results = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))

    assert calls == [1, 2, 3]
    assert len(results) == 60
    assert tuple(item.external_record_id for item in results) == tuple(
        sorted(item.external_record_id for item in results)
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"announceCount": "2", "data": []},
        {"announceCount": 2, "data": {}},
        {"announceCount": 2, "data": ["row"]},
    ],
)
def test_malformed_schema_fails_closed(payload: object):
    source = make_source(lambda request: response_json(payload))

    with pytest.raises(SourceProtocolError, match="SZSE response schema"):
        source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("title", "", "missing_title"),
        ("attachPath", "", "missing_pdf_path"),
        ("publishTime", "", "missing_publication_date"),
        ("secCode", [], "missing_identity"),
    ],
)
def test_partial_invalid_rows_are_recorded_as_safe_rejections(
    field: str, value: object, reason: str
):
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 2
    fixture["data"][0][field] = value
    source = make_source(lambda request: response_json(fixture))

    results = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))

    assert len(accepted(results)) == 1
    failures = rejected(results)
    assert len(failures) == 1
    assert failures[0].reason == reason
    assert failures[0].external_record_id.startswith("szse:rejected:")


def test_attachment_path_outside_exact_static_host_is_rejected_without_fetch():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 2
    fixture["data"][0]["attachPath"] = "https://www.szse.cn/not-static.PDF"
    source = make_source(lambda request: response_json(fixture))

    results = source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))

    assert {item.reason for item in rejected(results)} == {"invalid_pdf_path"}
    assert len(accepted(results)) == 1


def test_fetch_unknown_reference_never_uses_network_and_duplicate_is_cached():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 2
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "www.szse.cn":
            return response_json(fixture)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Last-Modified": "Fri, 25 Apr 2025 11:56:13 GMT",
                "X-SZSE-Request-ID": "szse-request-1",
            },
            content=b"%PDF-1.7\nszse",
        )

    source = make_source(handler)
    reference = accepted(source.search("300750", datetime(2025, 4, 30, tzinfo=UTC)))[0]
    unknown = SourceReferenceValue(
        adapter_key=reference.adapter_key,
        external_record_id="szse:unknown",
        external_version=reference.external_version,
        canonical_url=reference.canonical_url,
        title=reference.title,
        published_at=reference.published_at,
        source_role=reference.source_role,
        fetch_locator=dict(reference.fetch_locator),
        metadata=dict(reference.metadata),
    )

    before = list(calls)
    with pytest.raises(ValueError, match="unknown or mismatched"):
        source.fetch(unknown)
    assert calls == before
    first = source.fetch(reference)
    second = source.fetch(reference)

    assert calls.count(reference.canonical_url) == 1
    assert first is second
    assert first.final_url == reference.canonical_url
    assert first.mime_type == "application/pdf"
    assert first.last_modified == "Fri, 25 Apr 2025 11:56:13 GMT"
    assert first.provider_request_id == "szse-request-1"
    assert first.metadata["external_record_id"] == reference.external_record_id


def test_evicted_download_is_never_fetched_twice_in_same_lifecycle():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 2
    pdf_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.szse.cn":
            return response_json(fixture)
        pdf_calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-x",
        )

    source = make_source(handler, max_cache_bytes=10)
    references = accepted(
        source.search("300750", datetime(2025, 4, 30, tzinfo=UTC))
    )
    source.fetch(references[0])
    source.fetch(references[1])

    with pytest.raises(SourceProtocolError, match="evicted"):
        source.fetch(references[0])

    assert pdf_calls.count(references[0].canonical_url) == 1


def test_close_clears_known_references():
    fixture = json.loads(FIXTURE.read_text())
    fixture["announceCount"] = 2
    source = make_source(lambda request: response_json(fixture))
    reference = accepted(source.search("300750", datetime(2025, 4, 30, tzinfo=UTC)))[0]

    source.close()

    with pytest.raises(ValueError, match="unknown or mismatched"):
        source.fetch(reference)
