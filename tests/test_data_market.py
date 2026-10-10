"""src.data 市场温度计数据（指数股息率 / 10Y 国债收益率）单元测试。

全部 monkeypatch akshare 函数返回真实列名的原始帧，不联网。
"""

import akshare as ak
import pandas as pd
import pytest

from src import data
from src.data import (
    get_10y_bond_yield,
    get_index_constituents,
    get_index_dividend_yield,
    get_index_pe_history,
)


def _index_value_frame():
    """stock_zh_index_value_csindex 原始列名的最小帧（新在前，含一条 NaN 股息率）。"""
    return pd.DataFrame(
        {
            "日期": ["2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01"],
            "指数代码": ["922"] * 4,
            "股息率1": [4.14, float("nan"), 4.20, 4.05],
            "股息率2": [4.12, 4.10, 4.18, 4.03],
            "市盈率1": [8.86] * 4,
            "市盈率2": [11.08] * 4,
        }
    )


def _bond_frame():
    """bond_zh_us_rate 原始列名的最小帧（升序，末尾一天为 NaN）。"""
    return pd.DataFrame(
        {
            "日期": pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01"]),
            "中国国债收益率10年": [1.83, 1.82, float("nan")],
        }
    )


def test_index_dividend_yield_takes_latest_valid(monkeypatch):
    calls = []

    def fake(symbol):
        calls.append(symbol)
        return _index_value_frame()

    monkeypatch.setattr(ak, "stock_zh_index_value_csindex", fake)

    assert get_index_dividend_yield("000922") == pytest.approx(4.14)  # 最新且非 NaN
    assert calls == ["000922"]


def test_index_dividend_yield_failure_returns_none(monkeypatch):
    def boom(symbol):
        raise ConnectionError("网络超时")

    monkeypatch.setattr(ak, "stock_zh_index_value_csindex", boom)

    assert get_index_dividend_yield("000922") is None


def test_bond_yield_takes_last_valid_and_requests_recent_window(monkeypatch):
    calls = {}

    def fake(start_date):
        calls["start_date"] = start_date
        return _bond_frame()

    monkeypatch.setattr(ak, "bond_zh_us_rate", fake)

    assert get_10y_bond_yield() == pytest.approx(1.82)
    assert len(calls["start_date"]) == 8  # 仅请求近期区间（YYYYMMDD），避免全量拉取


def test_bond_yield_failure_returns_none(monkeypatch):
    def boom(start_date):
        raise ConnectionError("网络超时")

    monkeypatch.setattr(ak, "bond_zh_us_rate", boom)

    assert get_10y_bond_yield() is None


def _constituents_frame():
    """index_stock_cons_csindex 原始列名的最小帧（含一条重复代码）。"""
    return pd.DataFrame(
        {
            "日期": ["2026-09-30"] * 3,
            "指数代码": ["000015"] * 3,
            "成分券代码": ["601088", "600015", "601088"],
            "成分券名称": ["中国神华", "华夏银行", "中国神华"],
        }
    )


def test_index_constituents_dedup_and_sorted(monkeypatch):
    calls = []

    def fake(symbol):
        calls.append(symbol)
        return _constituents_frame()

    monkeypatch.setattr(ak, "index_stock_cons_csindex", fake)

    out = get_index_constituents("000015")

    assert list(out.columns) == ["code", "name"]
    assert out["code"].tolist() == ["600015", "601088"]  # 去重且按代码升序
    assert out["name"].tolist() == ["华夏银行", "中国神华"]
    assert calls == ["000015"]


def test_index_constituents_failure_returns_none(monkeypatch):
    def boom(symbol):
        raise ConnectionError("网络超时")

    monkeypatch.setattr(ak, "index_stock_cons_csindex", boom)

    assert get_index_constituents("000015") is None


# ---------------------------------------------------------------------------
# get_index_pe_history
# ---------------------------------------------------------------------------


def _index_pe_frame():
    """stock_index_pe_lg() 原始列名的帧；被测列为 ``滚动市盈率``（含一条 NaN）。"""
    return pd.DataFrame(
        {
            "日期": pd.to_datetime(
                ["2026-09-30", "2026-10-06", "2026-09-29", "2026-10-01"]
            ),
            "指数": ["上证红利"] * 4,
            "等权静态市盈率": [6.0, 6.1, 6.2, 6.3],
            "静态市盈率": [6.5, 6.6, 6.7, 6.8],
            "静态市盈率中位数": [6.4, 6.5, 6.6, 6.7],
            "等权滚动市盈率": [5.9, 6.0, 6.1, 6.2],
            "滚动市盈率": [7.1, float("nan"), 7.0, 7.2],
            "滚动市盈率中位数": [7.0, 7.1, 7.2, 7.3],
        }
    )


def test_index_pe_history_normalizes_and_drops_nan_pe(monkeypatch):
    calls = []

    def stock_index_pe_lg(**kwargs):
        calls.append(kwargs)
        return _index_pe_frame()

    monkeypatch.setattr(ak, "stock_index_pe_lg", stock_index_pe_lg)

    out = get_index_pe_history("上证红利")

    assert calls == [{"symbol": "上证红利"}]
    assert list(out.columns) == ["date", "pe"]
    assert out["date"].tolist() == list(
        pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01"])
    )  # 升序
    assert out["pe"].tolist() == pytest.approx([7.0, 7.1, 7.2])  # 取滚动市盈率
    assert out["pe"].notna().all()  # calc_index_pe_position 不应看到 NaN


def test_index_pe_history_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "stock_index_pe_lg", lambda **kwargs: pd.DataFrame())

    out = get_index_pe_history("上证红利")

    assert out is not None and out.empty
    assert list(out.columns) == ["date", "pe"]


# ---------------------------------------------------------------------------
# 取数缓存分档（configure_cache）：温度计三项都走日频档
# ---------------------------------------------------------------------------


def test_market_indicators_use_daily_tier(monkeypatch, tmp_path):
    """股息率/国债默认缓存（日频档）：每个交易日更新一次即可。

    若被误标为实时档（默认不缓存），下面的计数会是 2。
    """
    counts = {"yield": 0, "bond": 0}

    def fake_index_value(symbol):
        counts["yield"] += 1
        return _index_value_frame()

    def fake_bond(start_date):
        counts["bond"] += 1
        return _bond_frame()

    monkeypatch.setattr(ak, "stock_zh_index_value_csindex", fake_index_value)
    monkeypatch.setattr(ak, "bond_zh_us_rate", fake_bond)
    data.configure_cache(tmp_path)

    for _ in range(2):
        get_index_dividend_yield("000922")
        get_10y_bond_yield()

    assert counts == {"yield": 1, "bond": 1}
