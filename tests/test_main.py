"""main 编排入口（run / main）的集成测试。

全部 ``src.data.*`` 均被 monkeypatch（规范形状 fixture），不联网；
自选列表与评分规则由测试写入 ``tmp_path``，不依赖仓库内可由用户改动的 ``config/``，
因此期望分数完全由本文件声明的权重/阈值推出。
"""

from datetime import date
import logging

import pandas as pd
import pytest
import yaml

import main
from src import config, data

AS_OF = date(2026, 10, 6)

STOCK_A = "000001"  # 平安银行（分红强）
STOCK_B = "600036"  # 招商银行（分红弱）
FUND_CODE = "510880"  # 红利ETF

# 与 config/rules.yaml 同口径：股票/基金权重各自合计 100
STOCKS_CONFIG = {
    "stocks": [
        {"code": STOCK_A, "name": "平安银行"},
        {"code": STOCK_B, "name": "招商银行"},
    ]
}
FUNDS_CONFIG = {
    "funds": [
        {"code": FUND_CODE, "name": "红利ETF", "type": "etf", "index": "上证红利"}
    ]
}
RULES_CONFIG = {
    "stocks": {
        "indicators": {
            "dividend_yield": {"weight": 30, "thresholds": {"high": 4.0, "mid": 2.0}},
            "dividend_years": {"weight": 20, "thresholds": {"high": 5, "mid": 3}},
            "payout_ratio": {"weight": 10, "thresholds": {"min": 20, "max": 70}},
            "pe_vs_industry": {
                "weight": 15,
                "thresholds": {"discount": -30, "premium": 30},
            },
            "pb_vs_industry": {
                "weight": 10,
                "thresholds": {"discount": -30, "premium": 30},
            },
            "ma60_position": {
                "weight": 10,
                "thresholds": {"sweet_low": -5, "sweet_high": 5, "max_deviation": 20},
            },
            "momentum_5d": {"weight": 5, "thresholds": {"oversold": -10, "overbought": 10}},
        }
    },
    "funds": {
        "indicators": {
            "discount_rate": {"weight": 25, "thresholds": {"discount": -1, "premium": 1}},
            "nav_trend_20d": {"weight": 25, "thresholds": {"pullback": -10, "rally": 5}},
            "index_pe_vs_history": {"weight": 25, "thresholds": {"low": 30, "high": 70}},
            "dividend_frequency": {"weight": 15, "thresholds": {"high": 2, "mid": 1}},
            "fund_size": {"weight": 10, "thresholds": {"min": 1}},
        }
    },
    "data": {"kline_days": 120, "pe_cache_days": 7},
    "output": {"buy_top_n": 5, "avoid_bottom_n": 5},
}

# 全部数据接口的默认替身：返回 None（获取失败）
DATA_FUNCTIONS = (
    "get_stock_spot",
    "get_kline",
    "get_dividend_history",
    "get_industry_pe_pb",
    "get_fund_quotes",
    "get_fund_nav_history",
    "get_fund_latest_nav",
    "get_fund_dividend_history",
    "get_fund_overview",
    "resolve_index_symbol",
    "get_index_pe_history",
)


@pytest.fixture
def config_dir(tmp_path):
    """把自选列表与规则写入临时配置目录，并返回该目录。"""
    directory = tmp_path / "config"
    directory.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", FUNDS_CONFIG),
        ("rules.yaml", RULES_CONFIG),
    ):
        (directory / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return directory


def _kline(closes):
    return pd.DataFrame(
        {
            "date": pd.date_range(end="2026-10-06", periods=len(closes), freq="D"),
            "close": [float(value) for value in closes],
        }
    )


def _dividends(entries):
    return pd.DataFrame(
        {
            "date": pd.to_datetime([day for day, _ in entries]),
            "dividend_per_share": [float(amount) for _, amount in entries],
        }
    )


SPOT = pd.DataFrame(
    {
        "code": [STOCK_A, STOCK_B],
        "name": ["平安银行", "招商银行"],
        "price": [10.0, 20.0],
        "pe": [5.0, 8.0],
        "pb": [0.5, 1.2],
    }
)
KLINE_A = _kline([9.0] * 60 + [10.0] * 10)
KLINE_B = _kline([20.0] * 70)
DIVIDENDS_A = _dividends(
    [
        ("2022-06-10", 1.0),
        ("2023-06-10", 1.0),
        ("2024-06-10", 1.0),
        ("2025-06-10", 1.0),
        ("2026-06-10", 1.0),
    ]
)
DIVIDENDS_B = _dividends([("2024-07-01", 0.2)])
INDUSTRY = {
    STOCK_A: {"industry": "银行", "pe": 10.0, "pb": 1.0},
    STOCK_B: {"industry": "银行", "pe": 10.0, "pb": 1.0},
}
FUND_QUOTES = pd.DataFrame(
    {"code": [FUND_CODE], "name": ["红利ETF"], "price": [3.00], "iopv": [3.05]}
)
FUND_NAV = pd.DataFrame(
    {
        "date": pd.date_range(end="2026-10-06", periods=25, freq="D"),
        "close": [1.0] * 5 + [1.0 + 0.0015 * i for i in range(20)],
    }
)
FUND_DIVIDENDS = pd.DataFrame(
    {"date": pd.to_datetime(["2024-12-12", "2025-11-17", "2026-06-11"])}
)
INDEX_PE = pd.DataFrame(
    {
        "date": pd.date_range(end="2026-10-06", periods=11, freq="D"),
        "pe": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 20.0, 19.0, 18.0, 17.0, 15.0],
    }
)


def _patch_data(monkeypatch, **overrides):
    """替换 src.data 的全部对外函数；未指定的返回 None（模拟获取失败）。"""
    for name in DATA_FUNCTIONS:
        monkeypatch.setattr(data, name, overrides.get(name, lambda *a, **k: None))


def _happy_overrides(**extra):
    """正常路径的数据替身；extra 可覆盖单个接口。"""
    return {
        "get_stock_spot": lambda codes: SPOT,
        "get_kline": lambda code, days=120, as_of=None: {
            STOCK_A: KLINE_A,
            STOCK_B: KLINE_B,
        }[code],
        "get_dividend_history": lambda code: {
            STOCK_A: DIVIDENDS_A,
            STOCK_B: DIVIDENDS_B,
        }[code],
        "get_industry_pe_pb": lambda code, cache_dir, cache_days=7: INDUSTRY[code],
        "get_fund_quotes": lambda fund_type: FUND_QUOTES,
        "get_fund_nav_history": lambda code, fund_type, days=120: FUND_NAV,
        "get_fund_dividend_history": lambda code: FUND_DIVIDENDS,
        "get_fund_overview": lambda code: {
            "scale": "222.76亿元（截止至：2026年06月30日）",
            "tracker": "上证红利指数",
        },
        "resolve_index_symbol": lambda configured, tracker: "上证红利",
        "get_index_pe_history": lambda symbol: INDEX_PE,
        **extra,
    }


def _section_rows(report, header):
    """取 ``header`` 段落的数据行（跳过表头与分隔线，遇下一段落/摘要停止）。"""
    lines = report.splitlines()
    rows = []
    for line in lines[lines.index(header) + 1 :]:
        stripped = line.strip()
        if stripped in ("【股票】", "【基金】") or stripped.startswith("扫描"):
            break
        if not stripped or stripped.startswith("排名") or set(stripped) <= set("- "):
            continue
        rows.append(line)
    return rows


def _row_for(report, text):
    matches = [line for line in report.splitlines() if text in line]
    assert len(matches) == 1, f"{text!r} 在报告中出现 {len(matches)} 次"
    return matches[0]


def test_run_happy_path_reports_all_instruments_sorted_by_total(
    monkeypatch, config_dir, tmp_path
):
    _patch_data(monkeypatch, **_happy_overrides())

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    stock_rows = _section_rows(report, "【股票】")
    assert len(stock_rows) == 2
    assert "平安银行" in stock_rows[0] and "92.5" in stock_rows[0]
    assert "招商银行" in stock_rows[1] and "25.0" in stock_rows[1]

    fund_rows = _section_rows(report, "【基金】")
    assert len(fund_rows) == 1
    assert "红利ETF" in fund_rows[0] and "75.0" in fund_rows[0]
    assert "510880" in fund_rows[0]

    assert "扫描 3 只标的（股票 2 / 基金 1），数据不足 0" in report


def test_run_stock_missing_from_spot_table_still_scored(
    monkeypatch, config_dir, tmp_path, caplog
):
    spot = SPOT[SPOT["code"] != STOCK_A].reset_index(drop=True)
    _patch_data(monkeypatch, **_happy_overrides(get_stock_spot=lambda codes: spot))

    with caplog.at_level(logging.WARNING):
        report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    # 行情表可用但缺该股 → 仍然逐股告警，便于定位停牌/退市
    assert f"股票 {STOCK_A} 不在行情表中" in caplog.text

    row = _row_for(report, "平安银行")
    # 价格缺失 → 股息率/派息率/PE/PB/均线位置不可评分；分红年数(1.0)与 5 日动量(0.5) 仍打分：
    # (20 + 0.5×5) / (20+5) × 100 = 90.0
    assert "90.0" in row and "连续分红" in row
    assert "N/A" not in row and "数据不足" not in row
    assert "招商银行" in report  # 其他标的照常


def test_run_kline_failure_keeps_other_indicators_scoring(
    monkeypatch, config_dir, tmp_path
):
    def failing_kline(code, days=120, as_of=None):
        if code == STOCK_B:
            raise ConnectionError("模拟行情接口失败")
        return KLINE_A

    _patch_data(monkeypatch, **_happy_overrides(get_kline=failing_kline))

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    row = _row_for(report, "招商银行")
    # K 线缺失 → 均线/动量/技术面不可评分；分红与估值类仍打分：
    # (0.5×15 + 0.5×10) / (30+20+10+15+10) × 100 = 14.7
    assert "14.7" in row
    assert "N/A" not in row and "数据不足" not in row
    assert "平安银行" in report and "红利ETF" in report  # 其余标的不受影响


def test_run_includes_price_position_in_rows(monkeypatch, config_dir, tmp_path):
    def kline_for(code, days=120, as_of=None):
        if code == STOCK_B:
            return _kline([20.0] * 60 + [10.0] * 10)  # 最新价处于窗口最低 → 低位
        return KLINE_A  # [9]*60 + [10]*10 → 最新价处于窗口最高 → 高位

    _patch_data(monkeypatch, **_happy_overrides(get_kline=kline_for))

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "120日高位 100%" in _row_for(report, "平安银行")
    assert "120日低位 0%" in _row_for(report, "招商银行")


def test_run_all_sources_failing_reports_insufficient_data(
    monkeypatch, config_dir, tmp_path
):
    _patch_data(monkeypatch)  # 所有数据源返回 None

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    for name in ("平安银行", "招商银行", "红利ETF"):
        row = _row_for(report, name)
        assert "N/A" in row and "数据不足" in row
    assert "数据不足 3" in report


def test_run_spot_table_failure_warns_once_not_per_stock(
    monkeypatch, config_dir, tmp_path, caplog
):
    """整张行情表不可用时只保留表级告警，不再逐股重复误导性提示。"""
    _patch_data(monkeypatch)  # 所有数据源返回 None，含行情表

    with caplog.at_level(logging.WARNING):
        main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "get_stock_spot 获取失败" in caplog.text  # 表级失败仍有告警
    assert "不在行情表中" not in caplog.text  # 不逐股重复


def test_run_resets_quote_source_state(monkeypatch, config_dir, tmp_path):
    """每轮 run() 开始重置东财熔断，下一轮重新尝试东财主源。"""
    _patch_data(monkeypatch, **_happy_overrides())

    data._eastmoney_quotes_down = True  # 模拟上一轮运行遗留的熔断状态

    main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert data._eastmoney_quotes_down is False


def test_run_passes_data_rules_and_as_of_through(monkeypatch, config_dir, tmp_path):
    calls = {"kline": [], "industry": [], "nav": [], "quotes": [], "symbol": []}

    def get_kline(code, days=120, as_of=None):
        calls["kline"].append((code, days, as_of))
        return KLINE_A

    def get_industry_pe_pb(code, cache_dir, cache_days=7):
        calls["industry"].append((code, cache_dir, cache_days))
        return {"industry": "银行", "pe": 10.0, "pb": 1.0}

    def get_fund_nav_history(code, fund_type, days=120):
        calls["nav"].append((code, fund_type, days))
        return None

    def get_fund_quotes(fund_type):
        calls["quotes"].append(fund_type)
        return FUND_QUOTES

    def resolve_index_symbol(configured, tracker):
        calls["symbol"].append((configured, tracker))
        return None

    _patch_data(
        monkeypatch,
        **_happy_overrides(
            get_kline=get_kline,
            get_industry_pe_pb=get_industry_pe_pb,
            get_fund_nav_history=get_fund_nav_history,
            get_fund_quotes=get_fund_quotes,
            resolve_index_symbol=resolve_index_symbol,
        ),
    )

    main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert calls["kline"] == [(STOCK_A, 120, AS_OF), (STOCK_B, 120, AS_OF)]
    assert calls["industry"] == [
        (STOCK_A, tmp_path, 7),
        (STOCK_B, tmp_path, 7),
    ]
    assert calls["nav"] == [(FUND_CODE, "etf", 120)]
    assert calls["quotes"] == ["etf"]  # 每种基金类型只拉一次行情
    assert calls["symbol"] == [("上证红利", "上证红利指数")]


def test_run_list_valued_index_degrades_instead_of_aborting(monkeypatch, tmp_path):
    # 用户把 funds.yaml 的 index 写成多元素列表：真实 resolve_index_symbol 内部
    # pd.isna 对数组取真值会抛 ValueError，必须被 _fetch 捕获并降级为
    # 「指数估值缺数据」，其余指标与标的不受影响。
    funds = {
        "funds": [
            {
                "code": FUND_CODE,
                "name": "红利ETF",
                "type": "etf",
                "index": ["上证红利", "中证红利"],
            }
        ]
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", funds),
        ("rules.yaml", RULES_CONFIG),
    ):
        (config_dir / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    _patch_data(
        monkeypatch,
        **_happy_overrides(resolve_index_symbol=data.resolve_index_symbol),
    )

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    fund_row = _row_for(report, "红利ETF")
    assert "N/A" not in fund_row  # 指数 PE 之外的 4 个指标仍打分
    assert "4/5" in fund_row
    assert "平安银行" in report  # 股票不受基金配置问题影响


def test_main_prints_report_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(main, "run", lambda: "扫描 0 只标的")

    assert main.main() == 0

    captured = capsys.readouterr()
    assert "扫描 0 只标的" in captured.out


def test_main_reports_config_error_and_returns_one(monkeypatch, capsys):
    def failing_run():
        raise config.ConfigError("stocks.yaml: 配置文件不存在")

    monkeypatch.setattr(main, "run", failing_run)

    assert main.main() == 1

    captured = capsys.readouterr()
    assert "stocks.yaml: 配置文件不存在" in captured.err
