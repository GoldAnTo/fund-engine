"""Compliance gate for AI-generated text (non-investment-advice boundary).

The product promises never to emit recommendations, target prices, return
promises, or personalized investment advice.  This module turns that promise
from copy into a mechanism: every piece of LLM-generated text is evaluated
before it reaches the ledger.

Ported and adapted from the Verifiable-Company-Research-Agent
``compliance/rules.py`` (MIT).  Unlike VCRA we have no LLM rewrite stage, so
``REWRITE`` hits are treated as refused at call sites for now; the
three-action model is kept so a rewrite path can be added without changing
the contract.

Engineering details kept from the source: base64 data-URIs are stripped
before scanning (random strings spuriously match "buy"/"sell"), ASCII
keywords match on word boundaries, and each category records at most one
hit to keep audit output readable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ViolationCategory(str, Enum):
    BUY_SELL_ADVICE = "buy_sell_advice"
    TARGET_PRICE = "target_price"
    RETURN_PROMISE = "return_promise"
    STOCK_RECOMMENDATION = "stock_recommendation"
    POSITION_GUIDANCE = "position_guidance"
    PERSONALIZED_INVESTMENT_ADVICE = "personalized_investment_advice"


class ComplianceAction(str, Enum):
    ALLOW = "allow"
    REWRITE = "rewrite"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class ComplianceHit:
    category: ViolationCategory
    matched_snippet: str


@dataclass(frozen=True, slots=True)
class ComplianceDecision:
    is_hit: bool
    action: ComplianceAction
    hits: tuple[ComplianceHit, ...] = ()
    summary_reason: str = ""


class ComplianceRefusedError(Exception):
    """Raised when AI-generated text is refused by the compliance gate."""

    def __init__(self, decision: ComplianceDecision) -> None:
        self.decision = decision
        super().__init__(
            f"compliance refused: {decision.summary_reason} "
            f"({', '.join(h.matched_snippet for h in decision.hits)})"
        )


_CATEGORY_KEYWORDS: dict[ViolationCategory, tuple[str, ...]] = {
    ViolationCategory.BUY_SELL_ADVICE: (
        "买入", "卖出", "要不要买", "能不能买", "能买吗", "建议买入", "建议卖出",
        "值得买", "值得卖", "抄底", "逃顶", "梭哈", "满仓", "清仓",
        "buy now", "sell now", "buy", "sell",
    ),
    ViolationCategory.TARGET_PRICE: (
        "目标价", "目标股价", "合理价位", "估值区间",
        "target price", "target_price", "price target",
    ),
    ViolationCategory.RETURN_PROMISE: (
        "收益承诺", "稳赚", "稳赚不赔", "保本保息", "翻倍", "十倍股",
        "年化收益", "预期收益", "expected return", "收益预测", "保证收益",
    ),
    ViolationCategory.STOCK_RECOMMENDATION: (
        "个股推荐", "推荐哪只", "推荐股票", "强烈推荐", "首选标的", "核心标的",
        "必买", "龙头首选",
    ),
    ViolationCategory.POSITION_GUIDANCE: (
        "加仓", "减仓", "持仓指导", "仓位", "建仓", "止盈", "止损",
        "调仓", "重仓", "轻仓", "配置方案",
    ),
    ViolationCategory.PERSONALIZED_INVESTMENT_ADVICE: (
        "适合我买吗", "个性化投资建议", "适合你购买", "我该不该买",
        "帮我选股票", "这只股票能买吗", "现在可以入场吗", "给我配置方案",
    ),
}

_REFUSE_CATEGORIES = frozenset(
    {
        ViolationCategory.BUY_SELL_ADVICE,
        ViolationCategory.STOCK_RECOMMENDATION,
        ViolationCategory.POSITION_GUIDANCE,
        ViolationCategory.PERSONALIZED_INVESTMENT_ADVICE,
    }
)
_REWRITE_CATEGORIES = frozenset(
    {
        ViolationCategory.TARGET_PRICE,
        ViolationCategory.RETURN_PROMISE,
    }
)

# 报告可能内嵌 base64 图表，随机字符串会偶发命中 "buy"/"sell"，扫描前先剥离。
_DATA_URI_RE = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)", re.IGNORECASE)


def _sanitize(text: str) -> str:
    return _DATA_URI_RE.sub("", text)


def _keyword_hit(lowered: str, keyword: str) -> bool:
    """ASCII 关键词按整词命中；中文（非 ASCII）走子串匹配。"""
    if keyword.isascii() and keyword.isalpha() and " " not in keyword:
        return bool(re.search(rf"\b{re.escape(keyword.lower())}\b", lowered))
    return keyword.lower() in lowered


def evaluate_compliance(text: str | None) -> ComplianceDecision:
    """评估一段文本是否越过非投顾边界。空文本按允许处理。"""
    raw = _sanitize((text or "").strip())
    if not raw:
        return ComplianceDecision(
            is_hit=False, action=ComplianceAction.ALLOW, summary_reason="empty text"
        )

    lowered = raw.lower()
    hits: list[ComplianceHit] = []
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _keyword_hit(lowered, kw):
                hits.append(ComplianceHit(category=category, matched_snippet=kw))
                break  # 每类只记首个命中，减少审计噪音

    if not hits:
        return ComplianceDecision(
            is_hit=False, action=ComplianceAction.ALLOW,
            summary_reason="no violation keywords matched",
        )

    categories = {h.category for h in hits}
    if categories & _REFUSE_CATEGORIES:
        action = ComplianceAction.REFUSE
        summary = "命中投资建议或个性化导向表达"
    elif categories & _REWRITE_CATEGORIES:
        action = ComplianceAction.REWRITE
        summary = "命中收益或价格预测表达"
    else:
        action = ComplianceAction.REFUSE
        summary = "命中违规表达，默认拒绝（保守口径）"
    return ComplianceDecision(
        is_hit=True, action=action, hits=tuple(hits), summary_reason=summary
    )


def assert_compliant(*texts: str | None) -> None:
    """Refuse loudly if any of ``texts`` crosses the boundary.

    ``REWRITE`` is treated as refused until a rewrite stage exists.
    """
    for text in texts:
        decision = evaluate_compliance(text)
        if decision.is_hit:
            raise ComplianceRefusedError(decision)
