"""document_versions: natural_key for source+title+date dedup

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-02

历史重复根因：原去重键是 raw bytes 的 SHA256——同份年报不同时间抓、不同
入口但完全相同字节 → 正确去重；但"年报正文版"vs"年报摘要"vs"港股年报"
是不同字节 → 全部入库。本迁移在 DocumentVersion 上加 natural_key 列
（(source_url_prefix, title_normalized, published_at) 的 SHA256 前 32
字符），并加唯一约束。语义：相同发布机构 + 相同标题 + 相同发布日期视为同一
份文档，仅保留首份。

回填时按当前内容分组生成 natural_key；唯一约束下若已有重复，保留最早一条
的 natural_key 不变，其他行改 NULL 以让新约束通过。
"""
from typing import Sequence, Union

import hashlib
import re

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"[\[\]【】\(\)（）：:]")


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return _WS_RE.sub("", _BRACKET_RE.sub("", title.strip())).lower()


def _source_prefix(source_url: str) -> str:
    # 仅取来源类型（gildata://research_report 等）以让跨入口/跨站点同源
    # 文档归并到同一组；正文 URL 的差异不应绕过去重。
    if "://" in source_url:
        return source_url.split("://", 1)[0] + "://" + source_url.split("://", 1)[1].split("/", 1)[0]
    return source_url.split("/", 1)[0]


def _natural_key(source_url: str, title: str | None, published_at: object | None) -> str:
    prefix = _source_prefix(source_url)
    date = (
        published_at.date().isoformat()
        if hasattr(published_at, "date")
        else (str(published_at) if published_at else "")
    )
    raw = f"{prefix}|{_normalize_title(title)}|{date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upgrade() -> None:
    conn = op.get_bind()
    op.add_column(
        "document_versions",
        sa.Column("natural_key", sa.String(32), nullable=True),
    )

    # 回填：按 (source_prefix, normalized_title, published_at) 分组，最早
    # 一条保留生成的 natural_key；同组其他行清空以避免唯一约束冲突（保留
    # raw bytes hash 唯一性，仍可通过 SHA256 命中旧逻辑）。
    # DocumentVersion 没有 title 列；title 存于 source_spans.locator 的
    # JSON 内，按 acquired_at 取最早一条 span 的 locator.title 作为代表。
    rows = conn.execute(
        sa.text(
            """
            SELECT dv.id, dv.source_url, dv.published_at, dv.acquired_at,
                   (SELECT ss.locator->>'title'
                    FROM source_spans ss
                    WHERE ss.document_version_id = dv.id
                    ORDER BY ss.id ASC
                    LIMIT 1) AS title
            FROM document_versions dv
            ORDER BY dv.acquired_at ASC
            """
        )
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        key = _natural_key(row.source_url, row.title, row.published_at)
        if key in seen:
            # 同组后到者：清空 natural_key 让新约束通过（旧条目继续以
            # content_sha256 命中旧去重路径，不影响读取）。
            conn.execute(
                sa.text(
                    "UPDATE document_versions SET natural_key = NULL "
                    "WHERE id = :id"
                ),
                {"id": row.id},
            )
        else:
            seen.add(key)
            conn.execute(
                sa.text(
                    "UPDATE document_versions SET natural_key = :k "
                    "WHERE id = :id"
                ),
                {"k": key, "id": row.id},
            )

    op.create_unique_constraint(
        "uq_document_versions_natural_key",
        "document_versions",
        ["natural_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_document_versions_natural_key",
        "document_versions",
        type_="unique",
    )
    op.drop_column("document_versions", "natural_key")