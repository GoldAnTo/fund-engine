"""Pure rules for admitting event-evidence source links."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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

    parsed = urlparse(source_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源 URL 必须使用 HTTP 或 HTTPS 协议。",
            False,
        )

    hostname = parsed.hostname
    if not hostname:
        return SourceAdmission(SourceStatus.INVALID, "来源 URL 缺少主机名。", False)

    normalized_host = hostname.lower().rstrip(".")
    if normalized_host == "example.com" or normalized_host.endswith(".test"):
        return SourceAdmission(
            SourceStatus.INVALID,
            "来源为测试域名，不能作为有效证据来源。",
            False,
        )

    if parser_version == "user-pasted-v1":
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
