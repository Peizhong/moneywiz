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
    calc_rsi,
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
