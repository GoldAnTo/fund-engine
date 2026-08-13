"""Bounded, redirect-safe HTTP transport for official exchange sources."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil
from types import MappingProxyType
from typing import Any, Callable, Final, Literal
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.acquisition.sources import (
    SourceDescriptor,
    SourceReferenceValue,
    SourceUnavailable,
)


DEFAULT_MAX_REDIRECTS: Final = 2
MAX_RETRY_AFTER_SECONDS: Final = 3600
MAX_RETRY_AFTER_LENGTH: Final = 128
_USER_AGENT: Final = (
    "fund-engine-official-exchange-acquisition/1.0 "
    "(+bounded public-disclosure client)"
)
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_REQUEST_ID_HEADERS: Final = (
    "x-request-id",
    "x-sse-request-id",
    "x-szse-request-id",
    "request-id",
)
_SECRET_KEY_RE: Final = re.compile(
    r"(?i)(authorization|cookie|credential|password|secret|api[-_ ]?key|token)"
)
_SECRET_VALUE_RE: Final = re.compile(
    r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie)\s*:|"
    r"(?:token|api[\s_-]*key|password|secret|credential)\s*[:=]|"
    r"\b(?:basic|bearer)\s+\S+"
)


class SourceProtocolError(Exception):
    """A non-retryable, sanitized official-source protocol violation."""

    retryable = False

    def __init__(
        self, message: str, *, diagnostics: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.diagnostics = MappingProxyType(dict(diagnostics or {}))


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    """The only transport response values exchange adapters may observe."""

    content: bytes
    final_url: str
    mime_type: str
    status: int
    etag: str | None
    last_modified: str | None
    source_request_id: str | None


class ExchangeSourceDescriptor(SourceDescriptor):
    """SourceDescriptor variant for real hierarchical official HTTPS URLs."""

    def validate_reference(self, reference: SourceReferenceValue) -> None:
        if reference.adapter_key != self.adapter_key:
            raise ValueError("reference adapter_key does not match descriptor")
        if reference.source_role not in self.allowed_source_roles:
            raise ValueError("reference source_role is not allowed by descriptor")
        raw_url = reference.canonical_url
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except (TypeError, ValueError):
            parsed = None
            port = None
        if (
            parsed is None
            or not isinstance(raw_url, str)
            or raw_url != raw_url.strip()
            or "#" in raw_url
            or "\\" in raw_url
            or any(character.isspace() for character in raw_url)
            or re.search(r"%(?![0-9A-Fa-f]{2})", raw_url)
            or _unsafe_url_path(raw_url)
            or parsed.scheme not in self.allowed_schemes
            or parsed.hostname not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            error = ValueError("canonical_url is outside the adapter URL boundary")
            error.__context__ = None
            raise error from None


def create_exchange_http_client() -> httpx.Client:
    """Create the production client with explicit, proxy-independent bounds."""
    return httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        trust_env=False,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT},
        auth=None,
        cookies=None,
    )


def _unsafe_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return any(ord(character) < 32 or ord(character) == 127 for character in value) or (
        _SECRET_VALUE_RE.search(normalized) is not None
    )


def _unsafe_query_key(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    compact = re.sub(r"[^a-z0-9]", "", normalized.casefold())
    return any(
        marker in compact
        for marker in (
            "authorization",
            "cookie",
            "credential",
            "password",
            "secret",
            "apikey",
            "token",
        )
    )


def _unsafe_url_path(value: str) -> bool:
    try:
        path = urlsplit(value).path
    except ValueError:
        return True
    lowered = path.casefold()
    if "%2f" in lowered or "%5c" in lowered:
        return True
    return any(
        segment in {".", "..", "%2e", "%2e%2e", ".%2e", "%2e."}
        for segment in lowered.split("/")
    )


class ExchangeHttpTransport:
    """Stream official responses while enforcing exact hosts and byte limits."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        allowed_hosts: frozenset[str],
        policy: SourcePolicy = B_SCOPE_POLICY,
        max_response_bytes: int | None = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        hosts = frozenset(host.casefold().rstrip(".") for host in allowed_hosts)
        if not hosts or not hosts <= policy.exact_hosts:
            raise ValueError("allowed_hosts must be exact policy hosts")
        if any(host in policy.suffix_hosts for host in hosts):
            raise ValueError("allowed_hosts must be exact policy hosts")
        limit = policy.max_response_bytes if max_response_bytes is None else max_response_bytes
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit <= 0
            or limit > policy.max_response_bytes
        ):
            raise ValueError("max_response_bytes must not widen source policy")
        if (
            not isinstance(max_redirects, int)
            or isinstance(max_redirects, bool)
            or not 0 <= max_redirects <= DEFAULT_MAX_REDIRECTS
        ):
            raise ValueError("max_redirects exceeds the fixed transport limit")
        self._client = client
        self._allowed_hosts = hosts
        self._policy = policy
        self._max_response_bytes = limit
        self._max_redirects = max_redirects
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    def _validate_url(self, raw_url: str, *, redirect: bool = False) -> str:
        label = "redirect URL" if redirect else "request URL boundary"
        if (
            not isinstance(raw_url, str)
            or raw_url != raw_url.strip()
            or not raw_url
            or "#" in raw_url
            or "\\" in raw_url
            or any(character.isspace() for character in raw_url)
            or any(ord(character) < 32 or ord(character) == 127 for character in raw_url)
            or re.search(r"%(?![0-9A-Fa-f]{2})", raw_url)
            or _unsafe_url_path(raw_url)
        ):
            raise SourceProtocolError(f"unsafe {label}") from None
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError:
            raise SourceProtocolError(f"unsafe {label}") from None
        host = parsed.hostname.casefold().rstrip(".") if parsed.hostname else ""
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
            or host not in self._allowed_hosts
            or host not in self._policy.exact_hosts
            or not parsed.path.startswith("/")
            or any(_unsafe_query_key(key) for key, _value in parse_qsl(parsed.query, keep_blank_values=True))
        ):
            raise SourceProtocolError(f"unsafe {label}") from None
        return raw_url

    def request(
        self,
        method: str,
        url: str,
        *,
        expected: Literal["json", "pdf"],
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> SafeHttpResponse:
        normalized_method = method.upper()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError("exchange transport supports only GET and POST")
        if expected not in {"json", "pdf"}:
            raise ValueError("unsupported expected response kind")
        supplied_headers = dict(headers or {})
        if any(
            _SECRET_KEY_RE.search(key) is not None
            or not isinstance(value, str)
            or _unsafe_text(value)
            for key, value in supplied_headers.items()
        ):
            raise SourceProtocolError("unsafe request metadata") from None
        safe_headers = dict(supplied_headers)
        safe_headers.update(
            {
                "Accept": "application/pdf" if expected == "pdf" else "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": _USER_AGENT,
            }
        )

        current_url = self._validate_url(url)
        current_method = normalized_method
        current_params = params
        current_json = json_body
        redirects = 0
        while True:
            response: httpx.Response | None = None
            try:
                self._client.cookies.clear()
                request = self._client.build_request(
                    current_method,
                    current_url,
                    params=current_params,
                    json=current_json,
                    headers=safe_headers,
                    cookies={},
                )
                self._sanitize_prepared_request(request)
                response = self._client.send(
                    request,
                    stream=True,
                    auth=None,
                    follow_redirects=False,
                )
                self._client.cookies.clear()
                if response.status_code in _REDIRECT_STATUSES:
                    if redirects >= self._max_redirects:
                        raise SourceProtocolError("exchange redirect limit exceeded") from None
                    location = response.headers.get("location")
                    if not location:
                        raise SourceProtocolError("exchange redirect is missing a target") from None
                    if (
                        location != location.strip()
                        or "#" in location
                        or "\\" in location
                        or any(character.isspace() for character in location)
                        or re.search(r"%(?![0-9A-Fa-f]{2})", location)
                        or _unsafe_url_path(location)
                    ):
                        raise SourceProtocolError("unsafe redirect URL") from None
                    target = urljoin(str(response.url), location)
                    current_url = self._validate_url(target, redirect=True)
                    redirects += 1
                    current_params = None
                    if response.status_code in {301, 302, 303}:
                        current_method = "GET"
                        current_json = None
                    continue

                self._raise_for_status(response.status_code, response.headers)
                content = self._read_bounded(response)
                mime_type = self._validate_response_type(
                    response.headers.get("content-type"), content, expected
                )
                return SafeHttpResponse(
                    content=content,
                    final_url=self._validate_url(str(response.url)),
                    mime_type=mime_type,
                    status=response.status_code,
                    etag=self._safe_scalar(response.headers.get("etag")),
                    last_modified=self._safe_scalar(
                        response.headers.get("last-modified")
                    ),
                    source_request_id=self._request_id(response.headers),
                )
            except SourceProtocolError:
                raise
            except httpx.TimeoutException:
                raise SourceUnavailable(
                    "official exchange request timed out",
                    retryable=True,
                    diagnostics={"error_type": "timeout"},
                ) from None
            except httpx.NetworkError:
                raise SourceUnavailable(
                    "official exchange network request failed",
                    retryable=True,
                    diagnostics={"error_type": "network"},
                ) from None
            except httpx.RequestError:
                raise SourceUnavailable(
                    "official exchange network request failed",
                    retryable=True,
                    diagnostics={"error_type": "network"},
                ) from None
            except (httpx.InvalidURL, UnicodeError, ValueError):
                raise SourceProtocolError("invalid official exchange request") from None
            finally:
                if response is not None:
                    response.close()
                self._client.cookies.clear()

    def _sanitize_prepared_request(self, request: httpx.Request) -> None:
        """Remove inherited credentials, then validate the complete request."""
        sensitive_names = {
            name
            for name, value in request.headers.multi_items()
            if _SECRET_KEY_RE.search(name) is not None or _unsafe_text(value)
        }
        for name in sensitive_names:
            del request.headers[name]
        request.headers["Accept-Encoding"] = "identity"
        request.headers["User-Agent"] = _USER_AGENT
        self._validate_url(str(request.url))

    def _raise_for_status(self, status: int, headers: httpx.Headers) -> None:
        if status == 429:
            raise SourceUnavailable(
                "official exchange rate limited the request",
                retryable=True,
                diagnostics={
                    "status": 429,
                    "retry_after_seconds": self._retry_after(
                        headers.get("retry-after")
                    ),
                },
            ) from None
        if 500 <= status <= 599:
            raise SourceUnavailable(
                "official exchange service is unavailable",
                retryable=True,
                diagnostics={"status": status},
            ) from None
        if status < 200 or status >= 300:
            raise SourceUnavailable(
                "official exchange rejected the request",
                retryable=False,
                diagnostics={"status": status},
            ) from None

    def _read_bounded(self, response: httpx.Response) -> bytes:
        content_encoding = response.headers.get("content-encoding", "").strip().casefold()
        if content_encoding not in {"", "identity"}:
            raise SourceProtocolError("unsupported exchange content encoding") from None
        raw_length = response.headers.get("content-length")
        if raw_length is not None:
            if re.fullmatch(r"0|[1-9][0-9]*", raw_length) is None:
                raise SourceProtocolError("malformed exchange content length") from None
            if int(raw_length) > self._max_response_bytes:
                raise SourceProtocolError("exchange response exceeded byte limit") from None
        chunks: list[bytes] = []
        byte_count = 0
        for chunk in response.iter_bytes():
            byte_count += len(chunk)
            if byte_count > self._max_response_bytes:
                raise SourceProtocolError("exchange response exceeded byte limit") from None
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content:
            raise SourceProtocolError("official exchange returned an empty body") from None
        return content

    @staticmethod
    def _validate_response_type(
        raw_content_type: str | None,
        content: bytes,
        expected: Literal["json", "pdf"],
    ) -> str:
        if not raw_content_type:
            raise SourceProtocolError(
                "unsupported exchange response type",
                diagnostics={"error_type": "response_type"},
            ) from None
        parts = [part.strip().casefold() for part in raw_content_type.split(";")]
        media_type = parts[0]
        parameters = {
            key.strip(): value.strip().strip('"')
            for part in parts[1:]
            if "=" in part
            for key, value in [part.split("=", 1)]
        }
        charset = parameters.get("charset")
        if charset is not None and charset not in {"utf-8", "utf8"}:
            raise SourceProtocolError(
                "unsupported exchange response type",
                diagnostics={"error_type": "response_type"},
            ) from None
        if expected == "pdf":
            if media_type != "application/pdf" or not content.startswith(b"%PDF-"):
                raise SourceProtocolError(
                    "unsupported exchange response type",
                    diagnostics={"error_type": "response_type"},
                ) from None
        else:
            if media_type not in {"application/json", "text/json", "text/plain"}:
                raise SourceProtocolError(
                    "unsupported exchange response type",
                    diagnostics={"error_type": "response_type"},
                ) from None
            if content.startswith(b"%PDF-"):
                raise SourceProtocolError(
                    "unsupported exchange response type",
                    diagnostics={"error_type": "response_type"},
                ) from None
            valid_utf8 = True
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                valid_utf8 = False
            if not valid_utf8:
                raise SourceProtocolError(
                    "unsupported exchange response type",
                    diagnostics={"error_type": "response_type"},
                ) from None
        normalized = media_type
        if charset is not None:
            normalized += "; charset=utf-8"
        return normalized

    def _retry_after(self, value: str | None) -> int | None:
        if value is None or len(value) > MAX_RETRY_AFTER_LENGTH:
            return None
        if re.fullmatch(r"[0-9]+", value) is not None:
            return min(int(value), MAX_RETRY_AFTER_SECONDS)
        try:
            retry_at = parsedate_to_datetime(value)
            now = self._clock()
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            retry_at.tzinfo is None
            or retry_at.utcoffset() is None
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            return None
        delay = ceil((retry_at.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
        return min(max(delay, 0), MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _safe_scalar(value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate or len(candidate) > 256 or _unsafe_text(candidate):
            return None
        return candidate

    @classmethod
    def _request_id(cls, headers: httpx.Headers) -> str | None:
        for name in _REQUEST_ID_HEADERS:
            value = cls._safe_scalar(headers.get(name))
            if value is not None:
                return value
        return None

    def close(self) -> None:
        self._client.close()
