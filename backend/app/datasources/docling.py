"""PDF parser adapters: PypdfAdapter (current) + DoclingAdapter (S3).

The workbench reads two flavours of PDF today:

- **Text-layer PDFs** (most modern annual reports, research notes, news
  attachments) — a fast pypdf pass over the text layer is enough to produce
  ``(page, paragraph)`` locators.  This is what ``PypdfAdapter`` does.
- **Layout-rich PDFs** (multi-column research reports, scanned statements,
  cross-page tables) — needs a layout-aware parser to give the workbench
  bbox / charspan / table-cell coordinates.  ``DoclingAdapter`` is the
  planned home for that; S3 will implement it against the real docling
  package once the dependency is approved.

Both adapters normalise their output to the same ``ParsedSpan`` shape so
the rest of the pipeline (ingest → repository → query layer) does not care
which parser produced a span.  See spec
``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md`` §3.3.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from typing import Iterable, Protocol

from pypdf import PdfReader

from app.documents.locators import (
    SourceLocatorV1,
    coerce_locator_v1,
    compute_text_sha256,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARSER_VERSION_PYPDF = "pypdf-v1"
PARSER_VERSION_DOCLING_STUB = "docling-stub-v1"

# Default parser version for callers that don't pick a family explicitly.
# Currently points at pypdf because that is the only fully-implemented
# adapter.  When S3 lands DoclingAdapter, flip this to the docling version
# string and update the legacy shim's PARSER_VERSION.
PARSER_VERSION = PARSER_VERSION_PYPDF

# 通用文本层抽取常量 (原 app/services/pdf_text.py 持有)
_CJK_END_RE = re.compile(r"[一-鿿，。、；：）％]$")
_CJK_START_RE = re.compile(r"^[一-鿿（]")
_TABLE_ROW_RE = re.compile(r"^\S+\s+[\d,]+(?:\.\d+)?(?:\s+[\d,]+(?:\.\d+)?)*$")
_YEAR_HEADER_RE = re.compile(r"^20\d{2}年(?:\s+20\d{2}年)*$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PdfParseError(Exception):
    """Raised when a PDF yields no extractable text layer."""


# ---------------------------------------------------------------------------
# ParsedSpan — common output shape for every parser adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedSpan:
    """One parsed span ready to be persisted as a ``SourceSpan``.

    Carries a v1 locator (so the write path can validate round-trip) plus
    the text hashes the round-trip checker needs.  Callers that still
    consume the legacy ``dict`` locator shape can use
    :meth:`legacy_locator_dict` for a single-step downgrade.
    """

    locator: SourceLocatorV1
    verbatim_text: str
    text_sha256: str
    context_hash: str

    def legacy_locator_dict(self) -> dict:
        """Render a legacy-compatible dict for callers that have not yet
        migrated to v1 (notably ``DocumentService.add_span`` until S4
        wires the v1 column through).
        """
        d = self.locator.to_storage_dict()
        d.setdefault("parser", self.locator.parser_version)
        return d


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class PdfParserAdapter(Protocol):
    """Pluggable PDF parser.

    Implementations must be stateless beyond configuration (``__init__``
    may take client / model handles but must not hold per-document state).
    The same adapter instance can be reused across many ``extract_spans``
    calls.
    """

    parser_version: str

    def extract_spans(
        self, raw: bytes, *, document_sha256: str
    ) -> list[ParsedSpan]: ...


# ---------------------------------------------------------------------------
# PypdfAdapter — current behaviour, v1 locators
# ---------------------------------------------------------------------------


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines: no space across CJK boundaries, space otherwise."""
    out = ""
    for line in lines:
        if not out:
            out = line
            continue
        if _CJK_END_RE.search(out[-1:]) and _CJK_START_RE.match(line):
            out += line
        else:
            out += " " + line
    return out


def _is_table_block(lines: list[str]) -> bool:
    """Detect a year-header / unit-line + 2+ numeric rows block."""
    rows = sum(1 for ln in lines if _TABLE_ROW_RE.match(ln))
    has_scaffold = any(
        _YEAR_HEADER_RE.match(ln) or ln.startswith("单位") for ln in lines
    )
    return rows >= 2 and has_scaffold


def _split_into_paragraphs(page_text: str) -> list[list[str]]:
    """Split a page's text layer into non-empty paragraph blocks.

    A paragraph block is a list of non-blank lines that should be joined
    together (soft-wrap joining is handled by the caller, not here).

    Two kinds of "blank line" count as paragraph breaks:

    - a fully empty line (a bare ``\\n`` after another ``\\n``);
    - a whitespace-only line, which is what reportlab / Word-style
      generators emit when they want vertical space between paragraphs
      without an explicit blank line in the text layer.

    Both are matched by ``\\n\\s*\\n`` so pypdf's output — which has no
    explicit paragraph delimiter — still groups paragraphs when the
    document uses whitespace-only spacers.
    """
    blocks: list[list[str]] = []
    for block in re.split(r"\n\s*\n", page_text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines:
            blocks.append(lines)
    return blocks


class PypdfAdapter:
    """Text-layer PDF parser using pypdf.

    Reproduces the historical behaviour of ``app.services.pdf_text`` while
    upgrading the locator to v1.  Locators remain ``page / paragraph`` —
    no bbox or charspan — so round-trip is not possible for individual
    spans; the migration script in S4 will mark these spans as
    ``__upgraded='paragraph-only'`` so the workbench can show a banner.
    """

    parser_version: str = PARSER_VERSION_PYPDF

    def __init__(self) -> None:
        # pypdf has no client / model state today; constructor reserved
        # for future configuration (e.g. password list, parser version
        # override).
        pass

    def extract_spans(
        self, raw: bytes, *, document_sha256: str
    ) -> list[ParsedSpan]:
        reader = PdfReader(io.BytesIO(raw))
        spans: list[ParsedSpan] = []
        page_paragraphs: list[list[str]] = []

        for page_no, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            page_paragraphs.append(_split_into_paragraphs(text))

        if not any(page_paragraphs):
            raise PdfParseError("PDF has no extractable text layer (scanned?)")

        for page_no, paragraphs in enumerate(page_paragraphs, start=1):
            for para_no, lines in enumerate(paragraphs, start=1):
                verbatim = (
                    "\n".join(lines)
                    if _is_table_block(lines)
                    else _join_lines(lines)
                )
                legacy = {
                    "page": page_no,
                    "paragraph": para_no,
                    "parser": self.parser_version,
                }
                locator = coerce_locator_v1(
                    legacy,
                    document_sha256=document_sha256,
                    parser_version=self.parser_version,
                )
                spans.append(
                    ParsedSpan(
                        locator=locator,
                        verbatim_text=verbatim,
                        text_sha256=compute_text_sha256(verbatim),
                        context_hash="",  # Filled by add_context_hashes below.
                    )
                )

        _add_context_hashes(spans)
        return spans


# ---------------------------------------------------------------------------
# DoclingAdapter — stub, S3 will fill in
# ---------------------------------------------------------------------------


class DoclingAdapter:
    """Stub adapter that defers the real Docling integration to S3.

    S3 is responsible for:

    1. Pinning ``docling>=2.115,<3.0`` in ``pyproject.toml`` (separate
       spec for that review — Docling models have their own licence
       review surface and we want explicit opt-in).
    2. Building a ``DoclingClient`` wrapper (lazy import to keep the
       dev environment fast when Docling is not installed).
    3. Mapping ``DoclingDocument`` pages / texts / tables to ParsedSpan,
       populating bbox / charspan / table cell from the provenance
       items.

    Until S3 lands, instantiating with the default constructor raises
    ``NotImplementedError`` so callers cannot accidentally pick the
    stub path.  Tests should patch or substitute via dependency
    injection (see ``tests/test_pdf_parser_adapters.py``).
    """

    parser_version: str = PARSER_VERSION_DOCLING_STUB

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "DoclingAdapter is a stub pending S3; "
            "use PypdfAdapter for now, or inject a fake Docling client."
        )

    def extract_spans(
        self, raw: bytes, *, document_sha256: str
    ) -> list[ParsedSpan]:
        raise NotImplementedError  # pragma: no cover — constructor guards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_spans(raw: bytes) -> list[tuple[dict, str]]:
    """Module-level helper: parse ``raw`` with the default adapter and
    return ``(legacy_locator_dict, verbatim_text)`` tuples.

    Kept for back-compat with the historical ``app.services.pdf_text``
    entry point and the three call sites that depend on it (seed script,
    test, verify script).  New code should instantiate
    :class:`PypdfAdapter` directly and consume :class:`ParsedSpan`.
    """
    digest = hashlib.sha256(raw).hexdigest()
    adapter = PypdfAdapter()
    spans = adapter.extract_spans(raw, document_sha256=digest)
    return [(s.legacy_locator_dict(), s.verbatim_text) for s in spans]


def _add_context_hashes(spans: list[ParsedSpan]) -> None:
    """Stabilise each span's ``context_hash`` in place.

    A context hash is ``sha256(page || prev_text || next_text)`` truncated
    to 32 hex chars.  Same page / same neighbours → same hash, so a
    re-extraction that yields the same paragraph boundaries (typical
    after a non-breaking parser upgrade) preserves context stability
    without re-running the rest of the pipeline.

    The function mutates the ``context_hash`` field on each span.  It's
    defined as a free function so both adapters can share it.
    """
    for idx, span in enumerate(spans):
        prev_text = spans[idx - 1].verbatim_text if idx > 0 else ""
        next_text = (
            spans[idx + 1].verbatim_text if idx + 1 < len(spans) else ""
        )
        page = span.locator.page
        h = hashlib.sha256()
        h.update(str(page).encode("utf-8"))
        h.update(b"\x00")
        h.update(prev_text.encode("utf-8"))
        h.update(b"\x00")
        h.update(next_text.encode("utf-8"))
        # ``slots=True`` dataclass: replace via object.__setattr__.
        object.__setattr__(span, "context_hash", h.hexdigest()[:32])


def iter_parser_versions(adapters: Iterable[PdfParserAdapter]) -> dict[str, str]:
    """Return a {family: parser_version} map for diagnostics / logging.

    ``family`` is the parser prefix (``pypdf`` / ``docling``).  If two
    adapters share a family, the last one wins; this is intentional —
    the caller is expected to register each family at most once.
    """
    from app.documents.locators import parser_family

    return {parser_family(a.parser_version): a.parser_version for a in adapters}
