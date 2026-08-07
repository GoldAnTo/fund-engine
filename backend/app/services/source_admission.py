"""Pure rules for admitting event-evidence source links."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address
from urllib.parse import urlparse


class SourceStatus(StrEnum):
    """The admissibility of a source link for event evidence."""

    ACCESSIBLE = "accessible"
    PASTED_UNVERIFIED = "pasted_unverified"
    INVALID = "invalid"


@dataclass(frozen=True)
class SourceAdmission:
    """The pure classification result for one source link."""

    status: SourceStatus
    reason: str
    can_accept: bool


def classify_source(
    source_url: str | None,
    parser_version: str,
    content_verified: bool,
) -> SourceAdmission:
    """Classify a source link without making network or database calls."""
    if not source_url or not source_url.strip():
        return SourceAdmission(SourceStatus.INVALID, "缺少来源 URL。", False)

    try:
        parsed = urlparse(source_url)
    except ValueError:
        return SourceAdmission(SourceStatus.INVALID, "来源 URL 格式无效。", False)

    if parsed.scheme.lower() not in {"http", "https"}:
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源 URL 必须使用 HTTP 或 HTTPS 协议。",
            False,
        )

    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return SourceAdmission(SourceStatus.INVALID, "来源 URL 格式无效。", False)

    if not hostname:
        return SourceAdmission(SourceStatus.INVALID, "来源 URL 缺少主机名。", False)

    normalized_host = hostname.lower().rstrip(".")
    if normalized_host == "example.com" or normalized_host.endswith(".test"):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源为测试域名，不能作为有效证据来源。",
            False,
        )

    if _is_local_or_single_label_host(normalized_host):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源主机为本地、私网或单标签地址，不能作为有效证据来源。",
            False,
        )

    if parser_version.startswith("user-pasted-v"):
        return SourceAdmission(
            SourceStatus.PASTED_UNVERIFIED,
            "来源由用户粘贴解析，尚未完成内容验证。",
            False,
        )

    if not content_verified:
        return SourceAdmission(
            SourceStatus.PASTED_UNVERIFIED,
            "来源内容尚未验证。",
            False,
        )

    return SourceAdmission(
        SourceStatus.ACCESSIBLE,
        "来源链接可访问且内容已验证。",
        True,
    )


def _is_local_or_single_label_host(hostname: str) -> bool:
    """Return whether a hostname cannot represent a public evidence source."""
    if hostname == "localhost" or "." not in hostname:
        return True

    try:
        address = ip_address(hostname)
    except ValueError:
        return False

    return address.is_loopback or address.is_private
