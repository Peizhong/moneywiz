"""src.scorer 打分引擎（档位归一化与加权总分）的单元测试。

全部使用内联字典构造，不联网、不读文件、不依赖配置加载；
阈值与 config/rules.yaml 一致。
"""

import logging

import pytest

from src.scorer import (
    apply_sustainability_penalty,
    evaluate_sustainability,
    normalize,
    score_breakdown,
    score_instrument,
)

# 与 config/rules.yaml 一致的档位阈值
YIELD_TH = {"high": 4.0, "mid": 2.0}
YIELD_PCT_TH = {"high": 70, "mid": 40}
VOL_TH = {"low": 20, "mid": 30}
MAX_DD_TH = {"low": 15, "mid": 25}
YEARS_TH = {"high": 5, "mid": 3}
PAYOUT_TH = {"min": 20, "max": 70}
PE_INDUSTRY_TH = {"discount": -30, "premium": 30}
PB_INDUSTRY_TH = {"discount": -30, "premium": 30}
MA60_TH = {"sweet_low": -5, "sweet_high": 5, "max_deviation": 20}
MOMENTUM_TH = {"oversold": -10, "overbought": 10}
DISCOUNT_TH = {"discount": -1, "premium": 1}
NAV_TREND_TH = {"pullback": -10, "rally": 5}
INDEX_PE_TH = {"low": 30, "high": 70}
FREQUENCY_TH = {"high": 2, "mid": 1}
FUND_SIZE_TH = {"min": 1}


@pytest.mark.parametrize(
    ("indicator", "value", "thresholds", "expected"),
    [
        # dividend_yield {high: 4.0, mid: 2.0}
        ("dividend_yield", 4.0, YIELD_TH, 1.0),
        ("dividend_yield", 3.0, YIELD_TH, 0.5),
        ("dividend_yield", 1.99, YIELD_TH, 0.0),
        # payout_ratio {min: 20, max: 70}
        ("payout_ratio", 50, PAYOUT_TH, 1.0),
        ("payout_ratio", 19, PAYOUT_TH, 0.0),
        ("payout_ratio", 71, PAYOUT_TH, 0.0),
        # pe_vs_industry {discount: -30, premium: 30}
        ("pe_vs_industry", -35, PE_INDUSTRY_TH, 1.0),
        ("pe_vs_industry", 0, PE_INDUSTRY_TH, 0.5),
        ("pe_vs_industry", 35, PE_INDUSTRY_TH, 0.0),
        # ma60_position {sweet_low: -5, sweet_high: 5, max_deviation: 20}
        ("ma60_position", 0, MA60_TH, 1.0),
        ("ma60_position", 12, MA60_TH, 0.5),
        ("ma60_position", 25, MA60_TH, 0.0),
        # index_pe_vs_history {low: 30, high: 70}
        ("index_pe_vs_history", 20, INDEX_PE_TH, 1.0),
        ("index_pe_vs_history", 50, INDEX_PE_TH, 0.5),
        ("index_pe_vs_history", 80, INDEX_PE_TH, 0.0),
        # fund_size {min: 1}
        ("fund_size", 0.5, FUND_SIZE_TH, 0.0),
        ("fund_size", 2, FUND_SIZE_TH, 1.0),
        # 同档位形状的其余指标同样逐档校验
        ("dividend_years", 5, YEARS_TH, 1.0),
        ("dividend_years", 3, YEARS_TH, 0.5),
        ("dividend_years", 2, YEARS_TH, 0.0),
        ("dividend_frequency", 2, FREQUENCY_TH, 1.0),
        ("dividend_frequency", 1, FREQUENCY_TH, 0.5),
        ("dividend_frequency", 0, FREQUENCY_TH, 0.0),
        ("pb_vs_industry", -35, PB_INDUSTRY_TH, 1.0),
        ("pb_vs_industry", 0, PB_INDUSTRY_TH, 0.5),
        ("pb_vs_industry", 35, PB_INDUSTRY_TH, 0.0),
        ("discount_rate", -2, DISCOUNT_TH, 1.0),
        ("discount_rate", 0, DISCOUNT_TH, 0.5),
        ("discount_rate", 2, DISCOUNT_TH, 0.0),
        ("nav_trend_20d", -15, NAV_TREND_TH, 1.0),
        ("nav_trend_20d", 0, NAV_TREND_TH, 0.5),
        ("nav_trend_20d", 10, NAV_TREND_TH, 0.0),
        ("momentum_5d", -15, MOMENTUM_TH, 1.0),
        ("momentum_5d", 0, MOMENTUM_TH, 0.5),
        ("momentum_5d", 15, MOMENTUM_TH, 0.0),
        # dividend_yield_percentile {high: 70, mid: 40}
        ("dividend_yield_percentile", 85, YIELD_PCT_TH, 1.0),
        ("dividend_yield_percentile", 50, YIELD_PCT_TH, 0.5),
        ("dividend_yield_percentile", 20, YIELD_PCT_TH, 0.0),
        # volatility {low: 20, mid: 30}：越低越好
        ("volatility", 13, VOL_TH, 1.0),
        ("volatility", 20, VOL_TH, 1.0),
        ("volatility", 26, VOL_TH, 0.5),
        ("volatility", 41, VOL_TH, 0.0),
        # max_drawdown {low: 15, mid: 25}：越低越好
        ("max_drawdown", 10, MAX_DD_TH, 1.0),
        ("max_drawdown", 15, MAX_DD_TH, 1.0),
        ("max_drawdown", 20, MAX_DD_TH, 0.5),
        ("max_drawdown", 47, MAX_DD_TH, 0.0),
    ],
)
def test_normalize_tier_boundaries(indicator, value, thresholds, expected):
    assert normalize(indicator, value, thresholds) == expected


@pytest.mark.parametrize(
    ("indicator", "thresholds"),
    [
        ("dividend_yield", YIELD_TH),
        ("payout_ratio", PAYOUT_TH),
        ("ma60_position", MA60_TH),
        ("fund_size", FUND_SIZE_TH),
    ],
)
def test_normalize_none_value_returns_none(indicator, thresholds):
    assert normalize(indicator, None, thresholds) is None


RULES_60_40 = {
    "indicators": {
        "dividend_yield": {"weight": 60, "thresholds": YIELD_TH},
        "dividend_years": {"weight": 40, "thresholds": YEARS_TH},
    }
}


def test_score_instrument_weighted_total():
    result = score_instrument(
        {"dividend_yield": 5.0, "dividend_years": 3}, RULES_60_40
    )

    # (1.0 × 60 + 0.5 × 40) / (60 + 40) × 100 = 80.0
    assert result["scores"] == {"dividend_yield": 1.0, "dividend_years": 0.5}
    assert result["total"] == 80.0
    assert result["missing"] == []


@pytest.mark.parametrize("missing_value", [None, "absent"])
def test_score_instrument_missing_indicator_is_renormalized(missing_value):
    values = {"dividend_yield": 5.0}
    if missing_value is None:
        values["dividend_years"] = None

    result = score_instrument(values, RULES_60_40)

    # 分母只含实际参与评分的权重：1.0 × 60 / 60 × 100 = 100.0
    assert result["scores"] == {"dividend_yield": 1.0}
    assert result["total"] == 100.0
    assert result["missing"] == ["dividend_years"]


def test_score_breakdown_rows_carry_weight_and_contribution():
    """逐指标明细：贡献 = 档位分 × 权重 / 已评分权重之和 × 100，各行之和即扣分前总分。"""
    rows = score_breakdown({"dividend_yield": 5.0, "dividend_years": 3}, RULES_60_40)

    assert [row["name"] for row in rows] == ["dividend_yield", "dividend_years"]  # 保持规则顺序
    assert rows[0] == {
        "name": "dividend_yield",
        "value": 5.0,
        "score": 1.0,
        "weight": 60,
        "contribution": 60.0,
    }
    assert rows[1]["score"] == 0.5
    assert rows[1]["contribution"] == pytest.approx(20.0)
    assert sum(row["contribution"] for row in rows) == pytest.approx(score_instrument(
        {"dividend_yield": 5.0, "dividend_years": 3}, RULES_60_40
    )["total"])


def test_score_breakdown_missing_indicator_has_no_contribution():
    """数据缺失的指标仍占一行（让用户看到"缺了什么"），但档位/贡献为 None。"""
    rows = score_breakdown({"dividend_yield": 5.0, "dividend_years": None}, RULES_60_40)

    missing = rows[1]
    assert missing["name"] == "dividend_years"
    assert missing["value"] is None
    assert missing["score"] is None
    assert missing["contribution"] is None
    assert rows[0]["contribution"] == pytest.approx(100.0)  # 分母只含实际参与评分的权重


def test_score_instrument_all_missing_total_is_none():
    result = score_instrument(
        {"dividend_yield": None, "dividend_years": None}, RULES_60_40
    )

    assert result["scores"] == {}
    assert result["total"] is None
    assert result["missing"] == ["dividend_yield", "dividend_years"]


def test_score_instrument_unknown_indicator_warns_and_is_missing(caplog):
    rules = {"indicators": {"mystery": {"weight": 100, "thresholds": {}}}}

    with caplog.at_level(logging.WARNING):
        result = score_instrument({"mystery": 5.0}, rules)

    assert result["scores"] == {}
    assert result["total"] is None
    assert result["missing"] == ["mystery"]
    assert any(
        record.levelno == logging.WARNING and "mystery" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# 分红可持续性扣分（P1）
# ---------------------------------------------------------------------------

PENALTY_CFG = {
    "eps_decline_penalty": 10.0, "eps_repeated_penalty": 15.0,
    "repeated_periods": 4, "repeated_min_negative": 3,
    "negative_cash_penalty": 15.0, "cash_cover_penalty": 10.0,
    "cash_decline_penalty": 5.0, "cash_repeated_penalty": 15.0,
    "debt_jump_penalty": 10.0, "debt_jump_threshold": 10.0,
    "interest_cover_penalty": 10.0, "interest_cover_threshold": 2.0,
    "max_penalty": 45.0,
}


def test_dividend_trend_tier_uses_higher_better():
    assert normalize("dividend_trend", 0.0, {"high": 0, "mid": -30}) == 1.0
    assert normalize("dividend_trend", -10.0, {"high": 0, "mid": -30}) == 0.5
    assert normalize("dividend_trend", -40.0, {"high": 0, "mid": -30}) == 0.0


def test_penalty_single_flags():
    clean = {"eps_growth": 5.0, "op_cash_per_share": 2.0, "cash_cover": 50.0}
    assert apply_sustainability_penalty(80.0, clean, PENALTY_CFG) == (80.0, 0.0)

    declined = {"eps_growth": -5.0, "op_cash_per_share": 2.0, "cash_cover": 50.0}
    assert apply_sustainability_penalty(80.0, declined, PENALTY_CFG) == (72.0, 10.0)

    over_cover = {"eps_growth": 5.0, "op_cash_per_share": 0.5, "cash_cover": 200.0}
    assert apply_sustainability_penalty(80.0, over_cover, PENALTY_CFG) == (72.0, 10.0)

    negative_cash = {"eps_growth": 5.0, "op_cash_per_share": -0.1, "cash_cover": None}
    assert apply_sustainability_penalty(80.0, negative_cash, PENALTY_CFG) == (68.0, 15.0)


def test_penalty_coverage_and_negative_cash_are_separate_dimensions():
    """覆盖（coverage）与现金流的正负（cashflow）是两个独立维度，同时成立时叠加。

    该组合在生产中不可达：``cash_cover`` 仅在 ``op_cash > 0`` 时计算，与「现金流为负」
    天然互斥；这里只是维度聚合逻辑的单元测试。
    """
    both = {"eps_growth": -5.0, "op_cash_per_share": -0.1, "cash_cover": 200.0}
    total, penalty = apply_sustainability_penalty(80.0, both, PENALTY_CFG)
    assert penalty == 35.0  # 10（盈利下滑）+ 15（现金流为负）+ 10（分红超现金流）
    assert total == pytest.approx(52.0)


def test_penalty_cap_and_defaults():
    cfg = dict(PENALTY_CFG, eps_decline_penalty=30.0, negative_cash_penalty=30.0)
    total, penalty = apply_sustainability_penalty(
        80.0, {"eps_growth": -5.0, "op_cash_per_share": -0.1}, cfg
    )
    assert penalty == 45.0  # 30 + 30 = 60 → 封顶 45
    assert total == pytest.approx(44.0)

    # 未提供配置 → 用默认值（单期盈利下滑默认已降为 5）；total/警示缺失 → 原样返回
    assert apply_sustainability_penalty(80.0, {"eps_growth": -5.0}, None) == (76.0, 5.0)
    assert apply_sustainability_penalty(None, {"eps_growth": -5.0}, PENALTY_CFG) == (None, 0.0)
    assert apply_sustainability_penalty(80.0, None, PENALTY_CFG) == (80.0, 0.0)


# ---------------------------------------------------------------------------
# 红标判定（唯一口径来源）：四维度 profit / cashflow / coverage / debt
# ---------------------------------------------------------------------------


def test_evaluate_sustainability_returns_ordered_flags():
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "op_cash_per_share": 0.5, "cash_cover": 200.0,
         "debt_ratio_yoy": 13.0, "interest_cover": 1.4}, PENALTY_CFG
    )
    # debt 维度也只取最重一条：两条同为 10 分时取表格在前的 debt_jump
    assert [(f["key"], f["dimension"]) for f in out] == [
        ("eps_decline", "profit"), ("cash_cover", "coverage"), ("debt_jump", "debt"),
    ]
    assert out[0]["value"] == pytest.approx(-12.9)
    assert out[0]["penalty"] == 10.0
    # 只有利息保障不足时，出的是 interest_cover
    assert [f["key"] for f in evaluate_sustainability(
        {"interest_cover": 1.4}, PENALTY_CFG)] == ["interest_cover"]


def test_evaluate_sustainability_keeps_one_flag_per_dimension():
    # 连续恶化触发时单期不再叠加；现金流同理
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "eps_history": [
            {"period": "2026-06-30", "growth": -12.9},
            {"period": "2026-03-31", "growth": -3.0},
            {"period": "2025-12-31", "growth": -1.0},
            {"period": "2025-09-30", "growth": 2.0},
        ], "op_cash_per_share": -0.1}, PENALTY_CFG
    )
    keys = [f["key"] for f in out]
    assert "eps_repeated" in keys and "eps_decline" not in keys
    assert out[[f["key"] for f in out].index("eps_repeated")]["window"] == 4
    assert "negative_cash" in keys and "cash_decline" not in keys


def test_evaluate_sustainability_cash_trend_flags():
    # 单期：最新一期同季同比为负、但未构成连续恶化 → cash_decline
    single = evaluate_sustainability(
        {"cash_yoy_history": [
            {"period": "2026-06-30", "yoy": -22.0},
            {"period": "2026-03-31", "yoy": 3.0},
            {"period": "2025-12-31", "yoy": 4.0},
            {"period": "2025-09-30", "yoy": 5.0},
        ]}, PENALTY_CFG
    )
    assert [(f["key"], f["dimension"], f["value"], f["penalty"]) for f in single] == [
        ("cash_decline", "cashflow", -22.0, 5.0)
    ]

    # 连续：4 期中 3 期为负 → cash_repeated（window 一并带出，单期不再叠加）
    repeated = evaluate_sustainability(
        {"cash_yoy_history": [
            {"period": "2026-06-30", "yoy": -2.0},
            {"period": "2026-03-31", "yoy": -3.0},
            {"period": "2025-12-31", "yoy": -4.0},
            {"period": "2025-09-30", "yoy": 5.0},
        ]}, PENALTY_CFG
    )
    assert [(f["key"], f["value"], f["window"], f["penalty"]) for f in repeated] == [
        ("cash_repeated", 3, 4, 15.0)
    ]

    # 同季同比 = 0 不触发（严格小于）
    assert evaluate_sustainability(
        {"cash_yoy_history": [{"period": "2026-06-30", "yoy": 0.0}]}, PENALTY_CFG
    ) == []


def test_evaluate_sustainability_cashflow_keeps_heaviest_flag():
    # 两条同为 15 分时取「绝对水平为负」（更直接的那条），不叠加
    out = evaluate_sustainability(
        {"op_cash_per_share": -0.1, "cash_yoy_history": [
            {"period": "2026-06-30", "yoy": -2.0},
            {"period": "2026-03-31", "yoy": -3.0},
            {"period": "2025-12-31", "yoy": -4.0},
            {"period": "2025-09-30", "yoy": 5.0},
        ]}, PENALTY_CFG
    )
    assert [(f["key"], f["value"]) for f in out] == [("negative_cash", -0.1)]


def test_evaluate_sustainability_short_series_skips_repeated():
    # Review Focus 4：不足 4 期 → 不触发连续判据，单期判据仍生效
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "eps_history": [
            {"period": "2026-06-30", "growth": -12.9},
            {"period": "2026-03-31", "growth": -3.0},
        ]}, PENALTY_CFG
    )
    assert [f["key"] for f in out] == ["eps_decline"]


def test_evaluate_sustainability_boundaries():
    assert evaluate_sustainability({"eps_growth": 0.0}, PENALTY_CFG) == []
    assert evaluate_sustainability({"debt_ratio_yoy": 10.0}, PENALTY_CFG)[0]["key"] == "debt_jump"
    assert evaluate_sustainability({"interest_cover": 2.0}, PENALTY_CFG) == []
    # 银行：利息支付倍数缺失 + 负债率平稳 → 零红标
    assert evaluate_sustainability(
        {"debt_ratio_yoy": 0.3, "interest_cover": None}, PENALTY_CFG) == []


def test_evaluate_sustainability_ignores_nonpositive_interest_cover():
    # 格力型：利息净收入为正（倍数为负）属财务健康，不得误报
    assert evaluate_sustainability({"interest_cover": -860.0}, PENALTY_CFG) == []
    assert evaluate_sustainability({"interest_cover": 0.0}, PENALTY_CFG) == []


def test_evaluate_sustainability_empty_input_returns_empty():
    assert evaluate_sustainability(None, PENALTY_CFG) == []
    assert evaluate_sustainability({}, PENALTY_CFG) == []
    assert evaluate_sustainability(
        {"eps_growth": None, "op_cash_per_share": None, "cash_cover": None,
         "debt_ratio_yoy": None, "interest_cover": None,
         "eps_history": [], "cash_yoy_history": []}, PENALTY_CFG
    ) == []


def test_penalty_caps_at_four_dimension_total():
    both = {"eps_growth": -12.9, "op_cash_per_share": -0.1,
            "cash_cover": 200.0, "debt_ratio_yoy": 13.0}
    # 利润 10 + 现金流 15 + 覆盖 10 + 负债 10 = 45（未触发封顶）
    assert apply_sustainability_penalty(80.0, both, PENALTY_CFG) == (44.0, 45.0)
    # 现金流维度只取最重一条：negative_cash(15) 与 cash_repeated(15) 不叠加
    assert evaluate_sustainability(
        {"op_cash_per_share": -0.1, "cash_cover": None}, PENALTY_CFG
    )[0]["key"] == "negative_cash"
