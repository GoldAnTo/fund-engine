"""Bounded process-local reuse of complete, deterministic default PDF parses.

This caches no authorization, persisted span, candidate, or gate result. Callers
must still compare every replayed locator/text/context against frozen evidence.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock

from app.datasources import docling
from app.datasources.docling import ParsedSpan, PypdfAdapter
from app.documents.locators import SourceLocatorV1


class PdfReplayCache:
    """LRU bounded by both entry count and serialized parse payload bytes."""

    def __init__(self, *, max_entries: int = 16, max_bytes: int = 32 * 1024 * 1024):
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("PDF replay cache limits must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple, bytes] = OrderedDict()
        self._payload_bytes = 0
        self._lock = Lock()

    def parse(
        self, parser: PypdfAdapter, raw: bytes, *, document_sha256: str
    ) -> list[ParsedSpan]:
        # The default adapter is stateless. Unknown instance configuration or
        # subclasses must not share a cache whose configuration we cannot name.
        if type(parser) is not PypdfAdapter or vars(parser):
            return parser.extract_spans(raw, document_sha256=document_sha256)
        key = (
            hashlib.sha256(raw).hexdigest(),  # Never trust the database's SHA.
            document_sha256,  # Also binds the locator's requested document ID.
            type(parser),
            type(parser).extract_spans,
            parser.parser_version,
            docling.PARSER_VERSION_PYPDF,
            docling.PYPDF_PACKAGE_VERSION,
            docling.PYPDF_CONFIG_VERSION,
        )
        with self._lock:
            payload = self._entries.get(key)
            if payload is not None:
                self._entries.move_to_end(key)
        if payload is not None:
            return self._decode(payload)

        # Parsing stays outside the lock. Concurrent cold misses may duplicate
        # work, but neither exceptions nor partly parsed documents are cached.
        parsed = parser.extract_spans(raw, document_sha256=document_sha256)
        payload = json.dumps(
            [
                {
                    "locator": span.locator.model_dump(mode="json"),
                    "verbatim_text": span.verbatim_text,
                    "text_sha256": span.text_sha256,
                    "context_hash": span.context_hash,
                }
                for span in parsed
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self._max_bytes:
            return parsed
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._payload_bytes -= len(previous)
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._payload_bytes + len(payload) > self._max_bytes
            ):
                _, removed = self._entries.popitem(last=False)
                self._payload_bytes -= len(removed)
            self._entries[key] = payload
            self._payload_bytes += len(payload)
        # The immutable serialized value, not these mutable locator objects,
        # owns the cache entry. Every subsequent caller receives a fresh decode.
        return parsed

    @staticmethod
    def _decode(payload: bytes) -> list[ParsedSpan]:
        return [
            ParsedSpan(
                locator=SourceLocatorV1.model_validate(row["locator"]),
                verbatim_text=row["verbatim_text"],
                text_sha256=row["text_sha256"],
                context_hash=row["context_hash"],
            )
            for row in json.loads(payload)
        ]


DEFAULT_PDF_REPLAY_CACHE = PdfReplayCache()
