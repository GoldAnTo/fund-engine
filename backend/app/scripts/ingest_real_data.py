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
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.gildata import adapters
from app.datasources.gildata.client import GildataMCPClient
from app.env import load_local_env
from app.models.ledger import Base, ResearchCase, Stock, ValuationSnapshot
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.services.ingest import DocumentService

SOURCE_GILDATA = "gildata"
RESEARCH_SOURCE_URL = "gildata://research_report"
ANNOUNCEMENT_SOURCE_URL = "gildata://announcement"
NEWS_SOURCE_URL = "gildata://news"
PARSER_VERSION = "gildata-mcp-1"

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    """Return the explicit case id, else the first ResearchCase, else None."""
    if case_id is not None:
        return case_id
    first = session.scalar(
        select(ResearchCase).order_by(ResearchCase.created_at).limit(1)
    )
    return first.id if first is not None else None


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
) -> dict:
    """Ingest real Gildata data into *session*.

    Returns a summary dict with counts of frozen documents, spans, and
    valuation snapshots written (or skipped as duplicates).  Query
    parameters default to the AI-compute constants; the command API passes
    caller-supplied overrides through here.
    """
    research_queries = research_queries or RESEARCH_QUERIES
    announcement_query = announcement_query or ANNOUNCEMENT_QUERY
    news_query = news_query or NEWS_QUERY
    quote_query = quote_query or QUOTE_QUERY
    quote_stock_code = quote_stock_code or QUOTE_STOCK_CODE

    document_service = DocumentService(DocumentRepository(session))
    instruments = InstrumentRepository(session)
    resolved_case_id = _resolve_case_id(session, case_id)

    summary = {
        "research_reports": 0,
        "announcements": 0,
        "news": 0,
        "spans": 0,
        "valuations_written": 0,
        "valuations_skipped": 0,
        "stock_id": None,
        "case_id": str(resolved_case_id) if resolved_case_id else None,
    }

    span_locator_extra = (
        {"case_id": str(resolved_case_id)} if resolved_case_id is not None else {}
    )

    # 1. Research reports -> DocumentVersion + SourceSpan.
    for query in research_queries:
        reports = adapters.fetch_research_report(client, query)
        for report in reports[:3]:
            content = report.get("content", "")
            if not content:
                continue
            published_at = _parse_datetime(report.get("publish_date", ""))
            version = document_service.freeze(
                raw=content.encode("utf-8"),
                source_url=RESEARCH_SOURCE_URL,
                published_at=published_at,
                parser_version=PARSER_VERSION,
            )
            document_service.add_span(
                document_version_id=version.id,
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
            summary["research_reports"] += 1
            summary["spans"] += 1

    # 2. Announcements -> DocumentVersion + SourceSpan.
    announcements = adapters.fetch_announcement(client, announcement_query)
    for ann in announcements[:3]:
        content = ann.get("content", "") or ann.get("title", "")
        if not content:
            continue
        published_at = _parse_datetime(ann.get("publish_date", ""))
        version = document_service.freeze(
            raw=content.encode("utf-8"),
            source_url=ANNOUNCEMENT_SOURCE_URL,
            published_at=published_at,
            parser_version=PARSER_VERSION,
        )
        document_service.add_span(
            document_version_id=version.id,
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
        summary["announcements"] += 1
        summary["spans"] += 1

    # 2b. News/舆情 -> DocumentVersion + SourceSpan.
    news_items = adapters.fetch_news(client, news_query)
    for news in news_items[:3]:
        content = news.get("content", "") or news.get("title", "")
        if not content:
            continue
        published_at = _parse_datetime(news.get("publish_date", ""))
        version = document_service.freeze(
            raw=content.encode("utf-8"),
            source_url=NEWS_SOURCE_URL,
            published_at=published_at,
            parser_version=PARSER_VERSION,
        )
        document_service.add_span(
            document_version_id=version.id,
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
        summary["news"] += 1
        summary["spans"] += 1

    # 3. Market quote -> ValuationSnapshot rows for the resolved stock.
    quotes = adapters.fetch_quote(client, quote_query)
    if quotes:
        quote = quotes[0]
        resolved_code = quote.get("stock_code", "") or quote_stock_code
        stock = ensure_stock(
            session,
            instruments,
            code=resolved_code,
            name=quote.get("stock_name", "") or "寒武纪",
        )
        summary["stock_id"] = str(stock.id)

        as_of = date.today()
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
        default=None,
        help="optional ResearchCase UUID to tag ingested spans against "
        "(defaults to the first existing case)",
    )
    args = parser.parse_args()

    load_local_env()  # backend/.env (gitignored); shell env still wins

    case_id: uuid.UUID | None = None
    if args.case_id:
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
