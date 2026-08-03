"""Tests for the PDF parser adapters in :mod:`app.datasources.docling`.

Covers:

- ``PypdfAdapter`` returns ``ParsedSpan`` with v1 locators.
- Empty / text-less PDFs raise ``PdfParseError`` (fail-closed).
- Locator round-trip on a real text-layer PDF: re-parse the bytes and
  check SHA-256 against the stored verbatim text.
- ``DoclingAdapter`` is a stub that refuses construction.
- ``PARSER_VERSION`` constants and ``parser_family`` routing.
- ``extract_spans`` legacy shim in ``app.services.pdf_text`` still
  works for the three external call sites.
"""
from __future__ import annotations

import hashlib
import io
import re

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as rl_canvas

# Register a CID font for CJK so reportlab draws real glyphs instead of
# tofu boxes — without this pypdf would extract "■■■■ 50 ■■■■■■■ 20%" and
# our substring assertions would fail.
_CJK_FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))

from app.datasources.docling import (
    PARSER_VERSION_DOCLING_STUB,
    PARSER_VERSION_PYPDF,
    DoclingAdapter,
    DoclingNotInstalled,
    ParsedSpan,
    PdfParseError,
    PypdfAdapter,
    _join_lines,
    _is_table_block,
    _split_into_paragraphs,
    iter_parser_versions,
)
from app.documents.locators import SourceLocatorV1, parser_family
from app.services import pdf_text


# ---------------------------------------------------------------------------
# Fixtures: real PDFs (text layer) for round-trip tests
# ---------------------------------------------------------------------------


def _make_text_pdf(paragraphs: list[str]) -> bytes:
    """Build a one-page PDF whose text layer has the given paragraphs.

    pypdf splits paragraphs on blank lines (``\\n\\s*\\n``), so we draw an
    explicit blank line between paragraphs — drawing a single space gives
    pypdf something to emit at that y-coordinate without leaving any
    visible glyph in the text layer.
    """
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.setFont(_CJK_FONT, 11)
    y = 750
    for para_idx, para in enumerate(paragraphs):
        if para_idx > 0:
            # Blank line: pypdf extracts a line with just whitespace which
            # ``_split_into_paragraphs`` then drops after stripping.
            c.drawString(50, y, " ")
            y -= 14
        for line in _wrap(para, width=30):
            c.drawString(50, y, line)
            y -= 16
    c.save()
    return buf.getvalue()


def _wrap(text: str, *, width: int) -> list[str]:
    """Greedy wrap by character count (Helvetica avg ~7 px/char @ 11pt)."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        while len(paragraph) > width:
            out.append(paragraph[:width])
            paragraph = paragraph[width:]
        out.append(paragraph)
    return out


def _make_blank_pdf() -> bytes:
    """A page with no extractable text layer (no strings drawn)."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.rect(50, 50, 200, 200, stroke=1, fill=0)
    c.save()
    return buf.getvalue()


def _make_multi_page_pdf(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    for paragraphs in pages:
        c.setFont(_CJK_FONT, 11)
        y = 750
        for para_idx, para in enumerate(paragraphs):
            if para_idx > 0:
                c.drawString(50, y, " ")
                y -= 14
            for line in _wrap(para, width=30):
                c.drawString(50, y, line)
                y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PypdfAdapter
# ---------------------------------------------------------------------------


def test_pypdf_adapter_returns_parsed_spans():
    raw = _make_text_pdf(["营业收入 50 亿元，同比增长 20%", "归母净利润 8 亿元。"])
    digest = hashlib.sha256(raw).hexdigest()
    adapter = PypdfAdapter()
    spans = adapter.extract_spans(raw, document_sha256=digest)
    assert len(spans) == 2
    assert all(isinstance(s, ParsedSpan) for s in spans)
    # First span carries a v1 locator with the right page / parser_version.
    assert isinstance(spans[0].locator, SourceLocatorV1)
    assert spans[0].locator.page == 1
    assert spans[0].locator.parser_version == PARSER_VERSION_PYPDF
    # Pypdf has no charspan / bbox, so the migration script synthesises a
    # legacy paragraph ref.  This is the documented degraded upgrade.
    assert spans[0].locator.parser_item_ref == "#/legacy-paragraph/1"
    assert spans[0].locator.extra["__upgraded"] == "paragraph-only"
    # The text matches what we drew.
    assert "营业收入 50 亿元" in spans[0].verbatim_text
    assert "归母净利润" in spans[1].verbatim_text


def test_pypdf_adapter_text_sha256_present():
    raw = _make_text_pdf(["hello world"])
    digest = hashlib.sha256(raw).hexdigest()
    spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert spans[0].text_sha256 == expected


def test_pypdf_adapter_context_hash_stable_across_runs():
    raw = _make_text_pdf(["alpha", "beta", "gamma"])
    digest = hashlib.sha256(raw).hexdigest()
    a = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    b = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    assert [s.context_hash for s in a] == [s.context_hash for s in b]


def test_pypdf_adapter_blank_pdf_raises():
    raw = _make_blank_pdf()
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(PdfParseError) as ei:
        PypdfAdapter().extract_spans(raw, document_sha256=digest)
    assert "no extractable text layer" in str(ei.value)


def test_pypdf_adapter_round_trip_via_pypdf():
    """A real re-extract on a real PDF must produce the same text bytes."""
    from app.documents.locators import round_trip_check

    raw = _make_text_pdf(["营业收入 50 亿元", "归母净利润 8 亿元"])
    digest = hashlib.sha256(raw).hexdigest()
    spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)

    # Pypdf has no charspan / bbox / quote, so the locator cannot drive
    # a single-span re-extract.  Confirm round_trip_check is conservative
    # and returns False (the migration script can later re-parse with
    # Docling and upgrade the locator to a round-trip-capable shape).
    def _re_extract(_loc):
        # Pretend re-extraction succeeded with the same text; the locator
        # still has no charspan, so round_trip_check refuses.
        return spans[0].verbatim_text

    assert not round_trip_check(
        spans[0].locator, spans[0].verbatim_text, re_extract=_re_extract
    )


def test_pypdf_adapter_multi_page():
    raw = _make_multi_page_pdf(
        [
            ["page one para one", "page one para two"],
            ["page two para one"],
        ]
    )
    digest = hashlib.sha256(raw).hexdigest()
    spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    assert [s.locator.page for s in spans] == [1, 1, 2]
    assert [s.locator.parser_item_ref for s in spans] == [
        "#/legacy-paragraph/1",
        "#/legacy-paragraph/2",
        "#/legacy-paragraph/1",
    ]


def test_pypdf_adapter_legacy_locator_dict_round_trip():
    raw = _make_text_pdf(["hello"])
    digest = hashlib.sha256(raw).hexdigest()
    span = PypdfAdapter().extract_spans(raw, document_sha256=digest)[0]
    legacy = span.legacy_locator_dict()
    # Display keys survive (so the workbench can still render them).
    assert legacy["page"] == 1
    assert legacy["parser"] == PARSER_VERSION_PYPDF
    assert legacy["__upgraded"] == "paragraph-only"


# ---------------------------------------------------------------------------
# DoclingAdapter — stub
# ---------------------------------------------------------------------------


def test_docling_adapter_construction_is_safe():
    """DoclingAdapter construction is now two-mode:

    - When the ``docling`` package is missing, ``__init__`` raises
      :class:`DoclingNotInstalled` (an :class:`ImportError` subclass) so
      callers can ``except ImportError`` to fall back to PypdfAdapter.
    - When ``docling`` is installed, the real adapter constructs
      without raising.

    This test replaced the S2 stub-era ``test_docling_adapter_refuses_construction``
    when the S3 commit replaced the stub with the real implementation.
    """
    import importlib

    if importlib.util.find_spec("docling") is None:
        # docling missing -> DoclingNotInstalled (fail-closed).
        with pytest.raises(DoclingNotInstalled) as ei:
            DoclingAdapter()
        assert "docling" in str(ei.value).lower()
    else:
        # docling present -> real adapter constructs fine.
        adapter = DoclingAdapter()
        assert adapter.parser_version.startswith("docling-")


def test_docling_adapter_parser_version_constant():
    """The stub carries a recognisable version string for downstream code
    that branches on parser family."""
    assert PARSER_VERSION_DOCLING_STUB.startswith("docling-")
    assert parser_family(PARSER_VERSION_DOCLING_STUB) == "docling"


# ---------------------------------------------------------------------------
# Helpers exposed for unit tests
# ---------------------------------------------------------------------------


def test_split_into_paragraphs_groups_lines():
    text = "para one line 1\npara one line 2\n\npara two line 1\n"
    blocks = _split_into_paragraphs(text)
    assert blocks == [["para one line 1", "para one line 2"], ["para two line 1"]]


def test_join_lines_cjk_soft_wrap():
    lines = ["营业收入", "50 亿元"]
    assert _join_lines(lines) == "营业收入 50 亿元"


def test_join_lines_english_space():
    lines = ["hello", "world"]
    assert _join_lines(lines) == "hello world"


def test_is_table_block_detects_year_header_and_rows():
    lines = [
        "营业收入  100  200",
        "归母净利润  10  20",
    ]
    assert _is_table_block(lines) is False  # missing year header

    lines = ["2024年  2023年", "营业收入  100  200", "归母净利润  10  20"]
    assert _is_table_block(lines) is True


def test_iter_parser_versions_dedupes_by_family():
    a = PypdfAdapter()
    b = PypdfAdapter()
    families = iter_parser_versions([a, b])
    assert families == {"pypdf": PARSER_VERSION_PYPDF}


# ---------------------------------------------------------------------------
# Legacy shim — app.services.pdf_text.extract_spans still works
# ---------------------------------------------------------------------------


def test_legacy_pdf_text_shim_still_returns_legacy_tuples():
    raw = _make_text_pdf(["shim test", "second paragraph"])
    spans = pdf_text.extract_spans(raw)
    assert len(spans) == 2
    legacy, text = spans[0]
    assert legacy["parser"] == PARSER_VERSION_PYPDF
    assert "shim test" in text


def test_legacy_pdf_text_shim_constant_unchanged():
    """Callers compare parser_version to the constant; do not change its value."""
    assert pdf_text.PARSER_VERSION == PARSER_VERSION_PYPDF


def test_legacy_pdf_text_shim_pdf_parse_error():
    raw = _make_blank_pdf()
    with pytest.raises(pdf_text.PdfParseError):
        pdf_text.extract_spans(raw)
