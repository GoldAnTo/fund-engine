"""Official Shanghai Stock Exchange listed-company announcement adapter."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    SearchItem,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
)
from app.datasources.exchanges.http import (
    ExchangeHttpTransport,
    ExchangeSourceDescriptor,
    SafeHttpResponse,
    SourceProtocolError,
    create_exchange_http_client,
)


_SEARCH_URL: Final = (
    "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
)
_REFERER: Final = "https://www.sse.com.cn/disclosure/listedinfo/announcement/"
_PDF_ORIGIN: Final = "https://www.sse.com.cn"
_SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
_PAGE_SIZE: Final = 20
_MAX_RESULTS: Final = _PAGE_SIZE * B_SCOPE_POLICY.per_adapter_page_limit
_MAX_KNOWN_REFERENCES: Final = 1000
_SECURITY_CODE_RE: Final = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_DATE_RE: Final = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_SAFE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET_RE: Final = re.compile(
    r"(?i)(?:authorization|cookie|password|credential|secret|token|api[\s_-]*key)\s*[:=]|"
    r"\b(?:basic|bearer)\s+\S+"
)
_DESCRIPTOR: Final = ExchangeSourceDescriptor(
    adapter_key="sse",
    provider_identity="Shanghai Stock Exchange",
    allowed_schemes=frozenset({"https"}),
    allowed_hosts=frozenset(
        {"query.sse.com.cn", "www.sse.com.cn", "static.sse.com.cn"}
    ),
    allowed_source_roles=frozenset({"company_disclosure"}),
)


IdentityKey = tuple[str, str, str]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _display_text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", _text(value)).split())


def _safe_provider_text(value: str) -> bool:
    return (
        len(value) <= 1000
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and _SECRET_RE.search(unicodedata.normalize("NFKC", value)) is None
    )


def _opaque_digest(value: object) -> str:
    try:
        material = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        material = type(value).__name__.encode("ascii", errors="replace")
    return hashlib.sha256(material).hexdigest()[:24]


def _identity_key(reference: SourceReferenceValue) -> IdentityKey:
    return (
        reference.adapter_key,
        reference.external_record_id,
        reference.external_version,
    )


class SSEAnnouncementSource(SourceAdapter):
    """Search official SSE JSON and fetch only previously returned PDFs.

    SSE publication dates are date-only. They are interpreted as source-calendar
    dates in Asia/Shanghai and admitted only when that local date is before the
    cutoff's Asia/Shanghai calendar date.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        transport: ExchangeHttpTransport | None = None,
        max_cache_bytes: int = B_SCOPE_POLICY.max_response_bytes,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("provide either client or transport, not both")
        if (
            not isinstance(max_cache_bytes, int)
            or isinstance(max_cache_bytes, bool)
            or max_cache_bytes <= 0
            or max_cache_bytes > B_SCOPE_POLICY.max_response_bytes
        ):
            raise ValueError("max_cache_bytes must remain within source policy")
        self._transport = transport or ExchangeHttpTransport(
            client or create_exchange_http_client(),
            allowed_hosts=_DESCRIPTOR.allowed_hosts,
        )
        self._max_cache_bytes = max_cache_bytes
        self._cache_bytes = 0
        self._known: OrderedDict[IdentityKey, SourceReferenceValue] = OrderedDict()
        self._downloads: OrderedDict[IdentityKey, RetrievedEnvelope] = OrderedDict()
        self._fetched: OrderedDict[IdentityKey, None] = OrderedDict()

    @property
    def descriptor(self) -> SourceDescriptor:
        return _DESCRIPTOR

    def search(self, query: str, cutoff: datetime) -> tuple[SearchItem, ...]:
        normalized_query = _display_text(query)
        if not normalized_query:
            raise ValueError("query must not be blank")
        if not _safe_provider_text(normalized_query):
            raise ValueError("query contains unsafe text")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        security_code = self._single_security_code(normalized_query)

        candidates: dict[tuple[str, ...], SearchItem] = {}
        for page in range(1, B_SCOPE_POLICY.per_adapter_page_limit + 1):
            response = self._transport.request(
                "GET",
                _SEARCH_URL,
                expected="json",
                params=self._search_params(
                    normalized_query, security_code, cutoff, page
                ),
                headers={"Referer": _REFERER},
            )
            payload = self._decode_payload(response)
            rows, page_count = self._validate_payload(payload)
            for row in rows:
                item = self._map_row(row, security_code, cutoff)
                if isinstance(item, SourceReferenceValue):
                    item = self._track_reference(item)
                if isinstance(item, SourceReferenceValue):
                    discriminator = (
                        "accepted",
                        item.adapter_key,
                        item.external_record_id,
                        item.external_version,
                    )
                else:
                    discriminator = (
                        "rejected",
                        item.adapter_key,
                        item.external_record_id,
                        item.reason,
                    )
                candidates.setdefault(discriminator, item)
                if len(candidates) >= _MAX_RESULTS:
                    break
            if len(candidates) >= _MAX_RESULTS or page >= page_count or not rows:
                break
        return tuple(
            sorted(
                candidates.values(),
                key=lambda item: (
                    item.external_record_id,
                    item.external_version
                    if isinstance(item, SourceReferenceValue)
                    else "",
                    0 if isinstance(item, SourceReferenceValue) else 1,
                    item.reason if isinstance(item, RejectedSearchItem) else "",
                ),
            )
        )

    @staticmethod
    def _single_security_code(query: str) -> str:
        codes = tuple(dict.fromkeys(_SECURITY_CODE_RE.findall(query)))
        if len(codes) != 1:
            raise ValueError("query must contain exactly one security code")
        return codes[0]

    @staticmethod
    def _search_params(
        query: str, security_code: str, cutoff: datetime, page: int
    ) -> dict[str, Any]:
        local_cutoff = cutoff.astimezone(_SHANGHAI).date()
        parsed_dates = []
        for raw in _DATE_RE.findall(query):
            try:
                parsed_dates.append(datetime.fromisoformat(raw).date())
            except ValueError:
                continue
        begin = min(parsed_dates) if parsed_dates else local_cutoff - timedelta(days=365)
        end = min(max(parsed_dates), local_cutoff) if parsed_dates else local_cutoff
        if begin > end:
            begin = end
        return {
            "isPagination": "true",
            "productId": security_code,
            "keyWord": "",
            "securityType": "0101,120100,020100,020200,120200",
            "reportType2": "",
            "reportType": "ALL",
            "beginDate": begin.isoformat(),
            "endDate": end.isoformat(),
            "pageHelp.pageSize": _PAGE_SIZE,
            "pageHelp.pageNo": page,
            "pageHelp.beginPage": page,
            "pageHelp.endPage": page,
            "pageHelp.cacheSize": 1,
        }

    @staticmethod
    def _decode_payload(response: SafeHttpResponse) -> object:
        try:
            return json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceProtocolError("invalid SSE response JSON") from None

    @staticmethod
    def _validate_payload(payload: object) -> tuple[list[Mapping[str, Any]], int]:
        if not isinstance(payload, Mapping):
            raise SourceProtocolError("invalid SSE response schema") from None
        page_help = payload.get("pageHelp")
        rows = payload.get("result")
        if not isinstance(page_help, Mapping) or not isinstance(rows, list):
            raise SourceProtocolError("invalid SSE response schema") from None
        required_numbers = ("pageNo", "pageSize", "pageCount", "total")
        if any(
            type(page_help.get(name)) is not int or page_help[name] < 0
            for name in required_numbers
        ) or any(not isinstance(row, Mapping) for row in rows):
            raise SourceProtocolError("invalid SSE response schema") from None
        return rows, max(1, page_help["pageCount"])

    def _map_row(
        self,
        row: Mapping[str, Any],
        requested_security_code: str,
        cutoff: datetime,
    ) -> SearchItem:
        title = _display_text(row.get("TITLE"))
        security_code = _display_text(row.get("SECURITY_CODE"))
        publication_raw = _text(row.get("SSEDATE"))
        path_raw = _text(row.get("URL"))
        provider_id = self._provider_id(row)
        raw_values = (title, security_code, publication_raw, path_raw, provider_id or "")
        if any(value and not _safe_provider_text(value) for value in raw_values):
            return self._rejection(row, "unsafe_provider_data")
        if (
            re.fullmatch(r"\d{6}", security_code) is not None
            and security_code != requested_security_code
        ):
            return self._rejection(row, "security_scope_mismatch", title=title)

        published_at: datetime | None = None
        source_date: str | None = None
        if publication_raw:
            try:
                parsed_date = datetime.strptime(publication_raw, "%Y-%m-%d").date()
            except ValueError:
                return self._rejection(row, "invalid_publication_date")
            source_date = parsed_date.isoformat()
            published_at = datetime.combine(
                parsed_date, datetime.min.time(), tzinfo=_SHANGHAI
            ).astimezone(UTC)

        reason: str | None = None
        if not security_code or re.fullmatch(r"\d{6}", security_code) is None:
            reason = "missing_identity"
        elif not title:
            reason = "missing_title"
        elif not publication_raw:
            reason = "missing_publication_date"
        elif not path_raw:
            reason = "missing_pdf_path"
        if reason is not None:
            return self._rejection(row, reason, title=title, published_at=published_at)

        pdf_url = self._pdf_url(path_raw)
        if pdf_url is None:
            return self._rejection(
                row, "invalid_pdf_path", title=title, published_at=published_at
            )
        assert published_at is not None and source_date is not None
        source_calendar_date = datetime.strptime(source_date, "%Y-%m-%d").date()
        cutoff_calendar_date = cutoff.astimezone(_SHANGHAI).date()
        if source_calendar_date > cutoff_calendar_date:
            return self._rejection(
                row, "after_cutoff", title=title, published_at=published_at
            )
        if source_calendar_date == cutoff_calendar_date:
            return self._rejection(
                row,
                "publication_time_unresolved",
                title=title,
                published_at=published_at,
            )

        external_id = (
            f"sse:{provider_id}"
            if provider_id is not None
            else self._fallback_id(
                security_code, title, source_date, path_raw
            )
        )
        metadata: dict[str, Any] = {
            "provider_identity": self.descriptor.provider_identity,
            "security_code": security_code,
            "source_publication": source_date,
            "publication_timezone": "Asia/Shanghai",
            "publication_precision": "date",
            "publication_time_resolved": False,
        }
        if provider_id is not None:
            metadata["provider_record_id"] = provider_id
        return SourceReferenceValue(
            adapter_key=self.descriptor.adapter_key,
            external_record_id=external_id,
            external_version=f"published:{source_date}",
            canonical_url=pdf_url,
            title=title,
            published_at=published_at,
            source_role="company_disclosure",
            fetch_locator={"canonical_pdf_url": pdf_url},
            metadata=metadata,
        )

    @staticmethod
    def _provider_id(row: Mapping[str, Any]) -> str | None:
        for field in ("BULLETIN_ID", "ANNOUNCEMENT_ID", "ID"):
            value = row.get(field)
            if type(value) is int:
                candidate = str(value)
            else:
                candidate = _text(value)
            if candidate and _SAFE_ID_RE.fullmatch(candidate):
                return candidate
        return None

    @staticmethod
    def _fallback_id(
        security_code: str, title: str, source_date: str, path: str
    ) -> str:
        material = json.dumps(
            ["sse", security_code, title, source_date, path],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sse:{hashlib.sha256(material).hexdigest()[:24]}"

    @staticmethod
    def _pdf_url(path: str) -> str | None:
        candidate = f"{_PDF_ORIGIN}{path}" if path.startswith("/") else path
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return None
        if (
            candidate != candidate.strip()
            or "#" in candidate
            or "\\" in candidate
            or any(character.isspace() for character in candidate)
            or parsed.scheme != "https"
            or parsed.hostname != "www.sse.com.cn"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or not parsed.path.casefold().endswith(".pdf")
        ):
            return None
        return candidate

    def _rejection(
        self,
        row: Mapping[str, Any],
        reason: str,
        *,
        title: str = "",
        published_at: datetime | None = None,
    ) -> RejectedSearchItem:
        safe_title = title if title and _safe_provider_text(title) else "Rejected provider item"
        metadata: dict[str, Any] = {
            "provider_identity": self.descriptor.provider_identity,
            "source_type": "company_disclosure",
        }
        source_publication = _text(row.get("SSEDATE"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_publication) is not None:
            metadata.update(
                {
                    "source_publication": source_publication,
                    "publication_timezone": "Asia/Shanghai",
                    "publication_precision": "date",
                    "publication_time_resolved": False,
                }
            )
        return RejectedSearchItem(
            adapter_key=self.descriptor.adapter_key,
            reason=reason,
            external_record_id=f"sse:rejected:{_opaque_digest(row)}",
            title=safe_title,
            published_at=published_at,
            metadata=metadata,
        )

    def _track_reference(self, reference: SourceReferenceValue) -> SearchItem:
        key = _identity_key(reference)
        existing = self._known.get(key)
        if existing is not None:
            self._known.move_to_end(key)
            if existing == reference:
                return existing
            return self._reference_rejection(reference, "identity_conflict")
        self._known[key] = reference
        while len(self._known) > _MAX_KNOWN_REFERENCES:
            stale_key, _stale_reference = self._known.popitem(last=False)
            stale = self._downloads.pop(stale_key, None)
            if stale is not None:
                self._cache_bytes -= len(stale.content)
        return reference

    def _reference_rejection(
        self, reference: SourceReferenceValue, reason: str
    ) -> RejectedSearchItem:
        return RejectedSearchItem(
            adapter_key=reference.adapter_key,
            reason=reason,
            external_record_id=reference.external_record_id,
            title=reference.title,
            published_at=reference.published_at,
            metadata={
                "provider_identity": self.descriptor.provider_identity,
                "source_type": "company_disclosure",
            },
        )

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        key = _identity_key(reference)
        known = self._known.get(key)
        if known is None or known != reference or reference.fetch_locator.get(
            "canonical_pdf_url"
        ) != reference.canonical_url:
            raise ValueError("unknown or mismatched SSE source reference") from None
        cached = self._downloads.get(key)
        if cached is not None:
            self._downloads.move_to_end(key)
            return cached
        if key in self._fetched:
            raise SourceProtocolError("SSE downloaded bytes were evicted") from None
        if len(self._fetched) >= _MAX_KNOWN_REFERENCES:
            raise SourceProtocolError("SSE fetched identity limit exceeded") from None
        response = self._transport.request(
            "GET", reference.canonical_url, expected="pdf"
        )
        final = urlsplit(response.final_url)
        if response.final_url != reference.canonical_url and not (
            final.scheme == "https"
            and final.hostname == "static.sse.com.cn"
            and final.username is None
            and final.password is None
            and final.fragment == ""
        ):
            raise SourceProtocolError("SSE final PDF URL did not match reference") from None
        self._fetched[key] = None
        envelope = self._envelope(reference, response)
        self._cache_download(key, envelope)
        return envelope

    def _envelope(
        self, reference: SourceReferenceValue, response: SafeHttpResponse
    ) -> RetrievedEnvelope:
        return RetrievedEnvelope(
            content=response.content,
            mime_type=response.mime_type,
            final_url=response.final_url,
            etag=response.etag,
            last_modified=response.last_modified,
            provider_request_id=response.source_request_id,
            metadata={
                "adapter_key": self.descriptor.adapter_key,
                "external_record_id": reference.external_record_id,
                "provider_identity": self.descriptor.provider_identity,
                "status": response.status,
            },
        )

    def _cache_download(
        self, key: IdentityKey, envelope: RetrievedEnvelope
    ) -> None:
        size = len(envelope.content)
        if size > self._max_cache_bytes:
            raise SourceProtocolError("SSE PDF exceeds adapter cache limit") from None
        while self._downloads and self._cache_bytes + size > self._max_cache_bytes:
            _old_key, old = self._downloads.popitem(last=False)
            self._cache_bytes -= len(old.content)
        self._downloads[key] = envelope
        self._cache_bytes += size

    def close(self) -> None:
        self._known.clear()
        self._downloads.clear()
        self._fetched.clear()
        self._cache_bytes = 0
        self._transport.close()


SSESource = SSEAnnouncementSource
SseAnnouncementSource = SSEAnnouncementSource

__all__ = ["SSEAnnouncementSource", "SSESource", "SseAnnouncementSource"]
