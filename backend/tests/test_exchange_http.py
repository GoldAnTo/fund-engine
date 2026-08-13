"""Security and failure-boundary tests for official exchange HTTP transport."""
from __future__ import annotations

import gzip
from datetime import UTC, datetime

import httpx
import pytest

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.acquisition.sources import SourceUnavailable
from app.datasources.exchanges.http import (
    ExchangeHttpTransport,
    SourceProtocolError,
    create_exchange_http_client,
)


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def transport_for(handler, **kwargs: object) -> ExchangeHttpTransport:
    return ExchangeHttpTransport(
        client_for(handler),
        allowed_hosts=frozenset({"query.sse.com.cn", "www.sse.com.cn"}),
        **kwargs,
    )


def test_production_client_disables_environment_proxy_and_redirects(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", FakeClient)

    create_exchange_http_client()

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["auth"] is None
    assert captured["cookies"] is None
    assert isinstance(captured["timeout"], httpx.Timeout)
    timeout = captured["timeout"]
    assert timeout.connect is not None
    assert timeout.read is not None
    assert timeout.write is not None
    assert timeout.pool is not None
    assert "fund-engine" in captured["headers"]["User-Agent"]
    assert "Mozilla" not in captured["headers"]["User-Agent"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.sse.com.cn/path",
        "https://query.sse.com.cn.evil.test/path",
        "https://www.szse.cn/path",
        "http://query.sse.com.cn/path",
        "https://user:pass@query.sse.com.cn/path",
        "https://query.sse.com.cn/path#fragment",
        "https://query.sse.com.cn/path?token=unsafe",
        " https://query.sse.com.cn/path",
    ],
)
def test_request_uses_exact_adapter_and_policy_url_boundary_without_network(url: str):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    value = transport_for(handler)

    with pytest.raises(SourceProtocolError, match="URL boundary") as caught:
        value.request("GET", url, expected="json")

    assert calls == 0
    assert url not in str(caught.value)


def test_adapter_host_scope_cannot_widen_policy():
    with pytest.raises(ValueError, match="allowed_hosts"):
        ExchangeHttpTransport(
            client_for(lambda request: httpx.Response(200, json={})),
            allowed_hosts=frozenset({"outside.example"}),
        )


def test_transport_limits_cannot_widen_source_policy():
    with pytest.raises(ValueError, match="max_response_bytes"):
        transport_for(
            lambda request: httpx.Response(200, json={}),
            max_response_bytes=B_SCOPE_POLICY.max_response_bytes + 1,
        )


def test_redirect_target_is_validated_before_following():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/file"})

    value = transport_for(handler)

    with pytest.raises(SourceProtocolError, match="redirect"):
        value.request(
            "GET", "https://query.sse.com.cn/search", expected="json"
        )

    assert calls == ["https://query.sse.com.cn/search"]


def test_sse_www_redirect_to_exact_official_static_host_is_allowed():
    calls: list[str] = []
    canonical = "https://www.sse.com.cn/disclosure/announcement.pdf"
    final = "https://static.sse.com.cn/disclosure/announcement.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "www.sse.com.cn":
            return httpx.Response(301, headers={"Location": final})
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nofficial",
        )

    value = ExchangeHttpTransport(
        client_for(handler),
        allowed_hosts=frozenset(
            {"query.sse.com.cn", "www.sse.com.cn", "static.sse.com.cn"}
        ),
    )

    response = value.request("GET", canonical, expected="pdf")

    assert calls == [canonical, final]
    assert response.final_url == final
    assert response.content == b"%PDF-1.7\nofficial"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.static.sse.com.cn/disclosure/announcement.pdf",
        "https://notstatic.sse.com.cn/disclosure/announcement.pdf",
        "https://static.sse.com.cn.evil.test/disclosure/announcement.pdf",
    ],
)
def test_sse_static_sibling_and_spoof_redirects_are_rejected_before_following(
    target: str,
):
    calls: list[str] = []
    canonical = "https://www.sse.com.cn/disclosure/announcement.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(301, headers={"Location": target})

    value = ExchangeHttpTransport(
        client_for(handler),
        allowed_hosts=frozenset(
            {"query.sse.com.cn", "www.sse.com.cn", "static.sse.com.cn"}
        ),
    )

    with pytest.raises(SourceProtocolError, match="redirect"):
        value.request("GET", canonical, expected="pdf")

    assert calls == [canonical]


def test_injected_auto_redirect_client_never_requests_off_policy_target():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/file"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    value = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset({"query.sse.com.cn", "www.sse.com.cn"}),
    )

    with pytest.raises(SourceProtocolError, match="redirect"):
        value.request(
            "GET", "https://query.sse.com.cn/search", expected="json"
        )

    assert calls == ["https://query.sse.com.cn/search"]


def test_injected_client_credentials_and_response_cookies_never_leave_process():
    outgoing: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outgoing.append(request)
        if request.url.host == "query.sse.com.cn":
            return httpx.Response(
                307,
                headers={
                    "Location": "https://www.sse.com.cn/result",
                    "Set-Cookie": "response-cookie=top-secret; Path=/",
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        auth=("client-user", "client-password"),
        cookies={"preloaded-cookie": "cookie-secret"},
        headers={
            "Authorization": "Bearer default-secret",
            "Proxy-Authorization": "Basic proxy-secret",
            "X-Api-Key": "api-secret",
        },
    )
    value = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset({"query.sse.com.cn", "www.sse.com.cn"}),
    )

    response = value.request(
        "GET",
        "https://query.sse.com.cn/search",
        expected="json",
        headers={"Referer": "https://www.sse.com.cn/official-page"},
    )

    assert response.content == b"{}"
    assert len(outgoing) == 2
    for request in outgoing:
        lowered = {name.casefold(): value for name, value in request.headers.items()}
        assert "authorization" not in lowered
        assert "proxy-authorization" not in lowered
        assert "cookie" not in lowered
        assert "x-api-key" not in lowered
        assert lowered["accept-encoding"] == "identity"
        assert lowered["accept"] == "application/json"
        assert "fund-engine" in lowered["user-agent"]
        assert lowered["referer"] == "https://www.sse.com.cn/official-page"
        assert "secret" not in repr(request.headers).casefold()
    assert len(client.cookies) == 0


def test_duplicate_inherited_credential_headers_are_removed_before_send():
    outgoing: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outgoing.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers=[
            ("Authorization", "Bearer first-secret"),
            ("Authorization", "Bearer second-secret"),
            ("Cookie", "session=first-secret"),
            ("Cookie", "session=second-secret"),
        ],
    )
    value = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset({"query.sse.com.cn", "www.sse.com.cn"}),
    )

    response = value.request(
        "GET", "https://query.sse.com.cn/search", expected="json"
    )

    assert response.content == b"{}"
    assert len(outgoing) == 1
    lowered_names = {name.casefold() for name in outgoing[0].headers}
    assert "authorization" not in lowered_names
    assert "cookie" not in lowered_names


def test_json_post_preserves_safe_content_type_and_required_headers():
    outgoing: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outgoing.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
        )

    value = transport_for(handler)

    value.request(
        "POST",
        "https://query.sse.com.cn/search",
        expected="json",
        json_body={"safe": "value"},
        headers={"Referer": "https://www.sse.com.cn/official-page"},
    )

    headers = outgoing[0].headers
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    assert headers["Accept-Encoding"] == "identity"
    assert "fund-engine" in headers["User-Agent"]
    assert headers["Referer"] == "https://www.sse.com.cn/official-page"


@pytest.mark.parametrize(
    "location",
    [
        "https://user:pass@www.sse.com.cn/file.pdf",
        "https://www.sse.com.cn/file.pdf#fragment",
        "https://www.sse.com.cn/file.pdf?credential=unsafe",
        " https://www.sse.com.cn/file.pdf",
        "https://www.sse.com.cn/%ZZ",
        "https://www.sse.com.cn/a%2Fchild.pdf",
        "https://www.sse.com.cn/a%5Cchild.pdf",
        "https://www.sse.com.cn/a/../child.pdf",
    ],
)
def test_redirect_rejects_credentials_fragments_and_unsafe_syntax(location: str):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": location})

    value = transport_for(handler)

    with pytest.raises(SourceProtocolError, match="redirect") as caught:
        value.request(
            "GET", "https://query.sse.com.cn/search", expected="json"
        )

    assert location not in str(caught.value)
    assert "user:pass" not in str(caught.value)
    assert calls == 1


def test_redirect_loop_is_stopped_at_low_fixed_maximum():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        target = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(307, headers={"Location": target})

    value = transport_for(handler, max_redirects=2)

    with pytest.raises(SourceProtocolError, match="redirect limit"):
        value.request("GET", "https://query.sse.com.cn/a", expected="json")

    assert calls == 3


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout("late"), httpx.ReadTimeout("late")])
def test_timeouts_map_to_sanitized_retryable_unavailability(failure: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    value = transport_for(handler)

    with pytest.raises(SourceUnavailable) as caught:
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert caught.value.retryable is True
    assert caught.value.diagnostics == {"error_type": "timeout"}
    assert "late" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_network_failure_maps_to_sanitized_retryable_unavailability():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("Cookie: session=top-secret")

    value = transport_for(handler)

    with pytest.raises(SourceUnavailable) as caught:
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert caught.value.retryable is True
    assert caught.value.diagnostics == {"error_type": "network"}
    assert "secret" not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_remote_protocol_failure_is_treated_as_retryable_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Authorization: Basic top-secret")

    value = transport_for(handler)

    with pytest.raises(SourceUnavailable) as caught:
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert caught.value.retryable is True
    assert caught.value.diagnostics == {"error_type": "network"}
    assert "secret" not in repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("12", 12),
        ("999999999", 3600),
        ("Thu, 13 Aug 2026 12:01:00 GMT", 60),
        ("Thu, 13 Aug 2026 11:59:00 GMT", 0),
        ("9" * 10_000, None),
        ("invalid", None),
        ("-1", None),
    ],
    ids=[
        "delta",
        "clamped-delta",
        "future-http-date",
        "past-http-date",
        "oversized-digits",
        "invalid",
        "negative",
    ],
)
def test_429_exposes_only_safe_bounded_retry_after_without_sleeping(
    retry_after: str, expected: int | None
):
    client = client_for(
        lambda request: httpx.Response(
            429,
            headers={
                "Retry-After": retry_after,
                "Set-Cookie": "session=top-secret",
            },
        )
    )
    value = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset({"query.sse.com.cn", "www.sse.com.cn"}),
        clock=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(SourceUnavailable) as caught:
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert caught.value.retryable is True
    assert caught.value.diagnostics == {
        "status": 429,
        "retry_after_seconds": expected,
    }
    assert "secret" not in repr(caught.value.diagnostics)
    assert len(client.cookies) == 0


def test_streaming_stops_as_soon_as_maximum_bytes_are_exceeded():
    stream = ChunkStream(b"1234", b"5678", b"must-not-be-read")
    value = transport_for(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
        ),
        max_response_bytes=5,
    )

    with pytest.raises(SourceProtocolError, match="byte limit"):
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert stream.yielded == 2


@pytest.mark.parametrize("content_length", ["wat", "-1", "1.5", " 4"])
def test_malformed_content_length_is_rejected(content_length: str):
    value = transport_for(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Length": content_length,
            },
            content=b"{}",
        )
    )

    with pytest.raises(SourceProtocolError, match="content length"):
        value.request("GET", "https://query.sse.com.cn/search", expected="json")


def test_advertised_oversize_body_is_rejected_before_streaming():
    stream = ChunkStream(b"{}")
    value = transport_for(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Length": "6"},
            stream=stream,
        ),
        max_response_bytes=5,
    )

    with pytest.raises(SourceProtocolError, match="byte limit"):
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert stream.yielded == 0


def test_compressed_body_is_rejected_before_decompression_or_iteration():
    compressed = gzip.compress(b"x" * 5_000_000)
    stream = ChunkStream(compressed)
    value = transport_for(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            stream=stream,
        ),
        max_response_bytes=1024,
    )

    with pytest.raises(SourceProtocolError, match="content encoding"):
        value.request("GET", "https://query.sse.com.cn/search", expected="json")

    assert stream.yielded == 0


@pytest.mark.parametrize(
    ("expected", "mime_type", "content"),
    [
        ("json", "text/html", b"<html>blocked</html>"),
        ("json", "application/octet-stream", b"{}"),
        ("pdf", "text/plain; charset=utf-8", b"not-pdf"),
        ("pdf", "application/pdf", b"not-pdf"),
        ("json", "application/json", b"%PDF-1.7"),
        ("json", "application/json; charset=gbk", b"{}"),
    ],
)
def test_non_pdf_non_text_and_mime_sniff_mismatches_fail_closed(
    expected: str, mime_type: str, content: bytes
):
    value = transport_for(
        lambda request: httpx.Response(
            200, headers={"Content-Type": mime_type}, content=content
        )
    )

    with pytest.raises(SourceProtocolError, match="response type"):
        value.request(
            "GET", "https://www.sse.com.cn/file.pdf", expected=expected
        )


def test_empty_body_is_rejected():
    value = transport_for(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "application/json"}, content=b""
        )
    )

    with pytest.raises(SourceProtocolError, match="empty"):
        value.request("GET", "https://query.sse.com.cn/search", expected="json")


def test_safe_response_exposes_only_allowlisted_transport_values():
    value = transport_for(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "ETag": '"safe-etag"',
                "Last-Modified": "Wed, 13 Aug 2026 00:00:00 GMT",
                "X-Request-Id": "request-7",
                "Set-Cookie": "session=top-secret",
            },
            content=b'{"ok":true}',
        )
    )

    response = value.request(
        "GET", "https://query.sse.com.cn/search", expected="json"
    )

    assert response.content == b'{"ok":true}'
    assert response.final_url == "https://query.sse.com.cn/search"
    assert response.mime_type == "application/json; charset=utf-8"
    assert response.status == 200
    assert response.etag == '"safe-etag"'
    assert response.last_modified == "Wed, 13 Aug 2026 00:00:00 GMT"
    assert response.source_request_id == "request-7"
    assert "secret" not in repr(response)
    assert not hasattr(response, "headers")
    assert not hasattr(response, "cookies")


def test_custom_policy_cannot_enable_suffix_guessing_for_transport():
    policy = SourcePolicy(
        version="test-v1",
        enabled_adapter_keys=frozenset({"sse"}),
        allowed_source_roles=frozenset({"company_disclosure"}),
        exact_hosts=frozenset({"www.szse.cn"}),
        suffix_hosts=frozenset({"sse.com.cn"}),
        max_response_bytes=10,
        per_adapter_page_limit=1,
        permission_declarations=(("sse", "official"),),
    )

    with pytest.raises(ValueError, match="exact policy hosts"):
        ExchangeHttpTransport(
            client_for(lambda request: httpx.Response(200, json={})),
            allowed_hosts=frozenset({"evil.sse.com.cn"}),
            policy=policy,
        )
