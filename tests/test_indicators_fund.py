"""src.indicators 基金指标函数（折溢价/净值趋势/PE 分位/分红频率/规模）的单元测试。

全部使用本地构造的帧与字符串，不联网、不读文件。
基金历史帧规范列：``date``（datetime64，升序）、``close``（float）；
指数 PE 帧规范列：``date``、``pe``（float，升序）；基金分红帧仅需 ``date`` 列。
"""

from datetime import date

import pandas as pd
import pytest

from src.indicators import (
    calc_dividend_frequency,
    calc_fund_discount,
    calc_fund_size,
    calc_index_pe_position,
    calc_nav_trend,
)

AS_OF = date(2026, 10, 6)


def _nav_frame(closes):
    """按净值序列构造规范基金历史帧（日期升序，仅含 date/close 两列）。"""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(closes), freq="D"),
            "close": [float(close) for close in closes],
        }
    )


def _pe_frame(values, column="pe"):
    """按 PE 序列构造指数 PE 帧（日期升序）。"""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(values), freq="D"),
            column: [float(value) for value in values],
        }
    )


def _dividend_frame(days):
    """构造规范基金分红帧（仅含 date 列，升序）。"""
    return pd.DataFrame({"date": pd.to_datetime(days)})


def test_fund_discount_below_nav_is_negative():
    # (0.99 - 1.0) / 1.0 × 100 = -1.0，负值表示折价
    assert calc_fund_discount(0.99, 1.0) == pytest.approx(-1.0)


def test_fund_discount_above_nav_is_positive():
    # (1.02 - 1.0) / 1.0 × 100 = 2.0，正值表示溢价
    assert calc_fund_discount(1.02, 1.0) == pytest.approx(2.0)


@pytest.mark.parametrize("nav", [None, 0.0])
def test_fund_discount_invalid_nav_returns_none(nav):
    assert calc_fund_discount(0.99, nav) is None


def test_fund_discount_none_price_returns_none():
    assert calc_fund_discount(None, 1.0) is None


def test_nav_trend_twenty_days_ago_to_latest():
    frame = _nav_frame([1.0] + [1.05] * 19 + [1.10])

    # 基准为倒数第 days+1 行（20 期前）：(1.10 - 1.0) / 1.0 × 100 = 10.0
    assert calc_nav_trend(frame) == pytest.approx(10.0)


def test_nav_trend_insufficient_rows_returns_none():
    assert calc_nav_trend(_nav_frame([1.0] * 20)) is None


def test_nav_trend_none_returns_none():
    assert calc_nav_trend(None) is None


def test_nav_trend_zero_base_returns_none():
    frame = _nav_frame([0.0] + [1.0] * 19 + [1.10])

    assert calc_nav_trend(frame) is None


def test_index_pe_position_is_percentile_of_history_range():
    # 历史区间 [10, 40]、最新 30 → (30 - 10) / (40 - 10) × 100 = 66.66...
    frame = _pe_frame([10.0, 20.0, 40.0, 30.0])

    assert calc_index_pe_position(frame) == pytest.approx(66.67, rel=1e-3)


def test_index_pe_position_uses_named_column():
    frame = _pe_frame([10.0, 30.0], column="pe_ttm")

    assert calc_index_pe_position(frame, column="pe_ttm") == pytest.approx(100.0)


def test_index_pe_position_insufficient_rows_returns_none():
    assert calc_index_pe_position(_pe_frame([30.0])) is None


def test_index_pe_position_flat_history_returns_none():
    assert calc_index_pe_position(_pe_frame([10.0, 10.0])) is None


def test_index_pe_position_none_returns_none():
    assert calc_index_pe_position(None) is None


def test_dividend_frequency_counts_last_year():
    frame = _dividend_frame(["2025-01-01", "2026-01-15", "2026-04-20", "2026-07-10"])

    # 截至 2026-10-06 的一年窗口内 3 次，2025-01-01 那次在窗口外
    assert calc_dividend_frequency(frame, AS_OF) == 3


def test_dividend_frequency_empty_frame_returns_zero():
    assert calc_dividend_frequency(_dividend_frame([]), AS_OF) == 0


def test_dividend_frequency_none_returns_none():
    assert calc_dividend_frequency(None, AS_OF) is None


def test_fund_size_parses_yi():
    assert calc_fund_size("222.76亿元（截止至：2026年06月30日）") == pytest.approx(222.76)


def test_fund_size_parses_wan_as_yi():
    assert calc_fund_size("5000万元（截止至：2026年06月30日）") == pytest.approx(0.5)


@pytest.mark.parametrize("scale", ["---", None])
def test_fund_size_unparsable_returns_none(scale):
    assert calc_fund_size(scale) is None
