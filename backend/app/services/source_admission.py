"""Pure rules for admitting event-evidence source links."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
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

    address = _parse_ip_address(normalized_host)
    if _is_non_public_ip_or_local_host(normalized_host, address):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源主机为本地、私网、保留或单标签地址，不能作为有效证据来源。",
            False,
        )

    if address is None and not _is_valid_domain_hostname(normalized_host):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源主机名格式无效。",
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


def _parse_ip_address(hostname: str) -> IPv4Address | IPv6Address | None:
    """Return a parsed IP address, or ``None`` when hostname is a domain."""
    try:
        return ip_address(hostname)
    except ValueError:
        return None


def _is_non_public_ip_or_local_host(
    hostname: str,
    address: IPv4Address | IPv6Address | None,
) -> bool:
    """Return whether a hostname cannot represent a public evidence source."""
    if address is not None:
        return not address.is_global

    if _is_abbreviated_loopback_ipv4(hostname):
        return True

    return hostname == "localhost" or "." not in hostname


def _is_abbreviated_loopback_ipv4(hostname: str) -> bool:
    """Recognize legacy numeric forms such as ``127.1`` as loopback."""
    labels = hostname.split(".")
    return (
        2 <= len(labels) <= 4
        and labels[0] == "127"
        and all(label.isdecimal() for label in labels)
    )


def _is_valid_domain_hostname(hostname: str) -> bool:
    """Validate domain syntax locally, without resolving the hostname."""
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False

    if len(ascii_hostname) > 253:
        return False

    return all(_is_valid_domain_label(label) for label in ascii_hostname.split("."))


def _is_valid_domain_label(label: str) -> bool:
    """Return whether a single IDNA-normalized domain label is syntactically valid."""
    return (
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
    )
