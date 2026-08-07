"""Tests for pure event-evidence source admission rules."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services.source_admission import (
    SourceAdmission,
    SourceStatus,
    classify_source,
)


@pytest.mark.parametrize(
    ("source_url", "reason_fragment"),
    [
        ("https://example.test/source", "测试"),
        ("https://example.com/source", "测试"),
        (None, "URL"),
        ("ftp://publisher.example/report", "HTTP"),
    ],
)
def test_invalid_sources_are_rejected(source_url: str | None, reason_fragment: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False
    assert reason_fragment in result.reason


def test_valid_unverified_source_requires_manual_verification():
    result = classify_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="docling-v2",
        content_verified=False,
    )

    assert result.status is SourceStatus.PASTED_UNVERIFIED
    assert result.can_accept is False
    assert "验证" in result.reason


def test_user_pasted_parser_requires_manual_verification():
    result = classify_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="user-pasted-v1",
        content_verified=True,
    )

    assert result.status is SourceStatus.PASTED_UNVERIFIED
    assert result.can_accept is False
    assert "粘贴" in result.reason


def test_valid_verified_source_is_accessible():
    result = classify_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result == SourceAdmission(
        status=SourceStatus.ACCESSIBLE,
        reason="来源链接可访问且内容已验证。",
        can_accept=True,
    )


def test_source_admission_is_immutable():
    result = classify_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
    )

    with pytest.raises(FrozenInstanceError):
        result.can_accept = False
