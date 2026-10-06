"""src.indicators 分红与估值指标函数的单元测试。

全部使用本地构造的 fixture DataFrame，不联网、不读文件。
"""

from datetime import date

import pandas as pd
import pytest

from src.indicators import (
    calc_dividend_trend,
    calc_dividend_years,
    calc_dividend_yield,
    calc_payout_ratio,
    calc_pb_vs_industry,
    calc_pe_vs_industry,
    calc_ttm_dividend_per_share,
)

AS_OF = date(2026, 10, 6)


def _dividend_frame(entries):
    """构造规范分红帧。entries 为 (日期字符串, 税前每股分红) 序列，日期须升序。"""
    return pd.DataFrame(
        {
            "date": pd.to_datetime([day for day, _ in entries]),
            "dividend_per_share": [amount for _, amount in entries],
        }
    )


@pytest.fixture
def cmb_dividends():
    """模拟招行分红：2026-07-10 每股 1.003 元、2026-01-16 每股 1.013 元、2024-05-01 每股 0.90 元。"""
    return _dividend_frame(
        [
            ("2024-05-01", 0.90),
            ("2026-01-16", 1.013),
            ("2026-07-10", 1.003),
        ]
    )


def test_dividend_yield_uses_ttm_window(cmb_dividends):
    # (1.013 + 1.003) / 40 × 100 = 5.04，2024-05-01 那条在 TTM 窗口外
    assert calc_dividend_yield(cmb_dividends, 40.0, AS_OF) == pytest.approx(5.04)


def test_dividend_yield_zero_when_only_outside_window():
    frame = _dividend_frame([("2024-05-01", 0.90)])

    assert calc_dividend_yield(frame, 40.0, AS_OF) == 0.0


def test_dividend_yield_none_data_returns_none():
    assert calc_dividend_yield(None, 40.0, AS_OF) is None


@pytest.mark.parametrize("price", [None, 0])
def test_dividend_yield_invalid_price_returns_none(price, cmb_dividends):
    assert calc_dividend_yield(cmb_dividends, price, AS_OF) is None


def test_dividend_years_consecutive_streak():
    frame = _dividend_frame(
        [
            ("2020-06-01", 0.50),
            ("2023-06-01", 0.60),
            ("2024-06-01", 0.70),
            ("2025-06-01", 0.80),
            ("2026-06-01", 0.90),
        ]
    )

    assert calc_dividend_years(frame, AS_OF) == 4


def test_dividend_years_zero_when_latest_year_stale():
    frame = _dividend_frame([("2023-06-01", 0.60)])

    assert calc_dividend_years(frame, AS_OF) == 0


def test_dividend_years_empty_frame_returns_zero():
    assert calc_dividend_years(_dividend_frame([]), AS_OF) == 0


def test_dividend_years_none_returns_none():
    assert calc_dividend_years(None, AS_OF) is None


def test_payout_ratio():
    assert calc_payout_ratio(5.04, 10.0) == pytest.approx(50.4)


@pytest.mark.parametrize("pe", [None, 0])
def test_payout_ratio_invalid_pe_returns_none(pe):
    assert calc_payout_ratio(5.04, pe) is None


@pytest.mark.parametrize(
    ("stock_pe", "industry_pe", "expected"),
    [(10.0, 12.5, -20.0), (15.0, 12.5, 20.0)],
)
def test_pe_vs_industry(stock_pe, industry_pe, expected):
    assert calc_pe_vs_industry(stock_pe, industry_pe) == pytest.approx(expected)


@pytest.mark.parametrize("industry_pe", [None, 0])
def test_pe_vs_industry_invalid_industry_pe_returns_none(industry_pe):
    assert calc_pe_vs_industry(10.0, industry_pe) is None


@pytest.mark.parametrize(
    ("stock_pb", "industry_pb", "expected"),
    [(1.0, 1.25, -20.0), (1.5, 1.25, 20.0)],
)
def test_pb_vs_industry(stock_pb, industry_pb, expected):
    assert calc_pb_vs_industry(stock_pb, industry_pb) == pytest.approx(expected)


@pytest.mark.parametrize("industry_pb", [None, 0])
def test_pb_vs_industry_invalid_industry_pb_returns_none(industry_pb):
    assert calc_pb_vs_industry(1.0, industry_pb) is None


def test_ttm_dividend_per_share_sums_trailing_window():
    frame = _dividend_frame(
        [("2024-05-01", 0.9), ("2026-01-16", 1.013), ("2026-07-10", 1.003)]
    )

    # 窗口内合计：1.013 + 1.003 = 2.016（与股息率的 TTM 口径一致）
    assert calc_ttm_dividend_per_share(frame, AS_OF) == pytest.approx(2.016)


def test_ttm_dividend_per_share_edge_cases():
    assert calc_ttm_dividend_per_share(None, AS_OF) is None  # 获取失败不可评分
    assert calc_ttm_dividend_per_share(_dividend_frame([]), AS_OF) == pytest.approx(0.0)
    # 窗口外只有旧分红 → 0
    assert calc_ttm_dividend_per_share(
        _dividend_frame([("2024-05-01", 0.9)]), AS_OF
    ) == pytest.approx(0.0)


def test_dividend_trend_compares_recent_three_years():
    # 近 3 年（2024-2026）合计 2.1；前 3 年（2021-2023）合计 3.0 → -30%
    frame = _dividend_frame(
        [
            ("2021-06-10", 1.0),
            ("2022-06-10", 1.0),
            ("2023-06-10", 1.0),
            ("2024-06-10", 0.7),
            ("2025-06-10", 0.7),
            ("2026-06-10", 0.7),
        ]
    )
    assert calc_dividend_trend(frame, AS_OF) == pytest.approx(-30.0)


def test_dividend_trend_rising_and_edges():
    rising = _dividend_frame(
        [
            ("2021-06-10", 0.5),
            ("2022-06-10", 0.5),
            ("2023-06-10", 0.5),
            ("2024-06-10", 1.0),
            ("2025-06-10", 1.0),
            ("2026-06-10", 1.0),
        ]
    )
    assert calc_dividend_trend(rising, AS_OF) == pytest.approx(100.0)

    # 前 3 年为零（新分红公司）且近 3 年有分红 → 视为增长
    new_payer = _dividend_frame([("2024-06-10", 0.5), ("2025-06-10", 0.5)])
    assert calc_dividend_trend(new_payer, AS_OF) == pytest.approx(100.0)

    # 完全无分红或获取失败 → 不可评分
    assert calc_dividend_trend(_dividend_frame([]), AS_OF) is None
    assert calc_dividend_trend(None, AS_OF) is None
