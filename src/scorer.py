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
    "dividend_yield_percentile": _higher_better,  # 当前股息率相对自身历史的分位
    "dividend_years": _higher_better,
    "volatility": _two_sided("low", "mid"),  # 年化波动率：越低越好（稳定优先）
    "max_drawdown": _two_sided("low", "mid"),  # 近一年最大回撤：越低越好
    "dividend_frequency": _higher_better,
    "dividend_trend": _higher_better,  # 阈值 {high: 0, mid: -30}：分红下降越大分越低
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

# 分红可持续性红标的默认扣分（可在 rules.yaml 的 stocks.sustainability 覆盖）
DEFAULT_SUSTAINABILITY_PENALTY = {
    "eps_decline_penalty": 10.0,  # 净利润增长率 < 0
    "negative_cash_penalty": 15.0,  # 每股经营性现金流 ≤ 0
    "cash_cover_penalty": 10.0,  # 近 12 个月分红 ÷ 经营现金流 > 100%
    "max_penalty": 30.0,  # 合计封顶
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


def apply_sustainability_penalty(
    total: float | None, sustainability: dict | None, config: dict | None = None
) -> tuple[float | None, float]:
    """按财报红标对总分扣分；返回 ``(调整后总分, 实际扣减百分比)``。

    红标：盈利下滑（**最新报告期**净利润同比增长率 < 0，含中报/季报）、经营现金流
    为负、分红超现金流（>100%；与经营现金流为负互斥，前者优先）。扣减为总分的
    百分比并受 ``max_penalty`` 封顶。
    ``total`` 为 None 或数据缺失 → 原样返回（0 扣减）。
    """
    if total is None or not sustainability:
        return total, 0.0
    cfg = {**DEFAULT_SUSTAINABILITY_PENALTY, **(config or {})}
    penalty = 0.0
    eps_growth = sustainability.get("eps_growth")
    if eps_growth is not None and float(eps_growth) < 0:
        penalty += float(cfg["eps_decline_penalty"])
    op_cash = sustainability.get("op_cash_per_share")
    cash_cover = sustainability.get("cash_cover")
    if op_cash is not None and float(op_cash) <= 0:
        penalty += float(cfg["negative_cash_penalty"])
    elif cash_cover is not None and float(cash_cover) > 100:
        penalty += float(cfg["cash_cover_penalty"])
    penalty = min(penalty, float(cfg["max_penalty"]))
    if penalty <= 0:
        return total, 0.0
    return round(total * (1 - penalty / 100.0), 1), penalty
