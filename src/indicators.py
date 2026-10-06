"""股票分红与估值指标（纯函数）。

输入为 data 层归一化后的数据帧或标量；不联网、不读文件、不依赖配置。
分红帧规范列：``date``（datetime64，升序）、``dividend_per_share``（税前每股分红，float）。
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def calc_dividend_yield(
    dividend_data: pd.DataFrame | None,
    price: float | None,
    as_of: date,
    window_days: int = 365,
) -> float | None:
    """近 window_days 天每股分红之和 / 股价 × 100（TTM 股息率，%）。

    dividend_data 为 None（获取失败）或 price 缺失/为 0 → None；空帧（无分红）→ 0.0。
    """
    if dividend_data is None or price is None or price == 0:
        return None
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=window_days)
    in_window = dividend_data.loc[dividend_data["date"] > cutoff, "dividend_per_share"]
    return float(in_window.sum()) / price * 100.0


def calc_dividend_years(dividend_data: pd.DataFrame | None, as_of: date) -> int | None:
    """截至 as_of 的连续分红年数（从最近分红年份起逐年回数）。

    None（获取失败）→ None；空帧（无分红）→ 0；最近一次分红早于去年 → 0（断档）。
    """
    if dividend_data is None:
        return None
    if dividend_data.empty:
        return 0

    years = set(dividend_data["date"].dt.year)
    latest = max(years)
    if latest < as_of.year - 1:
        return 0

    count = 0
    year = latest
    while year in years:
        count += 1
        year -= 1
    return count


def calc_payout_ratio(dividend_yield: float | None, pe: float | None) -> float | None:
    """分红率（%）= 股息率(%) × PE，即每股分红 / 每股收益 × 100。

    dividend_yield 或 pe 缺失、pe 为 0 → None。
    """
    if dividend_yield is None or pe is None or pe == 0:
        return None
    return float(dividend_yield * pe)


def calc_pe_vs_industry(
    stock_pe: float | None, industry_pe: float | None
) -> float | None:
    """个股 PE 相对行业 PE 的偏离度（%）；任一缺失或行业 PE 为 0 → None。"""
    if stock_pe is None or industry_pe is None or industry_pe == 0:
        return None
    return (stock_pe - industry_pe) / industry_pe * 100.0


def calc_pb_vs_industry(
    stock_pb: float | None, industry_pb: float | None
) -> float | None:
    """个股 PB 相对行业 PB 的偏离度（%）；任一缺失或行业 PB 为 0 → None。"""
    if stock_pb is None or industry_pb is None or industry_pb == 0:
        return None
    return (stock_pb - industry_pb) / industry_pb * 100.0
