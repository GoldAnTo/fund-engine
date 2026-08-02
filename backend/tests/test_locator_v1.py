"""Tests for the SourceLocatorV1 schema, helpers and round-trip helper.

Covers the spec's acceptance criteria:

- Required fields are enforced; page number alone is rejected.
- Localization channels (text_position / text_quote / bbox+parser_item_ref /
  table cell) are validated, and the three-of-one rule holds.
- ``coerce_locator_v1`` upgrades the legacy pypdf shape
  (``{page, paragraph, parser}``) without losing display keys.
- ``round_trip_check`` is conservative: no localization → always False;
  re-extract failure → False; SHA match → True.
- Edge cases: bbox zero-extent, text_position empty range, table_col
  without table_row, unknown parser prefix, missing required fields.
"""
from __future__ import annotations

import hashlib
from typing import Callable

import pytest

from app.documents.locators import (
    LOCATOR_SCHEMA_V1,
    LocatorBbox,
    LocatorInvalidError,
    SourceLocatorV1,
    TextPosition,
    TextQuote,
    coerce_locator_v1,
    compute_text_sha256,
    parser_family,
    round_trip_check,
    validate_locator_v1,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SHA = "a" * 64  # valid 64-char sha256


def _base(**overrides) -> dict:
    """A minimal valid v1 locator (text_position channel)."""
    out: dict = {
        "schema": "source-locator/v1",
        "document_sha256": _SHA,
        "page": 3,
        "parser_version": "docling-v2.115.0",
        "text_position": {"start": 120, "end": 245},
    }
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# SourceLocatorV1 — happy path
# ---------------------------------------------------------------------------


def test_minimal_locator_valid():
    loc = SourceLocatorV1.model_validate(_base())
    assert loc.schema == LOCATOR_SCHEMA_V1
    assert loc.page == 3
    assert loc.parser_version == "docling-v2.115.0"
    assert loc.text_position is not None
    assert loc.text_position.start == 120


def test_text_quote_channel_valid():
    loc = SourceLocatorV1.model_validate(
        _base(text_position=None, text_quote={"exact": "营业收入"})
    )
    assert loc.text_quote is not None
    assert loc.text_quote.exact == "营业收入"


def test_bbox_with_parser_item_ref_valid():
    loc = SourceLocatorV1.model_validate(
        _base(
            text_position=None,
            bbox={"l": 90, "t": 280, "r": 506, "b": 306},
            parser_item_ref="#/texts/57",
        )
    )
    assert loc.bbox is not None
    assert loc.parser_item_ref == "#/texts/57"


def test_table_cell_channel_valid():
    loc = SourceLocatorV1.model_validate(
        _base(text_position=None, table_row=4, table_col=2)
    )
    assert loc.table_row == 4
    assert loc.table_col == 2


def test_to_storage_dict_drops_none_fields():
    loc = SourceLocatorV1.model_validate(_base(table_row=4))
    d = loc.to_storage_dict()
    assert "table_col" not in d
    assert "bbox" not in d
    assert d["table_row"] == 4
    assert d["page"] == 3


def test_to_storage_dict_flattens_extra():
    loc = SourceLocatorV1.model_validate(
        _base(extra={"title": "Q3 2025 报告", "sec_name": "寒武纪"})
    )
    d = loc.to_storage_dict()
    assert d["title"] == "Q3 2025 报告"
    assert d["sec_name"] == "寒武纪"


# ---------------------------------------------------------------------------
# SourceLocatorV1 — rejection cases
# ---------------------------------------------------------------------------


def test_page_only_is_rejected():
    """Page number alone is not enough to round-trip — must have a localization channel."""
    with pytest.raises(Exception):  # pydantic ValidationError
        SourceLocatorV1.model_validate(
            {
                "schema": "source-locator/v1",
                "document_sha256": _SHA,
                "page": 1,
                "parser_version": "docling-v2.115.0",
            }
        )


def test_standalone_parser_item_ref_is_accepted():
    """A synthesized legacy ref (e.g. ``#/legacy-paragraph/4``) is enough on its own
    for the schema check — round-trip is not implied, the migration script marks
    these as ``extra.__upgraded='paragraph-only'`` so callers can decide."""
    loc = SourceLocatorV1.model_validate(
        {
            "schema": "source-locator/v1",
            "document_sha256": _SHA,
            "page": 3,
            "parser_version": "pypdf-v1",
            "parser_item_ref": "#/legacy-paragraph/4",
        }
    )
    assert loc.parser_item_ref == "#/legacy-paragraph/4"


def test_short_sha_rejected():
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(_base(document_sha256="abc"))


def test_invalid_schema_rejected():
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(_base(schema="source-locator/v0"))


def test_negative_page_rejected():
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(_base(page=0))


def test_bbox_zero_width_rejected():
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(
            _base(
                text_position=None,
                bbox={"l": 100, "t": 100, "r": 100, "b": 100},
                parser_item_ref="#/x",
            )
        )


def test_text_position_empty_range_rejected():
    with pytest.raises(Exception):
        TextPosition(start=10, end=10)


def test_text_position_inverted_range_rejected():
    with pytest.raises(Exception):
        TextPosition(start=20, end=10)


def test_table_col_without_table_row_rejected():
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(_base(text_position=None, table_col=2))


def test_unknown_extra_keys_rejected_by_model():
    """Pydantic forbids extras; the ``extra`` field is the only escape hatch."""
    with pytest.raises(Exception):
        SourceLocatorV1.model_validate(_base(bogus_field="oops"))


# ---------------------------------------------------------------------------
# validate_locator_v1 — public API
# ---------------------------------------------------------------------------


def test_validate_locator_v1_returns_model():
    loc = validate_locator_v1(_base())
    assert isinstance(loc, SourceLocatorV1)


def test_validate_locator_v1_rejects_none():
    with pytest.raises(LocatorInvalidError) as ei:
        validate_locator_v1(None)
    assert "None" in str(ei.value)


def test_validate_locator_v1_rejects_non_dict():
    with pytest.raises(LocatorInvalidError) as ei:
        validate_locator_v1(["not", "a", "dict"])
    assert "dict" in str(ei.value)


def test_validate_locator_v1_rejects_invalid_dict():
    with pytest.raises(LocatorInvalidError) as ei:
        validate_locator_v1({"page": 1})  # missing everything
    assert ei.value.payload == {"page": 1}


# ---------------------------------------------------------------------------
# coerce_locator_v1 — legacy pypdf shape upgrade
# ---------------------------------------------------------------------------


def test_coerce_legacy_pypdf_minimal():
    legacy = {"page": 3, "paragraph": 4, "parser": "pypdf-v1"}
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    assert out.page == 3
    assert out.parser_item_ref == "#/legacy-paragraph/4"
    assert out.extra["__upgraded"] == "paragraph-only"
    # display keys survive in extra
    assert out.extra["parser"] == "pypdf-v1"


def test_coerce_legacy_preserves_title_and_sec_name():
    legacy = {
        "page": 5,
        "paragraph": 2,
        "parser": "pypdf-v1",
        "title": "寒武纪:2024 业绩说明会",
        "sec_name": "寒武纪",
    }
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    d = out.to_storage_dict()
    assert d["title"] == "寒武纪:2024 业绩说明会"
    assert d["sec_name"] == "寒武纪"


def test_coerce_legacy_with_char_range():
    legacy = {"page": 3, "char_start": 100, "char_end": 220, "parser": "pypdf-v1"}
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    assert out.text_position is not None
    assert out.text_position.start == 100
    assert out.text_position.end == 220
    # round-trip-capable, so no synthesized parser_item_ref
    assert out.parser_item_ref is None
    assert "__upgraded" not in out.extra


def test_coerce_legacy_with_text_quote():
    legacy = {
        "page": 7,
        "parser": "docling-v1",
        "text_quote_exact": "2025 年营业收入 50 亿元",
        "text_quote_prefix": "公司",
        "text_quote_suffix": "同比",
    }
    out = coerce_locator_v1(
        legacy, document_sha256=_SHA, parser_version="docling-v2.115.0"
    )
    assert out.text_quote is not None
    assert out.text_quote.exact == "2025 年营业收入 50 亿元"
    assert out.text_quote.prefix == "公司"
    assert out.text_quote.suffix == "同比"
    assert out.parser_version == "docling-v2.115.0"  # caller wins


def test_coerce_legacy_uses_page_no_alias():
    legacy = {"page_no": 9, "parser": "pypdf-v1"}
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    assert out.page == 9


def test_coerce_legacy_with_bbox():
    legacy = {
        "page": 4,
        "parser": "docling-v1",
        "bbox": {"l": 90, "t": 280, "r": 506, "b": 306},
    }
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="docling-v2.115.0")
    assert out.bbox is not None
    assert out.bbox.l == 90
    assert out.bbox.origin == "top-left"


def test_coerce_legacy_with_table_cell():
    legacy = {"page": 8, "table_row": 3, "table_col": 1, "parser": "docling-v1"}
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="docling-v2.115.0")
    assert out.table_row == 3
    assert out.table_col == 1


def test_coerce_legacy_unrecoverable_raises():
    """A locator with **no page** and no localization channel cannot be
    upgraded — the migration script must skip it."""
    legacy = {"parser": "pypdf-v1"}  # no page, no paragraph, no nothing
    with pytest.raises(LocatorInvalidError) as ei:
        coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    assert "no recoverable localization" in str(ei.value)


def test_coerce_legacy_page_only_degrades_gracefully():
    """A page-only legacy locator (no paragraph) is upgraded to a degraded
    ``#/legacy-page/N`` ref.  This is best-effort, not round-trip-capable;
    callers should treat ``extra.__upgraded='page-only'`` as a signal to
    re-parse when possible.
    """
    legacy = {"page": 4, "parser": "pypdf-v1"}
    out = coerce_locator_v1(legacy, document_sha256=_SHA, parser_version="pypdf-v1")
    assert out.page == 4
    assert out.parser_item_ref == "#/legacy-page/4"
    assert out.extra["__upgraded"] == "page-only"


def test_coerce_legacy_rejects_non_dict():
    with pytest.raises(LocatorInvalidError):
        coerce_locator_v1("not a dict", document_sha256=_SHA, parser_version="pypdf-v1")


# ---------------------------------------------------------------------------
# compute_text_sha256
# ---------------------------------------------------------------------------


def test_compute_text_sha256_strips_trailing_whitespace():
    a = compute_text_sha256("hello world  \n  foo")
    b = compute_text_sha256("hello world\nfoo")
    assert a == b


def test_compute_text_sha256_collapses_internal_whitespace():
    a = compute_text_sha256("hello   world")
    b = compute_text_sha256("hello world")
    assert a == b


def test_compute_text_sha256_preserves_newlines():
    """Paragraph breaks are not collapsed — they pin span boundaries."""
    a = compute_text_sha256("para1\n\npara2")
    b = compute_text_sha256("para1 para2")
    assert a != b


def test_compute_text_sha256_drops_empty_lines():
    a = compute_text_sha256("a\n\n\nb")
    b = compute_text_sha256("a\nb")
    assert a == b


def test_compute_text_sha256_cjk_passthrough():
    """No Unicode normalisation: a CJK char under NFKC and NFC must hash equal
    iff their byte representation is equal."""
    h1 = compute_text_sha256("营业收入")
    h2 = compute_text_sha256("营业收入")
    assert h1 == h2
    assert len(h1) == 64
    assert h1 == hashlib.sha256("营业收入".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# round_trip_check
# ---------------------------------------------------------------------------


def _ok_re_extract(text: str) -> Callable[[SourceLocatorV1], str]:
    def _extract(loc: SourceLocatorV1) -> str:
        return text
    return _extract


def _mismatch_re_extract(text: str) -> Callable[[SourceLocatorV1], str]:
    def _extract(loc: SourceLocatorV1) -> str:
        return text + "DIFFERENT"
    return _extract


def test_round_trip_match():
    loc = SourceLocatorV1.model_validate(_base())
    text = "营业收入 50 亿元"
    assert round_trip_check(loc, text, re_extract=_ok_re_extract(text))


def test_round_trip_mismatch():
    loc = SourceLocatorV1.model_validate(_base())
    text = "营业收入 50 亿元"
    assert not round_trip_check(
        loc, text, re_extract=_mismatch_re_extract(text)
    )


def test_round_trip_text_normalization_is_stable():
    """verbatim_text with trailing whitespace normalises to the same hash as the
    stripped version.  This is the basis for tolerating soft-wrap re-extracts
    that only differ in trailing padding.
    """
    text_stripped = "营业收入 50 亿元"
    text_padded = "营业收入 50 亿元   \n   "
    assert compute_text_sha256(text_stripped) == compute_text_sha256(text_padded)


def test_round_trip_rejects_substring_match():
    """A re-extract that drops content is **not** a round-trip match — the
    SHA differs because line breaks are preserved across lines."""
    loc = SourceLocatorV1.model_validate(_base())
    text = "营业收入 50 亿元"
    # Substring of verbatim: hashes differ because the actual text changed.
    assert not round_trip_check(
        loc, text, re_extract=_ok_re_extract("营业收入")
    )


def test_round_trip_no_localization_returns_false():
    """A locator with only a table cell + page has no parsable re-extract channel."""
    loc = SourceLocatorV1.model_validate(
        _base(text_position=None, table_row=4, table_col=2)
    )
    assert not round_trip_check(loc, "x", re_extract=_ok_re_extract("x"))


def test_round_trip_re_extract_raises_returns_false():
    def _boom(_loc: SourceLocatorV1) -> str:
        raise RuntimeError("parser offline")

    loc = SourceLocatorV1.model_validate(_base())
    assert not round_trip_check(loc, "anything", re_extract=_boom)


# ---------------------------------------------------------------------------
# parser_family
# ---------------------------------------------------------------------------


def test_parser_family_docling():
    assert parser_family("docling-v2.115.0") == "docling"


def test_parser_family_pypdf():
    assert parser_family("pypdf-v1") == "pypdf"


def test_parser_family_fixture():
    assert parser_family("fixture-v1") == "fixture"


def test_parser_family_unknown():
    assert parser_family("totally-new-parser-0.1") == "unknown"
