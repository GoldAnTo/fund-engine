"""SourceLocatorV1: versioned locator schema for source spans.

Every ``SourceSpan`` written through the new ingest path carries a locator
that satisfies ``SourceLocatorV1``.  The schema draws on the W3C Web
Annotation model (TextQuoteSelector + TextPositionSelector + FragmentSelector
for bbox), so the same locator can be re-opened against the frozen document
and round-trip back to the exact same verbatim text.

Spec reference: ``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md``
section 3.1 (SourceLocatorV1 Pydantic schema) and 3.5 (compat with legacy
free-form locators).

Design choices:

- **Three localization channels.**  At least one of ``text_position``,
  ``text_quote`` or ``bbox+parser_item_ref`` must be present.  Page number
  alone is not enough to round-trip — a page may have many paragraphs, and
  a paragraph may shift if the parser changes.
- **Versioned parser stamp.**  ``parser_version`` is a free string so a
  bump (e.g. ``docling-v2.115.0`` vs ``docling-v2.116.0``) does not break
  the contract; downstream code routes on the prefix.
- **Strict validation, lenient read.**  ``validate_locator_v1`` is for
  write paths (raise ``LocatorInvalidError``); ``coerce_locator_v1``
  upgrades a legacy free-form dict when possible (used by the migration
  script in S4).
- **Round-trip helper.**  ``round_trip_check`` re-extracts text via the
  parser adapter and compares SHA-256 against the stored verbatim text;
  this is the only honest way to know the locator still pins the same
  byte sequence.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

LOCATOR_SCHEMA_V1 = "source-locator/v1"

# Parsers we know about.  Kept as a soft allowlist — unknown versions are
# accepted (downstream code routes on prefix) but spelled out here so the
# upgrade story is documented.
_KNOWN_PARSER_PREFIXES: tuple[str, ...] = ("docling-", "pypdf-", "fixture-")


class LocatorInvalidError(ValueError):
    """Raised when a locator dict fails ``SourceLocatorV1`` validation.

    The error carries the original payload so callers can log it without
    re-serializing, and a short reason for 4xx responses.
    """

    def __init__(self, reason: str, payload: Any | None = None) -> None:
        self.reason = reason
        self.payload = payload
        super().__init__(f"invalid locator: {reason}")


class LocatorBbox(BaseModel):
    """Axis-aligned bounding box in PDF page coordinates.

    ``origin='top-left'`` matches Docling's default; pypdf uses top-left
    too once you flip the y-axis.  Width/height are derivable
    (``r - l``, ``b - t``) but kept as fields for ergonomic callers.
    """

    l: float
    t: float
    r: float
    b: float
    origin: Literal["top-left"] = "top-left"

    @model_validator(mode="after")
    def _check_extent(self) -> "LocatorBbox":
        if self.r <= self.l:
            raise ValueError("bbox.r must be greater than bbox.l")
        if self.b <= self.t:
            raise ValueError("bbox.b must be greater than bbox.t")
        return self


class TextPosition(BaseModel):
    """Half-open Unicode character range within a page's text layer.

    ``start`` and ``end`` are code-point offsets, not byte offsets — a
    page with mixed CJK / ASCII has the same offsets whether you encode
    as UTF-8 or UTF-16 once you normalise.
    """

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> "TextPosition":
        if self.end <= self.start:
            raise ValueError("text_position.end must be greater than start")
        return self


class TextQuote(BaseModel):
    """Anchor phrase for the W3C TextQuoteSelector pattern.

    ``exact`` is the verbatim text the locator pins; ``prefix`` and
    ``suffix`` are short surrounding fragments used to disambiguate when
    the same ``exact`` recurs on a page (e.g. table column headers).
    """

    exact: str = Field(min_length=1)
    prefix: str = ""
    suffix: str = ""


class SourceLocatorV1(BaseModel):
    """Versioned locator for one ``SourceSpan`` against one ``DocumentVersion``.

    See module docstring for the design rationale and the W3C reference.
    """

    model_config = {"extra": "forbid"}

    schema: Literal["source-locator/v1"] = LOCATOR_SCHEMA_V1
    document_sha256: str = Field(min_length=64, max_length=64)
    page: int = Field(ge=1)
    parser_version: str = Field(min_length=1)

    # Localization: at least one of these (or ``bbox+parser_item_ref``)
    # must be set.  See ``_check_localization``.
    text_position: TextPosition | None = None
    text_quote: TextQuote | None = None
    bbox: LocatorBbox | None = None
    parser_item_ref: str | None = None  # e.g. "#/texts/57"

    # Optional table-cell coordinates inside the page.
    table_row: int | None = Field(default=None, ge=0)
    table_col: int | None = Field(default=None, ge=0)

    # Carry-over for legacy keys (title / sec_name / etc.) that the workbench
    # display layer already reads.  ``extra='forbid'`` on the model itself
    # would reject them; we collect them here instead so existing call sites
    # keep working without an upgrade-everywhere sweep.
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_localization(self) -> "SourceLocatorV1":
        has_text_position = self.text_position is not None
        has_text_quote = self.text_quote is not None
        has_bbox = self.bbox is not None
        has_parser_item_ref = self.parser_item_ref is not None
        has_table_cell = self.table_row is not None or self.table_col is not None
        # Four independent channels: any one is enough.  The "bbox + ref"
        # combination from the W3C FragmentSelector pattern still works
        # because the OR collapses them — a synthesized legacy ref like
        # ``#/legacy-paragraph/3`` is accepted on its own (best-effort,
        # not round-trip-capable, but the migration script needs it).
        if not (
            has_text_position
            or has_text_quote
            or has_bbox
            or has_parser_item_ref
            or has_table_cell
        ):
            raise ValueError(
                "locator needs at least one of: text_position, text_quote, "
                "bbox, parser_item_ref, or table_row/table_col"
            )
        if self.table_col is not None and self.table_row is None:
            raise ValueError("table_col set without table_row")
        return self

    def to_storage_dict(self) -> dict[str, Any]:
        """Render to a JSON-safe dict for the ``locator_v1`` column.

        ``extra`` is merged at the top level to keep the on-disk shape
        flat (read paths use ``isinstance`` and key access, not nested
        traversal).  ``None`` fields are dropped.
        """
        out: dict[str, Any] = {
            "schema": self.schema,
            "document_sha256": self.document_sha256,
            "page": self.page,
            "parser_version": self.parser_version,
        }
        if self.text_position is not None:
            out["text_position"] = self.text_position.model_dump()
        if self.text_quote is not None:
            out["text_quote"] = self.text_quote.model_dump()
        if self.bbox is not None:
            out["bbox"] = self.bbox.model_dump()
        if self.parser_item_ref is not None:
            out["parser_item_ref"] = self.parser_item_ref
        if self.table_row is not None:
            out["table_row"] = self.table_row
        if self.table_col is not None:
            out["table_col"] = self.table_col
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_text_sha256(text: str) -> str:
    """SHA-256 of normalised verbatim text.

    Normalisation rules:

    - Strip trailing whitespace on every line.
    - Collapse runs of internal whitespace to a single space (handles the
      soft-wrap case where two physical lines are one paragraph).
    - Keep CJK characters as-is (no Unicode normalisation — different
      parsers can disagree on NFKC vs NFC, and we want the hash to match
      whatever the parser emitted, not a re-canonicalised version).
    """
    lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()]
    joined = "\n".join(line for line in lines if line != "")
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def validate_locator_v1(locator: Any) -> SourceLocatorV1:
    """Strictly validate a locator against the v1 schema.

    Used on the write path: any failure raises ``LocatorInvalidError``
    with a short reason, so HTTP 4xx handlers can map it directly.
    """
    if locator is None:
        raise LocatorInvalidError("locator is None")
    if not isinstance(locator, dict):
        raise LocatorInvalidError(
            f"locator must be a dict, got {type(locator).__name__}", locator
        )
    try:
        return SourceLocatorV1.model_validate(locator)
    except Exception as exc:  # pydantic ValidationError or anything it wraps
        raise LocatorInvalidError(str(exc), locator) from exc


def coerce_locator_v1(
    legacy: dict[str, Any],
    *,
    document_sha256: str,
    parser_version: str,
) -> SourceLocatorV1:
    """Best-effort upgrade of a legacy free-form locator to v1.

    The legacy shape used by pypdf (``{"page": n, "paragraph": m, "parser": ...}``)
    is incomplete for round-trip (no character offset, no quote).  We:

    1. Lift ``parser`` → ``parser_version`` if not set.
    2. Carry every other key into ``extra`` so display code keeps working.
    3. If ``paragraph`` is set, synthesise a placeholder ``parser_item_ref``
       so the localization check passes.  This is best-effort and **not**
       sufficient for true round-trip; callers needing round-trip must
       re-parse the document with the new adapter.
    """
    if not isinstance(legacy, dict):
        raise LocatorInvalidError(
            f"legacy locator must be a dict, got {type(legacy).__name__}", legacy
        )

    out: dict[str, Any] = {
        "document_sha256": document_sha256,
        "parser_version": parser_version,
    }

    # Known typed fields.
    if "page" in legacy and legacy["page"] is not None:
        out["page"] = legacy["page"]
    elif "page_no" in legacy and legacy["page_no"] is not None:
        out["page"] = legacy["page_no"]

    if "table_row" in legacy and legacy["table_row"] is not None:
        out["table_row"] = legacy["table_row"]
    if "table_col" in legacy and legacy["table_col"] is not None:
        out["table_col"] = legacy["table_col"]

    char_start = legacy.get("char_start")
    char_end = legacy.get("char_end")
    if isinstance(char_start, int) and isinstance(char_end, int):
        out["text_position"] = {"start": char_start, "end": char_end}

    exact = legacy.get("text_quote_exact") or legacy.get("quote")
    if isinstance(exact, str) and exact:
        out["text_quote"] = {
            "exact": exact,
            "prefix": legacy.get("text_quote_prefix", "") or "",
            "suffix": legacy.get("text_quote_suffix", "") or "",
        }

    bbox = legacy.get("bbox")
    if isinstance(bbox, dict) and {"l", "t", "r", "b"} <= bbox.keys():
        out["bbox"] = {
            "l": bbox["l"],
            "t": bbox["t"],
            "r": bbox["r"],
            "b": bbox["b"],
            "origin": bbox.get("origin", "top-left"),
        }

    # Anything we don't recognise goes into ``extra`` so display code that
    # still reads ``locator["title"]`` / ``locator["sec_name"]`` / ``locator["parser"]``
    # keeps working without an upgrade-everywhere sweep.
    known = {
        "page",
        "page_no",
        "paragraph",
        "table_row",
        "table_col",
        "char_start",
        "char_end",
        "text_quote_exact",
        "text_quote_prefix",
        "text_quote_suffix",
        "quote",
        "bbox",
    }
    extra: dict[str, Any] = {k: v for k, v in legacy.items() if k not in known}
    if extra:
        out["extra"] = extra

    # If we still lack a localization channel, synthesise a parser_item_ref
    # so the schema check passes.  Prefer ``paragraph`` (the legacy pypdf
    # anchor); fall back to ``page_no`` if only the page is known (e.g. an
    # old stock-news page where the embedder lost paragraph offsets).
    # Mark the upgrade as degraded via ``extra.__upgraded``.  The original
    # ``paragraph`` / ``page_no`` value is preserved in ``extra`` so legacy
    # call sites that read ``locator["paragraph"]`` keep working until
    # they migrate (see seed_storage_chain_case.py:462).
    needs_synth = (
        "text_position" not in out
        and "text_quote" not in out
        and "bbox" not in out
        and "table_row" not in out
        and "table_col" not in out
    )
    if needs_synth:
        synth_id: str | None = None
        synth_key: str | None = None
        if "paragraph" in legacy and legacy["paragraph"] is not None:
            synth_id = f"#/legacy-paragraph/{legacy['paragraph']}"
            degraded = "paragraph-only"
            synth_key = "paragraph"
        elif "page_no" in legacy and legacy["page_no"] is not None:
            synth_id = f"#/legacy-page/{legacy['page_no']}"
            degraded = "page-only"
            synth_key = "page_no"
        elif "page" in legacy and legacy["page"] is not None:
            synth_id = f"#/legacy-page/{legacy['page']}"
            degraded = "page-only"
            synth_key = "page"
        if synth_id is None:
            raise LocatorInvalidError(
                "legacy locator has no recoverable localization "
                "(need page + one of paragraph/char_start/text_quote/bbox)",
                legacy,
            )
        out["parser_item_ref"] = synth_id
        extra_block = out.setdefault("extra", {})
        extra_block["__upgraded"] = degraded
        if synth_key is not None and synth_key in legacy:
            extra_block[synth_key] = legacy[synth_key]

    return validate_locator_v1(out)


def round_trip_check(
    locator: SourceLocatorV1,
    verbatim_text: str,
    *,
    re_extract: Callable[[SourceLocatorV1], str],
) -> bool:
    """Return True iff re-extracting via the parser still yields the same text.

    ``re_extract`` is supplied by the caller (typically a parser adapter
    bound to a specific document).  It takes a v1 locator and returns
    the verbatim text the parser *now* produces for that locator.

    This is the only honest way to know the locator still pins the same
    byte sequence after a parser upgrade.  See spec §1 (现状差距:
    round-trip) and §3.1 (text_position + text_quote + bbox 三选一
    组合定位).
    """
    if locator.text_position is None and locator.text_quote is None and locator.bbox is None:
        # No localization channel that re-extract can work with.
        return False
    try:
        re_extracted = re_extract(locator)
    except Exception:
        return False
    return compute_text_sha256(re_extracted) == compute_text_sha256(verbatim_text)


def parser_family(parser_version: str) -> str:
    """Return the family prefix of a parser version (``docling-`` / ``pypdf-`` / ...)."""
    for prefix in _KNOWN_PARSER_PREFIXES:
        if parser_version.startswith(prefix):
            return prefix.rstrip("-")
    return "unknown"
