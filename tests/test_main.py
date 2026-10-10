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

import sys

import main
from src import config, constituents, data

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
    # 缓存三档故意取非默认值：默认值相同的话，main 是否真的读了配置就测不出来
    "data": {
        "kline_days": 120,
        "pe_cache_days": 7,
        "quote_cache_minutes": 15,
        "daily_cache_hours": 3,
        "slow_cache_days": 2,
    },
    "output": {"buy_top_n": 5, "avoid_bottom_n": 5},
}

# 全部数据接口的默认替身：返回 None（获取失败）
DATA_FUNCTIONS = (
    "get_stock_spot",
    "get_kline",
    "get_kline_raw",
    "get_dividend_history",
    "get_industry_pe_pb",
    "get_fund_quotes",
    "get_fund_nav_history",
    "get_fund_latest_nav",
    "get_fund_dividend_history",
    "get_fund_overview",
    "resolve_index_symbol",
    "get_index_pe_history",
    "get_index_dividend_yield",
    "get_10y_bond_yield",
    "get_financial_health",
)


CONSTITUENTS_CONFIG = {
    "indices": [
        {
            "index_code": "000015",
            "index_name": "上证红利",
            "updated_at": "2026-10-06T12:00:00",
            "constituents": [{"code": "601088", "name": "中国神华"}],
        }
    ]
}


@pytest.fixture
def config_dir(tmp_path):
    """把自选列表、规则与成分股写入临时配置目录，并返回该目录。"""
    directory = tmp_path / "config"
    directory.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", FUNDS_CONFIG),
        ("rules.yaml", RULES_CONFIG),
        ("dividend_index.yaml", CONSTITUENTS_CONFIG),
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
# 不复权长窗口：价格一路下行 → 当前 TTM 股息率（DIVIDENDS_A 每年 6-10 派 1 元）
# 是窗口内最高 → 股息率历史分位 100
RAW_KLINE_PCT = pd.DataFrame(
    {
        "date": pd.bdate_range("2026-01-05", periods=260),
        "close": [20.0 - index * 0.01 for index in range(260)],
    }
)
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


def _patch_data(monkeypatch, constituents_result=None, **overrides):
    """替换 src.data 的全部对外函数；未指定的返回 None（模拟获取失败）。

    成分股刷新一并打桩（constituents_result 为返回的有效名单，默认空）。
    """
    for name in DATA_FUNCTIONS:
        monkeypatch.setattr(data, name, overrides.get(name, lambda *a, **k: None))
    stub = {
        "refreshed": False,
        "added": [],
        "removed": [],
        "error": None,
        "constituents": constituents_result or [],
    }
    monkeypatch.setattr(constituents, "refresh_indices", lambda *a, **k: stub)


def _happy_overrides(**extra):
    """正常路径的数据替身；extra 可覆盖单个接口。"""
    return {
        "get_stock_spot": lambda codes: SPOT,
        "get_kline": lambda code, days=120, as_of=None: {
            STOCK_A: KLINE_A,
            STOCK_B: KLINE_B,
        }[code],
        "get_kline_raw": lambda code, days=790: RAW_KLINE_PCT,
        "get_dividend_history": lambda code: {
            STOCK_A: DIVIDENDS_A,
            STOCK_B: DIVIDENDS_B,
        }[code],
        "get_industry_pe_pb": lambda code, cache_dir, cache_days=7: INDUSTRY[code],
        "get_fund_quotes": lambda fund_type, codes: FUND_QUOTES,
        "get_fund_nav_history": lambda code, fund_type, days=120: FUND_NAV,
        "get_fund_dividend_history": lambda code: FUND_DIVIDENDS,
        "get_fund_overview": lambda code: {
            "scale": "222.76亿元（截止至：2026年06月30日）",
            "tracker": "上证红利指数",
        },
        "resolve_index_symbol": lambda configured, tracker: "上证红利",
        "get_index_pe_history": lambda symbol: INDEX_PE,
        "get_index_dividend_yield": lambda index_code="000922": 4.2,
        "get_10y_bond_yield": lambda: 1.8,
        "get_financial_health": lambda code, cache_dir, cache_days=30: {
            "eps_growth": 6.0,
            "op_cash_per_share": 2.0,
            "payout_stmt": 40.0,
        },
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


def test_run_merges_constituents_and_marks_new(monkeypatch, config_dir, tmp_path):
    entries = [
        {"code": STOCK_A, "name": "平安银行"},  # 与自选重叠 → 不重复扫描
        {"code": "601088", "name": "中国神华", "added": date.today().isoformat()},
    ]
    _patch_data(
        monkeypatch, constituents_result=entries, **_happy_overrides()
    )

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "中国神华(新增)" in report  # 新增成分股在报告里带标记
    # 自选 2 只 + 新成分股 1 只（重叠的平安银行不重复）+ 基金 1 只
    assert "扫描 4 只标的（股票 3 / 基金 1）" in report
    assert report.count("平安银行") == 1


def _write_rules(config_dir, **data_overrides):
    """覆盖 tmp 配置目录里的 rules.yaml（如 data.candidate_top_n）。"""
    rules = {
        **RULES_CONFIG,
        "data": {**RULES_CONFIG["data"], **data_overrides},
    }
    (config_dir / "rules.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _spot_with_cap(caps: dict):
    """带 market_cap（亿元）的规范行情帧；值为 None 表示该股取不到市值。"""
    codes = list(caps)
    return pd.DataFrame(
        {
            "code": codes,
            "name": codes,
            "price": [1.0] * len(codes),
            "pe": [1.0] * len(codes),
            "pb": [1.0] * len(codes),
            "market_cap": [caps[code] for code in codes],
        }
    )


POOL = [
    {"code": "600036", "name": "招商银行"},  # 10405.71 亿
    {"code": "000001", "name": "平安银行"},  # 2245.26 亿
    {"code": "601088", "name": "中国神华"},  # 8000.00 亿
    {"code": "920002", "name": "万达轴承"},  # 33.09 亿
]
POOL_CAPS = {"600036": 10405.71, "000001": 2245.26, "601088": 8000.0, "920002": 33.09}


def test_select_constituents_keeps_highest_market_cap():
    out = main._select_constituents(POOL, _spot_with_cap(POOL_CAPS), limit=2)

    # 入选按市值排序决定，输出保持名单原顺序
    assert [entry["code"] for entry in out["kept"]] == ["600036", "601088"]
    assert out["unranked"] == []
    assert out["threshold"] == pytest.approx(8000.0)  # 入选末位的总市值（亿元）


def test_select_constituents_limit_zero_keeps_all():
    out = main._select_constituents(POOL, _spot_with_cap(POOL_CAPS), limit=0)

    assert [entry["code"] for entry in out["kept"]] == [e["code"] for e in POOL]
    assert out["threshold"] is None


def test_select_constituents_drops_entries_without_market_cap():
    """严格口径：取不到市值的成分股不占名额、也不保留，单独列为 unranked。"""
    caps = {**POOL_CAPS, "000001": None}  # 平安银行市值为缺失
    spot = _spot_with_cap(caps).drop(index=0)  # 且招商银行不在行情表中

    out = main._select_constituents(POOL, spot, limit=2)

    assert [entry["code"] for entry in out["kept"]] == ["601088", "920002"]
    assert out["unranked"] == ["600036", "000001"]


def test_select_constituents_dedupes_codes_shared_by_indices():
    """同一只股票进入多个指数（合并名单里有重复条目）只占一个名额。"""
    entries = [
        {"code": "601088", "name": "中国神华"},
        {"code": "600028", "name": "中国石化"},
        {"code": "601088", "name": "中国神华"},  # 另一指数的同一条
        {"code": "600123", "name": "兰花科创"},
    ]
    spot = _spot_with_cap({"601088": 10367.55, "600028": 5000.0, "600123": 100.0})

    out = main._select_constituents(entries, spot, limit=2)

    assert [entry["code"] for entry in out["kept"]] == ["601088", "600028"]


def test_select_constituents_when_no_entry_has_market_cap_keeps_all():
    """一只也排不出市值（如行情表里没有这些代码）→ 等同整表不可用，不筛。"""
    spot = _spot_with_cap({"600036": None, "000001": None})

    out = main._select_constituents(POOL, spot, limit=2)

    assert [entry["code"] for entry in out["kept"]] == [e["code"] for e in POOL]
    assert out["threshold"] is None
    assert out["unranked"] == ["600036", "000001", "601088", "920002"]  # 全部缺市值


@pytest.mark.parametrize("spot", [None, pd.DataFrame()])
def test_select_constituents_without_spot_table_keeps_all(spot):
    """整表不可用 → 本轮不筛（否则一只都排不出市值，名单会被丢光）。"""
    out = main._select_constituents(POOL, spot, limit=2)

    assert [entry["code"] for entry in out["kept"]] == [e["code"] for e in POOL]
    assert out["threshold"] is None


def test_select_constituents_without_market_cap_column_keeps_all():
    """行情帧缺 market_cap 列（如旧 schema）同样按「整表不可用」处理。"""
    out = main._select_constituents(POOL, SPOT, limit=2)

    assert len(out["kept"]) == len(POOL)
    assert out["threshold"] is None


def test_run_trims_constituent_pool_by_market_cap(
    monkeypatch, config_dir, tmp_path, caplog
):
    """候选池在逐股取数前裁到前 N：被筛掉的成分股不进入取数与报告，自选不受影响。"""
    entries = [
        {"code": "601088", "name": "中国神华"},  # 8000 亿 → 入选
        {"code": "600028", "name": "中国石化"},  # 7000 亿 → 出局
        {"code": "600123", "name": "兰花科创"},  # 100 亿 → 出局
        {"code": "601088", "name": "中国神华"},  # 另一指数的同一条：不重复占名额
    ]
    _write_rules(config_dir, candidate_top_n=1)
    kline_calls = []

    def kline(code, days=120, as_of=None):
        kline_calls.append(code)
        return _kline([10.0] * 60)

    spot = _spot_with_cap(
        {
            STOCK_A: 2245.26,
            STOCK_B: 10405.71,
            "601088": 8000.0,
            "600028": 7000.0,
            "600123": 100.0,
        }
    )
    _patch_data(
        monkeypatch,
        constituents_result=entries,
        **_happy_overrides(get_stock_spot=lambda codes: spot, get_kline=kline),
    )

    with caplog.at_level(logging.INFO):
        report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "中国神华" in report
    assert "600028" not in report and "600123" not in report  # 被筛掉的成分股不进报告
    assert "600028" not in kline_calls  # 也没触发逐股取数
    assert "平安银行" in report and "招商银行" in report  # 自选不参与筛选
    assert "候选池按总市值取前 1：成分股 3 → 1（门槛 8000 亿）" in caplog.text


def test_run_warns_when_candidate_market_cap_missing(
    monkeypatch, config_dir, tmp_path, caplog
):
    """个股取不到市值 → 按严格口径剔除，并告警列出代码。"""
    entries = [
        {"code": "601088", "name": "中国神华"},
        {"code": "600028", "name": "中国石化"},
    ]
    _write_rules(config_dir, candidate_top_n=1)
    spot = _spot_with_cap({STOCK_A: 2245.26, STOCK_B: 10405.71, "601088": 8000.0})

    _patch_data(
        monkeypatch, constituents_result=entries, **_happy_overrides(get_stock_spot=lambda codes: spot)
    )

    with caplog.at_level(logging.WARNING):
        report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "600028" not in report
    assert "600028" in caplog.text and "市值缺失" in caplog.text


def test_run_skips_trim_when_spot_table_unavailable(
    monkeypatch, config_dir, tmp_path, caplog
):
    """整表不可用 → 本轮不筛：全部成分股照常进入扫描，并打一条显眼告警。"""
    entries = [
        {"code": "601088", "name": "中国神华"},
        {"code": "600028", "name": "中国石化"},
    ]
    _write_rules(config_dir, candidate_top_n=1)
    _patch_data(monkeypatch, constituents_result=entries, **_happy_overrides(get_stock_spot=lambda codes: None))

    with caplog.at_level(logging.WARNING):
        report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "中国神华" in report and "中国石化" in report
    assert "本轮不做市值筛选" in caplog.text


def test_run_omits_marker_for_old_constituents(monkeypatch, config_dir, tmp_path):
    entries = [
        {"code": "601088", "name": "中国神华", "added": "2026-01-01"},  # 早已加入
    ]
    _patch_data(monkeypatch, constituents_result=entries, **_happy_overrides())

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "中国神华" in report
    assert "(新增)" not in report


def test_run_applies_sustainability_penalty_to_total(monkeypatch, config_dir, tmp_path):
    def health(code, cache_dir, cache_days=30):
        return {"eps_growth": -12.6, "op_cash_per_share": 0.5, "payout_stmt": None}

    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=health))

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    # 平安银行：盈利单期下滑(-5%) + 分红超现金流(TTM 1.0/0.5=200% → -10%) = 扣 15% → 92.5→78.6
    assert "78.6" in _row_for(report, "平安银行")
    # 招商银行：TTM 分红较低，仅盈利单期下滑一项（-5%）→ 25.0→23.8
    assert "23.8" in _row_for(report, "招商银行")


def test_run_exposes_sustainability_flags(monkeypatch, config_dir, tmp_path):
    def health(code, cache_dir, cache_days=30):
        return {"eps_growth": -12.6, "eps_period": "2026-06-30",
                "op_cash_per_share": 0.5, "payout_stmt": None,
                "eps_history": [], "cash_yoy_history": [],
                "debt_ratio": 70.0, "debt_ratio_yoy": 13.0, "interest_cover": 1.4}

    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=health))

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)
    item = next(r for r in scan["stock_results"] if r["code"] == "000001")

    # debt 维度只取最重一条（spec 决策 7）：debt_jump 与 interest_cover 同为
    # 默认 10.0 时平局，取候选构造顺序靠前者 → debt_jump。故此处只有三条。
    assert [f["key"] for f in item["sustainability_flags"]] == [
        "eps_decline", "cash_cover", "debt_jump",
    ]
    assert item["sustainability"]["debt_ratio_yoy"] == pytest.approx(13.0)
    # 数据层新增字段一律原样透传给判定层
    assert item["sustainability"]["eps_history"] == []
    assert item["sustainability"]["cash_yoy_history"] == []
    assert item["sustainability"]["interest_cover"] == pytest.approx(1.4)


def test_run_exposes_repeated_flags_from_passed_through_history(
    monkeypatch, config_dir, tmp_path
):
    """两个序列与最新报告期锚点必须原样透传：丢键时连续恶化判据会静默消失。"""
    def health(code, cache_dir, cache_days=30):
        return {"eps_growth": -12.6, "eps_period": "2026-06-30",
                "op_cash_per_share": 0.5, "payout_stmt": None,
                "latest_period": "2026-06-30",
                "eps_history": [
                    {"period": "2026-06-30", "growth": -12.9},
                    {"period": "2026-03-31", "growth": -14.7},
                    {"period": "2025-12-31", "growth": -3.0},
                    {"period": "2025-09-30", "growth": 4.0},
                ],
                "cash_yoy_history": [
                    {"period": "2026-06-30", "yoy": -20.0},
                    {"period": "2025-12-31", "yoy": -10.0},
                    {"period": "2025-06-30", "yoy": -5.0},
                    {"period": "2024-12-31", "yoy": 8.0},
                ],
                "debt_ratio": 70.0, "debt_ratio_yoy": 13.0, "interest_cover": 1.4}

    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=health))

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)
    item = next(r for r in scan["stock_results"] if r["code"] == "000001")
    flags = {flag["key"]: flag for flag in item["sustainability_flags"]}

    # 两期内各 3 期为负 → 连续恶化（15）压过单期下滑（5），维度内只留最重一条
    assert flags["eps_repeated"]["dimension"] == "profit"
    assert flags["eps_repeated"]["value"] == 3
    assert flags["eps_repeated"]["window"] == 4
    assert flags["cash_repeated"]["dimension"] == "cashflow"
    assert flags["cash_repeated"]["value"] == 3
    assert flags["cash_repeated"]["window"] == 4
    assert "eps_decline" not in flags and "cash_decline" not in flags
    # 趋势门禁（决策 11 延伸）依赖锚点透传：丢键时 cash_* 会整体静默消失
    assert item["sustainability"]["latest_period"] == "2026-06-30"


def test_run_sustainability_flags_empty_when_health_unavailable(monkeypatch, config_dir, tmp_path):
    # get_financial_health 返回 None（``_happy_overrides`` 的默认替身是健康数据，
    # 故此处显式覆盖为 None）→ sustainability 为 None，
    # flags 必须是空列表而非 None（reporter 依赖这个区分）
    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=lambda *a, **k: None))

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)
    item = next(r for r in scan["stock_results"] if r["code"] == "000001")

    assert item["sustainability"] is None
    assert item["sustainability_flags"] == []


def test_run_scores_dividend_trend(monkeypatch, tmp_path):
    rules = {
        "stocks": {
            "indicators": {
                "dividend_trend": {"weight": 100, "thresholds": {"high": 0, "mid": -30}}
            }
        },
        "funds": {
            "indicators": {
                "discount_rate": {
                    "weight": 100,
                    "thresholds": {"discount": -1, "premium": 1},
                }
            }
        },
        "data": {"kline_days": 120, "pe_cache_days": 7},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", FUNDS_CONFIG),
        ("rules.yaml", rules),
        ("dividend_index.yaml", CONSTITUENTS_CONFIG),
    ):
        (config_dir / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    _patch_data(monkeypatch, **_happy_overrides())

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    # DIVIDENDS_A：近 3 年 3.0 vs 前 3 年 2.0 → +50%（tier 1.0，满分档并入亮点）
    row = _row_for(report, "平安银行")
    assert "分红趋势 +50%" in row
    assert "100.0" in row


def test_run_scores_dividend_yield_percentile(monkeypatch, tmp_path):
    rules = {
        "stocks": {
            "indicators": {
                "dividend_yield_percentile": {
                    "weight": 100,
                    "thresholds": {"high": 70, "mid": 40},
                }
            }
        },
        "funds": {
            "indicators": {
                "discount_rate": {
                    "weight": 100,
                    "thresholds": {"discount": -1, "premium": 1},
                }
            }
        },
        "data": {"kline_days": 120, "pe_cache_days": 7},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", FUNDS_CONFIG),
        ("rules.yaml", rules),
        ("dividend_index.yaml", CONSTITUENTS_CONFIG),
    ):
        (config_dir / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    _patch_data(monkeypatch, **_happy_overrides())

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    # 平安银行：价格一路下行、每年稳定派 1 元 → 当前股息率为窗口最高 → 满分档
    row = _row_for(report, "平安银行")
    assert "股息率分位 100%" in row
    assert "100.0" in row


def test_run_scores_stability_and_reports_liquidity_hint(monkeypatch, tmp_path):
    rules = {
        "stocks": {
            "indicators": {
                "volatility": {"weight": 50, "thresholds": {"low": 20, "mid": 30}},
                "max_drawdown": {"weight": 50, "thresholds": {"low": 15, "mid": 25}},
            }
        },
        "funds": {
            "indicators": {
                "discount_rate": {
                    "weight": 100,
                    "thresholds": {"discount": -1, "premium": 1},
                }
            }
        },
        "data": {"kline_days": 120, "pe_cache_days": 7},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, payload in (
        ("stocks.yaml", STOCKS_CONFIG),
        ("funds.yaml", FUNDS_CONFIG),
        ("rules.yaml", rules),
        ("dividend_index.yaml", CONSTITUENTS_CONFIG),
    ):
        (config_dir / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def wild_closes():
        closes = [10.0]
        for index in range(259):
            step = 1.05 if index % 2 == 0 else 0.95
            closes.append(closes[-1] * step * 0.997)  # ±5% 交替 + 慢下行 → 高波动 + 深回撤
        return closes

    def raw_kline_for(code, days=790):
        return pd.DataFrame(
            {"date": pd.bdate_range("2025-10-08", periods=260), "close": wild_closes()}
        )

    def kline_for(code, days=120, as_of=None):
        frame = _kline([10.0] * 70)
        frame["volume"] = [50.0] * 70  # 日均成交额约 5 万元（手×100×价）
        return frame

    _patch_data(
        monkeypatch,
        **_happy_overrides(get_kline=kline_for, get_kline_raw=raw_kline_for),
    )

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    row = _row_for(report, "平安银行")
    assert "波动率" in row  # ±5% 交替 → 年化 ~78% → 零分档进风险列
    assert "最大回撤" in row  # 慢下行累积 → 回撤 >25% → 零分档进风险列
    assert "日均成交" in row  # 5 万 << 默认 5000 万 → 流动性提示


def test_run_surfaces_sustainability_warnings(monkeypatch, config_dir, tmp_path):
    def health(code, cache_dir, cache_days=30):
        return {
            "eps_growth": -12.6,
            "eps_period": "2026-06-30",
            "op_cash_per_share": 0.5,
            "payout_stmt": 118.0,
        }

    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=health))

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    row = _row_for(report, "平安银行")
    assert "盈利下滑 13%（2026中报）" in row
    # TTM 每股分红 1.0 ÷ 每股经营现金流 0.5 = 200%
    assert "分红超现金流 200%" in row
    other = _row_for(report, "红利ETF")
    assert "盈利下滑" not in other  # 基金不参与个股财报检查


def test_run_includes_market_header(monkeypatch, config_dir, tmp_path):
    _patch_data(monkeypatch, **_happy_overrides())

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "【板块温度计】" in report
    assert "中证红利股息率 4.20%" in report
    assert "10Y国债 1.80%" in report
    assert "利差 +2.40pct" in report
    # 上证红利 PE 分位复用 get_index_pe_history 的 fixture
    assert "上证红利PE分位" in report


def test_run_includes_price_position_in_rows(monkeypatch, config_dir, tmp_path):
    def kline_for(code, days=120, as_of=None):
        if code == STOCK_B:
            return _kline([20.0] * 60 + [10.0] * 10)  # 最新价处于窗口最低 → 低位
        return KLINE_A  # [9]*60 + [10]*10 → 最新价处于窗口最高 → 高位

    _patch_data(monkeypatch, **_happy_overrides(get_kline=kline_for))

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "120日全收益高位 100%" in _row_for(report, "平安银行")
    assert "120日全收益低位 0%" in _row_for(report, "招商银行")


def test_run_position_window_follows_kline_days(monkeypatch, config_dir, tmp_path):
    """窗口文案必须随 data.kline_days 走——钉住 main 的透传，否则会静默回归。"""
    _patch_data(monkeypatch, **_happy_overrides())
    _write_rules(config_dir, kline_days=60)

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "60日全收益高位 100%" in _row_for(report, "平安银行")
    assert "120日" not in report


def test_run_all_sources_failing_reports_insufficient_data(
    monkeypatch, config_dir, tmp_path
):
    _patch_data(monkeypatch)  # 所有数据源返回 None

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    for name in ("平安银行", "招商银行", "红利ETF"):
        row = _row_for(report, name)
        assert "N/A" in row and "数据不足" in row
    assert "数据不足 3" in report
    assert "【板块温度计】" not in report  # 温度计数据全部缺失时不显示该行


def test_run_spot_table_failure_warns_once_not_per_stock(
    monkeypatch, config_dir, tmp_path, caplog
):
    """整张行情表不可用时只保留表级告警，不再逐股重复误导性提示。"""
    _patch_data(monkeypatch)  # 所有数据源返回 None，含行情表

    with caplog.at_level(logging.WARNING):
        main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "get_stock_spot 获取失败" in caplog.text  # 表级失败仍有告警
    assert "不在行情表中" not in caplog.text  # 不逐股重复


def test_run_configures_cache_before_reset(monkeypatch, config_dir, tmp_path):
    """顺序：configure_cache 必须在 reset_quote_source_state 之前（才能读到判定）。"""
    order = []
    monkeypatch.setattr(
        data,
        "configure_cache",
        lambda cache_dir, **ttls: order.append("configure"),
    )
    monkeypatch.setattr(data, "reset_quote_source_state", lambda: order.append("reset"))
    _patch_data(monkeypatch, **_happy_overrides())

    main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert order[:2] == ["configure", "reset"]


def test_run_configures_market_cache(monkeypatch, config_dir, tmp_path):
    calls = []
    monkeypatch.setattr(
        data,
        "configure_cache",
        lambda cache_dir, **ttls: calls.append((cache_dir, ttls)),
    )
    _patch_data(monkeypatch, **_happy_overrides())

    main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    # rules.yaml 的三档单位各不相同（分钟/小时/天），换算错了这里会差 60 倍
    assert calls == [
        (
            tmp_path,
            {
                "live_seconds": 15 * 60,
                "daily_seconds": 3 * 3600,
                "slow_seconds": 2 * 86400,
            },
        )
    ]


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

    def get_fund_quotes(fund_type, codes):
        calls["quotes"].append((fund_type, tuple(codes)))
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
    assert calls["quotes"] == [("etf", (FUND_CODE,))]  # 每种基金类型只拉一次行情
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
        ("dividend_index.yaml", CONSTITUENTS_CONFIG),
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


SCAN_STUB = {
    "report": "扫描 0 只标的",
    "stock_results": [],
    "rules": {"stocks": {"indicators": {}}},
    "output": {"buy_top_n": 5, "avoid_bottom_n": 5},
}


def _patch_scan(monkeypatch, **overrides):
    monkeypatch.setattr(main, "run_scan", lambda *a, **k: {**SCAN_STUB, **overrides})


class _FakeStdout:
    """够用的 stdout 替身：可指定是否为 TTY 并收集输出。"""

    def __init__(self, tty):
        self._tty = tty
        self.text = ""

    def isatty(self):
        return self._tty

    def write(self, text):
        self.text += text

    def flush(self):
        pass


def test_run_returns_report_text(monkeypatch):
    """run() 仍是「返回报告字符串」的入口（交互式所需数据由 run_scan 提供）。"""
    _patch_scan(monkeypatch)

    assert main.run() == "扫描 0 只标的"


def test_main_prints_report_and_returns_zero(monkeypatch, capsys):
    _patch_scan(monkeypatch)

    assert main.main([]) == 0

    captured = capsys.readouterr()
    assert "扫描 0 只标的" in captured.out


def test_main_enters_interactive_session_on_tty(monkeypatch):
    _patch_scan(monkeypatch, stock_results=[{"code": "600036"}])
    fake_stdout = _FakeStdout(tty=True)
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    calls = []
    monkeypatch.setattr(
        main.interactive, "session", lambda *args: calls.append(args)
    )

    assert main.main([]) == 0

    assert "扫描 0 只标的" in fake_stdout.text
    assert calls == [([{"code": "600036"}], {"indicators": {}}, SCAN_STUB["output"])]


def test_main_skips_interactive_without_tty(monkeypatch):
    _patch_scan(monkeypatch)
    monkeypatch.setattr(sys, "stdout", _FakeStdout(tty=False))
    calls = []
    monkeypatch.setattr(main.interactive, "session", lambda *args: calls.append(args))

    main.main([])

    assert calls == []  # 管道/重定向/Docker 下不阻塞


def test_main_no_interactive_flag_skips_session(monkeypatch):
    _patch_scan(monkeypatch)
    monkeypatch.setattr(sys, "stdout", _FakeStdout(tty=True))
    calls = []
    monkeypatch.setattr(main.interactive, "session", lambda *args: calls.append(args))

    main.main(["--no-interactive"])

    assert calls == []


def test_main_reports_config_error_and_returns_one(monkeypatch, capsys):
    def failing_scan():
        raise config.ConfigError("stocks.yaml: 配置文件不存在")

    monkeypatch.setattr(main, "run_scan", failing_scan)

    assert main.main([]) == 1

    captured = capsys.readouterr()
    assert "stocks.yaml: 配置文件不存在" in captured.err


def test_run_etf_discount_falls_back_to_latest_nav(monkeypatch, config_dir, tmp_path):
    """腾讯回退路径无 IOPV：折溢价用最新单位净值代理（日频口径）。"""
    quotes_without_iopv = pd.DataFrame(
        {"code": [FUND_CODE], "name": ["红利ETF"], "price": [1.02], "iopv": [None]}
    )
    _patch_data(
        monkeypatch,
        **_happy_overrides(
            get_fund_quotes=lambda fund_type, codes: quotes_without_iopv,
            get_fund_latest_nav=lambda code: 1.0,
        ),
    )

    report = main.run(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert "折溢价 +2.00%" in _row_for(report, "红利ETF")


def test_run_scan_exposes_snapshot_fields(monkeypatch, config_dir, tmp_path):
    """快照入口依赖 run_scan 返回的四个新键：market/fund_results/elapsed/as_of。"""
    _patch_data(monkeypatch, **_happy_overrides())

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert scan["as_of"] == AS_OF
    assert isinstance(scan["elapsed"], float) and scan["elapsed"] >= 0
    assert scan["market"]["dividend_yield"] == pytest.approx(4.2)
    assert scan["market"]["bond_10y"] == pytest.approx(1.8)
    assert [r["code"] for r in scan["stock_results"]] == [STOCK_A, STOCK_B]
    assert [r["code"] for r in scan["fund_results"]] == [FUND_CODE]
