from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from app.models.ledger import DocumentVersion, SourceSpan
from app.repositories.documents import DocumentRepository

# 留作 docling 默认值的占位（spec §3.2）。实际 PDF 解析走
# app.datasources.docling.PARSER_VERSION_PYPDF；docling-v1 字符串作为
# 未来切换时的目标。S3 接入真实 DoclingAdapter 后可以无缝替换。
PARSER_VERSION = "docling-v1"

_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"[\[\]【】\(\)（）：:]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _source_prefix(source_url: str) -> str:
    # 仅取来源类型（gildata://research_report 等）以让跨入口/跨站点同源
    # 文档归并到同一组；正文 URL 的差异不应绕过去重。
    if "://" in source_url:
        head, tail = source_url.split("://", 1)
        return f"{head}://{tail.split('/', 1)[0]}"
    return source_url.split("/", 1)[0]


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return _WS_RE.sub("", _BRACKET_RE.sub("", title.strip())).lower()


def compute_natural_key(
    source_url: str,
    title: str | None,
    published_at: datetime | None,
) -> str:
    """(source_prefix, normalized_title, published_date) 的 SHA256 前 32 字符。

    升级 SHA256-only 去重的设计：原策略会因年报正文/摘要/港股版各占不同字节
    而全部入库；自然键在文档语义层判重，让跨入口同篇文档合并到同一份版本。
    """
    prefix = _source_prefix(source_url)
    date = published_at.date().isoformat() if published_at else ""
    raw = f"{prefix}|{_normalize_title(title)}|{date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class DocumentService:
    """Freezes source material into immutable, content-addressed versions.

    Re-freezing identical bytes returns the existing version. Changed bytes
    append a new version that supersedes the previous one for the same source.
    A higher-level natural-key dedup also rejects same-source same-title
    same-date re-ingestion (年报正文 vs 摘要 vs 港股版 should not multiply).
    """

    def __init__(self, repository: DocumentRepository) -> None:
        self._repo = repository

    def freeze(
        self,
        raw: bytes,
        source_url: str,
        published_at: datetime | None = None,
        parser_version: str | None = None,
        title: str | None = None,
        natural_key: str | None = None,
        byte_size: int | None = None,
        language: str | None = None,
        parse_state: str = "success",
    ) -> DocumentVersion:
        """Freeze bytes into a DocumentVersion, deduping on two levels:

        1. content_sha256 — identical bytes (re-fetch of the same body) collapse
           to the existing version.
        2. natural_key — same source + same title + same publish date (年报
           正文/摘要/港股版不同字节但语义重复) also collapse to the earliest
           version.

        Callers wanting to distinguish new vs. duplicate ingest should read
        ``natural_key`` on the returned version and compare; otherwise this
        returns the same DocumentVersion either way (back-compat).

        S4 additions: ``title`` is now persisted (was only on span locator),
        and ``byte_size`` / ``language`` / ``parse_state`` let the workbench
        decide whether to fetch the full blob and whether to warn on a
        degraded parse.  All are optional and default to ``None`` / "success"
        for legacy callers.
        """
        version, _created = self._freeze(
            raw=raw,
            source_url=source_url,
            published_at=published_at,
            parser_version=parser_version,
            title=title,
            natural_key=natural_key,
            byte_size=byte_size,
            language=language,
            parse_state=parse_state,
        )
        return version

    def _freeze(
        self,
        *,
        raw: bytes,
        source_url: str,
        published_at: datetime | None,
        parser_version: str | None,
        title: str | None,
        natural_key: str | None,
        byte_size: int | None = None,
        language: str | None = None,
        parse_state: str = "success",
    ) -> tuple[DocumentVersion, bool]:
        digest = hashlib.sha256(raw).hexdigest()
        existing = self._repo.by_hash(digest)
        if existing is not None:
            return existing, False

        key = natural_key or (
            compute_natural_key(source_url, title, published_at)
            if title
            else ""
        )
        if key:
            prior_natural = self._repo.by_natural_key(key)
            if prior_natural is not None:
                return prior_natural, False

        prior = self._repo.latest_for_source(source_url)
        supersedes_id = (
            prior.id
            if prior is not None and prior.content_sha256 != digest
            else None
        )
        now = _utcnow()
        version = self._repo.insert_version(
            content_sha256=digest,
            source_url=source_url,
            natural_key=key,
            published_at=published_at,
            available_at=now,
            acquired_at=now,
            parser_version=parser_version or PARSER_VERSION,
            supersedes_id=supersedes_id,
            title=title,
            byte_size=byte_size if byte_size is not None else len(raw),
            language=language,
            parse_state=parse_state,
        )
        return version, True

    def add_span(
        self,
        document_version_id: uuid.UUID,
        locator: dict,
        verbatim_text: str,
        *,
        text_sha256: str | None = None,
        context_hash: str | None = None,
        locator_v1: dict | None = None,
    ) -> SourceSpan:
        """Append one SourceSpan to a DocumentVersion.

        S4 additions: ``text_sha256`` / ``context_hash`` / ``locator_v1``
        are optional; legacy callers that still pass a ``{page, paragraph,
        parser}`` dict continue to work.  New code should compute the
        hashes via ``app.documents.locators.compute_text_sha256`` and pass
        a v1 locator produced by ``coerce_locator_v1``.
        """
        return self._repo.insert_span(
            document_version_id=document_version_id,
            locator=locator,
            verbatim_text=verbatim_text,
            text_sha256=text_sha256,
            context_hash=context_hash,
            locator_v1=locator_v1,
        )
