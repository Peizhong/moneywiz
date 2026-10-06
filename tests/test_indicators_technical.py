"""src.indicators 技术指标函数（MA60/动量/MACD/RSI）的单元测试。

全部使用本地构造的 K 线帧，不联网、不读文件。
K 线帧规范列：``date``（datetime64，升序）、``close``（float）。
"""

import pandas as pd
import pytest

from src.indicators import (
    calc_ma,
    calc_ma60_position,
    calc_macd,
    calc_momentum_5d,
    calc_price_position,
    calc_rsi,
    calc_turnover_amount,
    calc_max_drawdown,
    calc_volatility,
)


def _kline_frame(closes):
    """按收盘价序列构造规范 K 线帧（日期升序，仅含 date/close 两列）。"""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(closes), freq="D"),
            "close": [float(close) for close in closes],
        }
    )


def test_ma60_uses_last_60_closes():
    frame = _kline_frame([10.0] * 59 + [11.0])
    ma = (59 * 10.0 + 11.0) / 60

    assert calc_ma(frame) == pytest.approx(ma)
    assert calc_ma60_position(11.0, ma) == pytest.approx((11.0 - ma) / ma * 100.0)


def test_ma_insufficient_rows_returns_none():
    assert calc_ma(_kline_frame([10.0] * 59)) is None


def test_ma_none_returns_none():
    assert calc_ma(None) is None


@pytest.mark.parametrize(
    ("close", "ma60"), [(None, 10.0), (10.0, None), (10.0, 0.0)]
)
def test_ma60_position_invalid_inputs_return_none(close, ma60):
    assert calc_ma60_position(close, ma60) is None


def test_momentum_5d_uses_sixth_last_close_as_base():
    frame = _kline_frame([10.0, 10.5, 10.2, 10.8, 10.3, 11.0])

    # (11.0 - 10.0) / 10.0 × 100 = 10.0
    assert calc_momentum_5d(frame) == pytest.approx(10.0)


def test_momentum_5d_insufficient_rows_returns_none():
    assert calc_momentum_5d(_kline_frame([10.0] * 5)) is None


def test_momentum_5d_none_returns_none():
    assert calc_momentum_5d(None) is None


def test_macd_golden_cross_after_flat_then_jump():
    result = calc_macd(_kline_frame([10.0] * 30 + [20.0]))

    assert result["signal"] == "金叉"
    assert result["dif"] > result["dea"] > 0
    assert result["hist"] == pytest.approx(2.0 * (result["dif"] - result["dea"]))


def test_macd_death_cross_after_flat_then_drop():
    result = calc_macd(_kline_frame([10.0] * 30 + [5.0]))

    assert result["signal"] == "死叉"


def test_macd_insufficient_rows_returns_none():
    assert calc_macd(_kline_frame([10.0] * 25)) is None


def test_macd_none_returns_none():
    assert calc_macd(None) is None


def test_rsi_monotonic_up_is_100():
    frame = _kline_frame([10.0 + offset for offset in range(15)])

    assert calc_rsi(frame) == pytest.approx(100.0)


def test_rsi_monotonic_down_is_0():
    frame = _kline_frame([24.0 - offset for offset in range(15)])

    assert calc_rsi(frame) == pytest.approx(0.0)


def test_rsi_mixed_series_between_bounds():
    frame = _kline_frame(
        [
            10.0,
            11.0,
            10.5,
            12.0,
            11.5,
            13.0,
            12.5,
            14.0,
            13.5,
            15.0,
            14.5,
            16.0,
            15.5,
            17.0,
            16.5,
            18.0,
        ]
    )

    assert 0.0 < calc_rsi(frame) < 100.0


def test_rsi_insufficient_rows_returns_none():
    assert calc_rsi(_kline_frame([10.0] * 14)) is None


def test_rsi_none_returns_none():
    assert calc_rsi(None) is None


def test_price_position_is_percentile_of_last_close_in_window():
    # 区间 [10, 20]，最新 15 → 50%
    assert calc_price_position(_kline_frame([10.0, 20.0, 15.0])) == pytest.approx(50.0)
    # 最新价即区间最低 / 最高
    assert calc_price_position(_kline_frame([20.0, 10.0])) == pytest.approx(0.0)
    assert calc_price_position(_kline_frame([10.0, 20.0])) == pytest.approx(100.0)


def test_price_position_invalid_inputs_return_none():
    assert calc_price_position(None) is None
    assert calc_price_position(_kline_frame([10.0])) is None  # 不足 2 行
    assert calc_price_position(_kline_frame([10.0, 10.0, 10.0])) is None  # 全平


def test_volatility_of_flat_series_is_zero():
    frame = _kline_frame([10.0] * 40)
    assert calc_volatility(frame) == pytest.approx(0.0)


def test_volatility_is_annualized_std_of_returns():
    # 收益率序列已知：+1% / -1% 交替 → 日标准差可复算，年化 × √244
    closes = [100.0]
    for index in range(59):
        closes.append(closes[-1] * (1.01 if index % 2 == 0 else 0.99))
    frame = _kline_frame(closes)
    returns = frame["close"].pct_change().dropna()
    expected = returns.std(ddof=1) * (244**0.5) * 100
    assert calc_volatility(frame) == pytest.approx(expected)


def test_volatility_insufficient_rows_return_none():
    assert calc_volatility(None) is None
    assert calc_volatility(_kline_frame([10.0] * 29)) is None


def test_max_drawdown_of_monotonic_rise_is_zero():
    frame = _kline_frame([10.0 + index * 0.1 for index in range(40)])

    assert calc_max_drawdown(frame) == pytest.approx(0.0)


def test_max_drawdown_measures_peak_to_trough():
    frame = _kline_frame([10.0] * 10 + [8.0] * 20 + [10.0] * 10)

    assert calc_max_drawdown(frame) == pytest.approx(20.0)


def test_max_drawdown_adds_back_dividend_drop_when_dividends_given():
    closes = [10.0] * 20 + [9.5] * 20  # 除息日一次性 -5%（每股派 0.5 元）
    frame = _kline_frame(closes)
    dividends = pd.DataFrame(
        {"date": [frame["date"].iloc[20]], "dividend_per_share": [0.5]}
    )

    assert calc_max_drawdown(frame) == pytest.approx(5.0)  # 纯价格口径
    assert calc_max_drawdown(frame, dividends) == pytest.approx(0.0)  # 总回报口径


def test_max_drawdown_none_or_short_returns_none():
    assert calc_max_drawdown(None) is None
    assert calc_max_drawdown(_kline_frame([10.0] * 29)) is None


def test_turnover_amount_is_median_volume_times_close_in_wan():
    # 成交量单位：手（×100 = 股）；成交额中位数换算成万元
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [10.0, 10.0, 10.0, 20.0, 10.0, 10.0],
            "volume": [1000, 2000, 3000, 1000, 5000, 6000],  # 手
        }
    )
    # 成交额（元）: 1e6, 2e6, 3e6, 2e6, 5e6, 6e6 → 中位数 (3e6+2e6)/2 = 2.5e6 → 250 万元
    assert calc_turnover_amount(frame) == pytest.approx(250.0)


def test_turnover_amount_missing_volume_returns_none():
    assert calc_turnover_amount(None) is None
    assert calc_turnover_amount(_kline_frame([10.0] * 60)) is None  # 无 volume 列
    small = pd.DataFrame(
        {"date": pd.date_range("2026-01-01", periods=3, freq="D"), "close": [1.0] * 3, "volume": [1] * 3}
    )
    assert calc_turnover_amount(small) is None  # 不足 5 行
