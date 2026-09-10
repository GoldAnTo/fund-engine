"""Ingest real Gildata (恒生聚源) material into the append-only evidence ledger.

CLI::

    python -m app.scripts.ingest_real_data [--case-id <uuid>]

Pulls real research reports, announcements, and a market quote for the AI-compute
thesis (寒武纪 688256 + 工业富联 601138) and freezes them into the ledger:

1. Research reports (``FinancialResearchReport``) -> one :class:`DocumentVersion`
   per report (frozen by content hash, so re-runs dedupe) + a :class:`SourceSpan`.
2. Announcements (``AnnouncementData``) -> ``DocumentVersion`` + ``SourceSpan``.
3. Market quote (``FinQuery``) -> PE(TTM)/PB/总市值 parsed into
   :class:`ValuationSnapshot` rows for the resolved :class:`Stock`.

The stock is resolved by code: looked up first, created (Company + Stock) if
missing.  Re-runs do not duplicate documents (freeze dedupes by sha256) nor
valuation snapshots (guarded by stock + date + metric + source).

Auth uses the ``GILDATA_TOKEN`` environment variable; the database URL comes
from ``DATABASE_URL`` (defaulting to a local SQLite file).  This script only
writes through the provided session; callers control the transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.gildata import adapters
from app.datasources.gildata.client import (
    GILDATA_RESPONSE_ERROR_MESSAGE,
    GildataMCPClient,
    GildataMCPError,
)
from app.datasources.gildata.governance import GildataEvidenceRights
from app.env import load_local_env
from app.models.ledger import (
    Base,
    DocumentVersion,
    SourceSpan,
    Stock,
    ValuationSnapshot,
)
from app.models.source_governance import ProviderRecord, SourceContract
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.services.ingest import DocumentService
from app.services.source_governance import (
    DECLARED_SOURCE_URL_EXPLICIT_METADATA_KEY,
    DECLARED_SOURCE_URL_METADATA_KEY,
    SourceGovernanceCompatibilityError,
    SourceGovernanceService,
)

SOURCE_GILDATA = "gildata"
PARSER_VERSION = "gildata-mcp-1"
_FROZEN_METADATA_SCOPE_KEY = "frozen_metadata_sha256"

# Fixed queries verified against the Gildata MCP tools.
RESEARCH_QUERIES = [
    "寒武纪算力芯片出货及估值研报观点",
    "工业富联AI服务器收入研报",
]
ANNOUNCEMENT_QUERY = "寒武纪近期公告"
NEWS_QUERY = "寒武纪 AI算力芯片 最新消息"
QUOTE_QUERY = "寒武纪最新股价行情"
QUOTE_STOCK_CODE = "688256"

# Metric definitions recorded on every ValuationSnapshot for auditability.
# Maps the canonical quote key -> (ledger metric_name, definition).
METRIC_DEFINITIONS = {
    "pe_ttm": ("PE(TTM)", "总市值/近四月归母净利润"),
    "pb": ("PB", "总市值/归属股东权益"),
    "total_mv": ("总市值", "总市值（亿元）"),
}

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_A_SHARE_CODE_RE = re.compile(r"^[0-9]{6}$")

_MACRO_WINDOW_SIZE = 20
_MACRO_ROW_FIELDS = (
    "metric_code",
    "metric_name",
    "frequency",
    "unit",
    "value",
    "date",
    "source",
)
_MACRO_ROW_SORT_FIELDS = (
    "date",
    "metric_code",
    "value",
    "unit",
    "source",
    "frequency",
    "metric_name",
)
_FULL_BODY_LOCATOR_IDENTITY_FIELDS = {
    "research_report": (
        "kind",
        "title",
        "org",
        "publish_date",
        "sec_code",
        "page",
        "paragraph",
        "parser",
    ),
    "announcement": (
        "kind",
        "title",
        "stock_code",
        "sec_name",
        "publish_date",
        "page",
        "paragraph",
        "parser",
    ),
    "news": (
        "kind",
        "title",
        "source",
        "sec_name",
        "publish_date",
        "page",
        "paragraph",
        "parser",
    ),
}
_MACRO_LOCATOR_IDENTITY_FIELDS = (
    "kind",
    "query",
    "metric_name",
    "metric_code",
    "frequency",
    "unit",
    "source",
    "peak_date",
    "peak_value",
    "latest_date",
    "latest_value",
    "n_points",
    "page",
    "paragraph",
    "parser",
)


class GildataRequestValidationError(ValueError):
    """Raised when a caller supplies an invalid Gildata ingest parameter."""


class _InvalidSecurityCodeError(ValueError):
    """Internal signal that a security identity cannot be normalized."""


@dataclass(frozen=True)
class _FullBodySpanSpec:
    locator: dict[str, object]
    verbatim_text: str
    text_sha256: str
    locator_identity_fields: tuple[str, ...]


@dataclass(frozen=True)
class _StoredGildataGovernance:
    exists: bool
    frozen_metadata_sha256: str | None


@dataclass(frozen=True)
class _MacroSpanSpec:
    locator: dict[str, object]
    verbatim_text: str
    text_sha256: str


@dataclass(frozen=True)
class _MacroDocumentSpec:
    raw: bytes
    title: str
    published_at: datetime | None
    spans: tuple[_MacroSpanSpec, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _previous_business_day(today: date | None = None) -> date:
    """Return the most recent weekday strictly before ``today``.

    Gildata's ``FinQuery`` 行情探针语义是「最新行情」——返回的行情不带
    ``trade_date``/``as_of_date`` 字段，caller 必须从调用时刻推断 quote
    数据反映的交易日。今天是周一 → 上周五（-3）；今天是周日 → 上周五
    （-2）；今天是周六 → 上周五（-1）；今天是工作日 → 昨日。

    注：仅处理周末。法定节假日（春节/国庆/中秋等）不在本函数范围内，
    走查脚本的 caller 若逢长假末段应显式覆盖（见 ``quote_as_of`` 入口
    参数；当前默认走推断，对周一/周末调用最准确）。

    Defect-8 修复（2026-08-02）：原实现 ``as_of = date.today()`` 把
    ingest 时刻当成行情交易日，会把「周六抓取周五收盘」错记为周六 as_of。
    """
    if today is None:
        today = date.today()
    one_day = timedelta(days=1)
    candidate = today - one_day
    while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
        candidate -= one_day
    return candidate


def _parse_date(value: str) -> date | None:
    """Best-effort parse of a Chinese/ISO date string into ``date``."""
    if not value:
        return None
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned[:10]).date()
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _parse_decimal(value: str) -> Decimal | None:
    """Parse a numeric string into Decimal; return None for blanks/non-numbers."""
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"--", "---", "null", "None", "NA"}:
        return None
    if not _NUM_RE.match(cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _market_for_code(code: str) -> tuple[str, str]:
    """Infer (suffixed_code, market) from a bare A-share numeric code."""
    base = code.split(".")[0]
    if base.startswith(("60", "68", "90", "11", "13", "56")):
        return f"{base}.SH", "SSE"
    if base.startswith(("00", "30", "12", "15", "18")):
        return f"{base}.SZ", "SZSE"
    if base.startswith(("43", "83", "87", "88")):
        return f"{base}.BJ", "BSE"
    return f"{base}.SH", "SSE"


def _bare_security_code(value: object) -> str:
    """Return a six-digit identity; the suffix is intentionally ignored."""
    if not isinstance(value, str):
        raise _InvalidSecurityCodeError("security code format is invalid")
    bare_code = value.strip().upper().split(".", 1)[0]
    if _A_SHARE_CODE_RE.fullmatch(bare_code) is None:
        raise _InvalidSecurityCodeError("security code format is invalid")
    return bare_code


def find_stock_by_code(session: Session, code: str) -> Stock | None:
    """Look up a Stock by code, tolerating with/without market suffix."""
    if not code:
        return None
    base = code.split(".")[0]
    candidates = {code, base, f"{base}.SH", f"{base}.SZ", f"{base}.BJ"}
    return session.scalar(
        select(Stock).where(Stock.code.in_(candidates)).limit(1)
    )


def ensure_stock(
    session: Session,
    instruments: InstrumentRepository,
    *,
    code: str,
    name: str,
) -> Stock:
    """Return an existing Stock by code, or create Company + Stock if missing."""
    stock = find_stock_by_code(session, code)
    if stock is not None:
        return stock
    suffixed_code, market = _market_for_code(code)
    company = instruments.add_company(code=suffixed_code, name=name, type="listed")
    return instruments.add_stock(
        company_id=company.id,
        code=suffixed_code,
        name=name or suffixed_code,
        market=market,
    )


def _valuation_exists(
    session: Session,
    stock_id: uuid.UUID,
    as_of: date,
    metric_name: str,
) -> bool:
    existing = session.scalar(
        select(ValuationSnapshot)
        .where(ValuationSnapshot.stock_id == stock_id)
        .where(ValuationSnapshot.as_of_date == as_of)
        .where(ValuationSnapshot.metric_name == metric_name)
        .where(ValuationSnapshot.source == SOURCE_GILDATA)
        .limit(1)
    )
    return existing is not None


def _resolve_case_id(session: Session, case_id: uuid.UUID | None) -> uuid.UUID | None:
    """Return only the explicitly selected Case.

    Frozen provider material must never be silently attached to whichever Case
    happens to be first in a shared ledger.  Callers that want Case-scoped
    ingestion must supply the ID after their own tenant access check.
    """
    del session
    return case_id


def _provider_uri(source_kind: str, raw: bytes) -> str:
    """Return the immutable URI for one frozen Gildata response body."""
    return f"gildata://{source_kind}/{hashlib.sha256(raw).hexdigest()}"


def _record_gildata_governance(
    session: Session,
    *,
    document: DocumentVersion,
    source_kind: str,
    query: str,
    rights: GildataEvidenceRights,
    declared_by: str,
    frozen_metadata_sha256: str | None = None,
) -> None:
    digest = document.content_sha256
    request_scope = {
        "source_kind": source_kind,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
    }
    if frozen_metadata_sha256 is not None:
        request_scope[_FROZEN_METADATA_SCOPE_KEY] = frozen_metadata_sha256
    try:
        SourceGovernanceService(session).record_event_intake(
            document=document,
            source_type="licensed_provider",
            source_metadata={
                "research_source_type": "licensed_provider",
                "provider_name": "gildata",
                "provider_record_id": f"{source_kind}:{digest}",
                "retrieval_reference": document.source_url,
                "request_scope": request_scope,
                "permissions": {
                    "ai_processing": rights.allow_ai_processing,
                    "display": rights.allow_display,
                    "export": False,
                    "api": False,
                },
                "contract_version": "gildata-local-rights-v1",
                "downstream_restrictions": ["仅限当前 Case 研究与人工审核"],
            },
            declared_by=declared_by,
            incoming_source_url=document.source_url,
        )
    except SourceGovernanceCompatibilityError as exc:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE) from exc


def _freeze_full_body(
    documents: DocumentService,
    *,
    raw: bytes,
    source_kind: str,
    published_at: datetime | None,
    title: str,
    allow_metadata_alias_on_reuse: bool = False,
) -> tuple[DocumentVersion, bool]:
    """Freeze one provider body by its complete bytes, never by display fields.

    A newly created record must preserve the current metadata exactly.  On a
    content-hash reuse, the first frozen title/date remain canonical and are
    validated with their span and governance bundle before the alias is used.
    """
    digest = hashlib.sha256(raw).hexdigest()
    source_url = _provider_uri(source_kind, raw)
    document, created = documents.freeze_with_status(
        raw=raw,
        source_url=source_url,
        published_at=published_at,
        parser_version=PARSER_VERSION,
        title=title,
        natural_key=f"gildata:{digest[:23]}",
        source_authority="licensed_research",
        infer_supersedes=False,
    )
    # The current provenance model owns exactly one SourceContract and
    # ProviderRecord per content-addressed DocumentVersion. If identical bytes
    # arrive under a different Gildata source kind, the global hash lookup can
    # return that other document; the URI check deliberately rejects it rather
    # than pretending one immutable record represents two provider sources.
    if (
        document.content_sha256 != digest
        or document.source_url != source_url
        or document.natural_key != f"gildata:{digest[:23]}"
        or document.parser_version != PARSER_VERSION
        or document.source_authority != "licensed_research"
        or document.supersedes_id is not None
        or (
            (created or not allow_metadata_alias_on_reuse)
            and document.title != title
        )
        or (
            (created or not allow_metadata_alias_on_reuse)
            and not _same_provider_datetime(document.published_at, published_at)
        )
        or document.byte_size != len(raw)
        or document.language is not None
        or document.parse_state != "success"
        or document.supplements_document_version_id is not None
        or document.claimed_page_reference is not None
    ):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    return document, created


def _same_provider_datetime(
    actual: datetime | None,
    expected: datetime | None,
) -> bool:
    """Compare provider dates across SQLite's timezone-naive round trip."""
    if actual is None or expected is None:
        return actual is expected

    def as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return as_utc(actual) == as_utc(expected)


def _spans_for_document(
    session: Session, document_version_id: uuid.UUID
) -> list[SourceSpan]:
    return list(
        session.scalars(
            select(SourceSpan).where(
                SourceSpan.document_version_id == document_version_id
            )
        )
    )


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _metadata_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _frozen_full_body_metadata_sha256(
    *,
    document: DocumentVersion,
    locator: object,
    locator_fields: tuple[str, ...],
    verbatim_text: str,
    text_sha256: str | None,
) -> str:
    """Attest first-seen metadata without persisting licensed body text."""
    if not isinstance(locator, dict):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    payload = {
        "document": {
            "content_sha256": document.content_sha256,
            "source_url": document.source_url,
            "natural_key": document.natural_key,
            "published_at": _metadata_timestamp(document.published_at),
            "available_at": _metadata_timestamp(document.available_at),
            "acquired_at": _metadata_timestamp(document.acquired_at),
            "parser_version": document.parser_version,
            "supersedes_id": (
                str(document.supersedes_id)
                if document.supersedes_id is not None
                else None
            ),
            "title": document.title,
            "byte_size": document.byte_size,
            "language": document.language,
            "parse_state": document.parse_state,
            "source_authority": document.source_authority,
            "supplements_document_version_id": (
                str(document.supplements_document_version_id)
                if document.supplements_document_version_id is not None
                else None
            ),
            "claimed_page_reference": document.claimed_page_reference,
        },
        "span": {
            "locator": {field: locator.get(field) for field in locator_fields},
            "text_sha256": text_sha256,
            "verbatim_sha256": hashlib.sha256(
                verbatim_text.encode("utf-8")
            ).hexdigest(),
        },
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_gildata_governance(
    session: Session,
    document: DocumentVersion,
) -> _StoredGildataGovernance:
    """Load and validate the immutable Gildata governance bundle."""
    contracts = tuple(
        session.scalars(
            select(SourceContract).where(
                SourceContract.document_version_id == document.id
            )
        )
    )
    records = tuple(
        session.scalars(
            select(ProviderRecord).where(
                ProviderRecord.document_version_id == document.id
            )
        )
    )
    if not contracts and not records:
        return _StoredGildataGovernance(False, None)
    if len(contracts) != 1 or len(records) != 1:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    contract = contracts[0]
    record = records[0]
    prefix = "gildata://"
    if not document.source_url.startswith(prefix):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    source_identity = document.source_url[len(prefix) :].split("/", 1)
    if len(source_identity) != 2:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    source_kind, uri_digest = source_identity
    record_scope = (
        dict(record.request_scope) if isinstance(record.request_scope, dict) else None
    )
    contract_metadata = (
        dict(contract.intake_metadata)
        if isinstance(contract.intake_metadata, dict)
        else None
    )
    contract_scope = (
        dict(contract_metadata.get("request_scope"))
        if contract_metadata is not None
        and isinstance(contract_metadata.get("request_scope"), dict)
        else None
    )
    expected_restrictions = ["仅限当前 Case 研究与人工审核"]
    expected_permissions = {
        "ai_processing": contract.allow_ai_processing,
        "display": contract.allow_display,
        "export": contract.allow_export,
        "api": contract.allow_api,
    }
    expected_metadata_keys = {
        "research_source_type",
        "provider_name",
        "provider_record_id",
        "retrieval_reference",
        "request_scope",
        "permissions",
        "contract_version",
        "downstream_restrictions",
        DECLARED_SOURCE_URL_METADATA_KEY,
        DECLARED_SOURCE_URL_EXPLICIT_METADATA_KEY,
    }
    if (
        uri_digest != document.content_sha256
        or contract.source_type != "licensed_provider"
        or contract.research_source_type != "licensed_provider"
        or str(contract.provider_or_tenant).casefold() != "gildata"
        or contract.region != "not_recorded"
        or contract.effective_from is not None
        or contract.effective_until is not None
        or contract.retention_policy != "case_retained"
        or contract.deletion_policy != "not_recorded"
        or list(contract.downstream_restrictions or []) != expected_restrictions
        or contract.contract_version != "gildata-local-rights-v1"
        or str(record.provider_name).casefold() != "gildata"
        or record.provider_record_id
        != f"{source_kind}:{document.content_sha256}"
        or record.content_sha256 != document.content_sha256
        or record.retrieval_reference != document.source_url
        or record.contract_version != contract.contract_version
        or record_scope is None
        or record_scope != contract_scope
        or record_scope.get("source_kind") != source_kind
        or not _is_sha256(record_scope.get("query_sha256"))
        or contract_metadata is None
        or set(contract_metadata) != expected_metadata_keys
        or contract_metadata.get("research_source_type") != "licensed_provider"
        or str(contract_metadata.get("provider_name")).casefold() != "gildata"
        or contract_metadata.get("provider_record_id") != record.provider_record_id
        or contract_metadata.get("retrieval_reference") != document.source_url
        or contract_metadata.get("permissions") != expected_permissions
        or contract_metadata.get("contract_version") != contract.contract_version
        or contract_metadata.get("downstream_restrictions")
        != expected_restrictions
        or contract_metadata.get(DECLARED_SOURCE_URL_METADATA_KEY)
        != document.source_url
        or contract_metadata.get(DECLARED_SOURCE_URL_EXPLICIT_METADATA_KEY) is not True
    ):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    attestation = record_scope.get(_FROZEN_METADATA_SCOPE_KEY)
    if attestation is not None and not _is_sha256(attestation):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    allowed_scope_keys = {"source_kind", "query_sha256"}
    if attestation is not None:
        allowed_scope_keys.add(_FROZEN_METADATA_SCOPE_KEY)
    if set(record_scope) != allowed_scope_keys:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    return _StoredGildataGovernance(True, attestation)


def _validate_full_body_reuse(
    session: Session,
    *,
    document: DocumentVersion,
    expected_spec: _FullBodySpanSpec,
) -> tuple[list[SourceSpan], str | None]:
    """Validate first-seen canonical metadata without trusting a later alias."""
    spans = _spans_for_document(session, document.id)
    if len(spans) != 1:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    span = spans[0]
    locator = span.locator
    stable_fields = ("kind", "page", "paragraph", "parser")
    descriptive_fields = tuple(
        field
        for field in expected_spec.locator_identity_fields
        if field not in stable_fields
    )
    if (
        not isinstance(locator, dict)
        or any(not isinstance(locator.get(field), str) for field in descriptive_fields)
        or span.verbatim_text != expected_spec.verbatim_text
        or span.text_sha256 != expected_spec.text_sha256
        or _locator_identity(locator, stable_fields)
        != _locator_identity(expected_spec.locator, stable_fields)
    ):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    stored_publish_date = locator.get("publish_date")
    parsed_publish_date = _parse_datetime(stored_publish_date)
    if (
        locator.get("title") != document.title
        or (
            bool(stored_publish_date.strip())
            and parsed_publish_date is None
        )
        or not _same_provider_datetime(document.published_at, parsed_publish_date)
    ):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    full_identity_matches = _full_body_span_identity(
        locator=locator,
        verbatim_text=span.verbatim_text,
        text_sha256=span.text_sha256,
        locator_fields=expected_spec.locator_identity_fields,
    ) == _full_body_span_identity(
        locator=expected_spec.locator,
        verbatim_text=expected_spec.verbatim_text,
        text_sha256=expected_spec.text_sha256,
        locator_fields=expected_spec.locator_identity_fields,
    )
    metadata_sha256 = _frozen_full_body_metadata_sha256(
        document=document,
        locator=locator,
        locator_fields=expected_spec.locator_identity_fields,
        verbatim_text=span.verbatim_text,
        text_sha256=span.text_sha256,
    )
    governance = _stored_gildata_governance(session, document)
    if governance.frozen_metadata_sha256 is not None:
        if governance.frozen_metadata_sha256 != metadata_sha256:
            raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
        return spans, metadata_sha256
    if not full_identity_matches:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    return (
        spans,
        None if governance.exists else metadata_sha256,
    )


def _locator_identity(
    locator: object,
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(locator, dict):
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    try:
        return tuple(
            json.dumps(
                locator.get(field),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for field in fields
        )
    except (TypeError, ValueError) as exc:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE) from exc


def _full_body_span_identity(
    *,
    locator: object,
    verbatim_text: str,
    text_sha256: str | None,
    locator_fields: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        verbatim_text,
        text_sha256 or "",
        *_locator_identity(locator, locator_fields),
    )


def _build_full_body_span_spec(
    *,
    locator: dict[str, object],
    verbatim_text: str,
) -> _FullBodySpanSpec:
    locator_kind = locator.get("kind")
    identity_fields = _FULL_BODY_LOCATOR_IDENTITY_FIELDS.get(locator_kind)
    if identity_fields is None:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    return _FullBodySpanSpec(
        locator=locator,
        verbatim_text=verbatim_text,
        text_sha256=hashlib.sha256(verbatim_text.encode("utf-8")).hexdigest(),
        locator_identity_fields=identity_fields,
    )


def _validate_macro_span_reuse(
    session: Session,
    *,
    document: DocumentVersion,
    expected_specs: list[_MacroSpanSpec],
) -> list[SourceSpan]:
    """Require every reused macro metric span and its identity to match."""
    spans = _spans_for_document(session, document.id)
    expected = Counter(
        _macro_span_identity(
            locator=spec.locator,
            verbatim_text=spec.verbatim_text,
            text_sha256=spec.text_sha256,
        )
        for spec in expected_specs
    )
    actual = Counter(
        _macro_span_identity(
            locator=span.locator,
            verbatim_text=span.verbatim_text,
            text_sha256=span.text_sha256,
        )
        for span in spans
    )
    if len(spans) != len(expected_specs) or actual != expected:
        raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
    return spans


def _macro_span_identity(
    *,
    locator: object,
    verbatim_text: str,
    text_sha256: str | None,
) -> tuple[str, ...]:
    """Project a macro span onto stable source identity fields.

    ``case_id`` is deliberately absent: a single immutable provider document
    may be attached to more than one research case.
    """
    locator_identity = _locator_identity(locator, _MACRO_LOCATOR_IDENTITY_FIELDS)
    return (verbatim_text, text_sha256 or "", *locator_identity)


def _macro_text(value: object) -> str:
    return "" if value is None else str(value)


def _macro_row_sort_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in _MACRO_ROW_SORT_FIELDS)


def _macro_numeric_value(row: dict[str, str]) -> Decimal | None:
    return _parse_decimal(row["value"])


def _build_macro_document_spec(
    *,
    rows: list[dict],
    query: str,
    locator_extra: dict[str, object],
) -> _MacroDocumentSpec:
    """Build one deterministic, bounded macro document and all of its spans.

    Each metric contributes at most ``_MACRO_WINDOW_SIZE`` canonical rows.
    The frozen body, span text, peak/latest facts and ``n_points`` all derive
    from that exact window, so no cited fact depends on bytes outside the
    document's content identity.
    """
    normalized_rows = [
        {field: _macro_text(row.get(field)) for field in _MACRO_ROW_FIELDS}
        for row in rows
    ]
    by_metric: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in normalized_rows:
        identity = (row["metric_name"], row["metric_code"])
        by_metric.setdefault(identity, []).append(row)

    canonical_groups = [
        (
            identity,
            sorted(items, key=_macro_row_sort_key)[-_MACRO_WINDOW_SIZE:],
        )
        for identity, items in sorted(by_metric.items())
    ]
    title = f"宏观时序 · {query[:40]}（{len(canonical_groups)} 个指标）"
    body_lines = [f"# 查询: {query}", ""]
    specs: list[_MacroSpanSpec] = []
    for (metric_name, metric_code), window in canonical_groups:
        body_lines.append(f"## {metric_name} [{metric_code}]")
        body_lines.extend(
            f"- {row['date']} | {row['value']} {row['unit']} | "
            f"{row['metric_code']} | {row['frequency']} | {row['source']}"
            for row in window
        )
        body_lines.append("")

        numeric_rows = [
            (row, numeric_value)
            for row in window
            if (numeric_value := _macro_numeric_value(row)) is not None
        ]
        if not numeric_rows:
            raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
        peak = max(
            numeric_rows,
            key=lambda item: (item[1], _macro_row_sort_key(item[0])),
        )[0]
        tail = window[-1]
        span_text = "\n".join(
            f"{row['date']}: {row['value']} {row['unit']}" for row in window
        )
        specs.append(
            _MacroSpanSpec(
                locator={
                    "kind": "macro_series",
                    "query": query,
                    "metric_name": metric_name,
                    "metric_code": metric_code,
                    "frequency": window[0]["frequency"],
                    "unit": window[0]["unit"],
                    "source": window[0]["source"],
                    "peak_date": peak["date"],
                    "peak_value": peak["value"],
                    "latest_date": tail["date"],
                    "latest_value": tail["value"],
                    "n_points": len(window),
                    **locator_extra,
                },
                verbatim_text=span_text,
                text_sha256=hashlib.sha256(span_text.encode("utf-8")).hexdigest(),
            )
        )

    latest_date = max(
        (row["date"] for _identity, window in canonical_groups for row in window),
        default="",
    )
    return _MacroDocumentSpec(
        raw="\n".join(body_lines).encode("utf-8"),
        title=title,
        published_at=_parse_datetime(latest_date),
        spans=tuple(specs),
    )


def _count_document_result(
    summary: dict,
    *,
    category: str,
    document: DocumentVersion,
    spans: list[SourceSpan],
    created: bool,
    counted_document_ids: set[uuid.UUID],
    counted_span_ids: set[uuid.UUID],
) -> None:
    """Count unique persisted entities while allowing every hit to validate."""
    if document.id in counted_document_ids:
        return
    counted_document_ids.add(document.id)
    new_span_ids = {span.id for span in spans} - counted_span_ids
    counted_span_ids.update(new_span_ids)
    if created:
        summary[category] += 1
        summary["spans"] += len(new_span_ids)
    else:
        summary[f"{category}_reused"] += 1
        summary["spans_reused"] += len(new_span_ids)


def ingest(
    session: Session,
    client: GildataMCPClient,
    *,
    case_id: uuid.UUID | None = None,
    research_queries: list[str] | None = None,
    announcement_query: str | None = None,
    news_query: str | None = None,
    quote_query: str | None = None,
    quote_stock_code: str | None = None,
    macro_queries: list[str] | None = None,
    declared_by: str = "system:gildata-ingest",
) -> dict:
    """Ingest real Gildata data into *session*.

    Returns a summary dict with counts of unique document/span entities seen
    in this run and valuation snapshots written (or skipped as duplicates).
    Repeated upstream hits are still fully validated, but never inflate the
    created/reused counters. Query parameters default to the AI-compute
    constants; the command API passes caller-supplied overrides through here.
    """
    research_queries = research_queries or RESEARCH_QUERIES
    announcement_query = announcement_query or ANNOUNCEMENT_QUERY
    news_query = news_query or NEWS_QUERY
    quote_query = quote_query or QUOTE_QUERY
    if quote_stock_code is None:
        quote_stock_code = QUOTE_STOCK_CODE
    try:
        requested_bare_code = _bare_security_code(quote_stock_code)
    except _InvalidSecurityCodeError as exc:
        raise GildataRequestValidationError(
            "quote_stock_code is invalid"
        ) from exc
    rights = GildataEvidenceRights.from_env()

    document_service = DocumentService(DocumentRepository(session))
    instruments = InstrumentRepository(session)
    resolved_case_id = _resolve_case_id(session, case_id)

    summary = {
        "research_reports": 0,
        "research_reports_reused": 0,
        "announcements": 0,
        "announcements_reused": 0,
        "news": 0,
        "news_reused": 0,
        "macro_series": 0,
        "macro_series_reused": 0,
        "spans": 0,
        "spans_reused": 0,
        "valuations_written": 0,
        "valuations_skipped": 0,
        "quote_identity_verified": False,
        "stock_id": None,
        "case_id": str(resolved_case_id) if resolved_case_id else None,
    }
    counted_document_ids: set[uuid.UUID] = set()
    counted_span_ids: set[uuid.UUID] = set()

    # Spans written through this script come from a structured text
    # payload (one ``content`` field per result), not a paginated PDF,
    # so ``page`` / ``paragraph`` are best-effort placeholders.  The
    # ``parser`` field carries the engine version so a future S4 backfill
    # can recognise these and synthesise a ``#/legacy-paragraph/1`` v1
    # ref instead of treating them as missing-page errors.  Without
    # these keys, the v1 read-path returns ``locator_v1=None`` and the
    # workbench cannot link a citation to any page — see the S5 read
    # test in tests/test_s5_v1_read_path.py for the expected shape.
    span_locator_extra = {
        "page": 1,
        "paragraph": 1,
        "parser": PARSER_VERSION,
        **(
            {"case_id": str(resolved_case_id)}
            if resolved_case_id is not None
            else {}
        ),
    }

    # 1. Research reports -> DocumentVersion + SourceSpan.  Process each
    # bounded response immediately: exact-body aliases reuse the first
    # governed metadata without accumulating every provider body in memory.
    for query in research_queries:
        reports = adapters.fetch_research_report(client, query)
        for report in reports[:3]:
            content = report.get("content", "")
            if not content:
                continue
            published_at = _parse_datetime(report.get("publish_date", ""))
            raw = content.encode("utf-8")
            span_spec = _build_full_body_span_spec(
                locator={
                    "kind": "research_report",
                    "title": report.get("title", ""),
                    "org": report.get("org", ""),
                    "publish_date": report.get("publish_date", ""),
                    "sec_code": report.get("sec_code", ""),
                    **span_locator_extra,
                },
                verbatim_text=content,
            )
            version, created = _freeze_full_body(
                document_service,
                raw=raw,
                source_kind="research-report",
                published_at=published_at,
                title=report.get("title", ""),
                allow_metadata_alias_on_reuse=True,
            )
            if not created:
                spans, frozen_metadata_sha256 = _validate_full_body_reuse(
                    session,
                    document=version,
                    expected_spec=span_spec,
                )
            else:
                frozen_metadata_sha256 = _frozen_full_body_metadata_sha256(
                    document=version,
                    locator=span_spec.locator,
                    locator_fields=span_spec.locator_identity_fields,
                    verbatim_text=span_spec.verbatim_text,
                    text_sha256=span_spec.text_sha256,
                )
            _record_gildata_governance(
                session,
                document=version,
                source_kind="research-report",
                query=query,
                rights=rights,
                declared_by=declared_by,
                frozen_metadata_sha256=frozen_metadata_sha256,
            )
            if resolved_case_id is not None:
                document_service.attach_to_case(
                    research_case_id=resolved_case_id,
                    document_version_id=version.id,
                )
            if created:
                spans = [
                    document_service.add_span(
                        document_version_id=version.id,
                        locator=span_spec.locator,
                        verbatim_text=span_spec.verbatim_text,
                        text_sha256=span_spec.text_sha256,
                    )
                ]
            _count_document_result(
                summary,
                category="research_reports",
                document=version,
                spans=spans,
                created=created,
                counted_document_ids=counted_document_ids,
                counted_span_ids=counted_span_ids,
            )

    # 2. Announcements -> DocumentVersion + SourceSpan.
    announcements = adapters.fetch_announcement(client, announcement_query)
    for ann in announcements[:3]:
        content = ann.get("content", "") or ann.get("title", "")
        if not content:
            continue
        published_at = _parse_datetime(ann.get("publish_date", ""))
        raw = content.encode("utf-8")
        span_spec = _build_full_body_span_spec(
            locator={
                "kind": "announcement",
                "title": ann.get("title", ""),
                "stock_code": ann.get("stock_code", ""),
                "sec_name": ann.get("sec_name", ""),
                "publish_date": ann.get("publish_date", ""),
                **span_locator_extra,
            },
            verbatim_text=content,
        )
        version, created = _freeze_full_body(
            document_service,
            raw=raw,
            source_kind="announcement",
            published_at=published_at,
            title=ann.get("title", ""),
            allow_metadata_alias_on_reuse=True,
        )
        if not created:
            spans, frozen_metadata_sha256 = _validate_full_body_reuse(
                session,
                document=version,
                expected_spec=span_spec,
            )
        else:
            frozen_metadata_sha256 = _frozen_full_body_metadata_sha256(
                document=version,
                locator=span_spec.locator,
                locator_fields=span_spec.locator_identity_fields,
                verbatim_text=span_spec.verbatim_text,
                text_sha256=span_spec.text_sha256,
            )
        _record_gildata_governance(
            session,
            document=version,
            source_kind="announcement",
            query=announcement_query,
            rights=rights,
            declared_by=declared_by,
            frozen_metadata_sha256=frozen_metadata_sha256,
        )
        if resolved_case_id is not None:
            document_service.attach_to_case(
                research_case_id=resolved_case_id, document_version_id=version.id
            )
        if created:
            spans = [
                document_service.add_span(
                    document_version_id=version.id,
                    locator=span_spec.locator,
                    verbatim_text=span_spec.verbatim_text,
                    text_sha256=span_spec.text_sha256,
                )
            ]
        _count_document_result(
            summary,
            category="announcements",
            document=version,
            spans=spans,
            created=created,
            counted_document_ids=counted_document_ids,
            counted_span_ids=counted_span_ids,
        )

    # 2b. News/舆情 -> DocumentVersion + SourceSpan.
    news_items = adapters.fetch_news(client, news_query)
    for news in news_items[:3]:
        content = news.get("content", "") or news.get("title", "")
        if not content:
            continue
        published_at = _parse_datetime(news.get("publish_date", ""))
        raw = content.encode("utf-8")
        span_spec = _build_full_body_span_spec(
            locator={
                "kind": "news",
                "title": news.get("title", ""),
                "source": news.get("source", ""),
                "sec_name": news.get("sec_name", ""),
                "publish_date": news.get("publish_date", ""),
                **span_locator_extra,
            },
            verbatim_text=content,
        )
        version, created = _freeze_full_body(
            document_service,
            raw=raw,
            source_kind="news",
            published_at=published_at,
            title=news.get("title", ""),
            allow_metadata_alias_on_reuse=True,
        )
        if not created:
            spans, frozen_metadata_sha256 = _validate_full_body_reuse(
                session,
                document=version,
                expected_spec=span_spec,
            )
        else:
            frozen_metadata_sha256 = _frozen_full_body_metadata_sha256(
                document=version,
                locator=span_spec.locator,
                locator_fields=span_spec.locator_identity_fields,
                verbatim_text=span_spec.verbatim_text,
                text_sha256=span_spec.text_sha256,
            )
        _record_gildata_governance(
            session,
            document=version,
            source_kind="news",
            query=news_query,
            rights=rights,
            declared_by=declared_by,
            frozen_metadata_sha256=frozen_metadata_sha256,
        )
        if resolved_case_id is not None:
            document_service.attach_to_case(
                research_case_id=resolved_case_id, document_version_id=version.id
            )
        if created:
            spans = [
                document_service.add_span(
                    document_version_id=version.id,
                    locator=span_spec.locator,
                    verbatim_text=span_spec.verbatim_text,
                    text_sha256=span_spec.text_sha256,
                )
            ]
        _count_document_result(
            summary,
            category="news",
            document=version,
            spans=spans,
            created=created,
            counted_document_ids=counted_document_ids,
            counted_span_ids=counted_span_ids,
        )

    # 2c. Macro/commodity time series (MacroIndustryData) -> DocumentVersion +
    # SourceSpan.  Each query's returned slice is frozen as one version so
    # extract/propose can reference the peak/latest pair as disclosed facts.
    # The complete deterministic body identifies the version; replays validate
    # every metric span before reporting it as reused.
    for mquery in macro_queries or []:
        rows = adapters.fetch_macro_series(client, mquery)
        if not rows:
            continue
        macro_spec = _build_macro_document_spec(
            rows=rows,
            query=mquery,
            locator_extra=span_locator_extra,
        )
        version, created = _freeze_full_body(
            document_service,
            raw=macro_spec.raw,
            source_kind="macro-industry",
            published_at=macro_spec.published_at,
            title=macro_spec.title,
        )
        _record_gildata_governance(
            session,
            document=version,
            source_kind="macro-industry",
            query=mquery,
            rights=rights,
            declared_by=declared_by,
        )
        if resolved_case_id is not None:
            document_service.attach_to_case(
                research_case_id=resolved_case_id, document_version_id=version.id
            )
        # Each metric group becomes its own span so extract can pin facts
        # to that metric without ambiguity.
        if created:
            spans = [
                document_service.add_span(
                    document_version_id=version.id,
                    locator=span_spec.locator,
                    verbatim_text=span_spec.verbatim_text,
                    text_sha256=span_spec.text_sha256,
                )
                for span_spec in macro_spec.spans
            ]
        else:
            spans = _validate_macro_span_reuse(
                session,
                document=version,
                expected_specs=list(macro_spec.spans),
            )
        _count_document_result(
            summary,
            category="macro_series",
            document=version,
            spans=spans,
            created=created,
            counted_document_ids=counted_document_ids,
            counted_span_ids=counted_span_ids,
        )

    # 3. Market quote -> ValuationSnapshot rows for the resolved stock.
    quotes = adapters.fetch_quote(client, quote_query)
    if quotes:
        quote = quotes[0]
        returned_code = quote.get("stock_code")
        if returned_code is None or returned_code == "":
            resolved_code = requested_bare_code
        else:
            try:
                returned_bare_code = _bare_security_code(returned_code)
            except _InvalidSecurityCodeError as exc:
                raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE) from exc
            if returned_bare_code != requested_bare_code:
                raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
            resolved_code = returned_bare_code
        summary["quote_identity_verified"] = True
        stock = ensure_stock(
            session,
            instruments,
            code=resolved_code,
            name=quote.get("stock_name", "") or "寒武纪",
        )
        summary["stock_id"] = str(stock.id)

        as_of = _previous_business_day()
        for quote_key, (metric_name, definition) in METRIC_DEFINITIONS.items():
            value = _parse_decimal(quote.get(quote_key, ""))
            if value is None:
                continue
            if _valuation_exists(session, stock.id, as_of, metric_name):
                summary["valuations_skipped"] += 1
                continue
            instruments.add_valuation_snapshot(
                stock_id=stock.id,
                as_of_date=as_of,
                metric_name=metric_name,
                metric_value=value,
                source=SOURCE_GILDATA,
                definition=definition,
            )
            summary["valuations_written"] += 1

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest real Gildata (恒生聚源) data into the evidence ledger."
    )
    parser.add_argument(
        "--case-id",
        required=True,
        help="ResearchCase UUID to tag ingested spans against; this must be "
        "explicit so a shared ledger never adopts a global default Case.",
    )
    args = parser.parse_args()

    load_local_env()  # backend/.env (gitignored); shell env still wins

    case_id = uuid.UUID(args.case_id)

    url = os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db")
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    session_local = sessionmaker(bind=engine, future=True)
    with GildataMCPClient.from_env() as client, session_local() as session:
        summary = ingest(session, client, case_id=case_id)
        session.commit()

    print("gildata ingest summary:", summary)
    print(
        f"  frozen documents: {summary['research_reports']} research + "
        f"{summary['announcements']} announcements + "
        f"{summary['news']} news ({summary['spans']} spans)"
    )
    print(
        f"  valuation snapshots: {summary['valuations_written']} written, "
        f"{summary['valuations_skipped']} skipped (duplicates)"
    )


if __name__ == "__main__":
    main()
