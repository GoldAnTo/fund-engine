"""Deterministic, source-bound key-factor candidate parsing.

This module intentionally produces *candidates*, never reviewed KeyFactor
records.  Each result retains the exact source excerpt and the versioned rule
that produced it so a researcher can reproduce, edit, accept, or reject it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


PARSER_VERSION = "key-factor-rules-v1"
_NUMERIC_FORECAST = re.compile(
    r"(?P<prefix>预计|预测|预期)\s*(?P<subject>.*?)?(?P<year>20\d{2})\s*年\s*"
    r"(?P<metric>[\u4e00-\u9fffA-Za-z0-9（）()、·\-]{2,40}?)"
    r"(?P<verb>为|达到|达|实现)\s*"
    r"(?P<value>[0-9][0-9,.]*(?:亿|万|千)?(?:元|美元|%|亿元)?)"
)
_GROWTH_MARKER = re.compile(r"(?P<comparison>同比|环比)(?P<change>增长|增加|上升|超过|下降|减少|下滑)")
_TRAILING_REPORTED_VALUE = re.compile(
    r"[0-9][0-9,.]*(?:亿|万|千)?(?:元|美元|%|亿元)?[，,]?$"
)


@dataclass(frozen=True, slots=True)
class KeyFactorCandidate:
    """A proposed factor whose fields remain editable before human review."""

    name: str
    metric_name: str
    expected_direction: str
    verification_window_start: date
    verification_window_end: date
    support_condition: str
    refutation_condition: str
    next_verification_event: str
    evidence_excerpt: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class KeyFactorParseResult:
    parser_version: str
    candidates: tuple[KeyFactorCandidate, ...]
    skipped_reason: str | None


class KeyFactorCandidateParser:
    """Extract only explicit, measurable factor candidates from frozen text.

    Direction is deliberately neutral: a numeric forecast is a comparison
    target, not proof that the reported direction will occur.  The candidate
    keeps default source and timing conditions visible for later human edits.
    """

    def parse(
        self, source_text: str, *, observed_period: date | None = None
    ) -> KeyFactorParseResult:
        candidates: list[KeyFactorCandidate] = []
        match = _NUMERIC_FORECAST.search(source_text)
        if match is not None:
            year = int(match.group("year"))
            metric = match.group("metric").strip()
            candidates.append(
                KeyFactorCandidate(
                    name=f"{year} 年{metric}预测兑现",
                    metric_name=metric,
                    expected_direction="neutral",
                    verification_window_start=date(year, 1, 1),
                    verification_window_end=date(year, 12, 31),
                    support_condition=(
                        f"公司后续披露的 {year} 年{metric}与冻结预测值按已确认比较规则核验。"
                    ),
                    refutation_condition=(
                        f"公司后续披露的 {year} 年{metric}未满足冻结预测的已确认比较规则。"
                    ),
                    next_verification_event=f"公司 {year} 年年度报告或同口径正式披露",
                    evidence_excerpt=match.group(0).strip(),
                    rule_id="cn_numeric_forecast_v1",
                )
            )
        if observed_period is not None:
            candidates.extend(
                self._business_growth_candidates(source_text, observed_period)
            )
        if not candidates:
            return KeyFactorParseResult(
                parser_version=PARSER_VERSION,
                candidates=(),
                skipped_reason="原文未出现可冻结的数值预测或明确业务指标；未生成候选因素。",
            )
        return KeyFactorParseResult(
            parser_version=PARSER_VERSION,
            candidates=tuple(candidates),
            skipped_reason=None,
        )

    @staticmethod
    def _business_growth_candidates(
        source_text: str, observed_period: date
    ) -> tuple[KeyFactorCandidate, ...]:
        year = observed_period.year
        candidates: list[KeyFactorCandidate] = []
        for clause in re.split(r"[；;。]", source_text):
            excerpt = clause.strip().strip("，,。")
            marker = _GROWTH_MARKER.search(excerpt)
            if marker is None:
                continue
            metric = _TRAILING_REPORTED_VALUE.sub("", excerpt[: marker.start()]).strip()
            if not metric:
                continue
            change = marker.group("change")
            direction = "negative" if change in {"下降", "减少", "下滑"} else "positive"
            direction_label = "下降" if direction == "negative" else "增长"
            candidates.append(
                KeyFactorCandidate(
                    name=f"{year} 年{metric}同比{direction_label}",
                    metric_name=metric,
                    expected_direction=direction,
                    verification_window_start=date(year, 1, 1),
                    verification_window_end=date(year, 12, 31),
                    support_condition=(
                        f"公司正式披露的 {year} 年{metric}与冻结原文中的同比变化一致。"
                    ),
                    refutation_condition=(
                        f"公司正式披露的 {year} 年{metric}未呈现冻结原文所述的同比变化。"
                    ),
                    next_verification_event=f"公司 {year} 年年度报告或同口径正式披露",
                    evidence_excerpt=excerpt,
                    rule_id="cn_business_growth_v1",
                )
            )
        return tuple(candidates)
