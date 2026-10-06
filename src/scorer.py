"""加权打分引擎（纯函数）。

把指标值按 thresholds 归一化为 1.0/0.5/0.0 三档，再按权重合成百分制总分；
不联网、不读文件、不依赖配置加载。

``None`` 表示数据缺失（不可评分），绝不按 0 分计入；未知指标名告警后按缺失处理。
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

Tier = Callable[[float, dict], float]


def _higher_better(value: float, thresholds: dict) -> float:
    """越高越好：``v>=high→1.0; v>=mid→0.5; else 0.0``。"""
    if value >= thresholds["high"]:
        return 1.0
    if value >= thresholds["mid"]:
        return 0.5
    return 0.0


def _two_sided(good_key: str, bad_key: str) -> Tier:
    """构造「越低越好」档位函数：``v<=good→1.0; v<bad→0.5; else 0.0``。

    用于 PE/PB 相对行业的折价（discount/premium）、场内折价率、
    净值回撤（pullback/rally）、动量超卖（oversold/overbought）、
    指数 PE 历史分位（low/high）。
    """

    def tier(value: float, thresholds: dict) -> float:
        if value <= thresholds[good_key]:
            return 1.0
        if value < thresholds[bad_key]:
            return 0.5
        return 0.0

    return tier


def _range(value: float, thresholds: dict) -> float:
    """区间内满分：``min<=v<=max→1.0; else 0.0``。"""
    if thresholds["min"] <= value <= thresholds["max"]:
        return 1.0
    return 0.0


def _sweet_band(value: float, thresholds: dict) -> float:
    """甜区满分：``sweet_low<=v<=sweet_high→1.0; |v|<=max_deviation→0.5; else 0.0``。"""
    if thresholds["sweet_low"] <= value <= thresholds["sweet_high"]:
        return 1.0
    if abs(value) <= thresholds["max_deviation"]:
        return 0.5
    return 0.0


def _at_least_min(value: float, thresholds: dict) -> float:
    """下限满分：``v>=min→1.0; else 0.0``。"""
    if value >= thresholds["min"]:
        return 1.0
    return 0.0


_TIERS: dict[str, Tier] = {
    "dividend_yield": _higher_better,
    "dividend_years": _higher_better,
    "dividend_frequency": _higher_better,
    "payout_ratio": _range,
    "pe_vs_industry": _two_sided("discount", "premium"),
    "pb_vs_industry": _two_sided("discount", "premium"),
    "discount_rate": _two_sided("discount", "premium"),
    "nav_trend_20d": _two_sided("pullback", "rally"),
    "momentum_5d": _two_sided("oversold", "overbought"),
    "ma60_position": _sweet_band,
    "index_pe_vs_history": _two_sided("low", "high"),
    "fund_size": _at_least_min,
}


def normalize(indicator: str, value: float | None, thresholds: dict) -> float | None:
    """把单个指标值归一化为 1.0/0.5/0.0。

    ``value`` 为 None（数据缺失）或指标名不在档位表内（告警后按缺失处理）
    时返回 None，绝不返回 0.0，也不抛异常。
    """
    tier = _TIERS.get(indicator)
    if tier is None:
        logger.warning("未知指标 %r，按不可评分处理", indicator)
        return None
    if value is None:
        return None
    return tier(value, thresholds)


def score_instrument(
    values: dict[str, float | None], rules_section: dict
) -> dict:
    """按 rules_section 对一只标的打分。

    rules_section 结构：``{"indicators": {name: {"weight": int, "thresholds": dict}}}``。

    返回 ``{"scores": {指标: 归一化分}, "total": float | None, "missing": [不可评分指标]}``；
    总分 = Σ(分×权重) / Σ(已评分权重) × 100（保留 1 位小数），
    无任何可评分指标时 total 为 None。
    """
    scores: dict[str, float] = {}
    missing: list[str] = []
    weighted = 0.0
    scored_weight = 0
    for name, spec in rules_section["indicators"].items():
        score = normalize(name, values.get(name), spec["thresholds"])
        if score is None:
            missing.append(name)
            continue
        scores[name] = score
        weighted += score * spec["weight"]
        scored_weight += spec["weight"]

    total = round(weighted / scored_weight * 100, 1) if scored_weight else None
    return {"scores": scores, "total": total, "missing": missing}
