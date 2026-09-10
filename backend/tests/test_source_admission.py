"""Tests for pure event-evidence source admission rules."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.services import source_admission
from app.services.source_admission import (
    SourceAdmission,
    SourceStatus,
    apply_source_contract,
    classify_source,
)


def _licensed_gildata_contract(
    *,
    ai: object = True,
    display: object = True,
    source_type: object = "licensed_provider",
    research_source_type: object = "licensed_provider",
    provider: object = "gildata",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        source_type=source_type,
        research_source_type=research_source_type,
        provider_or_tenant=provider,
        allow_ai_processing=ai,
        allow_display=display,
        effective_from=effective_from,
        effective_until=effective_until,
    )


def _classify_document_source(**kwargs) -> SourceAdmission:
    assert hasattr(source_admission, "classify_document_source"), (
        "document-aware source classifier is not implemented"
    )
    return source_admission.classify_document_source(**kwargs)


def _document_source_can_display(**kwargs) -> bool:
    assert hasattr(source_admission, "document_source_can_display"), (
        "document-aware display policy is not implemented"
    )
    return source_admission.document_source_can_display(**kwargs)


@pytest.mark.parametrize(
    "source_kind",
    ["research-report", "announcement", "news", "macro-industry"],
)
def test_authorised_gildata_document_kinds_are_admissible(source_kind: str) -> None:
    result = _classify_document_source(
        source_url=f"gildata://{source_kind}/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(provider="GILDATA"),
    )

    assert result.status is SourceStatus.ACCESSIBLE
    assert result.can_accept is True


@pytest.mark.parametrize(
    "source_url",
    [
        "gildata://wrong-kind/" + "a" * 64,
        "gildata://user@research-report/" + "a" * 64,
        "gildata://research-report:443/" + "a" * 64,
        "gildata://research-report/" + "a" * 64 + "?scope=all",
        "gildata://research-report/" + "a" * 64 + "?",
        "gildata://research-report/" + "a" * 64 + "#section",
        "gildata://research-report/" + "a" * 64 + "#",
        "gildata://research-report/" + "a" * 64 + "/extra",
        "gildata://research-report/" + "a" * 63,
        "gildata://research-report/" + "a" * 65,
        "gildata://research-report/" + "A" * 64,
        "gildata://research-report/" + "g" * 64,
        "gildata://research-report/%61" + "a" * 62,
        "GILDATA://research-report/" + "a" * 64,
        "gildata://Research-Report/" + "a" * 64,
    ],
)
def test_noncanonical_gildata_references_remain_invalid(source_url: str) -> None:
    result = _classify_document_source(
        source_url=source_url,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(),
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize("parser_version", [None, "html-v1", "GILDATA-MCP-1"])
def test_gildata_document_requires_exact_parser_prefix(
    parser_version: str | None,
) -> None:
    result = _classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version=parser_version,
        content_verified=True,
        contract=_licensed_gildata_contract(),
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize("content_verified", [False, 1])
def test_gildata_document_requires_literal_verified_true(
    content_verified: object,
) -> None:
    result = _classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=content_verified,
        contract=_licensed_gildata_contract(),
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize(
    "contract",
    [
        None,
        _licensed_gildata_contract(source_type="company_disclosure"),
        _licensed_gildata_contract(research_source_type="company_disclosure"),
        _licensed_gildata_contract(provider="other-provider"),
        _licensed_gildata_contract(provider=" gildata"),
        _licensed_gildata_contract(provider=123),
    ],
)
def test_gildata_document_requires_exact_contract_identity(contract: object) -> None:
    result = _classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=contract,
    )

    assert result.status is SourceStatus.INVALID
    assert result.can_accept is False


@pytest.mark.parametrize(
    ("ai", "display"),
    [(False, True), (True, False), (False, False), (1, True), (True, 1)],
)
def test_gildata_document_requires_both_literal_contract_rights(
    ai: object, display: object
) -> None:
    result = _classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=ai, display=display),
    )

    assert result.status is SourceStatus.RESTRICTED
    assert result.can_accept is False


def test_gildata_contract_effective_boundaries_are_inclusive() -> None:
    starts_at = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
    expires_at = starts_at + timedelta(hours=1)
    contract = _licensed_gildata_contract(
        effective_from=starts_at,
        effective_until=expires_at,
    )

    for instant in (starts_at, expires_at):
        result = _classify_document_source(
            source_url="gildata://research-report/" + "a" * 64,
            parser_version="gildata-mcp-1",
            content_verified=True,
            contract=contract,
            at=instant,
        )
        assert result.status is SourceStatus.ACCESSIBLE
        assert result.can_accept is True


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 9, 3, 9, 59, 59, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 11, 0, 1, tzinfo=timezone.utc),
    ],
)
def test_gildata_contract_outside_effective_window_is_restricted(
    instant: datetime,
) -> None:
    result = _classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(
            effective_from=datetime(2026, 9, 3, 10, tzinfo=timezone.utc),
            effective_until=datetime(2026, 9, 3, 11, tzinfo=timezone.utc),
        ),
        at=instant,
    )

    assert result.status is SourceStatus.RESTRICTED
    assert result.can_accept is False


def test_document_classifier_preserves_generic_http_admission() -> None:
    result = _classify_document_source(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="docling-v2",
        content_verified=True,
        contract=None,
    )

    assert result == SourceAdmission(
        status=SourceStatus.ACCESSIBLE,
        reason="来源链接可访问且内容已验证。",
        can_accept=True,
    )


def test_gildata_display_permission_does_not_require_ai_processing() -> None:
    assert _document_source_can_display(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=False, display=True),
    )


@pytest.mark.parametrize(
    "source_url",
    [
        "gil\ndata://research-report/" + "b" * 64,
        "gild\tata://research-report/" + "b" * 64,
        "gildata\r://research-report/" + "b" * 64,
        "\x00 gil\ndata://research-report/" + "b" * 64 + " \x1f",
    ],
    ids=["embedded-lf", "embedded-tab", "embedded-cr", "c0-trim-and-lf"],
)
def test_gildata_display_detection_uses_whatwg_scheme_normalization(
    source_url: str,
) -> None:
    assert not _document_source_can_display(
        source_url=source_url,
        parser_version="html-v1",
        content_verified=True,
        contract=None,
    )


def test_gildata_parser_is_an_additional_fail_closed_display_signal() -> None:
    assert not _document_source_can_display(
        source_url="opaque-source:research-report/" + "b" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=None,
    )


@pytest.mark.parametrize(
    ("source_url", "parser_version", "content_verified", "contract"),
    [
        (
            "gildata://research-report/" + "a" * 64,
            "gildata-mcp-1",
            True,
            None,
        ),
        (
            "gildata://research-report/" + "a" * 64,
            "gildata-mcp-1",
            True,
            _licensed_gildata_contract(display=False),
        ),
        (
            "gildata://research-report/" + "a" * 64,
            "gildata-mcp-1",
            True,
            _licensed_gildata_contract(provider=" gildata"),
        ),
        (
            "gildata://research-report/" + "a" * 63,
            "gildata-mcp-1",
            True,
            _licensed_gildata_contract(),
        ),
        (
            "gildata://research-report/" + "a" * 64,
            "html-v1",
            True,
            _licensed_gildata_contract(),
        ),
        (
            "gildata://research-report/" + "a" * 64,
            "gildata-mcp-1",
            False,
            _licensed_gildata_contract(),
        ),
        (
            "\x00gildata://research-report/" + "a" * 64,
            "gildata-mcp-1",
            True,
            None,
        ),
    ],
)
def test_gildata_display_fails_closed_without_verified_contract_identity(
    source_url: str,
    parser_version: str,
    content_verified: bool,
    contract: object | None,
) -> None:
    assert not _document_source_can_display(
        source_url=source_url,
        parser_version=parser_version,
        content_verified=content_verified,
        contract=contract,
    )


def test_display_contract_effective_boundaries_are_inclusive() -> None:
    starts_at = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
    expires_at = starts_at + timedelta(hours=1)
    contract = _licensed_gildata_contract(
        effective_from=starts_at,
        effective_until=expires_at,
    )

    assert _document_source_can_display(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=contract,
        at=starts_at,
    )
    assert _document_source_can_display(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=contract,
        at=expires_at,
    )
    assert not _document_source_can_display(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=contract,
        at=starts_at - timedelta(microseconds=1),
    )
    assert not _document_source_can_display(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=contract,
        at=expires_at + timedelta(microseconds=1),
    )


def test_generic_http_without_contract_keeps_historical_display_behavior() -> None:
    assert _document_source_can_display(
        source_url="https://example.com/historical",
        parser_version="html-v1",
        content_verified=True,
        contract=None,
    )


def test_generic_contract_display_requires_literal_right_and_active_window() -> None:
    at = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)

    assert _document_source_can_display(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="html-v1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=False, display=True),
        at=at,
    )
    assert not _document_source_can_display(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="html-v1",
        content_verified=True,
        contract=_licensed_gildata_contract(display=1),
        at=at,
    )
    assert not _document_source_can_display(
        source_url="https://www.cninfo.com.cn/report.pdf",
        parser_version="html-v1",
        content_verified=True,
        contract=_licensed_gildata_contract(effective_until=at - timedelta(seconds=1)),
        at=at,
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
