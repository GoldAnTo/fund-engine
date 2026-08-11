"""Ingest Gildata fund holdings only when an official report is matched.

``FinQuery`` exposes portfolio rows but does not provide a reliable report
publication date.  This importer therefore pairs every ``(fund, report
period)`` group with exactly one ``AnnouncementData`` seasonal report before
creating a formal :class:`HoldingDisclosure`.  An unmatched group, or one
whose provider contract forbids display, remains a counted intake gap rather
than becoming a misleading fund-exposure record.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.gildata import adapters
from app.datasources.gildata.client import GildataMCPClient
from app.env import load_local_env
from app.models.ledger import Base
from app.models.ledger import Fund, HoldingDisclosure, Stock
from app.models.source_governance import ProviderRecord
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.services.ingest import DocumentService
from app.services.instruments import InstrumentService
from app.services.source_governance import SourceGovernanceService

SOURCE_GILDATA_FUND_REPORT = "gildata_fund_report"
PARSER_VERSION = "gildata-fund-report-v1"
_FILING_KIND_PRECEDENCE = {
    "other": 0,
    "quarterly": 1,
    "annual": 2,
    "correction": 3,
}


@dataclass(frozen=True, slots=True)
class IngestStats:
    holding_rows_seen: int
    matched_reports: int
    holding_disclosures_written: int
    holding_disclosures_skipped_duplicate: int
    pending_match_rows: int
    pending_permission_rows: int
    invalid_rows: int
    out_of_scope_rows: int


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    return None


def _parse_datetime(value: str) -> datetime | None:
    parsed = _parse_date(value)
    return (
        datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        if parsed is not None
        else None
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _fund_code(value: str) -> str:
    return (value or "").strip().upper().removesuffix(".OF")


def _canonical_fund_code(value: str) -> str:
    """Remove provider exchange suffixes before comparing configured funds."""
    return _fund_code(value).split(".", 1)[0]


def _quarter_label(period: date) -> str:
    return f"{period.year}年第{((period.month - 1) // 3) + 1}季度"


def _filing_kind(title: str) -> str:
    normalized = "".join((title or "").split())
    if "更正" in normalized:
        return "correction"
    if "年度报告" in normalized:
        return "annual"
    if "季度" in normalized:
        return "quarterly"
    return "other"


def _matches_exact_fund_report(
    announcement: dict[str, str], *, fund_name: str, period: date
) -> bool:
    """Match a fund report using its fund name plus its exact calendar quarter.

    The provider's announcement result may contain other periods.  We require
    the returned title itself to name both the fund and the requested quarter;
    a search query alone is never enough evidence for the pairing.
    """
    title = "".join((announcement.get("title") or "").split())
    name = "".join((fund_name or "").split())
    reported_alias = "".join((announcement.get("sec_name") or "").split())
    period_marker_matches = (
        _quarter_label(period) in title
        or (period.month == 12 and f"{period.year}年年度报告" in title)
    )
    return bool(
        name
        and period_marker_matches
        and (name in title or name == reported_alias)
    )


def _find_exact_report(
    client: Any, *, fund_code: str, fund_name: str, period: date
) -> dict[str, str] | None:
    query = f"{fund_name} {fund_code} {_quarter_label(period)}报告"
    candidates = [
        announcement
        for announcement in adapters.fetch_announcement(client, query)
        if _matches_exact_fund_report(
            announcement, fund_name=fund_name, period=period
        )
        and _parse_datetime(announcement.get("publish_date", "")) is not None
    ]
    return candidates[0] if len(candidates) == 1 else None


def _ensure_fund(
    session: Session, instruments: InstrumentRepository, *, code: str, name: str
) -> Fund:
    existing = session.scalar(select(Fund).where(Fund.code == code).limit(1))
    if existing is not None:
        return existing
    return instruments.add_fund(code=code, name=name or code, fund_type="unknown")


def _stock_identity(code: str) -> tuple[str, str] | None:
    code = (code or "").strip().upper()
    if code.endswith(".SH") and code[:-3].isdigit() and len(code[:-3]) == 6:
        return code, "SSE"
    if code.endswith(".SZ") and code[:-3].isdigit() and len(code[:-3]) == 6:
        return code, "SZSE"
    if code.endswith(".HK") and code[:-3].isdigit():
        return code, "HKEX"
    if code.isdigit() and len(code) == 6:
        market = "SSE" if code.startswith(("60", "68", "90")) else "SZSE"
        suffix = "SH" if market == "SSE" else "SZ"
        return f"{code}.{suffix}", market
    return None


def _ensure_stock(
    session: Session, instruments: InstrumentRepository, *, code: str, name: str
) -> Stock | None:
    identity = _stock_identity(code)
    if identity is None:
        return None
    stock_code, market = identity
    existing = session.scalar(select(Stock).where(Stock.code == stock_code).limit(1))
    if existing is not None:
        return existing
    company = instruments.add_company(code=stock_code, name=name or stock_code, type="listed")
    return instruments.add_stock(
        company_id=company.id, code=stock_code, name=name or stock_code, market=market
    )


def _disclosure_exists(
    session: Session,
    *,
    fund_id: Any,
    stock_id: Any,
    report_period: date,
    source_document_version_id: Any,
) -> bool:
    return session.scalar(
        select(HoldingDisclosure.id)
        .where(HoldingDisclosure.fund_id == fund_id)
        .where(HoldingDisclosure.stock_id == stock_id)
        .where(HoldingDisclosure.report_period == report_period)
        .where(HoldingDisclosure.source == SOURCE_GILDATA_FUND_REPORT)
        .where(HoldingDisclosure.source_document_version_id == source_document_version_id)
        .limit(1)
    ) is not None


def _predecessor_for(
    session: Session,
    *,
    fund_id: Any,
    stock_id: Any,
    report_period: date,
    filing_kind: str,
    published_at: datetime,
) -> HoldingDisclosure | None:
    candidates = [
        disclosure
        for disclosure in session.scalars(
            select(HoldingDisclosure)
            .where(HoldingDisclosure.fund_id == fund_id)
            .where(HoldingDisclosure.stock_id == stock_id)
            .where(HoldingDisclosure.report_period == report_period)
        )
        if (
            _FILING_KIND_PRECEDENCE[disclosure.filing_kind]
            < _FILING_KIND_PRECEDENCE[filing_kind]
            or (
                _FILING_KIND_PRECEDENCE[disclosure.filing_kind]
                == _FILING_KIND_PRECEDENCE[filing_kind]
                and _as_utc(disclosure.published_at) < _as_utc(published_at)
            )
        )
    ]
    return max(
        candidates,
        key=lambda disclosure: (
            _FILING_KIND_PRECEDENCE[disclosure.filing_kind],
            disclosure.published_at,
            disclosure.created_at,
            str(disclosure.id),
        ),
        default=None,
    )


def _freeze_report(
    session: Session,
    *,
    announcement: dict[str, str],
    fund_code: str,
    period: date,
    permissions: dict[str, bool],
    case_id: uuid.UUID | None = None,
) -> tuple[Any, ProviderRecord]:
    published_at = _parse_datetime(announcement["publish_date"])
    assert published_at is not None
    source_url = announcement.get("source_url") or "gildata://fund-report"
    content = announcement.get("content") or announcement["title"]
    documents = DocumentService(DocumentRepository(session))
    document = documents.freeze(
        raw=content.encode("utf-8"),
        source_url=source_url,
        published_at=published_at,
        parser_version=PARSER_VERSION,
        title=announcement["title"],
        source_authority="primary_disclosure",
    )
    if case_id is not None:
        documents.attach_to_case(
            research_case_id=case_id, document_version_id=document.id
        )
    documents.add_span(
        document_version_id=document.id,
        locator={
            "kind": "fund_seasonal_report",
            "fund_code": fund_code,
            "report_period": period.isoformat(),
            "title": announcement["title"],
            "publish_date": announcement["publish_date"],
            "source_url": source_url,
            "page": 1,
            "paragraph": 1,
            "parser": PARSER_VERSION,
        },
        verbatim_text=content,
    )
    SourceGovernanceService(session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={
            "provider_name": "gildata",
            "provider_record_id": f"{fund_code}:{period.isoformat()}",
            "request_scope": {
                "tool": "AnnouncementData",
                "fund_code": fund_code,
                "report_period": period.isoformat(),
            },
            "retrieval_reference": source_url,
            "permissions": {
                "ai_processing": False,
                "display": bool(permissions.get("display", False)),
                "export": False,
                "api": False,
            },
            "downstream_restrictions": ["仅限基金披露核验与人工审核"],
        },
        declared_by="system:gildata-fund-holdings",
    )
    record = session.scalar(
        select(ProviderRecord)
        .where(ProviderRecord.document_version_id == document.id)
        .limit(1)
    )
    assert record is not None
    return document, record


def ingest(
    session: Session,
    client: Any,
    *,
    fund_codes: list[str],
    report_period: date,
    permissions: dict[str, bool] | None = None,
    case_id: uuid.UUID | None = None,
) -> IngestStats:
    """Fetch specified fund holdings and admit only exact, displayable reports."""
    permissions = dict(permissions or {})
    instruments = InstrumentRepository(session)
    rows_seen = matched_reports = written = skipped_duplicate = 0
    pending_match = pending_permission = invalid = out_of_scope = 0

    for requested_code in fund_codes:
        requested = _canonical_fund_code(requested_code)
        query = f"查询基金{requested}{_quarter_label(report_period)}公开披露的股票持仓明细，包括股票代码、股票名称、持仓权重、报告期"
        holdings = adapters.fetch_fund_stock_holdings(client, query)
        groups: dict[tuple[str, str], list[dict[str, str]]] = {}
        for holding in holdings:
            rows_seen += 1
            fund_code = _canonical_fund_code(holding["fund_code"])
            period = _parse_date(holding["report_period"])
            if period is None:
                invalid += 1
                continue
            if fund_code != requested or period != report_period:
                out_of_scope += 1
                continue
            groups.setdefault((fund_code, period.isoformat()), []).append(holding)

        for (fund_code, raw_period), group in groups.items():
            period = _parse_date(raw_period)
            if period is None:
                invalid += len(group)
                continue
            fund_name = max(
                (str(item.get("fund_name", "")) for item in group),
                key=len,
                default="",
            )
            announcement = _find_exact_report(
                client, fund_code=fund_code, fund_name=fund_name, period=period
            )
            if announcement is None:
                pending_match += len(group)
                continue
            matched_reports += 1
            document, provider_record = _freeze_report(
                session,
                announcement=announcement,
                fund_code=fund_code,
                period=period,
                permissions=permissions,
                case_id=case_id,
            )
            if not permissions.get("display", False):
                pending_permission += len(group)
                continue
            filing_kind = _filing_kind(announcement["title"])
            published_at = _parse_datetime(announcement["publish_date"])
            assert published_at is not None
            fund = _ensure_fund(session, instruments, code=fund_code, name=fund_name)
            for holding in group:
                stock = _ensure_stock(
                    session,
                    instruments,
                    code=holding["stock_code"],
                    name=holding.get("stock_name", ""),
                )
                try:
                    # Provider values are explicitly percentage points (for
                    # example 5.33 means 5.33%).  Ledger and read models use
                    # a 0..1 ratio so presentation can multiply once.
                    weight = Decimal(holding["weight"]) / Decimal("100")
                except (InvalidOperation, ValueError):
                    invalid += 1
                    continue
                if stock is None or weight <= 0:
                    invalid += 1
                    continue
                if _disclosure_exists(
                    session,
                    fund_id=fund.id,
                    stock_id=stock.id,
                    report_period=period,
                    source_document_version_id=document.id,
                ):
                    skipped_duplicate += 1
                    continue
                predecessor = _predecessor_for(
                    session,
                    fund_id=fund.id,
                    stock_id=stock.id,
                    report_period=period,
                    filing_kind=filing_kind,
                    published_at=published_at,
                )
                InstrumentService(session).add_holding_disclosure(
                    fund=fund,
                    stock=stock,
                    weight=weight,
                    report_period=period,
                    published_at=published_at,
                    source=SOURCE_GILDATA_FUND_REPORT,
                    source_document_version_id=document.id,
                    provider_record_id=provider_record.id,
                    coverage_status="partial",
                    filing_kind=filing_kind,
                    supersedes_disclosure_id=predecessor.id if predecessor else None,
                )
                written += 1

    return IngestStats(
        holding_rows_seen=rows_seen,
        matched_reports=matched_reports,
        holding_disclosures_written=written,
        holding_disclosures_skipped_duplicate=skipped_duplicate,
        pending_match_rows=pending_match,
        pending_permission_rows=pending_permission,
        invalid_rows=invalid,
        out_of_scope_rows=out_of_scope,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fund-codes",
        required=True,
        type=lambda value: [item.strip() for item in value.split(",") if item.strip()],
        help="comma-separated fund codes, for example 005827,110011.OF",
    )
    parser.add_argument(
        "--report-period",
        required=True,
        type=date.fromisoformat,
        help="frozen quarter-end report period, for example 2024-12-31",
    )
    parser.add_argument(
        "--allow-display",
        action="store_true",
        help="declare that the team's provider licence permits displaying the frozen report",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="query and validate the run, then roll back every ledger write",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a bounded, operator-visible fund disclosure intake."""
    args = _parse_args(argv)
    load_local_env()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db")
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with GildataMCPClient.from_env() as client, session_local() as session:
        stats = ingest(
            session,
            client,
            fund_codes=args.fund_codes,
            report_period=args.report_period,
            permissions={"display": args.allow_display},
        )
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    print(
        json.dumps(
            {
                "fund_codes": args.fund_codes,
                "report_period": args.report_period.isoformat(),
                "allow_display": args.allow_display,
                "dry_run": args.dry_run,
                **asdict(stats),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
