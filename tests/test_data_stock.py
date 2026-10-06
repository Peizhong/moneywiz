"""src.data 股票数据层（行情 / K 线 / 分红）单元测试。

全部通过 monkeypatch akshare 函数返回真实列名的原始帧，不联网。
"""

import logging
from datetime import date

import akshare as ak
import pandas as pd
import pytest

from src.data import get_dividend_history, get_kline, get_stock_spot

AS_OF = date(2026, 10, 6)


def _spot_frame():
    """stock_zh_a_spot_em() 原始列名的最小帧："-" 与 None 均为缺失。"""
    return pd.DataFrame(
        {
            "代码": ["600036", "000001"],
            "名称": ["招商银行", "平安银行"],
            "最新价": [40.0, 11.5],
            "市盈率-动态": ["-", 5.2],
            "市净率": [None, 0.7],
        }
    )


def _kline_frame(entries):
    """stock_zh_a_hist() 原始列名的帧；entries 为 (日期字符串, 收盘) 序列。"""
    count = len(entries)
    closes = [close for _, close in entries]
    return pd.DataFrame(
        {
            "日期": pd.to_datetime([day for day, _ in entries]),
            "股票代码": ["600036"] * count,
            "开盘": closes,
            "收盘": closes,
            "最高": closes,
            "最低": closes,
            "成交量": [1000] * count,
            "成交额": [10000.0] * count,
            "振幅": [1.0] * count,
            "涨跌幅": [1.0] * count,
            "涨跌额": [0.1] * count,
            "换手率": [0.5] * count,
        }
    )


def _dividend_frame():
    """stock_history_dividend_detail() 原始列名的帧。

    含一条 ``进度="预案"`` 与一条除权除息日 NaT 的记录，均应剔除；
    ``派息`` 单位为元/10 股（10.03 → 每股 1.003）。
    """
    return pd.DataFrame(
        {
            "公告日期": pd.to_datetime(
                ["2025-05-30", "2026-03-20", "2026-06-01", "2026-07-01"]
            ),
            "送股": [0, 0, 0, 0],
            "转增": [0, 0, 0, 0],
            "派息": [9.0, 10.03, 5.0, 8.0],
            "进度": ["实施", "实施", "预案", "实施"],
            "除权除息日": pd.to_datetime(
                ["2025-06-20", "2026-07-10", "2026-08-01", pd.NaT]
            ),
            "股权登记日": pd.to_datetime(
                ["2025-06-19", "2026-07-09", "2026-07-31", "2026-07-08"]
            ),
            "红股上市日": pd.to_datetime([pd.NaT] * 4),
        }
    )


# ---------------------------------------------------------------------------
# get_stock_spot
# ---------------------------------------------------------------------------


def test_spot_normalizes_columns_and_missing_values(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: _spot_frame())

    out = get_stock_spot()

    assert list(out.columns) == ["code", "name", "price", "pe", "pb"]
    assert out.loc[0, "code"] == "600036"
    assert out.loc[1, "code"] == "000001"  # 代码保持字符串，不丢前导零
    assert out.loc[0, "name"] == "招商银行"
    assert out.loc[0, "price"] == pytest.approx(40.0)
    assert pd.isna(out.loc[0, "pe"])  # "-" → 缺失
    assert pd.isna(out.loc[0, "pb"])  # None → 缺失
    assert out.loc[1, "pe"] == pytest.approx(5.2)
    assert out.loc[1, "pb"] == pytest.approx(0.7)


def test_spot_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: pd.DataFrame())

    out = get_stock_spot()

    assert out is not None and out.empty
    assert list(out.columns) == ["code", "name", "price", "pe", "pb"]


# ---------------------------------------------------------------------------
# get_kline
# ---------------------------------------------------------------------------


def test_kline_requests_qfq_over_two_times_window(monkeypatch):
    calls = []

    def stock_zh_a_hist(**kwargs):
        calls.append(kwargs)
        return _kline_frame([("2026-10-06", 39.8)])

    monkeypatch.setattr(ak, "stock_zh_a_hist", stock_zh_a_hist)

    out = get_kline("600036", days=120, as_of=AS_OF)

    assert calls == [
        {
            "symbol": "600036",
            "period": "daily",
            "start_date": "20260208",  # 2026-10-06 − 2×120 自然日
            "end_date": "20261006",
            "adjust": "qfq",
        }
    ]
    assert list(out.columns) == ["date", "close"]
    assert out["close"].tolist() == [39.8]


def test_kline_takes_last_days_and_drops_nan_close(monkeypatch):
    raw = _kline_frame(
        [
            ("2026-09-25", 39.0),
            ("2026-09-26", 39.2),
            ("2026-09-29", 39.3),
            ("2026-09-30", float("nan")),
            ("2026-10-06", 39.8),
        ]
    )
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kwargs: raw)

    out = get_kline("600036", days=3, as_of=AS_OF)

    assert list(out.columns) == ["date", "close"]
    assert out["date"].tolist() == list(pd.to_datetime(["2026-09-29", "2026-10-06"]))
    assert out["close"].tolist() == [39.3, 39.8]
    assert out["close"].notna().all()  # indicators 不应看到 NaN


def test_kline_output_is_ascending_by_date(monkeypatch):
    raw = _kline_frame(
        [("2026-10-06", 39.8), ("2026-09-30", 39.3), ("2026-09-29", 39.1)]
    )
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kwargs: raw)

    out = get_kline("600036", days=2, as_of=AS_OF)

    assert out["date"].is_monotonic_increasing
    assert out["close"].tolist() == [39.3, 39.8]  # 末 days 行 = 最近两个交易日


def test_kline_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kwargs: pd.DataFrame())

    out = get_kline("600036", as_of=AS_OF)

    assert out is not None and out.empty
    assert list(out.columns) == ["date", "close"]


# ---------------------------------------------------------------------------
# get_dividend_history
# ---------------------------------------------------------------------------


def test_dividend_history_filters_and_converts_per_10_shares(monkeypatch):
    calls = []

    def stock_history_dividend_detail(**kwargs):
        calls.append(kwargs)
        return _dividend_frame()

    monkeypatch.setattr(ak, "stock_history_dividend_detail", stock_history_dividend_detail)

    out = get_dividend_history("600036")

    assert calls == [{"symbol": "600036", "indicator": "分红"}]
    assert list(out.columns) == ["date", "dividend_per_share"]
    # 预案 与 NaT 除权除息日两条被剔除，其余按日期升序
    assert out["date"].tolist() == list(pd.to_datetime(["2025-06-20", "2026-07-10"]))
    assert out["dividend_per_share"].tolist() == pytest.approx([0.9, 1.003])


def test_dividend_history_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(
        ak, "stock_history_dividend_detail", lambda **kwargs: pd.DataFrame()
    )

    out = get_dividend_history("600036")

    assert out is not None and out.empty
    assert list(out.columns) == ["date", "dividend_per_share"]


# ---------------------------------------------------------------------------
# 失败重试
# ---------------------------------------------------------------------------


def test_call_retries_once_then_succeeds(monkeypatch):
    attempts = []

    def stock_zh_a_spot_em():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("远端断开")
        return _spot_frame()

    monkeypatch.setattr(ak, "stock_zh_a_spot_em", stock_zh_a_spot_em)

    out = get_stock_spot()

    assert len(attempts) == 2  # 失败后恰好重试 1 次
    assert out.loc[0, "code"] == "600036"


@pytest.mark.parametrize(
    ("ak_name", "fetch"),
    [
        ("stock_zh_a_spot_em", lambda: get_stock_spot()),
        ("stock_zh_a_hist", lambda: get_kline("600036", as_of=AS_OF)),
        ("stock_history_dividend_detail", lambda: get_dividend_history("600036")),
    ],
)
def test_failure_returns_none_after_retry_and_logs_akshare_name(
    monkeypatch, caplog, ak_name, fetch
):
    attempts = []

    def fake(*args, **kwargs):
        attempts.append(1)
        raise ConnectionError("网络超时")

    fake.__name__ = ak_name  # 仿冒真名，断言 warning 里出现的是 akshare 函数名
    monkeypatch.setattr(ak, ak_name, fake)

    with caplog.at_level(logging.WARNING):
        out = fetch()

    assert out is None
    assert len(attempts) == 2  # 重试 1 次
    assert ak_name in caplog.text  # warning 含 akshare 函数名
