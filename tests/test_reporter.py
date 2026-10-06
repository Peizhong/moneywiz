"""src.reporter 输出层（信号判定、排序、表格与摘要）的单元测试。

全部使用内联 result dict 构造，不联网、不依赖 akshare 与配置加载。
"""

import pytest

from src.reporter import render_report, signal_for

OUTPUT_CFG = {"buy_top_n": 5, "avoid_bottom_n": 5}

STOCK_HEADER = "【股票】"
FUND_HEADER = "【基金】"


def _result(code, name, total, scores, missing=None, tech=None):
    return {
        "code": code,
        "name": name,
        "total": total,
        "values": {},
        "scores": scores,
        "missing": missing or [],
        "tech": tech,
    }


STOCK_80 = _result(
    "600036",
    "招商银行",
    80.0,
    scores={
        "dividend_yield": 1.0,
        "dividend_years": 0.5,
        "payout_ratio": 1.0,
        "pe_vs_industry": 0.0,
    },
    tech={"macd": "金叉", "rsi": 56.0},
)
STOCK_40 = _result(
    "000001",
    "平安银行",
    40.0,
    scores={"dividend_yield": 0.0, "dividend_years": 1.0},
    missing=["payout_ratio"],
    tech={"macd": "死叉", "rsi": None},
)
STOCK_NONE = _result(
    "600000",
    "浦发银行",
    None,
    scores={},
    missing=["dividend_yield", "dividend_years"],
    tech=None,
)
# 基金 result 依契约不含 tech 键（仅股票有）
FUND_70 = {
    "code": "510880",
    "name": "红利ETF",
    "total": 70.0,
    "values": {},
    "scores": {"discount_rate": 1.0, "index_pe_vs_history": 0.0},
    "missing": [],
}

STOCK_PARTIAL = _result(
    "000002",
    "部分覆盖",
    87.5,
    scores={"dividend_yield": 1.0},
    missing=[
        "dividend_years",
        "payout_ratio",
        "pe_vs_industry",
        "pb_vs_industry",
        "ma60_position",
        "momentum_5d",
    ],
)
STOCK_FULL = _result(
    "600000",
    "全覆盖",
    50.0,
    scores={
        "dividend_yield": 1.0,
        "dividend_years": 0.5,
        "payout_ratio": 1.0,
        "pe_vs_industry": 0.5,
        "pb_vs_industry": 1.0,
        "ma60_position": 0.5,
        "momentum_5d": 1.0,
    },
)
STOCK_UNSCORED = _result(
    "600001",
    "全缺失",
    None,
    scores={},
    missing=[
        "dividend_yield",
        "dividend_years",
        "payout_ratio",
        "pe_vs_industry",
        "pb_vs_industry",
        "ma60_position",
        "momentum_5d",
    ],
)
FUND_UNSCORED = {
    "code": "510881",
    "name": "无分基金",
    "total": None,
    "values": {},
    "scores": {},
    "missing": [
        "discount_rate",
        "nav_trend_20d",
        "index_pe_vs_history",
        "dividend_frequency",
        "fund_size",
    ],
}


def _table_rows(report, header):
    """取出 ``header`` 段落表格的数据行（跳过表头与分隔线，遇下一段落停止）。"""
    lines = report.splitlines()
    start = lines.index(header) + 1
    rows = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped in (STOCK_HEADER, FUND_HEADER) or stripped.startswith("扫描"):
            break
        if not stripped or stripped.startswith("排名") or set(stripped) <= set("- "):
            continue
        rows.append(line)
    return rows


@pytest.mark.parametrize(
    ("rank", "total_count", "buy_top_n", "avoid_bottom_n", "has_score", "expected"),
    [
        # 无分优先于排名判定
        (1, 3, 5, 5, False, "数据不足"),
        (3, 3, 5, 5, False, "数据不足"),
        # n=6, buy=5, avoid=5 的边界
        (1, 6, 5, 5, True, "买入"),
        (5, 6, 5, 5, True, "买入"),
        (6, 6, 5, 5, True, "回避"),
        # n=3, buy=5：买入名额多于标的，全部买入
        (1, 3, 5, 5, True, "买入"),
        (2, 3, 5, 5, True, "买入"),
        (3, 3, 5, 5, True, "买入"),
        # 中间档为观察；回避区边界 (total_count - avoid_bottom_n)
        (5, 10, 2, 2, True, "观察"),
        (8, 10, 2, 2, True, "观察"),
        (9, 10, 2, 2, True, "回避"),
        (10, 10, 2, 2, True, "回避"),
    ],
)
def test_signal_for_rank_boundaries(
    rank, total_count, buy_top_n, avoid_bottom_n, has_score, expected
):
    assert (
        signal_for(rank, total_count, buy_top_n, avoid_bottom_n, has_score)
        == expected
    )


def test_render_report_stock_table_order_signals_and_labels():
    report = render_report([STOCK_40, STOCK_NONE, STOCK_80], [FUND_70], OUTPUT_CFG, 3.2)

    stock_rows = _table_rows(report, STOCK_HEADER)
    assert len(stock_rows) == 3

    first = stock_rows[0]
    assert "600036" in first and "招商银行" in first
    assert "80.0" in first
    assert "买入" in first
    assert "股息率、派息率" in first  # 档位 1.0 的指标，最多 2 个
    assert "PE估值" in first  # 档位 0.0 的指标
    assert "MACD 金叉 RSI 56" in first

    last = stock_rows[-1]
    assert "600000" in last
    assert "N/A" in last
    assert "数据不足" in last


def test_render_report_fund_table_and_summary():
    report = render_report([STOCK_40, STOCK_NONE, STOCK_80], [FUND_70], OUTPUT_CFG, 3.2)

    fund_rows = _table_rows(report, FUND_HEADER)
    assert len(fund_rows) == 1
    assert "510880" in fund_rows[0] and "红利ETF" in fund_rows[0]
    assert "70.0" in fund_rows[0]

    assert "扫描 4 只标的（股票 3 / 基金 1）" in report
    assert "数据不足 1" in report
    assert "耗时 3.2s" in report


def test_render_report_partial_coverage_disclosed():
    report = render_report([STOCK_PARTIAL], [], OUTPUT_CFG, 1.0)

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "87.5" in row and "1/7" in row
    assert "指标不全 1" in report


def test_render_report_full_coverage_has_no_partial_count():
    report = render_report([STOCK_FULL], [], OUTPUT_CFG, 1.0)

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "50.0" in row and "7/7" in row
    assert "指标不全 0" in report


def test_render_report_unscored_coverage_is_zero_of_all():
    report = render_report([STOCK_UNSCORED], [FUND_UNSCORED], OUTPUT_CFG, 0.0)

    stock_row = _table_rows(report, STOCK_HEADER)[0]
    fund_row = _table_rows(report, FUND_HEADER)[0]
    assert "N/A" in stock_row and "0/7" in stock_row
    assert "N/A" in fund_row and "0/5" in fund_row
    # 完全不可评分的标的不计入「指标不全」，只计入「数据不足」
    assert "数据不足 2" in report
    assert "指标不全 0" in report


def test_render_report_sorts_none_last_and_ties_by_code():
    tied_high = _result("600036", "乙", 40.0, scores={"dividend_yield": 1.0})
    tied_low = _result("000001", "甲", 40.0, scores={"dividend_yield": 0.5})
    no_score = _result("300750", "丙", None, scores={})

    report = render_report([no_score, tied_high, tied_low], [], OUTPUT_CFG, 1.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "000001" in rows[0]
    assert "600036" in rows[1]
    assert "300750" in rows[2]


def test_render_report_uses_output_cfg_thresholds():
    results = [
        _result("000001", "甲", 90.0, scores={"dividend_yield": 1.0}),
        _result("000002", "乙", 60.0, scores={"dividend_yield": 0.5}),
        _result("000003", "丙", 30.0, scores={"dividend_yield": 0.0}),
    ]

    report = render_report(results, [], {"buy_top_n": 1, "avoid_bottom_n": 1}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "买入" in rows[0]
    assert "观察" in rows[1]
    assert "回避" in rows[2]


def test_render_report_none_scores_render_dashes():
    # 契约允许 scores 为 None（如拉取失败），亮点/风险/技术均为 "-"
    item = _result("000001", "甲", None, scores=None)

    report = render_report([item], [], OUTPUT_CFG, 0.0)

    row = _table_rows(report, STOCK_HEADER)[0]
    assert row.split()[-3:] == ["-", "-", "-"]
    assert "数据不足" in row


def test_render_report_empty_lists_render_placeholders():
    report = render_report([], [], OUTPUT_CFG, 0.0)

    assert STOCK_HEADER in report
    assert FUND_HEADER in report
    assert "(无)" in report
    assert "扫描 0 只标的（股票 0 / 基金 0）" in report
    assert "数据不足 0" in report
