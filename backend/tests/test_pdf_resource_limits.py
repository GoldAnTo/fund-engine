from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.datasources.docling import PypdfAdapter, PdfParseError
from app.services.document_uploads import DocumentUploadService
from app.errors import ValidationFailedError


def test_page_limit_stops_before_text_extraction(monkeypatch):
    page = SimpleNamespace(extract_text=MagicMock(return_value='evidence'))
    monkeypatch.setattr('app.datasources.docling.PdfReader', lambda _: SimpleNamespace(pages=[page, page]))
    with pytest.raises(PdfParseError, match='page limit'):
        PypdfAdapter(max_pages=1).extract_spans(b'pdf', document_sha256='a'*64)
    page.extract_text.assert_not_called()


def test_text_limit_stops_before_next_page(monkeypatch):
    first = SimpleNamespace(extract_text=MagicMock(return_value='long text'))
    second = SimpleNamespace(extract_text=MagicMock())
    monkeypatch.setattr('app.datasources.docling.PdfReader', lambda _: SimpleNamespace(pages=[first, second]))
    with pytest.raises(PdfParseError, match='text limit'):
        PypdfAdapter(max_text_chars=8).extract_spans(b'pdf', document_sha256='a'*64)
    second.extract_text.assert_not_called()


def test_span_limit_rejects_instead_of_returning_partial_evidence(monkeypatch):
    page = SimpleNamespace(extract_text=lambda: 'First paragraph\n\nSecond paragraph')
    monkeypatch.setattr('app.datasources.docling.PdfReader', lambda _: SimpleNamespace(pages=[page]))
    with pytest.raises(PdfParseError, match='span limit'):
        PypdfAdapter(max_spans=1).extract_spans(b'pdf', document_sha256='a'*64)


def test_upload_service_rejects_oversize_before_pdf_parser(monkeypatch):
    parser = MagicMock(side_effect=AssertionError('parser must not start'))
    monkeypatch.setattr('app.services.document_uploads.PypdfAdapter', parser)
    with pytest.raises(ValidationFailedError, match='20 MiB'):
        DocumentUploadService._parse(raw=b'x'*(20*1024*1024+1), mime_type='application/pdf', file_name='large.pdf')
    parser.assert_not_called()
