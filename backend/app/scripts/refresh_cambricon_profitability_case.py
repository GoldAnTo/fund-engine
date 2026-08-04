"""Live Juyuan refresh for the Cambricon profitability case.

The refresh command is purely append-only: new provider observations become
``machine_generated`` evidence links ready for human review.  The frozen seed
snapshot, AI assessment, and any human review decisions are never mutated by a
refresh -- not on success, not on failure, not on duplication.

Design contract (see ``docs/superpowers/specs/2026-08-03-cambricon-profitability-case-design.md`` §6):

* One auditable ``FinQuery`` call reproduces the frozen seed observation.  The
  query is pinned to the frozen fixture so a provider that returns the seed
  payload deduplicates against the seed document by content hash; a provider
  that returns newer periods appends them as pending evidence.
* The raw response is frozen as a single ``DocumentVersion`` (content-addressed);
  identical bytes collapse to the existing version and append nothing.
* New facts are appended only as ``SourceStatement`` + ``EvidenceLink`` with
  ``review_state="machine_generated"``.  No new snapshot, assessment, or review
  is created.
* The CLI never prints request URLs (the provider token rides in the URL query
  string) and surfaces only token-free diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.gildata.adapters import parse_content, parse_table_markdown_payload
from app.datasources.gildata.client import GildataMCPClient, GildataMCPError
from app.env import load_local_env
from app.models.ledger import EvidenceLink
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.scripts.cambricon_profitability_data import load_case_data
from app.scripts.seed_cambricon_profitability_case import CASE_TITLE
from app.services.ingest import DocumentService
from app.services.research import ResearchService

# A single, auditable FinQuery pinned to the frozen seed observation.  Derived
# from the frozen fixture so a drift between the refresh query and the seed
# capture is impossible by construction.
REFRESH_QUERIES: tuple[str, ...] = (load_case_data().juyuan.query,)

# Synthetic, token-free source URL.  The real provider URL carries the token in
# its query string, so it must never be persisted or printed; this scheme keeps
# the ledger traceable without leaking credentials.
REFRESH_SOURCE_URL = "gildata://FinQuery/688256/profitability"
REFRESH_PARSER_VERSION = "cambricon-profitability-refresh-v1"
REVIEW_URL = "http://localhost:5173/review"


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of one refresh run.

    ``created_documents``/``duplicate_documents`` count distinct response
    bodies (one per refresh call): a brand-new payload creates one document;
    a payload whose content hash already exists creates none and counts as one
    duplicate.  ``pending_links`` is the number of machine-generated evidence
    links appended for human review.
    """

    document_ids: list[uuid.UUID]
    links: list[EvidenceLink]
    created_documents: int
    duplicate_documents: int
    pending_links: int


def _row_metric(row: dict[str, str]) -> str:
    return row.get("财务科目名称") or row.get("财务分析指标名称") or "盈利能力指标"


def _row_amount(row: dict[str, str]) -> str:
    return row.get("财务科目数额") or row.get("财务分析指标数额") or ""


def _row_period(row: dict[str, str]) -> str:
    report_period = row.get("报告期") or ""
    as_of = row.get("时间") or ""
    if report_period and as_of:
        return f"{report_period}（{as_of}）"
    return report_period or as_of


def refresh(
    session: Session,
    case_id: uuid.UUID,
    *,
    client: GildataMCPClient,
) -> RefreshResult:
    """Append live Juyuan observations as pending evidence.

    Raises ``ValueError`` if *case_id* is not the Cambricon profitability case,
    and ``GildataMCPError`` on transport failure or an unusable response.  No
    ledger row is written when the response duplicates the frozen seed document.
    """
    research_repo = ResearchRepository(session)
    case = research_repo.get_case(case_id)
    if case is None or case.title != CASE_TITLE:
        raise ValueError("Cambricon profitability case not found for refresh")

    theses = research_repo.theses_for_case(case_id)
    if not theses:
        raise ValueError("Cambricon profitability case has no thesis to attach evidence to")
    thesis = theses[0]

    query = REFRESH_QUERIES[0]
    raw_text = client.call_tool("FinQuery", {"query": query})

    content_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    document_repo = DocumentRepository(session)
    existing = document_repo.by_hash(content_sha256)
    if existing is not None:
        # Same response body as a prior capture (seed or earlier refresh):
        # append nothing, count one duplicate.  The frozen snapshot, assessment,
        # and review decisions are untouched.
        return RefreshResult(
            document_ids=[],
            links=[],
            created_documents=0,
            duplicate_documents=1,
            pending_links=0,
        )

    results = parse_content(raw_text)
    if not results:
        raise GildataMCPError("FinQuery returned no usable profitability results")

    # Parse every table before writing so a response with no data rows raises
    # before any ledger row is appended (no partial document without links).
    parsed_tables: list[tuple[str, str, list[dict[str, str]]]] = []
    total_rows = 0
    for item in results:
        api_name = item.get("api_name", "")
        table_markdown = item.get("table_markdown", "")
        rows = parse_table_markdown_payload(table_markdown)
        parsed_tables.append((api_name, table_markdown, rows))
        total_rows += len(rows)
    if total_rows == 0:
        raise GildataMCPError("FinQuery returned no parseable profitability rows")

    captured_at = datetime.now(timezone.utc)
    raw_bytes = raw_text.encode("utf-8")
    version = document_repo.insert_version(
        content_sha256=content_sha256,
        source_url=REFRESH_SOURCE_URL,
        published_at=None,
        available_at=captured_at,
        acquired_at=captured_at,
        parser_version=REFRESH_PARSER_VERSION,
        supersedes_id=None,
        title="Juyuan FinQuery 刷新响应（寒武纪盈利能力）",
        byte_size=len(raw_bytes),
        language="zh",
    )

    document_service = DocumentService(document_repo)
    research_service = ResearchService(research_repo)
    links: list[EvidenceLink] = []
    for api_name, table_markdown, rows in parsed_tables:
        if not rows:
            continue
        span = document_service.add_span(
            version.id,
            {
                "source": "raw_response",
                "provider": "gildata-juyuan",
                "tool": "FinQuery",
                "api_name": api_name,
            },
            table_markdown,
        )
        for row in rows:
            metric = _row_metric(row)
            amount = _row_amount(row)
            period = _row_period(row)
            unit = row.get("展示单位") or ""
            normalized_text = (
                f"{metric}（{period}）累计值 {amount} {unit}".strip()
            )
            statement = research_service.add_statement(
                span.id, normalized_text, kind="disclosed_fact"
            )
            link = research_service.link_evidence(
                thesis.id,
                statement.id,
                role="contextualizes",
                reason="聚源刷新追加的累计利润观测，待人工复核后决定是否纳入正式证据",
                scope={
                    "metric": metric,
                    "period": period,
                    "unit": unit,
                    "source": "Juyuan",
                    "kind": "refresh_pending",
                },
                available_at=captured_at,
            )
            links.append(link)

    return RefreshResult(
        document_ids=[version.id],
        links=links,
        created_documents=1,
        duplicate_documents=0,
        pending_links=len(links),
    )


def main() -> int:
    """CLI entry point.

    Returns nonzero -- with a token-free diagnostic on stderr -- when the
    provider token is missing or the refresh fails.  Never prints request URLs.
    """
    load_local_env()
    parser = argparse.ArgumentParser(
        description="Append live Juyuan evidence to the Cambricon profitability case."
    )
    parser.add_argument("--case-id", required=True, type=uuid.UUID)
    args = parser.parse_args()

    try:
        client = GildataMCPClient.from_env()
    except GildataMCPError as exc:
        # from_env's message names GILDATA_TOKEN without echoing any token
        # value or provider URL.
        print(str(exc), file=sys.stderr)
        return 1

    engine = create_engine(
        os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db"), future=True
    )
    session_local = sessionmaker(bind=engine, future=True)
    with client, session_local() as session:
        try:
            result = refresh(session, args.case_id, client=client)
            session.commit()
        except (GildataMCPError, ValueError) as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return 1

    print(
        json.dumps(
            {
                "case_id": str(args.case_id),
                "created_documents": result.created_documents,
                "duplicate_documents": result.duplicate_documents,
                "pending_links": result.pending_links,
                "review_url": REVIEW_URL,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
