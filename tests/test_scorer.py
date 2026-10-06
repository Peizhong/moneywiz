"""src.scorer 打分引擎（档位归一化与加权总分）的单元测试。

全部使用内联字典构造，不联网、不读文件、不依赖配置加载；
阈值与 config/rules.yaml 一致。
"""

import logging

import pytest

from src.scorer import normalize, score_instrument

# 与 config/rules.yaml 一致的档位阈值
YIELD_TH = {"high": 4.0, "mid": 2.0}
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
