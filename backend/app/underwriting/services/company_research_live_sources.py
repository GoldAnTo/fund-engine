"""Per-execution HTTP receipts for the unchanged, governed Alphabet evidence.

This module verifies fresh downloads of historical disclosures. It does not
discover new facts, alter the historical basis, or fall back to bundled bytes.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pypdf import PdfReader

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research_provenance import (
    canonical_source_refs,
    evidence_payload_source_refs,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_model_builder import (
    validate_company_research_evidence_payload_for_read,
)

_SCHEMA = "company-research.live-source-bundle.v1"
_EXTRACTOR = "alphabet-visible-disclosure-text.v1"
_MAX_RAW_BYTES = 10_000_000
_MAX_TEXT_BYTES = 3_000_000
_MAX_EXCERPT_CHARS = 60_000
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PAYLOAD_FIELDS = frozenset({
    "schema_version", "source_mode", "company_external_key", "cutoff_at",
    "governed_manifest_hash", "evidence_payload_hash", "captured_at",
    "documents", "excerpts", "excerpts_hash", "bundle_hash",
})
_DOCUMENT_FIELDS = frozenset({
    "source_id", "source_url", "final_url", "http_status", "media_type",
    "published_at", "available_at", "retrieved_at", "raw_hash", "raw_size",
    "raw_path", "text_hash", "text_size", "text_path", "extractor_version",
    "fact_keys",
})


@dataclass(frozen=True, slots=True)
class _SourceSpec:
    source_id: str
    source_url: str
    raw_hash: str
    raw_size: int
    media_type: str


_SOURCE_SPECS = (
    _SourceSpec(
        "alphabet-2025-10k",
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm",
        "c2f6301004f35411a20611c14ff01d80a85c0bcbab6053c80d8cc7f6fc747161",
        2_616_499,
        "text/html",
    ),
    _SourceSpec(
        "alphabet-2025-q4-release",
        "https://s206.q4cdn.com/479360582/files/doc_news/2026/Feb/04/attachments/2025q4-alphabet-earnings-release.pdf",
        "8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9",
        151_984,
        "application/pdf",
    ),
)


@dataclass(frozen=True, slots=True)
class CompanySourceHTTPResponse:
    body: bytes
    final_url: str
    status_code: int
    media_type: str


@dataclass(frozen=True, slots=True)
class FrozenCompanyResearchSourceBundle:
    """A JSON snapshot; callers receive independent copies of its payload."""

    _serialized_payload: str

    @property
    def payload(self) -> dict[str, object]:
        return json.loads(self._serialized_payload)

    @property
    def bundle_hash(self) -> str:
        return str(self.payload["bundle_hash"])


def _fail(message: str) -> ValidationError:
    return ValidationError(f"company research live sources: {message}")


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError):
        raise _fail("timestamp is invalid") from None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise _fail("content hash is invalid")
    return value


def _governed_evidence(evidence: object, source_refs: object):
    validate_company_research_evidence_payload_for_read(evidence)
    assert isinstance(evidence, Mapping)
    fixture = load_alphabet_golden_case_fixture()
    if (
        evidence["fixture_content_hash"] not in {fixture.content_hash, LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256}
        or _timestamp(evidence["cutoff"]) != fixture.cutoff
        or evidence["company_external_key"] != fixture.company_external_key
        or evidence["security_external_keys"] != list(fixture.security_external_keys)
        or canonical_source_refs(source_refs) != evidence_payload_source_refs(evidence)
    ):
        raise _fail("evidence boundary is not governed")
    expected = {fact.fact_key: fact.payload() for fact in fixture.facts}
    specs = {spec.source_url: spec for spec in _SOURCE_SPECS}
    facts = evidence["facts"]
    if {fact["fact_key"] for fact in facts} != set(expected):
        raise _fail("facts do not cover the governed evidence")
    for fact in facts:
        known = expected[fact["fact_key"]]
        if any(fact[key] != value for key, value in known.items() if key != "raw_hash"):
            raise _fail("fact does not match governed disclosure")
        spec = specs.get(fact["source_url"])
        if spec is None or fact["raw_hash"] != spec.raw_hash:
            raise _fail("source URL or raw hash is not governed")
    if {fact["source_url"] for fact in facts} != set(specs):
        raise _fail("source coverage is incomplete")
    return fixture


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _fail("source redirects are not permitted")


def _fetch_http(url: str) -> CompanySourceHTTPResponse:
    spec = next((item for item in _SOURCE_SPECS if item.source_url == url), None)
    if spec is None:
        raise _fail("source URL is not governed")
    user_agent = os.environ.get("COMPANY_RESEARCH_HTTP_USER_AGENT") or os.environ.get("SEC_USER_AGENT") or "fund-engine-company-research/1.0 (public-disclosure-research)"
    request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=25) as response:
            return CompanySourceHTTPResponse(
                body=response.read(min(spec.raw_size, _MAX_RAW_BYTES) + 1),
                final_url=response.geturl(),
                status_code=response.status,
                media_type=response.headers.get_content_type(),
            )
    except Exception:  # noqa: BLE001 - sanitize all exceptions from the HTTP boundary.
        # Upstream exception bodies/URLs are neither a public error nor audit data.
        raise _fail("public disclosure download failed") from None


class _VisibleHTMLText(HTMLParser):
    _void = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
    _blocks = frozenset({"p", "div", "table", "tr", "li", "section", "h1", "h2", "h3", "h4", "br"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = (bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "ix:header"} or "hidden" in attributes or "display:none" in (attributes.get("style") or "").replace(" ", "").lower())
        if tag not in self._void:
            self.stack.append((tag, hidden))
        if not hidden:
            self.parts.append("\n" if tag in self._blocks else " ")

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not (self.stack and self.stack[-1][1]):
            self.parts.append("\n" if tag in self._blocks else " ")

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]):
            self.parts.append(data)


def _extract_text(raw: bytes, spec: _SourceSpec) -> str:
    try:
        if spec.media_type == "text/html":
            parser = _VisibleHTMLText()
            parser.feed(raw.decode("utf-8-sig"))
            source = "".join(parser.parts)
        else:
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted or not 0 < len(reader.pages) <= 200:
                raise _fail("PDF page boundary is invalid")
            source = "\n".join(f"[PDF page {index + 1}]\n{page.extract_text() or ''}" for index, page in enumerate(reader.pages))
        text = "\n".join(line for part in source.splitlines() if (line := re.sub(r"\s+", " ", part).strip()))
        if len(text.strip()) < 40 or len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise _fail("disclosure text is empty or exceeds the boundary")
        return text
    except Exception:  # noqa: BLE001 - sanitize parser errors from external documents.
        raise _fail("disclosure text extraction failed") from None


def _excerpts(documents: list[dict[str, object]], texts: dict[str, str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    remaining = _MAX_EXCERPT_CHARS
    for document in documents:
        source_id = str(document["source_id"])
        text = texts[source_id]
        if document["media_type"] == "application/pdf":
            anchors = (("Quarterly results", "", 8500), ("Capital allocation and outlook", "capital expenditures", 6500))
        else:
            # Whole body headings avoid identical words in the contents and
            # the opening forward-looking-statement boilerplate.
            anchors = (("Business and products", r"(?im)^ITEM 1\. BUSINESS$", 8500), ("Cloud business", r"(?im)^Google Cloud$", 8500), ("Risk factors", r"(?im)^ITEM 1A\. RISK FACTORS$", 8500), ("Capital expenditures", r"(?im)^Capital Expenditures(?: and Leases)?$", 8500), ("Revenue and operating results", "The following table presents revenues by type", 8500))
        covered: list[tuple[int, int]] = []
        for label, anchor, length in anchors:
            if anchor.startswith("(?im)"):
                match = re.search(anchor, text)
                position = match.start() if match is not None else -1
            else:
                position = text.casefold().find(anchor.casefold()) if anchor else 0
            position = max(position, 0)
            start = max(0, position - (150 if position else 0))
            end = min(len(text), start + length, start + remaining)
            if end <= start or any(start >= left and end <= right for left, right in covered):
                continue
            excerpt_text = text[start:end]
            result.append({
                "excerpt_id": f"{source_id}:{start}-{end}",
                "source_id": source_id,
                "raw_hash": str(document["raw_hash"]),
                "source_url": str(document["source_url"]),
                "locator": f"{label}; extracted text characters {start}:{end}",
                "text": excerpt_text,
            })
            covered.append((start, end))
            remaining -= len(excerpt_text)
    return result


def _blob_path(root: Path, value: object, expected: str) -> Path:
    if not isinstance(root, Path) or value != expected:
        raise _fail("content address is invalid")
    candidate = root / expected
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise _fail("content address escapes storage")
    return candidate


def _read_blob(root: Path, relative: str, expected: str, size: int) -> bytes:
    path = _blob_path(root, relative, expected)
    try:
        with path.open("rb") as stream:
            result = stream.read(size + 1)
    except OSError:
        raise _fail("frozen disclosure content is unavailable") from None
    if len(result) != size:
        raise _fail("frozen disclosure size mismatch")
    return result


def _write_blob(root: Path, relative: str, contents: bytes) -> None:
    path = _blob_path(root, relative, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(contents)
    except FileExistsError:
        if _read_blob(root, relative, relative, len(contents)) != contents:
            raise _fail("existing frozen content has changed") from None


def capture_governed_alphabet_sources(
    evidence_payload: object,
    source_refs: object,
    *,
    now: Callable[[], datetime],
    storage_root: Path,
    fetcher: Callable[[str], CompanySourceHTTPResponse] | None = None,
) -> FrozenCompanyResearchSourceBundle:
    """Fetch both governed disclosures before publishing any local receipt."""
    fixture = _governed_evidence(evidence_payload, source_refs)
    assert isinstance(evidence_payload, Mapping)
    captured_at = _timestamp(now())
    if captured_at < fixture.cutoff:
        raise _fail("capture clock precedes the historical boundary")
    documents: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    blobs: list[tuple[str, bytes]] = []
    for spec in _SOURCE_SPECS:
        try:
            response = (fetcher or _fetch_http)(spec.source_url)
        except Exception:  # noqa: BLE001 - sanitize errors from injected and default fetchers.
            raise _fail("public disclosure download failed") from None
        if (type(response) is not CompanySourceHTTPResponse or type(response.body) is not bytes
            or response.status_code != 200 or response.final_url != spec.source_url
            or not isinstance(response.media_type, str)
            or response.media_type.split(";", 1)[0].strip().lower() != spec.media_type
            or not 0 < len(response.body) <= _MAX_RAW_BYTES
            or len(response.body) != spec.raw_size or _sha(response.body) != spec.raw_hash):
            raise _fail("download does not match governed disclosure")
        text = _extract_text(response.body, spec)
        text_bytes = text.encode("utf-8")
        text_hash = _sha(text_bytes)
        facts = [fact for fact in fixture.facts if fact.source_url == spec.source_url]
        raw_path, text_path = f"raw/{spec.raw_hash}.bin", f"text/{text_hash}.txt"
        document = {
            "source_id": spec.source_id, "source_url": spec.source_url,
            "final_url": response.final_url, "http_status": response.status_code,
            "media_type": spec.media_type, "published_at": max(fact.published_at for fact in facts).isoformat(),
            "available_at": max(fact.available_at for fact in facts).isoformat(),
            "retrieved_at": _timestamp(now()).isoformat(),
            "raw_hash": spec.raw_hash, "raw_size": len(response.body), "raw_path": raw_path,
            "text_hash": text_hash, "text_size": len(text_bytes), "text_path": text_path,
            "extractor_version": _EXTRACTOR, "fact_keys": sorted(fact.fact_key for fact in facts),
        }
        documents.append(document)
        texts[spec.source_id] = text
        blobs.extend(((raw_path, response.body), (text_path, text_bytes)))
    excerpts = _excerpts(documents, texts)
    payload = {
        "schema_version": _SCHEMA, "source_mode": "live_http_verified",
        "company_external_key": fixture.company_external_key, "cutoff_at": fixture.cutoff.isoformat(),
        "governed_manifest_hash": evidence_payload["fixture_content_hash"],
        "evidence_payload_hash": canonical_hash(evidence_payload), "captured_at": captured_at.isoformat(),
        "documents": documents, "excerpts": excerpts, "excerpts_hash": canonical_hash(excerpts),
    }
    payload["bundle_hash"] = canonical_hash(payload)
    for relative, contents in blobs:
        _write_blob(storage_root, relative, contents)
    validate_frozen_company_sources(payload, storage_root=storage_root)
    return FrozenCompanyResearchSourceBundle(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def validate_frozen_company_sources(payload: object, *, storage_root: Path, verify_raw: bool = True) -> None:
    """Validate receipt, governed metadata, files, and exact input selections."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
        raise _fail("receipt fields are invalid")
    if payload["bundle_hash"] != canonical_hash({key: value for key, value in payload.items() if key != "bundle_hash"}):
        raise _fail("receipt hash mismatch")
    fixture = load_alphabet_golden_case_fixture()
    cutoff = _timestamp(payload["cutoff_at"])
    captured_at = _timestamp(payload["captured_at"])
    if (payload["schema_version"] != _SCHEMA or payload["source_mode"] != "live_http_verified"
        or payload["company_external_key"] != fixture.company_external_key
        or cutoff != fixture.cutoff or captured_at < cutoff
        or payload["governed_manifest_hash"] not in {fixture.content_hash, LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256}):
        raise _fail("receipt boundary is not governed")
    _digest(payload["evidence_payload_hash"])
    documents = payload["documents"]
    if not isinstance(documents, list) or len(documents) != len(_SOURCE_SPECS):
        raise _fail("document coverage is invalid")
    texts: dict[str, str] = {}
    for document, spec in zip(documents, _SOURCE_SPECS, strict=True):
        if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
            raise _fail("document fields are invalid")
        facts = [fact for fact in fixture.facts if fact.source_url == spec.source_url]
        if (document["source_id"] != spec.source_id or document["source_url"] != spec.source_url
            or document["final_url"] != spec.source_url or document["http_status"] != 200
            or document["media_type"] != spec.media_type or document["raw_hash"] != spec.raw_hash
            or type(document["raw_size"]) is not int or document["raw_size"] != spec.raw_size
            or document["extractor_version"] != _EXTRACTOR
            or document["fact_keys"] != sorted(fact.fact_key for fact in facts)
            or _timestamp(document["published_at"]) != max(fact.published_at for fact in facts)
            or _timestamp(document["available_at"]) != max(fact.available_at for fact in facts)
            or _timestamp(document["retrieved_at"]) < captured_at):
            raise _fail("document is not governed")
        if not _timestamp(document["published_at"]) <= _timestamp(document["available_at"]) <= cutoff:
            raise _fail("document is unavailable at cutoff")
        text_hash = _digest(document["text_hash"])
        if type(document["text_size"]) is not int or not 0 < document["text_size"] <= _MAX_TEXT_BYTES:
            raise _fail("text size is invalid")
        raw_address, text_address = f"raw/{spec.raw_hash}.bin", f"text/{text_hash}.txt"
        _blob_path(storage_root, document["raw_path"], raw_address)
        text_bytes = _read_blob(storage_root, document["text_path"], text_address, document["text_size"])
        if _sha(text_bytes) != text_hash:
            raise _fail("frozen text hash mismatch")
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeError:
            raise _fail("frozen text encoding is invalid") from None
        if len(text.strip()) < 40:
            raise _fail("frozen text is empty")
        if verify_raw:
            raw = _read_blob(storage_root, document["raw_path"], raw_address, spec.raw_size)
            if _sha(raw) != spec.raw_hash or _extract_text(raw, spec) != text:
                raise _fail("frozen raw or extracted text hash mismatch")
        texts[spec.source_id] = text
    if payload["excerpts"] != _excerpts(documents, texts) or payload["excerpts_hash"] != canonical_hash(payload["excerpts"]):
        raise _fail("model input excerpts do not match frozen text")
