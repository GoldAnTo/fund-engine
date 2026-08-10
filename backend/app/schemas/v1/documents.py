"""Document library v1 wire DTOs.

Honest for the current schema: no publisher, MIME type, blob URL or parse
failure stage is fabricated.  ``title`` / ``org`` / ``doc_kind`` are derived
from the first SourceSpan locator that carries them (ingest-time metadata),
never invented; they stay ``None`` when no locator provides them.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class ProviderRecordDTO(V1Model):
    provider_name: str
    provider_record_id: str
    request_scope: dict[str, Any]
    retrieval_reference: str | None


class OriginalFileDTO(V1Model):
    """Inspectable provenance for a retained upload; never includes bytes."""

    file_name: str
    mime_type: str
    byte_size: int
    object_version: str
    uploaded_by: str
    retention_policy: str


class SourceContractDTO(V1Model):
    source_type: str
    provider_or_tenant: str
    permissions: dict[str, bool]
    status: Literal["admitted", "restricted"]
    region: str
    effective_from: datetime | None
    effective_until: datetime | None
    retention_policy: str
    deletion_policy: str
    downstream_restrictions: list[str]
    contract_version: str | None
    provider_record: ProviderRecordDTO | None = None


class DocumentSummaryDTO(V1Model):
    id: str
    content_sha256: str
    # A Case may retain audit metadata for a source whose terms forbid display.
    # Never expose its original location through a read response in that state.
    source_url: str | None
    published_at: str | None
    available_at: str
    acquired_at: str
    parser_version: str
    source_authority: str
    supersedes_id: str | None
    span_count: int
    statement_count: int
    parse_state: Literal["parsed", "partial", "failed", "unparsed"]
    supplements_document_version_id: str | None = None
    claimed_page_reference: str | None = None
    # Extraction watermark derived from AIRun audit records (defect-3 fix):
    # "extracted_empty" distinguishes a successful zero-output run from a
    # never-attempted version so batch extraction stops re-running it.
    extraction_state: Literal[
        "extracted", "extracted_empty", "failed", "not_attempted"
    ]
    last_extracted_at: str | None = None
    # Derived content-quality verdict (defect-4 fix): degenerate payloads
    # (4-char bodies, orphan table headers) stay in the ledger but are
    # flagged so the UI can badge/filter and extraction batches skip them.
    content_quality: Literal["ok", "degenerate", "unknown"] = "unknown"
    quality_reasons: list[str] = Field(default_factory=list)
    # Derived from span locator metadata when available (else None).
    title: str | None = None
    org: str | None = None
    doc_kind: str | None = None
    # Resolved from locator sec_code/stock_code; "寒武纪 (688256.SH)" when the
    # code matches a known Stock, the raw code otherwise, None when absent.
    entity: str | None = None
    source_contract: SourceContractDTO | None = None
    original_file: OriginalFileDTO | None = None


class SourceSpanDTO(V1Model):
    id: str
    document_version_id: str
    locator: dict[str, Any]
    verbatim_text: str
    citations: list[dict[str, Any]]
    # S4 / S5 of the Docling + locator-v1 spec:
    # ``locator_v1`` is the versioned v1 form when the span has been
    # upgraded (S4 backfill or a v1 write path); ``text_sha256`` is the
    # normalised verbatim hash used by the round-trip validator and by
    # callers that need to know whether a re-extracted span is
    # byte-identical to the stored one.  Both stay None for spans
    # written by legacy code paths.
    locator_v1: dict[str, Any] | None = None
    text_sha256: str | None = None


class DocumentListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    items: list[DocumentSummaryDTO]
    page: CursorPage


class DocumentDetailResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    document: DocumentSummaryDTO
    spans: list[SourceSpanDTO]
