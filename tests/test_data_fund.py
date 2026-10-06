"""src.data 基金数据层（行情 / 净值 / 分红 / 概况 / 指数 PE）单元测试。

全部通过 monkeypatch akshare 函数返回真实列名的原始帧，不联网。
"""

import logging
from datetime import datetime

import akshare as ak
import pandas as pd
import pytest

from src.data import (
    SUPPORTED_INDEX_PE,
    get_fund_dividend_history,
    get_fund_latest_nav,
    get_fund_nav_history,
    get_fund_overview,
    get_fund_quotes,
    get_index_pe_history,
    resolve_index_symbol,
)

ETF_CODE = "510880"
LOF_CODE = "161725"
NORMAL_CODE = "110022"


def _etf_quote_frame():
    """fund_etf_spot_em() 原始列名的帧；"-" 表示缺失。"""
    return pd.DataFrame(
        {
            "代码": ["510880", "510300"],
            "名称": ["红利ETF", "沪深300ETF"],
            "最新价": [3.05, "-"],
            "IOPV实时估值": ["-", 3.98],
            "涨跌幅": [0.5, 0.1],
            "基金折价率": [0.12, 0.05],
        }
    )


def _lof_quote_frame():
    """fund_lof_spot_em() 原始列名的帧：上游没有 IOPV 列。"""
    return pd.DataFrame(
        {
            "代码": ["161725"],
            "名称": ["白酒LOF"],
            "最新价": [0.88],
            "涨跌幅": [-0.3],
        }
    )


def _fund_hist_frame(entries):
    """fund_etf_hist_em()/fund_lof_hist_em() 原始列名的帧；entries 为 (日期, 收盘)。"""
    closes = [close for _, close in entries]
    return pd.DataFrame(
        {
            "日期": pd.to_datetime([day for day, _ in entries]),
            "开盘": closes,
            "收盘": closes,
            "最高": closes,
            "最低": closes,
            "成交量": [1000] * len(entries),
            "成交额": [10000.0] * len(entries),
            "振幅": [1.0] * len(entries),
            "涨跌幅": [1.0] * len(entries),
            "涨跌额": [0.1] * len(entries),
            "换手率": [0.5] * len(entries),
        }
    )


def _open_fund_nav_frame():
    """fund_open_fund_info_em(…, "单位净值走势") 原始列名的帧（含一条 NaN 净值）。"""
    return pd.DataFrame(
        {
            "净值日期": pd.to_datetime(
                ["2026-09-25", "2026-09-26", "2026-09-29", "2026-09-30", "2026-10-06"]
            ),
            "单位净值": [1.100, 1.120, 1.130, float("nan"), 1.250],
            "日增长率": [0.1, 0.2, 0.3, 0.0, 0.4],
        }
    )


def _fund_dividend_frame():
    """fund_open_fund_info_em(…, "分红送配详情") 原始列名的帧。

    ``每10份分红`` 为人类可读字符串（本层不解析）；含一条除息日 NaT 的记录
    （未实施，应剔除）。
    """
    return pd.DataFrame(
        {
            "年份": ["2026", "2025", "2026", "2025"],
            "权益登记日": ["2026-06-10", "2025-11-14", "2026-09-01", "2025-12-11"],
            "除息日": pd.to_datetime(
                ["2026-06-11", "2025-11-17", pd.NaT, "2025-12-12"]
            ),
            "每10份分红": [
                "每10份派现金0.0100元",
                "每10份派现金0.0050元",
                "每10份派现金0.0200元",
                "每10份派现金0.0050元",
            ],
            "分红发放日": ["2026-06-13", "2025-11-19", "2026-09-03", "2025-12-16"],
        }
    )


def _overview_frame(
    scale="222.76亿元（截止至：2026年06月30日）", tracker="上证红利指数"
):
    """fund_overview_em() 原始列名的单行帧；scale/tracker 可替换为坏值。"""
    return pd.DataFrame(
        {
            "基金代码": ["510880"],
            "基金简称": ["红利ETF"],
            "净资产规模": [scale],
            "跟踪标的": [tracker],
        }
    )


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


# ---------------------------------------------------------------------------
# get_fund_quotes
# ---------------------------------------------------------------------------


def test_etf_quotes_normalize_columns_and_missing_iopv(monkeypatch):
    calls = []

    def fund_etf_spot_em():
        calls.append(1)
        return _etf_quote_frame()

    monkeypatch.setattr(ak, "fund_etf_spot_em", fund_etf_spot_em)

    out = get_fund_quotes("etf")

    assert calls == [1]
    assert list(out.columns) == ["code", "name", "price", "iopv"]
    assert out["code"].tolist() == ["510880", "510300"]  # 代码保持字符串
    assert out["name"].tolist() == ["红利ETF", "沪深300ETF"]
    assert out["price"].tolist()[0] == pytest.approx(3.05)
    assert pd.isna(out["price"].tolist()[1])  # "-" → 缺失
    assert pd.isna(out["iopv"].tolist()[0])  # IOPV "-" → 缺失
    assert out["iopv"].tolist()[1] == pytest.approx(3.98)


def test_lof_quotes_fill_absent_iopv_with_missing(monkeypatch):
    monkeypatch.setattr(ak, "fund_lof_spot_em", lambda: _lof_quote_frame())

    out = get_fund_quotes("lof")

    assert list(out.columns) == ["code", "name", "price", "iopv"]
    assert out["code"].tolist() == ["161725"]
    assert out["price"].tolist() == pytest.approx([0.88])
    assert out["iopv"].isna().all()  # LOF 上游无 IOPV 列 → 全缺失


def test_normal_quotes_has_no_source_and_returns_none(monkeypatch):
    calls = []

    def fake():
        calls.append(1)
        return _etf_quote_frame()

    monkeypatch.setattr(ak, "fund_etf_spot_em", fake)
    monkeypatch.setattr(ak, "fund_lof_spot_em", fake)

    assert get_fund_quotes("normal") is None
    assert calls == []  # normal 无行情表，不应请求任何行情接口


def test_quotes_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "fund_etf_spot_em", lambda: pd.DataFrame())

    out = get_fund_quotes("etf")

    assert out is not None and out.empty
    assert list(out.columns) == ["code", "name", "price", "iopv"]


# ---------------------------------------------------------------------------
# get_fund_nav_history
# ---------------------------------------------------------------------------


def test_etf_nav_history_normalizes_columns_window_and_order(monkeypatch):
    calls = []

    def fund_etf_hist_em(**kwargs):
        calls.append(kwargs)
        return _fund_hist_frame(
            [("2026-10-06", 3.30), ("2026-09-30", 3.20), ("2026-10-07", 3.31)]
        )

    monkeypatch.setattr(ak, "fund_etf_hist_em", fund_etf_hist_em)

    out = get_fund_nav_history(ETF_CODE, "etf", days=120)

    assert len(calls) == 1
    assert calls[0]["symbol"] == ETF_CODE
    assert calls[0]["period"] == "daily"
    assert calls[0]["adjust"] == ""  # akshare 的默认复权方式
    start = datetime.strptime(calls[0]["start_date"], "%Y%m%d")
    end = datetime.strptime(calls[0]["end_date"], "%Y%m%d")
    assert (end - start).days == 240  # 与 get_kline 相同的 2×days 自然日窗口
    assert list(out.columns) == ["date", "close"]
    assert out["date"].tolist() == list(
        pd.to_datetime(["2026-09-30", "2026-10-06", "2026-10-07"])
    )  # 升序
    assert out["close"].tolist() == pytest.approx([3.20, 3.30, 3.31])


def test_lof_nav_history_normalizes_columns(monkeypatch):
    calls = []

    def fund_lof_hist_em(**kwargs):
        calls.append(kwargs)
        return _fund_hist_frame([("2026-10-06", 0.88)])

    monkeypatch.setattr(ak, "fund_lof_hist_em", fund_lof_hist_em)

    out = get_fund_nav_history(LOF_CODE, "lof")

    assert calls[0]["symbol"] == LOF_CODE
    assert calls[0]["period"] == "daily"
    assert list(out.columns) == ["date", "close"]
    assert out["close"].tolist() == pytest.approx([0.88])


def test_normal_nav_history_uses_open_fund_nav_trend(monkeypatch):
    calls = []

    def fund_open_fund_info_em(**kwargs):
        calls.append(kwargs)
        return _open_fund_nav_frame()

    monkeypatch.setattr(ak, "fund_open_fund_info_em", fund_open_fund_info_em)

    out = get_fund_nav_history(NORMAL_CODE, "normal", days=3)

    assert calls == [{"symbol": NORMAL_CODE, "indicator": "单位净值走势"}]
    assert list(out.columns) == ["date", "close"]
    # 末 3 行取窗口（09-29/09-30/10-06），其中 NaN 净值剔除；更早的 09-26 不参与
    assert out["date"].tolist() == list(
        pd.to_datetime(["2026-09-29", "2026-10-06"])
    )
    assert out["close"].tolist() == pytest.approx([1.130, 1.250])
    assert out["close"].notna().all()  # indicators 不应看到 NaN


def test_nav_history_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "fund_etf_hist_em", lambda **kwargs: pd.DataFrame())

    out = get_fund_nav_history(ETF_CODE, "etf")

    assert out is not None and out.empty
    assert list(out.columns) == ["date", "close"]


# ---------------------------------------------------------------------------
# get_fund_latest_nav
# ---------------------------------------------------------------------------


def test_latest_nav_returns_last_row_value(monkeypatch):
    calls = []

    def fund_open_fund_info_em(**kwargs):
        calls.append(kwargs)
        return _open_fund_nav_frame()

    monkeypatch.setattr(ak, "fund_open_fund_info_em", fund_open_fund_info_em)

    out = get_fund_latest_nav(NORMAL_CODE)

    assert calls == [{"symbol": NORMAL_CODE, "indicator": "单位净值走势"}]
    assert out == pytest.approx(1.25)  # 末行单位净值
    assert isinstance(out, float)


def test_latest_nav_with_nan_or_empty_source_returns_none(monkeypatch):
    frame = pd.DataFrame(
        {"净值日期": pd.to_datetime(["2026-10-06"]), "单位净值": [float("nan")]}
    )
    monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kwargs: frame)

    assert get_fund_latest_nav(NORMAL_CODE) is None

    monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kwargs: pd.DataFrame())

    assert get_fund_latest_nav(NORMAL_CODE) is None


# ---------------------------------------------------------------------------
# get_fund_dividend_history
# ---------------------------------------------------------------------------


def test_dividend_history_takes_ex_dividend_date_only(monkeypatch):
    calls = []

    def fund_open_fund_info_em(**kwargs):
        calls.append(kwargs)
        return _fund_dividend_frame()

    monkeypatch.setattr(ak, "fund_open_fund_info_em", fund_open_fund_info_em)

    out = get_fund_dividend_history(ETF_CODE)

    assert calls == [{"symbol": ETF_CODE, "indicator": "分红送配详情"}]
    assert list(out.columns) == ["date"]  # 只取除息日，不解析每10份分红
    assert out["date"].tolist() == list(
        pd.to_datetime(["2025-11-17", "2025-12-12", "2026-06-11"])
    )  # 除息日 NaT 剔除，其余升序


def test_dividend_history_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kwargs: pd.DataFrame())

    out = get_fund_dividend_history(ETF_CODE)

    assert out is not None and out.empty
    assert list(out.columns) == ["date"]


# ---------------------------------------------------------------------------
# get_fund_overview
# ---------------------------------------------------------------------------


def test_overview_returns_raw_scale_and_tracker(monkeypatch):
    calls = []

    def fund_overview_em(**kwargs):
        calls.append(kwargs)
        return _overview_frame()

    monkeypatch.setattr(ak, "fund_overview_em", fund_overview_em)

    out = get_fund_overview(ETF_CODE)

    assert calls == [{"symbol": ETF_CODE}]
    assert out == {
        "scale": "222.76亿元（截止至：2026年06月30日）",
        "tracker": "上证红利指数",
    }


def test_overview_missing_scale_is_none_and_tracker_kept_raw(monkeypatch):
    frame = _overview_frame(scale=float("nan"), tracker="该基金无跟踪标的")
    monkeypatch.setattr(ak, "fund_overview_em", lambda **kwargs: frame)

    out = get_fund_overview(ETF_CODE)

    # 原始串原样返回，是否可用于指数 PE 由 resolve_index_symbol 判定
    assert out == {"scale": None, "tracker": "该基金无跟踪标的"}


def test_overview_empty_source_returns_none(monkeypatch):
    monkeypatch.setattr(ak, "fund_overview_em", lambda **kwargs: pd.DataFrame())

    assert get_fund_overview(ETF_CODE) is None


# ---------------------------------------------------------------------------
# resolve_index_symbol
# ---------------------------------------------------------------------------


def test_supported_index_pe_is_the_stock_index_pe_lg_set():
    assert SUPPORTED_INDEX_PE == {
        "上证50",
        "沪深300",
        "上证380",
        "创业板50",
        "中证500",
        "上证180",
        "深证红利",
        "深证100",
        "中证1000",
        "上证红利",
        "中证100",
        "中证800",
    }


@pytest.mark.parametrize(
    ("configured", "tracker", "expected", "warns"),
    [
        (None, "上证红利指数", "上证红利", False),  # 去掉 "指数" 后缀
        (None, "上证红利全收益", "上证红利", False),  # 去掉 "全收益" 后缀
        (None, "该基金无跟踪标的", None, True),  # 无跟踪标的
        (None, "中证红利指数", None, True),  # 归一后仍不在支持集
        ("沪深300", None, "沪深300", False),  # configured 优先
        ("中证红利", None, None, True),  # 配置了不支持项也告警
    ],
    ids=[
        "tracker-with-index-suffix",
        "tracker-with-total-return-suffix",
        "no-tracker",
        "unsupported-tracker",
        "configured-supported",
        "configured-unsupported",
    ],
)
def test_resolve_index_symbol(
    monkeypatch, caplog, configured, tracker, expected, warns
):
    with caplog.at_level(logging.WARNING):
        out = resolve_index_symbol(configured, tracker)

    assert out == expected
    assert bool(caplog.records) is warns  # 成功解析必须零告警
    if warns:
        assert (configured or tracker) in caplog.text  # 告警含原始取值，便于定位配置


def test_resolve_index_symbol_without_any_hint_warns_and_returns_none(caplog):
    with caplog.at_level(logging.WARNING):
        out = resolve_index_symbol(None, None)

    assert out is None
    assert caplog.records


# ---------------------------------------------------------------------------
# get_index_pe_history
# ---------------------------------------------------------------------------


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
# 失败重试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ak_name", "fetch"),
    [
        ("fund_etf_spot_em", lambda: get_fund_quotes("etf")),
        ("fund_lof_spot_em", lambda: get_fund_quotes("lof")),
        ("fund_etf_hist_em", lambda: get_fund_nav_history(ETF_CODE, "etf")),
        ("fund_lof_hist_em", lambda: get_fund_nav_history(LOF_CODE, "lof")),
        ("fund_open_fund_info_em", lambda: get_fund_nav_history(NORMAL_CODE, "normal")),
        ("fund_open_fund_info_em", lambda: get_fund_latest_nav(NORMAL_CODE)),
        ("fund_open_fund_info_em", lambda: get_fund_dividend_history(ETF_CODE)),
        ("fund_overview_em", lambda: get_fund_overview(ETF_CODE)),
        ("stock_index_pe_lg", lambda: get_index_pe_history("上证红利")),
    ],
    ids=[
        "etf-quotes",
        "lof-quotes",
        "etf-nav",
        "lof-nav",
        "normal-nav",
        "latest-nav",
        "dividend",
        "overview",
        "index-pe",
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
