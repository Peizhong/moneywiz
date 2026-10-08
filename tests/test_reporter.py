"""src.reporter 输出层（信号判定、排序、表格与摘要）的单元测试。

全部使用内联 result dict 构造，不联网、不依赖 akshare 与配置加载。
"""

import pytest

from src.reporter import (
    _competition_ranks,
    _signals,
    ranked_rows,
    render_detail,
    render_report,
)

OUTPUT_CFG = {"buy_top_n": 5, "avoid_bottom_n": 5}

# render_detail 收的是 rules 的**股票段**（main.py 的 interactive.session 传
# rules["stocks"]），不是整份 rules——这里给最小可用的规则段
RULES_SECTION = {
    "indicators": {
        "dividend_yield": {"weight": 100, "thresholds": {"high": 4.0, "mid": 2.0}},
    },
}

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


def _signals_at(rank, total_count, buy_top_n, avoid_bottom_n, has_score, position=None):
    """构造 total_count 只标的（名次 = 下标 + 1），返回第 rank 名的信号。"""
    items = []
    for index in range(1, total_count + 1):
        total = None if (index == rank and not has_score) else float(1000 - index)
        item = _result(
            f"60{index:04d}", f"股{index}", total, scores={"dividend_yield": 1.0}
        )
        if index == rank and position is not None:
            item["position"] = position
        items.append(item)
    ranks = _competition_ranks(items)
    return _signals(items, ranks, total_count, buy_top_n, avoid_bottom_n)[rank - 1]


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
def test_signals_rank_boundaries(
    rank, total_count, buy_top_n, avoid_bottom_n, has_score, expected
):
    assert (
        _signals_at(rank, total_count, buy_top_n, avoid_bottom_n, has_score)
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


def test_high_position_stock_not_marked_buy_and_slot_passes_down():
    results = [
        _result(f"60000{i}", f"股{i}", float(100 - i), scores={"dividend_yield": 1.0})
        for i in range(1, 7)
    ]
    results[4]["position"] = 89.0  # 第 5 名处于 120 日高位

    report = render_report(results, [], {"buy_top_n": 5, "avoid_bottom_n": 1}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "买入" in rows[0] and "买入" in rows[3]
    assert "观察" in rows[4] and "买入" not in rows[4]
    assert "120日高位 89%" in rows[4]
    assert "买入" in rows[5]  # 空出的名额顺延给第 6 名


def test_high_position_extension_keeps_tie_group_whole():
    results = [
        _result("600001", "甲", 100.0, scores={"dividend_yield": 1.0}),
        _result("600002", "乙", 99.0, scores={"dividend_yield": 1.0}),
        _result("600003", "丙", 98.0, scores={"dividend_yield": 1.0}),
        _result("600004", "丁", 97.0, scores={"dividend_yield": 1.0}),
        _result("600005", "戊", 96.0, scores={"dividend_yield": 1.0}),  # 高位
        _result("600006", "己", 95.0, scores={"dividend_yield": 1.0}),  # 并列 6
        _result("600007", "庚", 95.0, scores={"dividend_yield": 1.0}),  # 并列 6
        _result("600008", "辛", 60.0, scores={"dividend_yield": 0.5}),
    ]
    results[4]["position"] = 89.0

    report = render_report(results, [], {"buy_top_n": 5, "avoid_bottom_n": 1}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "观察" in rows[4] and "买入" not in rows[4]
    assert "6(并列2)" in rows[5] and "买入" in rows[5]  # 递补整组收发
    assert "6(并列2)" in rows[6] and "买入" in rows[6]
    assert "末位" in rows[7]


def test_high_position_in_bottom_zone_still_marked_avoid():
    results = [
        _result(f"60000{i}", f"股{i}", float(100 - i), scores={"dividend_yield": 1.0})
        for i in range(1, 7)
    ]
    results[5]["position"] = 95.0  # 末位区且高位

    report = render_report(results, [], {"buy_top_n": 5, "avoid_bottom_n": 1}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "末位" in rows[5]  # 高位不影响末位判定
    assert "120日高位 95%" in rows[5]


def test_high_position_row_with_oversized_avoid_zone_stays_watch():
    # 表内标的少于 avoid_bottom_n（如仅 1 只基金、avoid=5）：末位区覆盖全表，
    # 高位被剥夺「买入」的标的应落为「观察」，不能误标「末位」
    item = _result("510880", "红利ETF", 67.5, scores={"dividend_yield": 1.0})
    item["position"] = 78.0

    report = render_report([item], [], {"buy_top_n": 5, "avoid_bottom_n": 5}, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "观察" in rows[0] and "末位" not in rows[0]


def test_buy_eligibility_uses_same_threshold_as_high_position_phrase():
    at_threshold = _result("600001", "甲", 100.0, scores={"dividend_yield": 1.0})
    at_threshold["position"] = 70.0  # 与「120日高位」同一阈值：≥70 即高位
    below = _result("600002", "乙", 90.0, scores={"dividend_yield": 1.0})
    below["position"] = 69.9

    report = render_report(
        [at_threshold, below], [], {"buy_top_n": 5, "avoid_bottom_n": 0}, 0.0
    )

    rows = _table_rows(report, STOCK_HEADER)
    assert "观察" in rows[0] and "120日高位 70%" in rows[0]
    assert "买入" in rows[1]


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
    result["sustainability"] = {"eps_period": None}
    result["sustainability_flags"] = [
        {"key": "eps_decline", "dimension": "profit", "value": -12.6, "penalty": 5.0},
        {"key": "cash_cover", "dimension": "coverage", "value": 200.4, "penalty": 10.0},
    ]

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "盈利下滑 13%" in row
    assert "分红超现金流 200%" in row


def test_sustainability_phrase_includes_report_period_label():
    result = _result("600690", "海尔智家", 87.8, scores={"dividend_yield": 1.0})
    result["sustainability"] = {"eps_period": "2026-06-30"}
    result["sustainability_flags"] = [
        {"key": "eps_decline", "dimension": "profit", "value": -12.9, "penalty": 5.0},
    ]

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "盈利下滑 13%（2026中报）" in row


def test_sustainability_period_label_mapping():
    from src.reporter import _sustainability_phrases

    flag = {"key": "eps_decline", "dimension": "profit", "value": -5.0, "penalty": 5.0}

    def phrase(period):
        return _sustainability_phrases([flag], {"eps_period": period})[0]

    assert phrase("2026-03-31") == "盈利下滑 5%（2026一季报）"
    assert phrase("2026-06-30") == "盈利下滑 5%（2026中报）"
    assert phrase("2026-09-30") == "盈利下滑 5%（2026三季报）"
    assert phrase("2025-12-31") == "盈利下滑 5%（2025年报）"
    assert phrase(None) == "盈利下滑 5%"  # 无报告期 → 退回旧文案
    assert phrase("not-a-date") == "盈利下滑 5%"


def test_sustainability_phrases_render_all_keys():
    from src.reporter import _sustainability_phrases

    def phrase(flag, period=None):
        return _sustainability_phrases([flag], {"eps_period": period})[0]

    assert phrase({"key": "eps_decline", "value": -13.0}, "2026-06-30") == "盈利下滑 13%（2026中报）"
    assert phrase({"key": "eps_decline", "value": -13.0}) == "盈利下滑 13%"
    assert phrase({"key": "eps_repeated", "value": 3, "window": 4}) == "盈利连续下滑 3/4 期"
    assert phrase({"key": "negative_cash", "value": -0.1}) == "经营现金流为负"
    assert phrase({"key": "cash_decline", "value": -22.0}) == "现金流下滑 22%"
    assert phrase({"key": "cash_repeated", "value": 3, "window": 4}) == "现金流连续下滑 3/4 期"
    assert phrase({"key": "cash_cover", "value": 118.0}) == "分红超现金流 118%"
    assert phrase({"key": "debt_jump", "value": 12.0}) == "负债率上升 12pp"
    assert phrase({"key": "interest_cover", "value": 1.4}) == "利息保障 1.4 倍"


def test_sustainability_phrases_skip_unknown_and_missing_flags():
    from src.reporter import _sustainability_phrases

    assert _sustainability_phrases([{"key": "future_key", "value": 1.0}]) == []
    assert _sustainability_phrases([]) == []
    assert _sustainability_phrases(None) == []


def test_risk_column_holds_six_items():
    # 4 条可持续性 + 流动性提示 + 高位提示 = 6 项，全部保留（原上限 4 会砍掉后两项）
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["sustainability_flags"] = [
        {"key": "eps_repeated", "dimension": "profit", "value": 3, "window": 4, "penalty": 15.0},
        {"key": "negative_cash", "dimension": "cashflow", "value": -0.1, "penalty": 15.0},
        {"key": "cash_cover", "dimension": "coverage", "value": 200.0, "penalty": 10.0},
        {"key": "debt_jump", "dimension": "debt", "value": 12.0, "penalty": 10.0},
    ]
    result["position"] = 82.0     # ≥ POSITION_HIGH → 高位提示
    result["turnover_wan"] = 1200.0  # < turnover_low(5000) → 流动性提示

    row = _table_rows(
        render_report([result], [], OUTPUT_CFG, 1.0, risk_hints={"turnover_low": 5000}),
        STOCK_HEADER,
    )[0]

    for phrase in ("盈利连续下滑 3/4 期", "经营现金流为负", "分红超现金流 200%",
                   "负债率上升 12pp", "日均成交 1200万", "120日高位 82%"):
        assert phrase in row


def test_sustainability_none_renders_no_warning():
    # Review Focus 5：sustainability 与 flags 均缺失 → 三处调用点都不出文案、不崩溃
    result = _result("000001", "平安银行", 80.0, scores={"dividend_yield": 1.0})
    result["values"] = {"dividend_yield": 5.0}  # 有贡献 → pre_total 非 None，详情页走到扣分链
    result["sustainability"] = None
    result.pop("sustainability_flags", None)

    report = render_report([result], [], OUTPUT_CFG, 1.0)
    detail = render_detail(ranked_rows([result], OUTPUT_CFG)[0], RULES_SECTION)

    assert "盈利下滑" not in report and "利息保障" not in report
    assert "加权小计 100.0" in detail  # 防惰性：确实走到了扣分链（而非「加权小计 N/A」）
    assert "可持续性扣分" not in detail


def test_dividend_yield_percentile_highlight_and_risk_text():
    good = _result("600100", "甲", 90.0, scores={"dividend_yield_percentile": 1.0})
    good["values"] = {"dividend_yield_percentile": 92.0}
    bad = _result("600101", "乙", 40.0, scores={"dividend_yield_percentile": 0.0})
    bad["values"] = {"dividend_yield_percentile": 12.0}

    report = render_report([good, bad], [], OUTPUT_CFG, 0.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "股息率分位 92%" in rows[0]  # 满分档 → 亮点
    assert "股息率分位 12%" in rows[1]  # 零分档 → 风险


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


def test_risk_hint_for_low_turnover():
    result = _result("600036", "招商银行", 70.0, scores={"dividend_yield": 1.0})
    result["turnover_wan"] = 3200.0

    report = render_report(
        [result], [], OUTPUT_CFG, 1.0, risk_hints={"turnover_low": 5000}
    )

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "日均成交 3200万" in row


def test_risk_hint_respects_turnover_threshold_and_large_amount_scale():
    result = _result("600036", "招商银行", 70.0, scores={"dividend_yield": 1.0})
    result["turnover_wan"] = 15000.0

    report = render_report(
        [result], [], OUTPUT_CFG, 1.0, risk_hints={"turnover_low": 20000}
    )

    row = _table_rows(report, STOCK_HEADER)[0]
    assert "日均成交 1.5亿" in row  # 大额以亿显示


def test_stability_tiers_show_in_highlight_and_risk_columns():
    calm = _result(
        "600900", "长江电力", 80.0, scores={"volatility": 1.0, "max_drawdown": 1.0}
    )
    calm["values"] = {"volatility": 13.3, "max_drawdown": 10.4}
    wild = _result(
        "002532", "天山铝业", 60.0, scores={"volatility": 0.0, "max_drawdown": 0.0}
    )
    wild["values"] = {"volatility": 51.0, "max_drawdown": 47.4}

    report = render_report([calm, wild], [], OUTPUT_CFG, 1.0)

    rows = _table_rows(report, STOCK_HEADER)
    assert "波动率 13.3%" in rows[0]  # 满分档 → 亮点
    assert "最大回撤 10.4%" in rows[0]
    assert "波动率 51.0%" in rows[1]  # 零分档 → 风险（不再有重复的提示行）
    assert "最大回撤 47.4%" in rows[1]


def test_negative_operating_cash_flow_warning():
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["sustainability"] = {"eps_growth": 5.0}  # 盈利为正（真实数据、无对应红标）
    result["sustainability_flags"] = [
        {"key": "negative_cash", "dimension": "cashflow", "value": -0.2, "penalty": 15.0},
    ]

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]

    assert "经营现金流为负" in row
    assert "盈利下滑" not in row  # 盈利为正 → 判定层不出 profit 红标 → 展示层不得凭空提示


def test_display_layer_renders_flags_only_never_rejudges_thresholds():
    # 本任务的核心不变量：展示层只看 flags。sustainability 里放的是**会触发旧逻辑**的原始值
    # （旧实现直接判 eps_growth<0 / op_cash<=0 / cash_cover>100，输出「盈利下滑」与
    # 「经营现金流为负」两条文案——cash_cover 那条被 elif 短路），
    # 但判定层的 flags 为空 → 风险列与详情扣分链一条红标文案都不许出。
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["values"] = {"dividend_yield": 5.0}  # 有贡献 → pre_total 非 None，详情页走到扣分链
    result["sustainability"] = {
        "eps_growth": -12.0,
        "op_cash_per_share": -0.2,
        "cash_cover": 200.0,
    }
    result["sustainability_flags"] = []

    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]
    detail = render_detail(ranked_rows([result], OUTPUT_CFG)[0], RULES_SECTION)

    assert "可持续性扣分" in detail  # 防惰性：确实走到了读 flags 的扣分链
    for text in (row, detail):  # 表格风险列与详情扣分链都只许读 flags
        assert "盈利下滑" not in text
        assert "经营现金流为负" not in text
        assert "分红超现金流" not in text


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


# ---------------------------------------------------------------------------
# 交互式详情（ranked_rows / row_label / render_detail）
# ---------------------------------------------------------------------------

DETAIL_RULES = {
    "indicators": {
        "dividend_yield": {"weight": 60, "thresholds": {"high": 4.0, "mid": 2.0}},
        "dividend_years": {"weight": 40, "thresholds": {"high": 5, "mid": 3}},
    },
    "sustainability": {
        "eps_decline_penalty": 10,
        "negative_cash_penalty": 15,
        "cash_cover_penalty": 10,
        "max_penalty": 30,
    },
}

# 股息率 5.0%（满分档 1.0）× 60 + 连续分红 3 年（0.5 档）× 40
# → 加权小计 80.0；盈利下滑红标（DETAIL_RULES 下 10%）→ 扣 10% → 总分 72.0
DETAIL_ITEM = {
    "code": "600036",
    "name": "招商银行",
    "total": 72.0,
    "values": {"dividend_yield": 5.0, "dividend_years": 3},
    "scores": {"dividend_yield": 1.0, "dividend_years": 0.5},
    "missing": [],
    "tech": {"macd": "金叉", "rsi": 56.0},
    "position": 45.0,
    "turnover_wan": 80000.0,
    "sustainability": {
        "eps_growth": -5.0,
        "eps_period": "2026-06-30",
        "op_cash_per_share": 2.0,
        "cash_cover": 50.0,
    },
    "sustainability_flags": [
        {"key": "eps_decline", "dimension": "profit", "value": -5.0, "penalty": 10.0},
    ],
}
DETAIL_ROW = {"item": DETAIL_ITEM, "rank": 1, "tied_count": 1, "signal": "买入"}


def _detail_line(text, needle):
    """详情表里包含 needle 的指标行（排除结尾的「缺失指标」汇总行）。"""
    matches = [
        line
        for line in text.splitlines()
        if needle in line and not line.startswith("缺失指标")
    ]
    assert len(matches) == 1, f"{needle!r} 在详情里出现 {len(matches)} 次"
    return matches[0]


def test_ranked_rows_carry_rank_and_signal():
    from src.reporter import ranked_rows

    rows = ranked_rows([STOCK_80, STOCK_40, STOCK_NONE], OUTPUT_CFG)

    assert [row["item"]["code"] for row in rows] == ["600036", "000001", "600000"]
    assert rows[0]["rank"] == 1 and rows[0]["signal"] == "买入"
    assert rows[-1]["signal"] == "数据不足"  # 总分 None 的标的
    assert rows[-1]["tied_count"] == 1


def test_ranked_rows_share_tie_group():
    from src.reporter import ranked_rows

    first = _result("600036", "招商银行", 80.0, scores={"dividend_yield": 1.0})
    second = _result("000001", "平安银行", 80.0, scores={"dividend_yield": 1.0})

    rows = ranked_rows([first, second], OUTPUT_CFG)

    assert [(row["rank"], row["tied_count"]) for row in rows] == [(1, 2), (1, 2)]


def test_row_label_lists_rank_name_total_and_signal():
    from src.reporter import ranked_rows, row_label

    rows = ranked_rows([STOCK_80, STOCK_NONE], OUTPUT_CFG)

    assert row_label(rows[0]) == "1  600036 招商银行  80.0  买入"
    assert row_label(rows[1]) == "2  600000 浦发银行  N/A  数据不足"


def test_render_detail_shows_breakdown_and_penalty_chain():
    from src.reporter import render_detail

    text = render_detail(DETAIL_ROW, DETAIL_RULES)

    assert "600036 招商银行" in text
    assert "总分 72.0" in text and "排名 1" in text and "信号 买入" in text
    metric = _detail_line(text, "股息率")
    assert "5.0%" in metric and "1.0" in metric and "60" in metric and "60.0" in metric
    assert "加权小计 80.0" in text
    assert "可持续性扣分 10%" in text and "盈利下滑 5%（2026中报）" in text
    assert "缺失指标：无（覆盖 2/2）" in text


def test_render_detail_marks_missing_indicator_rows():
    from src.reporter import render_detail

    item = {
        **DETAIL_ITEM,
        "total": 100.0,
        "values": {"dividend_yield": 5.0},
        "scores": {"dividend_yield": 1.0},
        "missing": ["dividend_years"],
        "sustainability": None,
        "sustainability_flags": [],
    }

    text = render_detail({"item": item, "rank": 2, "tied_count": 1, "signal": "观察"}, DETAIL_RULES)

    metric = _detail_line(text, "连续分红")
    assert "40" in metric  # 权重照常显示
    assert "缺失指标：连续分红（覆盖 1/2）" in text
    assert "可持续性扣分" not in text  # 无财报数据时不显示扣分行


def test_render_detail_without_any_score_states_data_shortage():
    from src.reporter import render_detail

    item = {
        **DETAIL_ITEM,
        "total": None,
        "values": {},
        "scores": {},
        "missing": ["dividend_yield", "dividend_years"],
        "sustainability": None,
        "sustainability_flags": [],
    }

    text = render_detail({"item": item, "rank": 3, "tied_count": 1, "signal": "数据不足"}, DETAIL_RULES)

    assert "总分 N/A" in text and "信号 数据不足" in text
    assert "加权小计 N/A" in text  # 不编造分数
    assert "缺失指标：股息率、连续分红（覆盖 0/2）" in text
