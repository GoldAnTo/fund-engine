"""Re-export shim — the PDF parser moved to :mod:`app.datasources.docling`.

Three call sites still import from here:

- :mod:`app.scripts.seed_storage_chain_case`
- :mod:`tests.test_pdf_and_storage_seed`
- :mod:`scripts.verify_ai_compute_slice`

This module preserves their public surface (``PARSER_VERSION``,
``PdfParseError``, ``extract_spans``) so the migration is a no-op for
them.  New code should import from :mod:`app.datasources.docling`
directly.

See spec
``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md`` §3.3
(Docling adapter) and §4 (compat re-export).
"""
from __future__ import annotations

from app.datasources.docling import (  # noqa: F401 — re-export
    PARSER_VERSION,
    PARSER_VERSION_DOCLING_STUB,
    PARSER_VERSION_PYPDF,
    ParsedSpan,
    PdfParseError,
    PypdfAdapter,
    extract_spans,
)
