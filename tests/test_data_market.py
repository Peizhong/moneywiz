"""src.data 市场温度计数据（指数股息率 / 10Y 国债收益率）单元测试。

全部 monkeypatch akshare 函数返回真实列名的原始帧，不联网。
"""

import akshare as ak
import pandas as pd
import pytest

from src.data import (
    get_10y_bond_yield,
    get_index_constituents,
    get_index_dividend_yield,
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
