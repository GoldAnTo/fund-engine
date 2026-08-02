"""Defect-8 fix verification: ValuationSnapshot as_of_date derived from
``date.today()`` was a caller-side lie — Gildata's ``FinQuery`` semantics is
「最新行情」without a ``trade_date`` field, so the actual quote reflects the
previous business day's close.  This module pins the new ``_previous_business_day``
helper to five weekday boundaries plus the default behavior.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.scripts.ingest_real_data import _previous_business_day


class TestPreviousBusinessDay:
    """Pin the business-day roll-back used for ValuationSnapshot.as_of_date."""

    @pytest.mark.parametrize(
        "today,expected",
        [
            # Mon 2026-08-03 → 上周五 2026-07-31
            (date(2026, 8, 3), date(2026, 7, 31)),
            # Tue 2026-08-04 → 周一 2026-08-03
            (date(2026, 8, 4), date(2026, 8, 3)),
            # Wed 2026-08-05 → 周二 2026-08-04
            (date(2026, 8, 5), date(2026, 8, 4)),
            # Thu 2026-08-06 → 周三 2026-08-05
            (date(2026, 8, 6), date(2026, 8, 5)),
            # Fri 2026-08-07 → 周四 2026-08-06
            (date(2026, 8, 7), date(2026, 8, 6)),
            # Sat 2026-08-08 → 上周五 2026-08-07
            (date(2026, 8, 8), date(2026, 8, 7)),
            # Sun 2026-08-09 → 上周五 2026-08-07
            (date(2026, 8, 9), date(2026, 8, 7)),
        ],
    )
    def test_weekday_rolls_back_to_previous_business_day(
        self, today: date, expected: date
    ) -> None:
        assert _previous_business_day(today) == expected

    def test_default_argument_uses_date_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no arg is passed, the helper must consult date.today() — the
        walkthrough script relies on this default at line 414 of
        ``ingest_real_data.py``.  Pin that contract so a future refactor does
        not accidentally drop it."""
        monkeypatch.setattr(
            "app.scripts.ingest_real_data.date", _FrozenDate(date(2026, 8, 3))
        )
        assert _previous_business_day() == date(2026, 7, 31)

    def test_year_boundary_rolls_back_across_jan_1(self) -> None:
        """Mon 2027-01-04 → Fri 2027-01-01 (元旦 was Friday).  Guards against
        naive ``timedelta(days=1)`` logic that could land on 2026-12-31 (Sat)."""
        assert _previous_business_day(date(2027, 1, 4)) == date(2027, 1, 1)

    def test_does_not_handle_cn_holidays(self) -> None:
        """Documented limitation: 春节/国庆/中秋等法定节假日不在本函数处理范围。
        五一劳动节（2026-05-01 周五）调用若不显式覆盖会错记为 as_of=2026-04-30，
        这是 caller 的责任（脚本接受 quote_as_of 入口参数覆盖；当前默认走
        推断，对周一/周末调用最准确）。"""
        # 五一假期 2026-05-04 (Mon) — 不显式覆盖，helper 只看周末
        # 会推到 2026-05-01 (Fri)，但 5/1 是法定假日，正确应是 4/30 (Thu)
        result = _previous_business_day(date(2026, 5, 4))
        # 仅断言 helper 不崩溃 + 返回工作日，断言不保证节假日正确
        assert result.weekday() < 5
        # 节假日场景下 helper 会"碰巧"把五一当作工作日处理（已知限制）
        assert result == date(2026, 5, 1)


class _FrozenDate:
    """Mimics ``datetime.date`` for ``monkeypatch.setattr(date, ...)``."""

    def __init__(self, frozen: date) -> None:
        self._frozen = frozen

    def today(self) -> date:
        return self._frozen
