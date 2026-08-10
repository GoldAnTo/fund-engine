"""Tests for pure event-evidence source admission rules."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.source_admission import (
    SourceAdmission,
    SourceStatus,
    apply_source_contract,
    classify_source,
)


@pytest.mark.parametrize(
    ("source_url", "reason_fragment"),
    [
        ("https://example.test/source", "测试"),
        ("https://example.com/source", "测试"),
        ("https://example.org/source", "测试"),
        ("https://example.net/source", "测试"),
        ("https://www.example.com/source", "测试"),
        ("https://evil.example.com/source", "测试"),
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
        "https://０x7f.0.0.1/report.pdf",
        "https://ｅxample.com/report.pdf",
        "https://www.ｔest/report.pdf",
    ],
)
def test_nfkc_equivalent_blocked_hosts_are_rejected(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


def test_valid_unicode_idn_source_is_accessible():
    result = classify_source(
        source_url="https://例子.公司.cn/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.ACCESSIBLE
    assert result.can_accept is True


@pytest.mark.parametrize(
    "source_url",
    [
        r"https://127.0.0.1\@www.cninfo.com.cn/report.pdf",
        "https://www.cninfo.com.cn/report.pdf\t",
    ],
)
def test_urls_with_browser_ambiguous_characters_are_rejected(source_url: str):
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


@pytest.mark.parametrize(
    "source_url",
    [
        "https://[::192.168.1.1]/report.pdf",
        "https://[::127.0.0.1]/report.pdf",
        "https://[::10.0.0.1]/report.pdf",
        "https://[::ffff:192.168.1.1]/report.pdf",
        "https://[::8.8.8.8]/report.pdf",
        "https://[::ffff:8.8.8.8]/report.pdf",
    ],
)
def test_ipv4_compatible_or_mapped_ipv6_addresses_are_rejected(source_url: str):
    result = classify_source(
        source_url=source_url,
        parser_version="docling-v2",
        content_verified=True,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


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


def test_expired_source_contract_cannot_enter_formal_evidence():
    admission = SourceAdmission(
        status=SourceStatus.ACCESSIBLE,
        reason="来源链接可访问且内容已验证。",
        can_accept=True,
    )
    contract = SimpleNamespace(
        allow_ai_processing=True,
        allow_display=True,
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )

    result = apply_source_contract(
        admission,
        contract,
        at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert result.status is SourceStatus.RESTRICTED
    assert result.can_accept is False
    assert "已失效" in result.reason
