"""Content-quality assessment for frozen document versions (defect-4 fix).

Walkthrough evidence: Gildata occasionally returns degenerate payloads —
a 4-character "相关研究" body, or an orphaned markdown table header
(``| % | 1个月 | …``) with no data rows.  These were frozen as first-class
DocumentVersions and diluted the document library, then wasted LLM
extraction calls.

This module is deliberately standalone: it derives a quality verdict from
span text at read/selection time, so no schema change is required and the
append-only ingest pipeline stays untouched.  Degenerate versions remain in
the ledger (nothing is hidden or deleted); they are flagged for the UI and
excluded from LLM extraction batches.
"""

from __future__ import annotations

import re
from typing import Literal

ContentQuality = Literal["ok", "degenerate", "unknown"]

# Below this many meaningful characters a document cannot carry an
# extractable statement (walkthrough's degenerate samples were 4-20 chars).
MIN_MEANINGFUL_CHARS = 50
# Below this ratio of meaningful characters the payload is mostly markup
# noise (orphan table separators, punctuation).
MIN_MEANINGFUL_RATIO = 0.3

_MEANINGFUL_RE = re.compile(r"[一-鿿0-9A-Za-z]")
# Markdown table separator rows: | --- | :---: | --- |
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _meaningful_chars(text: str) -> int:
    return len(_MEANINGFUL_RE.findall(text))


def assess_span_texts(texts: list[str]) -> tuple[ContentQuality, list[str]]:
    """Classify one document version from its span texts.

    Returns ``(quality, reasons)``:

    - ``unknown``: no span text at all (parse_state already covers this);
    - ``degenerate``: content present but structurally incapable of carrying
      evidence — reasons list the concrete triggers;
    - ``ok``: otherwise.
    """
    combined = "\n".join(t for t in texts if t).strip()
    if not combined:
        return "unknown", []

    reasons: list[str] = []
    meaningful = _meaningful_chars(combined)
    if meaningful < MIN_MEANINGFUL_CHARS:
        reasons.append(
            f"content_too_short: 有效字符 {meaningful} < {MIN_MEANINGFUL_CHARS}"
        )
    if meaningful / max(len(combined), 1) < MIN_MEANINGFUL_RATIO:
        reasons.append("low_information_density: 有效字符占比过低")

    lines = [ln for ln in combined.splitlines() if ln.strip()]
    if lines and all(
        _TABLE_ROW_RE.match(ln) or _TABLE_SEPARATOR_RE.match(ln) for ln in lines
    ):
        # Orphan table header (walkthrough sample `| % | 1个月 | …`): every
        # line is a table row/separator, but at most one non-separator row
        # exists — a real table has header + separator + >=1 data row, i.e.
        # at least two non-separator rows.
        non_separator = [
            ln for ln in lines if not _TABLE_SEPARATOR_RE.match(ln)
        ]
        if len(non_separator) <= 1:
            reasons.append("table_header_only: 仅剩表头/分隔行无数据行")

    if reasons:
        return "degenerate", reasons
    return "ok", []
