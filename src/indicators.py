"""股票分红与估值指标（纯函数）。

输入为 data 层归一化后的数据帧或标量；不联网、不读文件、不依赖配置。
分红帧规范列：``date``（datetime64，升序）、``dividend_per_share``（税前每股分红，float）。
"""

from __future__ import annotations

import math
import re
from datetime import date

import numpy as np
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


def calc_dividend_trend(
    dividend_data: pd.DataFrame | None, as_of: date, years: int = 3
) -> float | None:
    """近 ``years`` 个年度 vs 前 ``years`` 个年度的每股分红合计变化率（%）。

    年度按除权除息日所在日历年统计。前段合计为 0 时：近段有分红 → 100（视为增长，
    如新分红公司）；两段都为 0 或无任何分红 → None（不可评分）；获取失败 → None。
    """
    if dividend_data is None or dividend_data.empty:
        return None
    year_series = dividend_data["date"].dt.year
    recent = float(
        dividend_data.loc[
            year_series.between(as_of.year - years + 1, as_of.year),
            "dividend_per_share",
        ].sum()
    )
    previous = float(
        dividend_data.loc[
            year_series.between(as_of.year - 2 * years + 1, as_of.year - years),
            "dividend_per_share",
        ].sum()
    )
    if previous == 0:
        return 100.0 if recent > 0 else None
    return (recent - previous) / previous * 100.0


def calc_ttm_dividend_per_share(
    dividend_data: pd.DataFrame | None,
    as_of: date,
    window_days: int = 365,
) -> float | None:
    """近 window_days 天每股分红之和（元/股，税前）。

    与 ``calc_dividend_yield`` 同一 TTM 窗口口径；None（获取失败）→ None；
    空帧（无分红）或窗口内无分红 → 0.0。
    """
    if dividend_data is None:
        return None
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=window_days)
    in_window = dividend_data.loc[dividend_data["date"] > cutoff, "dividend_per_share"]
    return float(in_window.sum())


def calc_dividend_yield_percentile(
    dividend_data: pd.DataFrame | None,
    kline_raw: pd.DataFrame | None,
    min_days: int = 250,
    window_days: int = 365,
) -> float | None:
    """当前 TTM 股息率在自身历史序列中的分位（0-100）。

    逐日重算 ``TTM 每股分红 ÷ 当日收盘 × 100``（与 ``calc_dividend_yield``
    同一 TTM 窗口），分位 = 序列中 ≤ 当前值的占比。用于区分「股息率高」是
    绝对水平还是相对自身历史：4.6% 可能是该股近年高位（相对便宜），也可能
    是低位（为增长支付了高价）。

    要求 ``kline_raw`` 为**不复权**价格帧（前复权价已扣除后来的分红，会把
    历史股息率系统性算低）。数据不足（None、空帧、K 线不足 ``min_days`` 根）
    或窗口内完全没有分红（TTM 恒为 0，分位无意义）→ None。

    已知近似：分红帧只含现金派息，送股/转增未计入，除送转日附近的历史股息率
    会有短暂偏差（目标池近年少见）。
    """
    if dividend_data is None or kline_raw is None or kline_raw.empty:
        return None
    frame = kline_raw.dropna(subset=["close"])
    if len(frame) < min_days:
        return None
    div = dividend_data.dropna(subset=["date", "dividend_per_share"]).sort_values(
        "date"
    )
    if div.empty:
        return None

    days = frame["date"].to_numpy(dtype="datetime64[D]")
    div_days = div["date"].to_numpy(dtype="datetime64[D]")
    cumulative = np.concatenate(
        ([0.0], np.cumsum(div["dividend_per_share"].to_numpy(dtype=float)))
    )
    # TTM 窗口 (day - 365, day]（与 calc_ttm_dividend_per_share 同为开区间下界）
    ttm = cumulative[
        np.searchsorted(div_days, days, side="right")
    ] - cumulative[
        np.searchsorted(
            div_days, days - np.timedelta64(window_days, "D"), side="right"
        )
    ]
    if not ttm.any():
        return None
    yields = ttm / frame["close"].to_numpy(dtype=float) * 100.0
    current = yields[-1]
    return float((yields <= current).mean() * 100.0)


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


# ---------------------------------------------------------------------------
# 技术指标（K 线帧规范列：date（datetime64，升序）、close（float））
# ---------------------------------------------------------------------------


def calc_ma(kline: pd.DataFrame | None, window: int = 60) -> float | None:
    """最后 window 根收盘价的均值（默认 MA60）；kline 为 None 或行数不足 → None。"""
    if kline is None or len(kline) < window:
        return None
    return float(kline["close"].tail(window).mean())


def calc_ma60_position(close: float | None, ma60: float | None) -> float | None:
    """收盘价相对 MA60 的偏离度（%）= (close - ma60) / ma60 × 100。

    任一缺失或 ma60 为 0 → None。
    """
    if close is None or ma60 is None or ma60 == 0:
        return None
    return (close - ma60) / ma60 * 100.0


def calc_momentum_5d(kline: pd.DataFrame | None) -> float | None:
    """5 日动量（%）= (close[-1] - close[-6]) / close[-6] × 100；不足 6 行 → None。"""
    if kline is None or len(kline) < 6:
        return None
    closes = kline["close"]
    base = float(closes.iloc[-6])
    if base == 0:
        return None
    return (float(closes.iloc[-1]) - base) / base * 100.0


def calc_macd(kline: pd.DataFrame | None) -> dict | None:
    """MACD：DIF = EMA(close,12) - EMA(close,26)，DEA = EMA(DIF,9)，hist = 2×(DIF-DEA)。

    signal 为最后一根相对上一根 DIF-DEA 的变号结果：金叉 / 死叉 / ""（未变号）。
    kline 为 None 或不足 26 行 → None。

    EMA12/EMA26 从首根 K 线起算：若两者也用 min_periods=12/26，DIF 要到第 26 根才
    有效，再叠加 DEA 的 min_periods=9，DEA 需 34 根才有值，与「不足 26 行返回 None」
    的边界及 31 根的测试输入不符。
    """
    if kline is None or len(kline) < 26:
        return None

    close = kline["close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=1).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    hist = 2.0 * (dif - dea)

    prev_diff = float(dif.iloc[-2] - dea.iloc[-2])
    last_diff = float(dif.iloc[-1] - dea.iloc[-1])
    if prev_diff <= 0 < last_diff:
        signal = "金叉"
    elif prev_diff >= 0 > last_diff:
        signal = "死叉"
    else:
        signal = ""

    return {
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "hist": float(hist.iloc[-1]),
        "signal": signal,
    }


def calc_rsi(kline: pd.DataFrame | None, period: int = 14) -> float | None:
    """RSI（Wilder 平滑，alpha = 1/period）；kline 为 None 或不足 period+1 行 → None。"""
    if kline is None or len(kline) < period + 1:
        return None

    close = kline["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def calc_price_position(kline: pd.DataFrame | None) -> float | None:
    """最新收盘在窗口 [最低, 最高] 区间中的分位（0-100，越低越接近低位）。

    使用 K 线帧全部行（本项目为近 kline_days 根，默认 120）；行数不足 2 或
    区间为 0（全平）→ None。
    """
    if kline is None or len(kline) < 2:
        return None
    closes = pd.to_numeric(kline["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return None
    lowest, highest = float(closes.min()), float(closes.max())
    if highest == lowest:
        return None
    position = (float(closes.iloc[-1]) - lowest) / (highest - lowest) * 100
    return min(100.0, max(0.0, position))


def calc_volatility(kline: pd.DataFrame | None, trading_days: int = 244) -> float | None:
    """收盘价日收益率标准差 × √trading_days（年化波动率，%）。

    不足 30 行有效收盘价 → None。
    """
    if kline is None or len(kline) < 30:
        return None
    closes = pd.to_numeric(kline["close"], errors="coerce").dropna()
    if len(closes) < 30:
        return None
    returns = closes.pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.std(ddof=1) * math.sqrt(trading_days) * 100.0)


def calc_turnover_amount(kline: pd.DataFrame | None, days: int = 60) -> float | None:
    """近 ``days`` 日成交额中位数（万元；成交量按"手"×100×收盘价估算）。

    无 ``volume`` 列或有效行不足 5 → None。
    """
    if kline is None or kline.empty or "volume" not in kline.columns:
        return None
    window = kline.tail(days)
    close = pd.to_numeric(window["close"], errors="coerce")
    volume = pd.to_numeric(window["volume"], errors="coerce")
    amounts = (close * volume * 100.0).dropna()  # 手 → 股，乘以价格得元
    if len(amounts) < 5:
        return None
    return float(amounts.median()) / 10000.0


# ---------------------------------------------------------------------------
# 基金指标（基金历史帧规范列：date（datetime64，升序）、close（float）；
# 指数 PE 帧规范列：date、pe（float，升序）；基金分红帧仅需 date 列）
# ---------------------------------------------------------------------------


def calc_fund_discount(price: float | None, nav: float | None) -> float | None:
    """场内价格相对净值的折溢价率（%）= (price - nav) / nav × 100；负值=折价。

    price 或 nav 缺失、nav 为 0 → None。
    """
    if price is None or nav is None or nav == 0:
        return None
    return (price - nav) / nav * 100.0


def calc_nav_trend(history: pd.DataFrame | None, days: int = 20) -> float | None:
    """近 days 期净值变化率（%）= (最新净值 - days 期前净值) / days 期前净值 × 100。

    history 为 None 或不足 days+1 行 → None；基准净值为 0 → None。
    """
    if history is None or len(history) < days + 1:
        return None
    closes = history["close"]
    base = float(closes.iloc[-(days + 1)])
    if base == 0:
        return None
    return (float(closes.iloc[-1]) - base) / base * 100.0


def calc_index_pe_position(
    pe_history: pd.DataFrame | None, column: str = "pe"
) -> float | None:
    """指数最新 PE 在历史区间内的近似分位（%）= (last - min) / (max - min) × 100。

    pe_history 为 None、不足 2 行或 max == min → None；结果截断到 0-100。
    """
    if pe_history is None or len(pe_history) < 2:
        return None
    values = pe_history[column].astype(float)
    lowest = float(values.min())
    highest = float(values.max())
    if highest == lowest:
        return None
    position = (float(values.iloc[-1]) - lowest) / (highest - lowest) * 100.0
    return min(100.0, max(0.0, position))


def calc_dividend_frequency(
    dividend_data: pd.DataFrame | None,
    as_of: date,
    window_days: int = 365,
) -> int | None:
    """近 window_days 天的分红次数。

    None（获取失败）→ None；空帧（无分红）→ 0。
    """
    if dividend_data is None:
        return None
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=window_days)
    return int((dividend_data["date"] > cutoff).sum())


def calc_fund_size(scale: str | None) -> float | None:
    """解析净资产规模字符串为亿元数值。

    ``"222.76亿元（截止至：2026年06月30日）"`` → 222.76；
    ``"5000万元（…）"`` → 0.5；None、"---" 或无「数字+亿/万」单位 → None。
    """
    if scale is None:
        return None
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(亿|万)", str(scale))
    if match is None:
        return None
    amount = float(match.group(1))
    return amount / 10000.0 if match.group(2) == "万" else amount
