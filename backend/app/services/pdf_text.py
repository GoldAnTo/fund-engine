"""PDF text-layer extraction into SourceSpan-shaped paragraphs.

The evidence ledger freezes source documents as content-addressed bytes; this
module is the parser for real PDF material (the ``[PAGE n][PARA m]`` marker
convention only exists in hand-authored fixtures).  It extracts the embedded
text layer page by page and splits each page into paragraphs on blank lines,
joining soft-wrapped CJK lines (pypdf breaks mid-word at the draw boundary).

Locators are reproducible: ``{"page": n, "paragraph": m, "parser": "pypdf"}``
where ``paragraph`` is the 1-based index of the non-empty paragraph on that
page.  Scanned PDFs without a text layer yield no spans — the caller must
treat that as a parse failure, never as an empty document (fail-closed).

The parser version stamped on DocumentVersions is ``pypdf-v1`` so engine
re-runs can distinguish parser generations.
"""
from __future__ import annotations

import re

from pypdf import PdfReader

PARSER_VERSION = "pypdf-v1"

_CJK_END_RE = re.compile(r"[一-鿿，。、；：）％]$")
_CJK_START_RE = re.compile(r"^[一-鿿（]")

# Table-block detection: a year header line ("2025年 2024年") or a units line
# ("单位：亿元") plus at least two "label + numbers" rows.  Inside table blocks
# line structure is preserved verbatim — the rule-based FinancialTableExtractor
# depends on the row layout.
_TABLE_ROW_RE = re.compile(r"^\S+\s+[\d,]+(?:\.\d+)?(?:\s+[\d,]+(?:\.\d+)?)*$")
_YEAR_HEADER_RE = re.compile(r"^20\d{2}年(?:\s+20\d{2}年)*$")


def _is_table_block(lines: list[str]) -> bool:
    rows = sum(1 for ln in lines if _TABLE_ROW_RE.match(ln))
    has_scaffold = any(
        _YEAR_HEADER_RE.match(ln) or ln.startswith("单位") for ln in lines
    )
    return rows >= 2 and has_scaffold


class PdfParseError(Exception):
    """Raised when a PDF yields no extractable text layer."""


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


def extract_spans(raw: bytes) -> list[tuple[dict, str]]:
    """Return ``(locator, verbatim_text)`` paragraphs for a PDF byte string.

    Raises :class:`PdfParseError` when no text layer is extractable.
    """
    import io

    reader = PdfReader(io.BytesIO(raw))
    spans: list[tuple[dict, str]] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        para_no = 0
        for block in re.split(r"\n\s*\n", text):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if not lines:
                continue
            para_no += 1
            locator = {
                "page": page_no,
                "paragraph": para_no,
                "parser": PARSER_VERSION,
            }
            verbatim = (
                "\n".join(lines)
                if _is_table_block(lines)
                else _join_lines(lines)
            )
            spans.append((locator, verbatim))
    if not spans:
        raise PdfParseError("PDF has no extractable text layer (scanned?)")
    return spans
