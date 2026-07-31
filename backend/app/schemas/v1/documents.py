"""Document library v1 wire DTOs.

Honest for the current schema: no title, publisher, document type, MIME type,
blob URL or parse failure stage is fabricated (delivery 2 adds those fields).
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
