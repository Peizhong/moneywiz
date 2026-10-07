"""src.data 股票数据层（行情 / K 线 / 分红）单元测试。

全部通过 monkeypatch akshare 函数返回真实列名的原始帧，不联网。
"""

import json
import logging
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd
import pytest

from src import data
from src.cache import Cache
from src.data import (
    get_dividend_history,
    get_financial_health,
    get_kline,
    get_kline_raw,
    get_stock_spot,
    reset_quote_source_state,
)

AS_OF = date(2026, 10, 6)


def _spot_frame():
    """stock_zh_a_spot_em() 原始列名的最小帧："-" 与 None 均为缺失。

    东财的「总市值」单位为元（与腾讯的亿元不同），取数层负责换算。
    """
    return pd.DataFrame(
        {
            "代码": ["600036", "000001"],
            "名称": ["招商银行", "平安银行"],
            "最新价": [40.0, 11.5],
            "市盈率-动态": ["-", 5.2],
            "市净率": [None, 0.7],
            "总市值": ["-", 224526000000.0],
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

    out = get_stock_spot(["600036", "000001"])

    assert list(out.columns) == ["code", "name", "price", "pe", "pb", "market_cap"]
    assert out.loc[0, "code"] == "600036"
    assert out.loc[1, "code"] == "000001"  # 代码保持字符串，不丢前导零
    assert out.loc[0, "name"] == "招商银行"
    assert out.loc[0, "price"] == pytest.approx(40.0)
    assert pd.isna(out.loc[0, "pe"])  # "-" → 缺失
    assert pd.isna(out.loc[0, "pb"])  # None → 缺失
    assert out.loc[1, "pe"] == pytest.approx(5.2)
    assert out.loc[1, "pb"] == pytest.approx(0.7)


def test_spot_market_cap_converts_yuan_to_yi(monkeypatch):
    """东财「总市值」单位为元，统一换算成亿元（与腾讯口径一致）。"""
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: _spot_frame())

    out = get_stock_spot(["600036", "000001"]).set_index("code")

    assert out.loc["000001", "market_cap"] == pytest.approx(2245.26)
    assert pd.isna(out.loc["600036", "market_cap"])  # "-" → 缺失


def test_spot_empty_source_returns_empty_frame_with_columns(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: pd.DataFrame())

    out = get_stock_spot(["600036"])

    assert out is not None and out.empty
    assert list(out.columns) == ["code", "name", "price", "pe", "pb", "market_cap"]


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
    assert list(out.columns) == ["date", "close", "volume"]
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

    assert list(out.columns) == ["date", "close", "volume"]
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
    assert list(out.columns) == ["date", "close", "volume"]


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

    out = get_stock_spot(["600036"])

    assert len(attempts) == 2  # 失败后恰好重试 1 次
    assert out.loc[0, "code"] == "600036"


@pytest.mark.parametrize(
    ("ak_name", "fetch"),
    [
        ("stock_zh_a_spot_em", lambda: get_stock_spot(["600036"])),
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
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)  # 腾讯回退同样不可用

    with caplog.at_level(logging.WARNING):
        out = fetch()

    assert out is None
    assert len(attempts) == 2  # 重试 1 次
    assert ak_name in caplog.text  # warning 含 akshare 函数名


# ---------------------------------------------------------------------------
# 腾讯行情回退（东财 push2 对海外 IP 拒绝服务时的备用源）
# ---------------------------------------------------------------------------

# 2026-09-30 真实抓取的腾讯报文（GBK 编码）：沪 / 深 / 北交所各一条
QUOTES_TEXT = 'v_sh600036="1~招商银行~600036~41.26~40.51~40.59~699871~408796~291075~41.25~212~41.24~51~41.23~211~41.22~239~41.21~273~41.26~908~41.27~180~41.28~645~41.29~121~41.30~924~~20260930161447~0.75~1.85~41.38~40.52~41.26/699871/2878465122~699871~287847~0.34~6.86~~41.38~40.52~2.12~8511.50~10405.71~0.91~44.56~36.46~1.49~-1792~41.13~6.81~6.93~~~0.00~287846.5122~87.8838~213~   A~GP-A~2.93~1.08~4.89~11.28~1.11~41.83~34.28~0.39~0.98~12.90~20628944429~25219845601~-47.61~2.30~20628944429~~~7.46~-0.17~~CNY~0~___D__F__N~41.35~-3413~";\nv_sz000001="51~平安银行~000001~11.57~11.35~11.36~1045357~673452~371906~11.57~9160~11.56~1219~11.55~853~11.54~1249~11.53~782~11.58~1617~11.59~523~11.60~7361~11.61~4272~11.62~2196~~20260930161500~0.22~1.94~11.65~11.33~11.57/1045357/1205814858~1045357~120581~0.54~5.17~~11.65~11.33~2.82~2245.24~2245.26~0.48~12.49~10.22~1.27~-2706~11.53~4.37~5.27~~~0.18~120581.4858~102.6259~887~   A~GP-A~7.12~0.95~5.26~7.93~0.72~11.83~9.74~-0.01~-0.87~13.20~19405684991~19405918198~-9.26~6.23~19405684991~~~10.24~-0.17~~CNY~0~~11.65~-13858~";\nv_bj920002="62~万达轴承~920002~51.94~52.32~51.56~19680~9209~10472~51.81~1~51.50~11~51.25~10~51.10~2~51.06~2~51.94~13~51.95~5~51.96~15~52.00~48~52.02~9~~20260930153430~-0.38~-0.73~55.90~50.33~51.94/19680/103030092~19680~10303.01~4.41~51.55~~55.90~50.33~10.65~23.16~33.09~4.35~68.01~36.63~1.32~-64~52.35~46.70~52.79~~~~10303.0092~0.0000~0~ ~GP~-33.71~4.03~0.00~8.45~7.69~88.34~46.93~7.11~-1.80~-17.80~44597153~63704155~-55.17~-33.47~44597153~~~-30.88~-0.50~~CNY~1~NBFND~0.00~0";'

# 同日真实抓取的腾讯前复权日线（qfqday 行序：日期, 开, 收, 高, 低, 量）
KLINE_JSON = '{"code":0,"msg":"","data":{"sh600036":{"qfqday":[["2026-09-28","40.670","40.640","41.080","40.380","543918.000"],["2026-09-29","40.490","40.510","40.780","40.220","449346.000"],["2026-09-30","40.590","41.260","41.380","40.520","699871.000"]],"prec":"40.690","version":"18"}}}'

# 同日腾讯不复权日线（键为 day；行序同 qfqday，收盘价与复权口径不同）
RAW_KLINE_JSON = '{"code":0,"msg":"","data":{"sh600036":{"day":[["2026-09-28","40.980","40.950","41.380","40.690","543918.000"],["2026-09-29","40.800","40.820","41.090","40.530","449346.000"],["2026-09-30","40.900","41.570","41.690","40.830","699871.000"]]}}}'


class _FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _raise_connection_error(*args, **kwargs):
    raise ConnectionError("模拟网络不可用")


def _patch_http(monkeypatch, routes):
    """替换 requests.get：按 URL 关键字返回报文，并记录全部调用。"""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        for key, payload in routes.items():
            if key in url:
                return _FakeResponse(payload)
        raise AssertionError(f"未预期的请求：{url}")

    monkeypatch.setattr("src.data.requests.get", fake_get)
    return calls


def test_spot_falls_back_to_tencent_when_eastmoney_fails(monkeypatch, caplog):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", _raise_connection_error)
    calls = _patch_http(monkeypatch, {"qt.gtimg.cn": QUOTES_TEXT.encode("gbk")})

    with caplog.at_level(logging.WARNING):
        out = get_stock_spot(["600036", "000001", "920002"])

    assert list(out.columns) == ["code", "name", "price", "pe", "pb", "market_cap"]
    by_code = out.set_index("code")
    assert by_code.loc["600036", "name"] == "招商银行"
    assert by_code.loc["600036", "price"] == pytest.approx(41.26)
    assert by_code.loc["600036", "pe"] == pytest.approx(6.86)
    assert by_code.loc["600036", "pb"] == pytest.approx(0.91)
    # 腾讯总市值字段本身就是亿元，直接取值
    assert by_code.loc["600036", "market_cap"] == pytest.approx(10405.71)
    assert by_code.loc["000001", "pe"] == pytest.approx(5.17)  # 深市字段位置一致
    assert by_code.loc["000001", "pb"] == pytest.approx(0.48)
    assert by_code.loc["000001", "market_cap"] == pytest.approx(2245.26)
    assert by_code.loc["920002", "name"] == "万达轴承"  # 北交所（bj 前缀）
    assert by_code.loc["920002", "market_cap"] == pytest.approx(33.09)
    assert len(calls) == 1  # 一次批量请求
    assert "sh600036" in calls[0]["url"] and "sz000001" in calls[0]["url"]
    assert "回退腾讯" in caplog.text


def test_spot_primary_success_does_not_touch_tencent(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: _spot_frame())
    calls = []
    monkeypatch.setattr("src.data.requests.get", lambda *a, **k: calls.append(1))

    out = get_stock_spot(["600036"])

    assert list(out["code"]) == ["600036"]
    assert calls == []


def test_spot_skips_codes_with_unknown_prefix(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", _raise_connection_error)
    calls = _patch_http(monkeypatch, {"qt.gtimg.cn": QUOTES_TEXT.encode("gbk")})

    out = get_stock_spot(["200001", "600036"])  # 2 开头（B 股）不在支持范围 → 不请求也不出现

    assert list(out["code"]) == ["600036"]
    assert "200001" not in calls[0]["url"]


def test_spot_all_codes_unknown_returns_empty_frame_without_request(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", _raise_connection_error)
    calls = []
    monkeypatch.setattr("src.data.requests.get", lambda *a, **k: calls.append(1))

    out = get_stock_spot(["200001"])

    assert out is not None and out.empty
    assert list(out.columns) == ["code", "name", "price", "pe", "pb", "market_cap"]
    assert calls == []


def test_spot_ignores_legacy_cache_entry_without_market_cap(monkeypatch, tmp_path):
    """旧 schema（无 market_cap 列）的行情缓存不得复用——否则候选池会一只都排不出市值。"""
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: _spot_frame())
    data.configure_cache(tmp_path, ttl_hours=24)
    legacy = pd.DataFrame(
        {
            "code": ["600036"],
            "name": ["招商银行"],
            "price": [40.0],
            "pe": [6.86],
            "pb": [0.91],
        }
    )
    Cache(tmp_path / "market_cache.db").set("spot:600036", legacy)

    out = get_stock_spot(["600036"])

    assert list(out.columns) == ["code", "name", "price", "pe", "pb", "market_cap"]


def test_spot_both_sources_fail_returns_none(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", _raise_connection_error)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)

    assert get_stock_spot(["600036"]) is None


def test_kline_falls_back_to_tencent_qfq(monkeypatch, caplog):
    monkeypatch.setattr(ak, "stock_zh_a_hist", _raise_connection_error)
    calls = _patch_http(monkeypatch, {"fqkline": KLINE_JSON.encode("utf-8")})

    with caplog.at_level(logging.WARNING):
        out = get_kline("600036", days=120, as_of=AS_OF)

    assert list(out.columns) == ["date", "close", "volume"]
    assert out["close"].tolist() == pytest.approx([40.64, 40.51, 41.26])
    assert out["date"].is_monotonic_increasing
    assert calls[0]["params"]["param"] == "sh600036,day,,,120,qfq"
    assert "回退腾讯" in caplog.text


def test_kline_primary_success_does_not_touch_tencent(monkeypatch):
    raw = _kline_frame([("2026-09-30", 40.0)])
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kwargs: raw)
    calls = []
    monkeypatch.setattr("src.data.requests.get", lambda *a, **k: calls.append(1))

    out = get_kline("600036", as_of=AS_OF)

    assert not out.empty
    assert calls == []


def test_kline_both_sources_fail_returns_none(monkeypatch):
    monkeypatch.setattr(ak, "stock_zh_a_hist", _raise_connection_error)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)

    assert get_kline("600036", as_of=AS_OF) is None


# ---------------------------------------------------------------------------
# 通用 24 小时取数缓存（configure_cache）
# ---------------------------------------------------------------------------


def test_kline_cache_hit_skips_upstream(monkeypatch, tmp_path):
    calls = []

    def fake_hist(**kwargs):
        calls.append(1)
        return _kline_frame([("2026-09-30", 40.0)])

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    data.configure_cache(tmp_path, ttl_hours=24)

    first = get_kline("600036", as_of=AS_OF)
    second = get_kline("600036", as_of=AS_OF)

    assert len(calls) == 1  # 第二次命中缓存
    assert first.equals(second)


def test_cache_disabled_by_default_refetches(monkeypatch):
    calls = []

    def fake_hist(**kwargs):
        calls.append(1)
        return _kline_frame([("2026-09-30", 40.0)])

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)

    get_kline("600036", as_of=AS_OF)
    get_kline("600036", as_of=AS_OF)

    assert len(calls) == 2  # 未启用缓存 → 每次都请求


def test_failure_results_are_not_cached(monkeypatch, tmp_path):
    attempts = []

    def failing(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)
    data.configure_cache(tmp_path, ttl_hours=24)

    assert get_kline("600036", as_of=AS_OF) is None
    assert get_kline("600036", as_of=AS_OF) is None
    # 第一次调用失败后熔断生效，第二次直接跳过（attempts 不再增长）
    assert len(attempts) == 2
    # 但失败结果本身没有被当作"数据"缓存：该键不存在
    assert (
        Cache(tmp_path / "market_cache.db").get(
            f"kline:600036:120:{AS_OF.isoformat()}", ttl_seconds=24 * 3600
        )
        is None
    )


def test_expired_ttl_refetches(monkeypatch, tmp_path):
    calls = []

    def fake_hist(**kwargs):
        calls.append(1)
        return _kline_frame([("2026-09-30", 40.0)])

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    data.configure_cache(tmp_path, ttl_hours=0)  # 立即过期

    get_kline("600036", as_of=AS_OF)
    get_kline("600036", as_of=AS_OF)

    assert len(calls) == 2


def test_get_kline_raw_uses_tencent_without_adjust(monkeypatch):
    calls = _patch_http(monkeypatch, {"fqkline": RAW_KLINE_JSON.encode("utf-8")})

    frame = get_kline_raw("600036", days=790)

    assert list(frame["close"]) == pytest.approx([40.95, 40.82, 41.57])
    param = calls[0]["params"]["param"]
    assert param.startswith("sh600036,day,,,790")
    assert "qfq" not in param  # 不复权口径


def test_get_kline_raw_is_cached(monkeypatch, tmp_path):
    calls = _patch_http(monkeypatch, {"fqkline": RAW_KLINE_JSON.encode("utf-8")})
    data.configure_cache(tmp_path, ttl_hours=24)

    first = get_kline_raw("600036", days=790)
    second = get_kline_raw("600036", days=790)

    assert first is not None and second is not None
    assert len(calls) == 1  # 第二次命中缓存


def test_get_kline_raw_failure_returns_none(monkeypatch):
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)

    assert get_kline_raw("600036", days=790) is None


# 新浪日线报文（akshare stock_zh_a_daily 列）：成交量单位为"股"
SINA_KLINE = pd.DataFrame(
    {
        "date": pd.to_datetime(["2026-09-29", "2026-09-30"]),
        "close": [40.51, 41.26],
        "volume": [44934600.0, 69987100.0],
    }
)


def _patch_failing_hist(monkeypatch):
    def failing_hist(**kwargs):
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)


def test_get_kline_falls_back_to_sina_when_tencent_fails(monkeypatch):
    _patch_failing_hist(monkeypatch)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)  # 腾讯失败
    monkeypatch.setattr(
        ak, "stock_zh_a_daily", lambda symbol, adjust="", **kwargs: SINA_KLINE
    )

    frame = get_kline("600036", as_of=AS_OF)

    assert list(frame["close"]) == pytest.approx([40.51, 41.26])
    # 成交量由"股"折为"手"，与腾讯报文口径一致（44934600 股 → 449346 手）
    assert list(frame["volume"]) == pytest.approx([449346.0, 699871.0])


def test_get_kline_raw_falls_back_to_sina(monkeypatch):
    calls = []

    def fake_daily(symbol, adjust="", **kwargs):
        calls.append((symbol, adjust))
        return SINA_KLINE

    monkeypatch.setattr(ak, "stock_zh_a_daily", fake_daily)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)

    frame = get_kline_raw("600036", days=790)

    assert list(frame["close"]) == pytest.approx([40.51, 41.26])
    assert calls == [("sh600036", "")]  # 不复权口径


def test_tencent_kline_skipped_for_rest_of_run_after_failure(monkeypatch):
    attempts = []

    def failing_get(url, params=None, timeout=None):
        attempts.append(1)
        raise ConnectionError("腾讯不可用")

    _patch_failing_hist(monkeypatch)
    monkeypatch.setattr("src.data.requests.get", failing_get)
    monkeypatch.setattr(
        ak, "stock_zh_a_daily", lambda symbol, adjust="", **kwargs: SINA_KLINE
    )

    assert get_kline("600036", as_of=AS_OF) is not None  # 腾讯失败一次（含重试）
    assert get_kline("000001", as_of=AS_OF) is not None  # 第二只不再请求腾讯

    assert len(attempts) == 2  # 仅首只的 1 次 + 1 次重试


def test_reset_quote_source_state_re_enables_tencent_kline(monkeypatch):
    attempts = []

    def failing_get(url, params=None, timeout=None):
        attempts.append(1)
        raise ConnectionError("腾讯不可用")

    _patch_failing_hist(monkeypatch)
    monkeypatch.setattr("src.data.requests.get", failing_get)
    monkeypatch.setattr(
        ak, "stock_zh_a_daily", lambda symbol, adjust="", **kwargs: SINA_KLINE
    )

    get_kline("600036", as_of=AS_OF)
    reset_quote_source_state()  # 模拟下一次运行
    get_kline("600036", as_of=AS_OF)

    assert len(attempts) == 4  # 新运行重新尝试腾讯（各 2 次）


# ---------------------------------------------------------------------------
# 熔断：东财失败且回退成功后，本次运行内不再请求东财行情
# ---------------------------------------------------------------------------


def test_kline_after_successful_fallback_skips_eastmoney_for_rest_of_run(
    monkeypatch, caplog
):
    attempts = []

    def failing_hist(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _patch_http(monkeypatch, {"fqkline": KLINE_JSON.encode("utf-8")})

    with caplog.at_level(logging.WARNING):
        first = get_kline("600036", as_of=AS_OF)
        second = get_kline("600036", as_of=AS_OF)

    assert first is not None and second is not None
    assert len(attempts) == 2  # 仅第一只走了东财（含 1 次重试），第二只直接腾讯
    assert "已熔断" in caplog.text


def _stub_tencent_kline(monkeypatch):
    """腾讯 K 线打桩：只关心回退日志的测试不经过真实解析。"""
    monkeypatch.setattr(
        data,
        "_tencent_kline",
        lambda code, days: pd.DataFrame(
            {"date": [pd.Timestamp("2026-09-30")], "close": [40.0], "volume": [1.0]}
        ),
    )


def test_kline_fallback_notices_are_deduped_per_run(monkeypatch, caplog):
    """熔断生效后同类回退提示每次运行只打一行，不再逐只刷屏。"""

    def failing_hist(**kwargs):
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _stub_tencent_kline(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.data"):
        get_kline("600036", as_of=AS_OF)  # 首只：东财失败 → 熔断（warning 含代码）
        get_kline("000001", as_of=AS_OF)  # 已熔断：info 只此一行
        get_kline("000002", as_of=AS_OF)  # 同运行内第三只：静默

    notices = [m for m in caplog.messages if "腾讯行情" in m]
    assert len(notices) == 2  # warning（首只失败）+ info（首次熔断路径），各一条
    assert "600036" in notices[0]
    assert "000001" in notices[1]
    assert "000002" not in caplog.text


def test_breaker_notice_reappears_in_next_run(monkeypatch, tmp_path, caplog):
    """reset_quote_source_state 清空去重集合：下一次运行重新允许提示一次。"""

    def failing_hist(**kwargs):
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _stub_tencent_kline(monkeypatch)
    data.configure_cache(tmp_path, ttl_hours=24)

    get_kline("600036", as_of=AS_OF)  # 触发熔断并持久化
    reset_quote_source_state()  # 模拟下一次运行：沿用持久化判定

    with caplog.at_level(logging.INFO, logger="src.data"):
        get_kline("000001", as_of=AS_OF)  # 新运行的首只 → 重新提示一次
        get_kline("000002", as_of=AS_OF)  # 同运行第二只 → 静默

    breaker_notices = [
        m for m in caplog.messages if "已熔断" in m and "直接使用腾讯" in m
    ]
    assert len(breaker_notices) == 1  # 新一次运行重新提示，且仍只一条


def test_spot_after_successful_fallback_uses_tencent_directly(monkeypatch):
    attempts = []

    def failing_spot():
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_spot_em", failing_spot)
    _patch_http(monkeypatch, {"qt.gtimg.cn": QUOTES_TEXT.encode("gbk")})

    get_stock_spot(["600036"])
    out = get_stock_spot(["000001"])

    assert len(attempts) == 2  # 第二次不再尝试东财
    assert out is not None and out.iloc[0]["code"] == "000001"


def test_eastmoney_failure_trips_breaker_even_without_fallback(monkeypatch):
    attempts = []

    def failing_hist(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    monkeypatch.setattr("src.data.requests.get", _raise_connection_error)

    assert get_kline("600036", as_of=AS_OF) is None
    assert get_kline("000001", as_of=AS_OF) is None
    assert len(attempts) == 2  # 东财失败即熔断：第二只不再重试东财


# ---------------------------------------------------------------------------
# get_financial_health（分红可持续性：新浪财务分析指标）
# ---------------------------------------------------------------------------


def _financial_frame():
    """stock_financial_analysis_indicator 原始列名的最小帧（按报告期升序，含季报）。

    形态取「年报仍增长（2025-12-31 +3.0%）、最新中报已转负（-12.9%）」的分红
    陷阱场景：净利润增长率必须取最新报告期；每股现金流只能取年报行（中报的
    半年现金流与 TTM 分红不可比）。
    """
    return pd.DataFrame(
        {
            "日期": [
                "2023-12-31",
                "2024-12-31",
                "2025-12-31",
                "2026-03-31",
                "2026-06-30",
            ],
            "摊薄每股收益(元)": [0.9, 1.1, 1.0, 0.3, 0.5],
            "每股经营性现金流(元)": [1.1, 1.0, 2.77, 0.17, 0.6],
            "净利润增长率(%)": [8.0, 5.0, 3.0, -14.7, -12.9],
            "股息发放率(%)": [35.0, 40.0, 60.0, 0.1, 30.0],
        }
    )


def test_financial_health_eps_growth_uses_latest_period(monkeypatch, tmp_path):
    calls = {}

    def fake(symbol, start_year):
        calls["symbol"] = symbol
        calls["start_year"] = start_year
        return _financial_frame()

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

    out = get_financial_health("600015", tmp_path)

    # 增长率取最新报告期（中报 -12.9%），而不是年报行（+3.0%）；
    # 每股现金流/股息发放率固定取最近年报行
    assert out == {
        "eps_growth": pytest.approx(-12.9),
        "eps_period": "2026-06-30",
        "op_cash_per_share": pytest.approx(2.77),
        "payout_stmt": pytest.approx(60.0),
    }
    assert calls["symbol"] == "600015"
    assert int(calls["start_year"]) <= date.today().year - 3  # 只请求近几年的报告


def test_financial_health_eps_growth_falls_back_when_latest_blank(
    monkeypatch, tmp_path
):
    frame = _financial_frame()
    frame.loc[frame["日期"] == "2026-06-30", "净利润增长率(%)"] = None
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: frame
    )

    out = get_financial_health("600015", tmp_path)

    assert out["eps_growth"] == pytest.approx(-14.7)  # 最新行留空 → 回退到最近有效行
    assert out["eps_period"] == "2026-03-31"


def test_financial_health_annual_only_frame_uses_annual_period(monkeypatch, tmp_path):
    frame = _financial_frame()
    frame = frame[frame["日期"].str.endswith("12-31")]
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: frame
    )

    out = get_financial_health("600015", tmp_path)

    # 帧内只有年报行时与旧口径一致
    assert out["eps_growth"] == pytest.approx(3.0)
    assert out["eps_period"] == "2025-12-31"


def test_financial_health_without_annual_rows_returns_none(monkeypatch, tmp_path):
    quarterly_only = pd.DataFrame(
        {"日期": ["2026-06-30"], "每股经营性现金流(元)": [0.6]}
    )
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: quarterly_only
    )

    assert get_financial_health("600015", tmp_path) is None


def test_financial_health_failure_returns_none_and_is_not_cached(monkeypatch, tmp_path):
    def boom(symbol, start_year):
        raise ConnectionError("网络超时")

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", boom)

    assert get_financial_health("600015", tmp_path) is None
    assert not (tmp_path / "financial_cache.json").exists()  # 失败不写缓存


def test_financial_health_cache_hit_skips_upstream(monkeypatch, tmp_path):
    calls = []

    def fake(symbol, start_year):
        calls.append(1)
        return _financial_frame()

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

    first = get_financial_health("600015", tmp_path)
    second = get_financial_health("600015", tmp_path)

    assert first == second
    assert len(calls) == 1  # 第二次命中缓存，未再请求上游
    assert (tmp_path / "financial_cache.json").exists()


def test_financial_health_legacy_cache_entry_without_period_refetches(
    monkeypatch, tmp_path
):
    legacy = {
        "stocks": {
            "600015": {
                "eps_growth": 1.0,
                "op_cash_per_share": 2.0,
                "payout_stmt": None,
                "updated_at": datetime.now().isoformat(),  # 未过期，但缺 eps_period
            }
        }
    }
    (tmp_path / "financial_cache.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    calls = []

    def fake(symbol, start_year):
        calls.append(1)
        return _financial_frame()

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

    out = get_financial_health("600015", tmp_path)

    assert len(calls) == 1  # 旧格式（缺 eps_period）视为过期，重新拉取
    assert out["eps_period"] == "2026-06-30"


def test_financial_health_cache_expiry_refetches(monkeypatch, tmp_path):
    stale = {
        "stocks": {
            "600015": {
                "eps_growth": 1.0,
                "op_cash_per_share": 2.0,
                "payout_stmt": None,
                "updated_at": (datetime.now() - timedelta(days=31)).isoformat(),
            }
        }
    }
    (tmp_path / "financial_cache.json").write_text(
        json.dumps(stale, ensure_ascii=False), encoding="utf-8"
    )
    calls = []

    def fake(symbol, start_year):
        calls.append(1)
        return _financial_frame()

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

    out = get_financial_health("600015", tmp_path)

    assert len(calls) == 1  # 过期条目重新拉取
    assert out["eps_growth"] == pytest.approx(-12.9)


def test_financial_health_corrupt_cache_rebuilds(monkeypatch, tmp_path):
    (tmp_path / "financial_cache.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        ak,
        "stock_financial_analysis_indicator",
        lambda symbol, start_year: _financial_frame(),
    )

    assert get_financial_health("600015", tmp_path) is not None


def test_breaker_persists_across_runs_when_cache_enabled(monkeypatch, tmp_path):
    """首次运行触发熔断后，下一次运行（reset 重读持久化判定）不再重试东财。"""
    attempts = []

    def failing_hist(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _patch_http(monkeypatch, {"fqkline": KLINE_JSON.encode("utf-8")})
    data.configure_cache(tmp_path, ttl_hours=24)

    get_kline("600036", as_of=AS_OF)  # 第一次运行：东财失败 → 腾讯成功 → 熔断并持久化
    assert len(attempts) == 2

    reset_quote_source_state()  # 模拟下一次运行开始
    assert get_kline("600036", as_of=AS_OF) is not None
    assert len(attempts) == 2  # 直接沿用判定，未再请求东财


def test_stale_persisted_breaker_is_ignored(monkeypatch, tmp_path):
    """超过 30 分钟的持久化判定失效，重新尝试东财。"""
    attempts = []

    def failing_hist(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _patch_http(monkeypatch, {"fqkline": KLINE_JSON.encode("utf-8")})
    data.configure_cache(tmp_path, ttl_hours=24)
    # 直接写入一条 31 分钟前的过期判定
    Cache(tmp_path / "market_cache.db").set(
        "state:eastmoney_quotes_down",
        {"at": "old"},
        now=datetime.now() - timedelta(minutes=31),
    )

    reset_quote_source_state()
    get_kline("600036", as_of=AS_OF)

    assert len(attempts) == 2  # 判定已过期 → 仍然先试东财（失败后回退）


def test_reset_quote_source_state_retries_eastmoney(monkeypatch):
    attempts = []

    def failing_hist(**kwargs):
        attempts.append(1)
        raise ConnectionError("东财不可用")

    monkeypatch.setattr(ak, "stock_zh_a_hist", failing_hist)
    _patch_http(monkeypatch, {"fqkline": KLINE_JSON.encode("utf-8")})

    get_kline("600036", as_of=AS_OF)
    reset_quote_source_state()
    get_kline("000001", as_of=AS_OF)

    assert len(attempts) == 4  # 重置后重新尝试东财（各 2 次）
