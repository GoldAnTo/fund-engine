"""Document library v1 wire DTOs.

Honest for the current schema: no publisher, MIME type, blob URL or parse
failure stage is fabricated.  ``title`` / ``org`` / ``doc_kind`` are derived
from the first SourceSpan locator that carries them (ingest-time metadata),
never invented; they stay ``None`` when no locator provides them.
"""

from typing import Any, Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class DocumentSummaryDTO(V1Model):
    id: str
    content_sha256: str
    source_url: str
    published_at: str | None
    available_at: str
    acquired_at: str
    parser_version: str
    supersedes_id: str | None
    span_count: int
    statement_count: int
    parse_state: Literal["parsed", "unparsed"]
    # Extraction watermark derived from AIRun audit records (defect-3 fix):
    # "extracted_empty" distinguishes a successful zero-output run from a
    # never-attempted version so batch extraction stops re-running it.
    extraction_state: Literal[
        "extracted", "extracted_empty", "failed", "not_attempted"
    ]
    last_extracted_at: str | None = None
    # Derived from span locator metadata when available (else None).
    title: str | None = None
    org: str | None = None
    doc_kind: str | None = None
    # Resolved from locator sec_code/stock_code; "寒武纪 (688256.SH)" when the
    # code matches a known Stock, the raw code otherwise, None when absent.
    entity: str | None = None


class SourceSpanDTO(V1Model):
    id: str
    document_version_id: str
    locator: dict[str, Any]
    verbatim_text: str
    citations: list[dict[str, Any]]


class DocumentListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    items: list[DocumentSummaryDTO]
    page: CursorPage


class DocumentDetailResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    document: DocumentSummaryDTO
    spans: list[SourceSpanDTO]
