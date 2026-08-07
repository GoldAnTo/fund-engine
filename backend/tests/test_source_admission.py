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


def test_user_pasted_parser_versions_require_manual_verification():
    result = classify_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="user-pasted-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.PASTED_UNVERIFIED
    assert result.can_accept is False


@pytest.mark.parametrize(
    "source_url",
    [
        "https://[bad",
        "https://www.cninfo.com.cn:not-a-port/report.pdf",
    ],
)
def test_malformed_http_urls_are_rejected_without_raising(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize(
    "source_url",
    [
        "https://localhost/report.pdf",
        "https://127.0.0.1/report.pdf",
        "https://192.168.1.1/report.pdf",
        "https://[::1]/report.pdf",
        "https://fixture/report.pdf",
    ],
)
def test_local_and_single_label_hosts_are_rejected(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


def test_abbreviated_loopback_ipv4_is_rejected():
    result = classify_source(
        source_url="https://127.1/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize(
    "source_url",
    [
        "https://0x7f.0.0.1/report.pdf",
        "https://127.0x0.0.1/report.pdf",
        "https://0x0a.0.0.1/report.pdf",
        "https://0177.0.0.1/report.pdf",
    ],
)
def test_legacy_non_decimal_private_ipv4_literals_are_rejected(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize(
    "source_url",
    [
        "https://bad host.example/report.pdf",
        "https://-leading-hyphen.example/report.pdf",
        "https://trailing-hyphen-.example/report.pdf",
    ],
)
def test_malformed_domain_hostnames_are_rejected(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


def test_valid_public_ipv6_source_is_accessible():
    result = classify_source(
        source_url="https://[2606:4700:4700::1111]/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.ACCESSIBLE
    assert result.can_accept is True


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
