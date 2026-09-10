"""Verified historical disclosures for the separate group financial worksheet.

This package is a frozen historical input, not a live source adapter, forecast,
or replacement for the company research evidence/valuation contracts.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from zlib import error as ZlibError

from pypdf import PdfReader

from app.models.ledger import ValidationError
from app.underwriting.hashing import canonical_hash

# This v1 recipe, directory, originals, definitions, proofs and hashes are frozen
# for saved-draft replay. Future versions require a new directory and loader;
# never replace these files or constants when changing the default version.
_SCHEMA_V1 = "company-research.financial-baseline.v1"
_DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "alphabet_financial_baseline"
_BASELINE_HASH = "bbf540d45bc0b99535f9ece3c948e8a1dc36adafe904e76ca020856fe4ea6295"
_PROOFS_HASH = "385925f0dc687158476dc3b595e3971143af88ff9a85f939b8b319d61ea9db0f"
_FILES = {
    "alphabet-2025-10k-ir": "official-10k.pdf",
    "alphabet-2025-q4-release": "official-q4.pdf",
    "alphabet-2026-q2-10q": "alphabet_q2_2026_10q.html.gz",
    "alphabet-2026-q2-release": "alphabet_q2_2026_exhibit_99_1.html.gz",
}
_ERROR = "Alphabet financial baseline verification failed"
_FACT_FIELDS = frozenset({
    "fact_key", "label", "value", "unit", "period_start", "period_end", "scope",
    "state", "source_ids", "source_locator", "quote", "input_fact_keys",
})


class _VisibleText(HTMLParser):
    """Stable visible-text extraction; hidden XBRL is not citation evidence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "ix:hidden"}:
            self.hidden.append(tag)
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _normalize(text: str) -> str:
    return " ".join(text.split())


@lru_cache(maxsize=4)
def _cached_source_texts(raw: bytes, filename: str) -> tuple[tuple[str, str], ...]:
    """Cache only immutable extraction results for exact, already-verified bytes."""
    if filename.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        return tuple(
            (str(index), _normalize(page.extract_text() or ""))
            for index, page in enumerate(reader.pages, start=1)
        )
    parser = _VisibleText()
    parser.feed(raw.decode("utf-8"))
    return (("document", _normalize("".join(parser.parts))),)


def _source_texts(raw: bytes, filename: str) -> dict[str, str]:
    return dict(_cached_source_texts(raw, filename))


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite value")
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _reported_proof(texts: dict, proof: dict) -> tuple[str, str]:
    text = texts[proof["page"]]
    anchor = proof["anchor"]
    start = text.index(anchor)
    tail = text[start:]
    if proof["period_header"] not in tail or proof["unit_header"] not in tail:
        raise ValueError("missing table context")
    # Only numbers immediately following the exact row label can establish a
    # value. Searching for an amount elsewhere in the filing is insufficient.
    matches = list(re.finditer(re.escape(proof["row_label"]) + r"\s+", tail))
    match = matches[proof["occurrence"]]
    number = r"\(?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*\)?"
    row = re.match(
        rf"(?:(?:\$\s*)?{number}\s*){{{proof['columns']}}}", tail[match.end():]
    )
    if row is None:
        raise ValueError("missing numeric row")
    cells = re.findall(number, row.group())
    if len(cells) != proof["columns"]:
        raise ValueError("incorrect table columns")
    cell = cells[proof["column"]].replace(" ", "").replace(",", "")
    amount = Decimal(cell.strip("()"))
    if cell.startswith("("):
        amount = -amount
    if proof["positive_magnitude"]:
        amount = abs(amount)
    quote = tail[:match.end() + row.end()].strip()
    if proof["period_header"] not in quote or proof["unit_header"] not in quote:
        raise ValueError("row precedes its declared table context")
    return _decimal_text(amount), quote


def _formula_value(formula: str, facts: dict) -> tuple[str, list[str]]:
    names = []

    def visit(node):
        if isinstance(node, ast.Name):
            if node.id not in names:
                names.append(node.id)
            return Decimal(facts[node.id]["value"])
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 1:
            return Decimal(1)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("unsupported financial formula")

    with localcontext() as context:
        context.prec = 28
        amount = visit(ast.parse(formula, mode="eval").body)
        return _decimal_text(amount), names


def _validate_facts(payload: dict, proofs: dict, texts: dict) -> None:
    facts = {}
    source_ids = {source["id"] for source in payload["sources"]}
    for fact in payload["facts"]:
        key = fact["fact_key"]
        expected = _FACT_FIELDS | ({"formula"} if fact["state"] == "derived" else set())
        if (
            set(fact) != expected or key in facts
            or fact["scope"] not in {"group", "google_cloud"}
            or fact["unit"] not in {"USD_million", "million_shares", "ratio"}
            or not fact["source_ids"] or not set(fact["source_ids"]) <= source_ids
            or not isinstance(fact["value"], str)
            or not Decimal(fact["value"]).is_finite()
            or datetime.fromisoformat(fact["period_start"]) > datetime.fromisoformat(fact["period_end"])
        ):
            raise ValueError("invalid financial fact")
        if fact["state"] == "reported":
            proof = proofs[key]
            amount, quote = _reported_proof(texts[proof["source_id"]], proof)
            if (
                fact["value"] != amount or fact["quote"] != quote
                or fact["input_fact_keys"]
                or fact["source_ids"] != [proof["source_id"]]
                or fact["source_locator"] != proof["locator"]
            ):
                raise ValueError("reported fact does not match its original")
        elif fact["state"] == "derived":
            amount, inputs = _formula_value(fact["formula"], facts)
            parents = [facts[name] for name in inputs]
            expected_sources = sorted({source for parent in parents for source in parent["source_ids"]})
            if (
                amount != fact["value"] or inputs != fact["input_fact_keys"]
                or any(parent["scope"] != fact["scope"] for parent in parents)
                or fact["source_ids"] != expected_sources
                or fact["quote"] != "\n\n".join(dict.fromkeys(parent["quote"] for parent in parents))
                or fact["source_locator"] != "; ".join(dict.fromkeys(parent["source_locator"] for parent in parents))
            ):
                raise ValueError("derived fact does not match its inputs")
        else:
            raise ValueError("unknown fact state")
        facts[key] = fact
    if set(proofs) != {key for key, fact in facts.items() if fact["state"] == "reported"}:
        raise ValueError("incomplete proof coverage")


def load_alphabet_financial_baseline_v1(cutoff_at: datetime) -> dict:
    """Read independent JSON after authenticating originals, definitions and math.

    No network, DB, LLM, or fixture mutation is involved. Every call verifies the
    package and raw bytes before consulting the bounded immutable text cache.
    Cutoffs before any packaged disclosure are rejected.
    """
    try:
        if not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
            raise ValueError("an aware cutoff is required")
        cutoff = cutoff_at.astimezone(UTC)
        payload = json.loads((_DATA_ROOT / "baseline.json").read_text(encoding="utf-8"))
        proofs = json.loads((_DATA_ROOT / "proofs.json").read_text(encoding="utf-8"))
        if (
            set(payload) != {"schema_version", "content_hash", "sources", "facts", "research_gaps"}
            or payload["schema_version"] != _SCHEMA_V1
            or payload["content_hash"] != _BASELINE_HASH
            or canonical_hash({key: value for key, value in payload.items() if key != "content_hash"}) != _BASELINE_HASH
            or canonical_hash(proofs) != _PROOFS_HASH
            or {source["id"] for source in payload["sources"]} != set(_FILES)
        ):
            raise ValueError("package content changed")
        texts = {}
        for source in payload["sources"]:
            if set(source) != {"id", "title", "url", "raw_content_hash", "available_at"}:
                raise ValueError("invalid source metadata")
            available = datetime.fromisoformat(source["available_at"])
            if available.tzinfo is None or available > cutoff:
                raise ValueError("source unavailable at the cutoff")
            filename = _FILES[source["id"]]
            raw = (_DATA_ROOT / "raw" / filename).read_bytes()
            if filename.endswith(".gz"):
                raw = gzip.decompress(raw)
            if hashlib.sha256(raw).hexdigest() != source["raw_content_hash"]:
                raise ValueError("source original changed")
            texts[source["id"]] = _source_texts(raw, filename)
        _validate_facts(payload, proofs, texts)
        return payload
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError, ArithmeticError, IndexError, SyntaxError):
        raise ValidationError(_ERROR) from None


def load_alphabet_financial_baseline(cutoff_at: datetime) -> dict:
    """Load the default baseline for a new worksheet, independently of replay."""
    return load_alphabet_financial_baseline_v1(cutoff_at)


def authenticate_financial_baseline_snapshot(baseline: dict, cutoff_at: datetime) -> dict:
    """Authenticate a saved version against its frozen recipe and original bytes.

    Dispatch commits to both the saved schema and its governed content hash. It
    must not follow the default loader when a later baseline becomes available.
    The result is independently loaded JSON, never the caller's mutable object.
    """
    try:
        if (
            not isinstance(baseline, dict)
            or baseline.get("schema_version") != _SCHEMA_V1
            or baseline.get("content_hash") != _BASELINE_HASH
            or canonical_hash({key: value for key, value in baseline.items() if key != "content_hash"}) != _BASELINE_HASH
        ):
            raise ValueError("unsupported or changed saved financial baseline")
        authenticated = load_alphabet_financial_baseline_v1(cutoff_at)
        if baseline != authenticated:
            raise ValueError("saved financial baseline differs from its frozen version")
        return authenticated
    except (TypeError, ValueError):
        raise ValidationError(_ERROR) from None
