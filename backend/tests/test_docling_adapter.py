"""Tests for :class:`DoclingAdapter` in :mod:`app.datasources.docling`.

The real ``docling`` package is an *optional* dependency — CI may not
have it installed.  These tests follow two patterns:

1. **Always-run tests** — exercise the lazy-import / fail-closed path
   (``DoclingNotInstalled``), the bbox helper, the table serialiser,
   and the ``PARSER_VERSION_DOCLING`` constant.  No docling required.

2. **Optional tests** — exercise the full ``extract_spans`` flow against
   a mocked ``docling`` module injected via ``sys.modules``.  This
   proves the adapter correctly maps Docling's
   ``DocumentConverter`` / ``TextItem`` / ``TableItem`` shapes to our
   ``ParsedSpan`` / v1 locator contract without depending on the real
   package.  When ``docling`` is actually installed, a separate
   integration test (skipped here) would run the same flow against the
   real package.
"""
from __future__ import annotations

import io
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from app.datasources.docling import (
    PARSER_VERSION_DOCLING,
    DoclingAdapter,
    DoclingNotInstalled,
    LocatorBbox,
    ParsedSpan,
    PdfParseError,
    TextPosition,
)
from app.documents.locators import SourceLocatorV1, parser_family


# ---------------------------------------------------------------------------
# Always-run tests
# ---------------------------------------------------------------------------


def test_parser_version_is_docling_family():
    """The DoclingAdapter's parser_version must be classified as ``docling``.

    Downstream code (``iter_parser_versions``, ledger ``parser_version``
    column) routes on family; the wrong family would route Docling spans
    to the pypdf code path.
    """
    assert PARSER_VERSION_DOCLING.startswith("docling-")
    assert parser_family(PARSER_VERSION_DOCLING) == "docling"
    # Must NOT be the legacy stub sentinel — we are past S2.
    assert "stub" not in PARSER_VERSION_DOCLING


def test_docling_not_installed_is_import_error():
    """``DoclingNotInstalled`` must subclass ``ImportError``.

    Callers writing ``except ImportError`` to catch the "optional
    dependency missing" case must work uniformly.
    """
    assert issubclass(DoclingNotInstalled, ImportError)
    err = DoclingNotInstalled("docling is not installed; pip install 'docling>=2.115'")
    assert "docling" in str(err).lower()


def test_constructor_raises_when_docling_missing(monkeypatch):
    """Hiding ``docling`` from ``sys.modules`` forces the lazy import to fail.

    The error must be a :class:`DoclingNotInstalled` with a message
    that points the operator at the install command — the production
    fallback path logs this verbatim.
    """
    # Pop every docling module so the import inside ``__init__`` cannot
    # succeed via cache.  Then re-insert a ``None`` sentinel so that
    # ``import docling`` returns ``None`` and the dotted imports
    # (``docling.document_converter``) raise ``ImportError`` cleanly.
    hidden = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "docling" or name.startswith("docling.")
    }
    for name in list(hidden):
        monkeypatch.delitem(sys.modules, name)
    # Insert the sentinel AFTER delitem so it survives the monkeypatch
    # cleanup; monkeypatch.setitem would also work but setitem on a
    # ``None`` value is non-portable, and we want the test to be a clean
    # "docling is missing" simulation.
    sys.modules["docling"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(DoclingNotInstalled) as ei:
            DoclingAdapter()
        msg = str(ei.value).lower()
        assert "docling" in msg
        assert "pip install" in msg
    finally:
        # Restore so the rest of the suite sees a normal import env.
        sys.modules.pop("docling", None)
        for name, mod in hidden.items():
            sys.modules[name] = mod


# ---------------------------------------------------------------------------
# Bbox + table helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeBbox:
    l: float
    t: float
    r: float
    b: float


def test_bbox_from_converts_top_left():
    """Docling uses top-left origin; our ``LocatorBbox`` expects the same."""
    fake = _FakeBbox(l=10.0, t=20.0, r=110.0, b=40.0)
    out = DoclingAdapter._bbox_from(fake)
    assert isinstance(out, LocatorBbox)
    assert out.l == 10.0
    assert out.t == 20.0
    assert out.r == 110.0
    assert out.b == 40.0
    assert out.origin == "top-left"


def test_bbox_from_returns_none_on_missing_bbox():
    """A Docling item with no bbox should produce ``None``, not crash."""
    assert DoclingAdapter._bbox_from(None) is None


def test_bbox_from_returns_none_on_malformed_bbox():
    """A bbox with non-monotonic extents must NOT raise — the locator stays None."""
    bad = _FakeBbox(l=10.0, t=20.0, r=10.0, b=20.0)  # zero-area: r<=l, b<=t
    assert DoclingAdapter._bbox_from(bad) is None


@dataclass
class _FakeCell:
    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    text: str = ""


@dataclass
class _FakeTableData:
    num_rows: int
    num_cols: int
    table_cells: list[_FakeCell]


@dataclass
class _FakeTable:
    data: _FakeTableData


def test_table_to_text_serialises_row_by_row():
    """A 2x3 table should produce two lines, cells joined by spaces."""
    data = _FakeTableData(
        num_rows=2,
        num_cols=3,
        table_cells=[
            _FakeCell(0, 1, 0, 1, "营业收入"),
            _FakeCell(0, 1, 1, 2, "100"),
            _FakeCell(0, 1, 2, 3, "200"),
            _FakeCell(1, 2, 0, 1, "归母净利润"),
            _FakeCell(1, 2, 1, 2, "10"),
            _FakeCell(1, 2, 2, 3, "20"),
        ],
    )
    out = DoclingAdapter._table_to_text(_FakeTable(data=data))
    assert out == "营业收入 100 200\n归母净利润 10 20"


def test_table_to_text_handles_missing_cells():
    """Sparse tables (some cells empty) must still produce one line per row."""
    data = _FakeTableData(
        num_rows=2,
        num_cols=2,
        table_cells=[
            _FakeCell(0, 1, 0, 1, "label"),
            # (0,1,1) missing
            _FakeCell(1, 2, 0, 1, "label2"),
            _FakeCell(1, 2, 1, 2, "5"),
        ],
    )
    out = DoclingAdapter._table_to_text(_FakeTable(data=data))
    lines = out.split("\n")
    assert len(lines) == 2
    assert "label" in lines[0]
    assert "label2" in lines[1]
    assert "5" in lines[1]


def test_table_to_text_empty_data():
    """A table with no cells must produce empty text (so the span is skipped)."""
    data = _FakeTableData(num_rows=0, num_cols=0, table_cells=[])
    out = DoclingAdapter._table_to_text(_FakeTable(data=data))
    assert out == ""


# ---------------------------------------------------------------------------
# extract_spans — mocked docling pipeline
# ---------------------------------------------------------------------------


def _install_fake_docling(monkeypatch, *, texts, tables, raise_on_convert=False):
    """Inject fake ``docling.*`` modules into ``sys.modules``.

    The fake ``DocumentConverter.convert`` returns a fake document
    whose ``.texts`` and ``.tables`` are the supplied lists, and whose
    items expose the attributes our adapter reads (``text``, ``prov``,
    ``self_ref``).
    """

    @dataclass
    class _Prov:
        page_no: int
        bbox: _FakeBbox | None
        charspan: tuple[int, int] | None

    # Wrap plain dicts into objects with the attributes Docling exposes.
    def _wrap(item):
        if item is None:
            return None
        if isinstance(item, dict):
            prov = None
            if item.get("prov"):
                prov = _Prov(
                    page_no=item["prov"].get("page_no", 1),
                    bbox=item["prov"].get("bbox"),
                    charspan=item["prov"].get("charspan"),
                )
            return types.SimpleNamespace(
                text=item.get("text", ""),
                prov=[prov] if prov else [],
                self_ref=item.get("self_ref"),
            )
        return item

    @dataclass
    class _FakeDoc:
        texts: list
        tables: list

    class _FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, _stream):
            if raise_on_convert:
                raise RuntimeError("simulated docling failure")
            return types.SimpleNamespace(
                document=_FakeDoc(
                    texts=[_wrap(t) for t in texts],
                    tables=[_wrap(t) for t in tables],
                )
            )

    class _FakePdfFormatOption:
        def __init__(self, pipeline_options=None):
            self.pipeline_options = pipeline_options

    class _FakeInputFormat:
        PDF = "pdf"

    class _FakePdfPipelineOptions:
        def __init__(self):
            self.do_ocr = False
            self.do_table_structure = True

    # Build the fake package tree.
    docling_pkg = types.ModuleType("docling")
    docling_pkg.__path__ = []  # mark as a package

    converter_mod = types.ModuleType("docling.document_converter")
    converter_mod.DocumentConverter = _FakeConverter
    converter_mod.PdfFormatOption = _FakePdfFormatOption

    base_mod = types.ModuleType("docling.datamodel.base_models")
    base_mod.InputFormat = _FakeInputFormat

    pipeline_mod = types.ModuleType("docling.datamodel.pipeline_options")
    pipeline_mod.PdfPipelineOptions = _FakePdfPipelineOptions

    monkeypatch.setitem(sys.modules, "docling", docling_pkg)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel", types.ModuleType("docling.datamodel"))
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", pipeline_mod)
    # ``datamodel`` must look like a package for the dotted import.
    sys.modules["docling.datamodel"].__path__ = []  # type: ignore[attr-defined]


def test_extract_spans_maps_text_items_to_v1_locators(monkeypatch):
    """A Docling text item becomes one ParsedSpan with a v1 locator."""
    _install_fake_docling(
        monkeypatch,
        texts=[
            {
                "text": "营业收入 50 亿元",
                "prov": {
                    "page_no": 1,
                    "bbox": _FakeBbox(l=10, t=20, r=200, b=40),
                    "charspan": (0, 8),
                },
                "self_ref": "#/texts/0",
            },
        ],
        tables=[],
    )
    adapter = DoclingAdapter()
    raw = b"%PDF-fake"
    digest = "a" * 64
    spans = adapter.extract_spans(raw, document_sha256=digest)
    assert len(spans) == 1
    span = spans[0]
    assert isinstance(span, ParsedSpan)
    assert isinstance(span.locator, SourceLocatorV1)
    assert span.locator.document_sha256 == digest
    assert span.locator.page == 1
    assert span.locator.bbox is not None
    assert span.locator.text_position is not None
    assert span.locator.text_position == TextPosition(start=0, end=8)
    assert span.locator.text_quote is not None
    assert span.locator.text_quote.exact == "营业收入 50 亿元"
    assert span.locator.parser_item_ref == "#/texts/0"
    assert span.locator.parser_version == PARSER_VERSION_DOCLING
    assert span.verbatim_text == "营业收入 50 亿元"


def test_extract_spans_skips_text_without_provenance(monkeypatch):
    """Docling sometimes returns text items without prov — skip, do not synthesise."""
    _install_fake_docling(
        monkeypatch,
        texts=[
            {"text": "no prov here", "prov": None, "self_ref": None},
            {
                "text": "with prov",
                "prov": {
                    "page_no": 2,
                    "bbox": _FakeBbox(l=0, t=0, r=100, b=20),
                    "charspan": (0, 9),
                },
                "self_ref": "#/texts/1",
            },
        ],
        tables=[],
    )
    adapter = DoclingAdapter()
    spans = adapter.extract_spans(b"%PDF-fake", document_sha256="b" * 64)
    assert len(spans) == 1
    assert spans[0].verbatim_text == "with prov"


def test_extract_spans_emits_table_spans_with_row_preserved_text(monkeypatch):
    """Table items become spans whose verbatim text is row-by-row join."""
    _install_fake_docling(
        monkeypatch,
        texts=[],
        tables=[
            {
                "text": "",
                "prov": {
                    "page_no": 3,
                    "bbox": _FakeBbox(l=50, t=100, r=550, b=200),
                    "charspan": None,
                },
                "self_ref": "#/tables/0",
                "_data": _FakeTableData(
                    num_rows=2,
                    num_cols=2,
                    table_cells=[
                        _FakeCell(0, 1, 0, 1, "营业收入"),
                        _FakeCell(0, 1, 1, 2, "100"),
                        _FakeCell(1, 2, 0, 1, "归母净利润"),
                        _FakeCell(1, 2, 1, 2, "10"),
                    ],
                ),
            },
        ],
    )
    # The fake wraps dicts into SimpleNamespace; we need ``data`` to be
    # the table data, so the table item dict above needs a special hook.
    # The current fake inlines ``_data`` but doesn't expose ``data`` —
    # adjust: drop a handcrafted object.
    sys.modules["docling"]._patch_table = True  # type: ignore[attr-defined]
    # Replace the last table with a proper object that has ``data``.
    adapter = DoclingAdapter()
    # Reach into the fake's document via a re-run to set data correctly.
    # Simpler: drop the table and rebuild it via a custom converter.
    monkeypatch.setattr(
        adapter._converter,  # type: ignore[attr-defined]
        "convert",
        lambda _stream: types.SimpleNamespace(
            document=types.SimpleNamespace(
                texts=[],
                tables=[
                    types.SimpleNamespace(
                        data=_FakeTableData(
                            num_rows=2,
                            num_cols=2,
                            table_cells=[
                                _FakeCell(0, 1, 0, 1, "营业收入"),
                                _FakeCell(0, 1, 1, 2, "100"),
                                _FakeCell(1, 2, 0, 1, "归母净利润"),
                                _FakeCell(1, 2, 1, 2, "10"),
                            ],
                        ),
                        prov=[
                            types.SimpleNamespace(
                                page_no=3,
                                bbox=_FakeBbox(l=50, t=100, r=550, b=200),
                                charspan=None,
                            )
                        ],
                        self_ref="#/tables/0",
                    )
                ],
            )
        ),
    )
    spans = adapter.extract_spans(b"%PDF-fake", document_sha256="c" * 64)
    assert len(spans) == 1
    assert spans[0].verbatim_text == "营业收入 100\n归母净利润 10"
    assert spans[0].locator.page == 3
    assert spans[0].locator.bbox is not None
    assert spans[0].locator.parser_item_ref == "#/tables/0"


def test_extract_spans_raises_on_empty_document(monkeypatch):
    """A PDF that yields no text and no tables must fail-closed with PdfParseError."""
    _install_fake_docling(monkeypatch, texts=[], tables=[])
    adapter = DoclingAdapter()
    with pytest.raises(PdfParseError):
        adapter.extract_spans(b"%PDF-fake", document_sha256="d" * 64)


def test_extract_spans_populates_context_hash(monkeypatch):
    """After parsing, each span's ``context_hash`` must be filled in (non-empty)."""
    _install_fake_docling(
        monkeypatch,
        texts=[
            {
                "text": "alpha",
                "prov": {
                    "page_no": 1,
                    "bbox": _FakeBbox(l=0, t=0, r=10, b=10),
                    "charspan": (0, 5),
                },
                "self_ref": "#/texts/0",
            },
            {
                "text": "beta",
                "prov": {
                    "page_no": 1,
                    "bbox": _FakeBbox(l=0, t=20, r=10, b=30),
                    "charspan": (0, 4),
                },
                "self_ref": "#/texts/1",
            },
        ],
        tables=[],
    )
    adapter = DoclingAdapter()
    spans = adapter.extract_spans(b"%PDF-fake", document_sha256="e" * 64)
    assert len(spans) == 2
    # Both context hashes are non-empty and stable (the first is well-defined;
    # the second mixes the previous span's text).
    assert spans[0].context_hash != ""
    assert spans[1].context_hash != ""
    # Same input → same hash (determinism).
    spans2 = adapter.extract_spans(b"%PDF-fake", document_sha256="e" * 64)
    assert [s.context_hash for s in spans] == [s.context_hash for s in spans2]


def test_parser_family_routes_docling_correctly():
    """Sanity check: ``parser_family`` on the Docling version returns ``docling``."""
    assert parser_family(PARSER_VERSION_DOCLING) == "docling"
