"""Pure rules for admitting event-evidence source links."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
from unicodedata import normalize
from urllib.parse import urlparse


class SourceStatus(StrEnum):
    """The admissibility of a source link for event evidence."""

    ACCESSIBLE = "accessible"
    PASTED_UNVERIFIED = "pasted_unverified"
    RESTRICTED = "restricted"
    INVALID = "invalid"


@dataclass(frozen=True)
class SourceAdmission:
    """The pure classification result for one source link."""

    status: SourceStatus
    reason: str
    can_accept: bool


def source_contract_is_active(
    contract: object | None,
    *,
    at: datetime | None = None,
) -> bool:
    """Whether a frozen contract grants use at a specific instant.

    This deliberately treats an absent boundary as open-ended, while keeping
    comparisons in UTC so SQLite's timezone-naive test values and production
    timezone-aware values obey the same rule.
    """
    if contract is None:
        return True
    instant = _as_utc(at or datetime.now(timezone.utc))
    effective_from = getattr(contract, "effective_from", None)
    effective_until = getattr(contract, "effective_until", None)
    return (
        (effective_from is None or instant >= _as_utc(effective_from))
        and (effective_until is None or instant <= _as_utc(effective_until))
    )


def apply_source_contract(
    admission: SourceAdmission,
    contract: object | None,
    *,
    at: datetime | None = None,
) -> SourceAdmission:
    """Apply frozen use restrictions before a source can enter formal evidence.

    This remains deliberately duck-typed so the URL classifier stays a pure
    utility with no ORM dependency.  New intake documents always receive a
    SourceContract; pre-governance historical documents retain the existing
    provenance checks until they are explicitly re-admitted.
    """
    if contract is None:
        return admission
    if getattr(contract, "allow_ai_processing", False) is not True:
        return SourceAdmission(
            SourceStatus.RESTRICTED,
            "来源合同禁止 AI 处理；资料只能保留为线索，不能作为正式证据。",
            False,
        )
    if getattr(contract, "allow_display", False) is not True:
        return SourceAdmission(
            SourceStatus.RESTRICTED,
            "来源合同禁止展示；不能在审核工作台中作为正式证据处理。",
            False,
        )
    instant = _as_utc(at or datetime.now(timezone.utc))
    effective_from = getattr(contract, "effective_from", None)
    effective_until = getattr(contract, "effective_until", None)
    if effective_from is not None and instant < _as_utc(effective_from):
        return SourceAdmission(
            SourceStatus.RESTRICTED,
            "来源合同尚未生效；资料只能保留为线索，不能作为正式证据。",
            False,
        )
    if effective_until is not None and instant > _as_utc(effective_until):
        return SourceAdmission(
            SourceStatus.RESTRICTED,
            "来源合同已失效；资料只能保留为线索，不能作为正式证据。",
            False,
        )
    return admission


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def classify_source(
    source_url: str | None,
    parser_version: str,
    content_verified: bool,
) -> SourceAdmission:
    """Classify a source link without making network or database calls."""
    if not source_url or not source_url.strip():
        return SourceAdmission(SourceStatus.INVALID, "缺少来源 URL。", False)

    if _has_browser_ambiguous_characters(source_url):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源 URL 包含不安全字符。",
            False,
        )

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

    normalized_host = _normalize_hostname(hostname)
    if not normalized_host:
        return SourceAdmission(SourceStatus.INVALID, "来源主机名格式无效。", False)

    if "example" in normalized_host.split(".") or normalized_host.endswith(".test"):
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


def _has_browser_ambiguous_characters(source_url: str) -> bool:
    """Reject raw characters whose URL interpretation differs between parsers."""
    return any(
        character == "\\"
        or character.isspace()
        or ord(character) < 32
        or 127 <= ord(character) <= 159
        for character in source_url
    )


def _normalize_hostname(hostname: str) -> str | None:
    """Normalize a host with NFKC then IDNA before applying source rules."""
    try:
        return (
            normalize("NFKC", hostname)
            .encode("idna")
            .decode("ascii")
            .lower()
            .rstrip(".")
        )
    except UnicodeError:
        return None


def _parse_ip_address(hostname: str) -> IPv4Address | IPv6Address | None:
    """Return a parsed IP address, or ``None`` when hostname is a domain."""
    try:
        return ip_address(hostname)
    except ValueError:
        return _parse_historical_ipv4(hostname)


def _is_non_public_ip_or_local_host(
    hostname: str,
    address: IPv4Address | IPv6Address | None,
) -> bool:
    """Return whether a hostname cannot represent a public evidence source."""
    if address is not None:
        embedded_ipv4 = _embedded_ipv4_address(address)
        if embedded_ipv4 is not None:
            return True
        return not address.is_global

    return hostname == "localhost" or "." not in hostname


def _embedded_ipv4_address(address: IPv4Address | IPv6Address) -> IPv4Address | None:
    """Return an IPv6-mapped or IPv4-compatible address's embedded IPv4 value."""
    if not isinstance(address, IPv6Address):
        return None

    if address.ipv4_mapped is not None:
        return address.ipv4_mapped

    if int(address) <= 0xFFFFFFFF:
        return IPv4Address(int(address))

    return None


def _parse_historical_ipv4(hostname: str) -> IPv4Address | None:
    """Parse legacy URL IPv4 literals with decimal, octal, or hexadecimal parts."""
    labels = hostname.split(".")
    if not 1 <= len(labels) <= 4:
        return None

    try:
        parts = [_parse_historical_ipv4_part(label) for label in labels]
        value = _historical_ipv4_value(parts)
    except ValueError:
        return None

    return IPv4Address(value)


def _parse_historical_ipv4_part(value: str) -> int:
    """Parse one URL IPv4 part according to the historical base-prefix rules."""
    if value.startswith("0x"):
        digits = value[2:]
        if not digits or any(character not in "0123456789abcdef" for character in digits):
            raise ValueError("invalid hexadecimal IPv4 part")
        return int(digits, 16)

    if len(value) > 1 and value.startswith("0"):
        if any(character not in "01234567" for character in value):
            raise ValueError("invalid octal IPv4 part")
        return int(value, 8)

    if not value.isascii() or not value.isdecimal():
        raise ValueError("invalid decimal IPv4 part")
    return int(value, 10)


def _historical_ipv4_value(parts: list[int]) -> int:
    """Combine one to four historical IPv4 parts into an unsigned 32-bit value."""
    if len(parts) == 1 and parts[0] <= 0xFFFFFFFF:
        return parts[0]
    if len(parts) == 2 and parts[0] <= 0xFF and parts[1] <= 0xFFFFFF:
        return (parts[0] << 24) | parts[1]
    if len(parts) == 3 and parts[0] <= 0xFF and parts[1] <= 0xFF and parts[2] <= 0xFFFF:
        return (parts[0] << 24) | (parts[1] << 16) | parts[2]
    if len(parts) == 4 and all(part <= 0xFF for part in parts):
        return (
            (parts[0] << 24)
            | (parts[1] << 16)
            | (parts[2] << 8)
            | parts[3]
        )

    raise ValueError("historical IPv4 parts exceed their permitted range")


def _is_valid_domain_hostname(hostname: str) -> bool:
    """Validate an NFKC and IDNA-normalized domain without resolving it."""
    if len(hostname) > 253:
        return False

    return all(_is_valid_domain_label(label) for label in hostname.split("."))


def _is_valid_domain_label(label: str) -> bool:
    """Return whether a single IDNA-normalized domain label is syntactically valid."""
    return (
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
    )
