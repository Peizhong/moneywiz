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
        (6, 6, 5, 5, True, "末位"),
        # n=3, buy=5：买入名额多于标的，全部买入
        (1, 3, 5, 5, True, "买入"),
        (2, 3, 5, 5, True, "买入"),
        (3, 3, 5, 5, True, "买入"),
        # 中间档为观察；末位区边界 (total_count - avoid_bottom_n)
        (5, 10, 2, 2, True, "观察"),
        (8, 10, 2, 2, True, "观察"),
        (9, 10, 2, 2, True, "末位"),
        (10, 10, 2, 2, True, "末位"),
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


def test_ties_share_rank_and_boundary_signal():
    # 6 只同分：同属并列第 1，买入名额不因代码排序把同分裂开
    results = [
        _result(f"60{i:04d}", f"股{i}", 96.7, scores={"dividend_yield": 1.0})
        for i in range(1, 7)
    ]
    report = render_report(results, [], {"buy_top_n": 5, "avoid_bottom_n": 1}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert all("并列6" in row for row in rows)
    assert all("买入" in row for row in rows)


def test_competition_rank_skips_after_tie_group():
    results = [
        _result("000001", "甲", 90.0, scores={"dividend_yield": 1.0}),
        _result("000002", "乙", 90.0, scores={"dividend_yield": 1.0}),
        _result("000003", "丙", 30.0, scores={"dividend_yield": 0.0}),
    ]
    report = render_report(results, [], {"buy_top_n": 5, "avoid_bottom_n": 5}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "1(并列2)" in rows[0] and "1(并列2)" in rows[1]
    assert "3" in rows[2].split() and "并列" not in rows[2]


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
    assert "末位" in rows[2]
    assert "回避" not in report


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


def _detailed_result(position):
    result = _result(
        "600036",
        "招商银行",
        80.0,
        scores={"dividend_yield": 1.0, "payout_ratio": 1.0, "pe_vs_industry": 0.0},
    )
    result["values"] = {
        "dividend_yield": 4.5,
        "payout_ratio": 55.0,
        "pe_vs_industry": 42.3,
    }
    result["position"] = position
    return result


def test_highlights_and_risks_include_values_and_low_position():
    report = render_report([_detailed_result(23.4)], [], OUTPUT_CFG, 1.0)

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "股息率 4.5%" in row  # 亮点带数值
    assert "派息率 55%" in row
    assert "PE估值 +42%" in row  # 风险带数值（偏离行业为正）
    assert "120日低位 23%" in row  # 低位并入亮点文字


def test_high_and_middle_position_phrases():
    row = _table_rows(
        render_report([_detailed_result(82.2)], [], OUTPUT_CFG, 1.0), STOCK_HEADER
    )[0]
    assert "120日高位 82%" in row  # 高位并入风险文字

    row = _table_rows(
        render_report([_detailed_result(55.0)], [], OUTPUT_CFG, 1.0), STOCK_HEADER
    )[0]
    assert "120日中位 55%" in row

    row = _table_rows(
        render_report([_detailed_result(None)], [], OUTPUT_CFG, 1.0), STOCK_HEADER
    )[0]
    assert "120日" not in row  # 无 K 线数据时不显示位置


MARKET = {
    "dividend_yield": 4.14,
    "bond_10y": 1.82,
    "spread": 2.32,
    "index_pe_position": 14.0,
}


def test_sustainability_warnings_join_risk_column():
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["sustainability"] = {
        "eps_growth": -12.6,
        "op_cash_per_share": 0.5,
        "cash_cover": 200.4,
    }

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "盈利下滑 13%" in row
    assert "分红超现金流 200%" in row


def test_dividend_trend_detail_format():
    from src.reporter import _detail_text

    assert (
        _detail_text({"dividend_trend": 1.0}, {"dividend_trend": -12.3}, 1.0)
        == "分红趋势 -12%"
    )
    assert (
        _detail_text({"dividend_trend": 1.0}, {"dividend_trend": 55.0}, 1.0)
        == "分红趋势 +55%"
    )


def test_risk_hints_for_volatility_and_turnover():
    result = _result("600036", "招商银行", 70.0, scores={"dividend_yield": 1.0})
    result["volatility"] = 48.2
    result["turnover_wan"] = 3200.0

    report = render_report(
        [result],
        [],
        OUTPUT_CFG,
        1.0,
        risk_hints={"volatility_high": 35, "turnover_low": 5000},
    )

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "波动率 48%" in row
    assert "日均成交 3200万" in row


def test_risk_hints_respect_thresholds_and_large_amount_scale():
    result = _result("600036", "招商银行", 70.0, scores={"dividend_yield": 1.0})
    result["volatility"] = 20.0  # 低于阈值 → 不提示
    result["turnover_wan"] = 15000.0

    report = render_report(
        [result], [], OUTPUT_CFG, 1.0, risk_hints={"turnover_low": 20000}
    )

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "波动率" not in row
    assert "日均成交 1.5亿" in row  # 大额以亿显示


def test_negative_operating_cash_flow_warning():
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["sustainability"] = {
        "eps_growth": 5.0,
        "op_cash_per_share": -0.2,
        "cash_cover": None,
    }

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "经营现金流为负" in row
    assert "盈利下滑" not in row  # 增长为正不提示


def test_new_constituent_marker_in_name():
    result = _result("601088", "中国神华", 80.0, scores={"dividend_yield": 1.0})
    result["new_constituent"] = True

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "中国神华(新增)" in row

    result["new_constituent"] = False
    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]
    assert "中国神华" in row and "(新增)" not in row


def test_market_header_rendered_before_tables():
    report = render_report([STOCK_80], [], OUTPUT_CFG, 1.0, market=MARKET)

    assert "【板块温度计】" in report
    assert "中证红利股息率 4.14%" in report
    assert "10Y国债 1.82%" in report
    assert "利差 +2.32pct" in report
    assert "上证红利PE分位 14%" in report
    assert report.index("【板块温度计】") < report.index(STOCK_HEADER)


def test_market_header_absent_without_market():
    report = render_report([STOCK_80], [], OUTPUT_CFG, 1.0)

    assert "【板块温度计】" not in report


def test_market_header_skips_missing_fields():
    partial = dict(MARKET, bond_10y=None, spread=None, index_pe_position=None)
    report = render_report([STOCK_80], [], OUTPUT_CFG, 1.0, market=partial)
    assert "中证红利股息率 4.14%" in report
    assert "10Y国债" not in report and "利差" not in report

    empty = {key: None for key in MARKET}
    report = render_report([STOCK_80], [], OUTPUT_CFG, 1.0, market=empty)
    assert "【板块温度计】" not in report


def test_detail_falls_back_to_label_without_value():
    from src.reporter import _detail_text

    # 数值缺失（旧契约）时仅显示指标名；数值存在时带格式化的值
    assert _detail_text({"dividend_yield": 1.0}, {}, 1.0) == "股息率"
    assert (
        _detail_text({"dividend_yield": 1.0}, {"dividend_yield": 4.5}, 1.0)
        == "股息率 4.5%"
    )
    # 未知指标名与非法数值都不抛异常
    assert _detail_text({"custom_x": 1.0}, {"custom_x": 3}, 1.0) == "custom_x"
    assert _detail_text({"fund_size": 1.0}, {"fund_size": "n/a"}, 1.0) == "基金规模"
