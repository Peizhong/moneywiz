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

    用于 PE/PB 相对行业的折价（discount/premium）与动量超卖（oversold/overbought）。
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


_TIERS: dict[str, Tier] = {
    "dividend_yield": _higher_better,
    "dividend_yield_percentile": _higher_better,  # 当前股息率相对自身历史的分位
    "dividend_years": _higher_better,
    "volatility": _two_sided("low", "mid"),  # 年化波动率：越低越好（稳定优先）
    "max_drawdown": _two_sided("low", "mid"),  # 近一年最大回撤：越低越好
    "dividend_trend": _higher_better,  # 阈值 {high: 0, mid: -30}：分红下降越大分越低
    "payout_ratio": _range,
    "pe_vs_industry": _two_sided("discount", "premium"),
    "pb_vs_industry": _two_sided("discount", "premium"),
    "momentum_5d": _two_sided("oversold", "overbought"),
    "ma60_position": _sweet_band,
}

# 分红可持续性红标的默认扣分（可在 rules.yaml 的 stocks.sustainability 覆盖）
DEFAULT_SUSTAINABILITY_PENALTY = {
    "eps_decline_penalty": 5.0,  # 最新报告期净利润同比下滑（单期）
    "eps_repeated_penalty": 15.0,  # 近 repeated_periods 期中 ≥repeated_min_negative 期为负
    "repeated_periods": 4,  # 连续恶化判定窗口（利润与现金流共用）
    "repeated_min_negative": 3,  # 窗口内至少几期为负才算连续恶化
    "negative_cash_penalty": 15.0,  # 最近年报每股经营性现金流 ≤ 0
    "cash_cover_penalty": 10.0,  # 近 12 个月分红 ÷ 经营现金流 > 100%
    "cash_decline_penalty": 5.0,  # 每股经营性现金流同季同比下滑（单期）
    "cash_repeated_penalty": 15.0,  # 每股经营性现金流同季同比连续恶化
    "debt_jump_penalty": 10.0,  # 资产负债率同季同比上升 ≥ debt_jump_threshold
    "debt_jump_threshold": 10.0,  # 单位 pp（含等号）
    "interest_cover_penalty": 10.0,  # 利息支付倍数为正且 < interest_cover_threshold
    "interest_cover_threshold": 2.0,
    "max_penalty": 45.0,  # 合计封顶
}

# 红标维度与 flags 的输出顺序。每个维度取最重一条：维度内「标的是同一件事」的判据
# 互斥（连续恶化 vs 单期下滑、杠杆跳升 vs 利息保障不足），不重复计入。
_DIMENSIONS = ("profit", "cashflow", "coverage", "debt")


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


def _tier_rows(
    values: dict[str, float | None], rules_section: dict
) -> list[dict]:
    """逐指标求档位分（保持 rules 里的顺序）：``{name, value, score, weight}``。

    只调用一次 ``normalize``（未知指标只告警一次），供总分与明细共用。
    """
    return [
        {
            "name": name,
            "value": values.get(name),
            "score": normalize(name, values.get(name), spec["thresholds"]),
            "weight": spec["weight"],
        }
        for name, spec in rules_section["indicators"].items()
    ]


def score_instrument(
    values: dict[str, float | None], rules_section: dict
) -> dict:
    """按 rules_section 对一只标的打分。

    rules_section 结构：``{"indicators": {name: {"weight": int, "thresholds": dict}}}``。

    返回 ``{"scores": {指标: 归一化分}, "total": float | None, "missing": [不可评分指标]}``；
    总分 = Σ(分×权重) / Σ(已评分权重) × 100（保留 1 位小数），
    无任何可评分指标时 total 为 None。
    """
    rows = _tier_rows(values, rules_section)
    scored = [row for row in rows if row["score"] is not None]
    scored_weight = sum(row["weight"] for row in scored)
    total = (
        round(sum(row["score"] * row["weight"] for row in scored) / scored_weight * 100, 1)
        if scored_weight
        else None
    )
    return {
        "scores": {row["name"]: row["score"] for row in scored},
        "total": total,
        "missing": [row["name"] for row in rows if row["score"] is None],
    }


def score_breakdown(
    values: dict[str, float | None], rules_section: dict
) -> list[dict]:
    """逐指标明细（交互式详情用）：档位分、权重与对总分的贡献。

    每行 ``{"name", "value", "score", "weight", "contribution"}``；不可评分的指标
    仍是单独一行（``score``/``contribution`` 为 None，便于展示"缺了什么"），且不进入
    贡献的归一化分母。``contribution = score × weight / Σ(已评分权重) × 100``，
    **不取整**（展示层再格式化），因此各行 contribution 之和 = ``score_instrument``
    的扣分前总分。
    """
    rows = _tier_rows(values, rules_section)
    scored_weight = sum(row["weight"] for row in rows if row["score"] is not None)
    for row in rows:
        score = row["score"]
        row["contribution"] = (
            score * row["weight"] / scored_weight * 100
            if score is not None and scored_weight
            else None
        )
    return rows


def _flag(
    key: str, dimension: str, value: float | int, penalty: float, window: int | None = None
) -> dict:
    """一条红标明细；``window`` 仅 ``*_repeated`` 判据携带。"""
    flag = {
        "key": key,
        "dimension": dimension,
        "value": value,
        "penalty": float(penalty),
    }
    if window is not None:
        flag["window"] = int(window)
    return flag


def _repeated(series: list[dict] | None, key: str, cfg: dict) -> int | None:
    """前 ``repeated_periods`` 条中 < 0 的条数；序列不足 window 条 → None。

    数据不足（次新股报告期不够）不等于「未恶化」，故不给出条数，
    由调用方降级为单期判据。
    """
    window = int(cfg["repeated_periods"])
    if series is None or len(series) < window:
        return None
    negative = sum(1 for item in series[:window] if item[key] < 0)
    return negative if negative >= int(cfg["repeated_min_negative"]) else None


def _latest(series: list[dict] | None, key: str) -> float | None:
    """多期序列里最新一期（首条）的数值；无序列 → None。"""
    return series[0][key] if series else None


def _heaviest(candidates: list[dict]) -> list[dict]:
    """同一维度内只留最重一条（``max`` 取首个最大者）。

    平局时保留构造顺序靠前的那条，即更直接/更严重的判据优先
    （如「现金流绝对为负」先于「现金流连续恶化」、「杠杆跳升」先于「利息保障不足」）。
    """
    return [max(candidates, key=lambda flag: flag["penalty"])] if candidates else []


def _profit_flags(sustainability: dict, cfg: dict) -> list[dict]:
    """利润维度：连续恶化与单期下滑标的是同一件事 → 取最重一条。"""
    candidates = []
    repeated = _repeated(sustainability.get("eps_history"), "growth", cfg)
    if repeated is not None:
        candidates.append(
            _flag(
                "eps_repeated", "profit", repeated,
                cfg["eps_repeated_penalty"], window=cfg["repeated_periods"],
            )
        )
    eps_growth = sustainability.get("eps_growth")
    if eps_growth is not None and float(eps_growth) < 0:
        candidates.append(
            _flag("eps_decline", "profit", float(eps_growth), cfg["eps_decline_penalty"])
        )
    return _heaviest(candidates)


def _cash_trend_is_current(sustainability: dict) -> bool:
    """现金流趋势窗口是否锚在**最新报告期**（决策 11 的延伸）。

    数据层会把「去年同季基期缺失或 ≤ 0」的期整条剔除（从负值算变化率没有意义），
    最近几期都被剔除时，``cash_yoy_history`` 的前 4 条会整体前移到很旧的报告期
    （实测 601166 兴业银行停在 2024Q1–Q4）。窗口停在两年前 = 没有当期趋势数据
    = 数据不足，不得按「近 N 期」判定：否则用旧数据扣分，且文案读起来像当期。
    缺 ``latest_period``（旧缓存、手改数据）同样保守降级。
    """
    history = sustainability.get("cash_yoy_history")
    latest_period = sustainability.get("latest_period")
    return (
        bool(history)
        and isinstance(latest_period, str)
        and history[0].get("period") == latest_period
    )


def _cashflow_flags(sustainability: dict, cfg: dict) -> list[dict]:
    """现金流维度：绝对水平为负、连续恶化、单期下滑同指现金流走弱 → 取最重一条。

    绝对水平的口径固定为最近年报（半年现金流与 TTM 分红不可比），趋势判定
    改用同季同比（每股经营性现金流是累计 YTD 值，相邻期不可直接比较）；趋势判据
    另要求窗口锚在最新报告期（:func:`_cash_trend_is_current`），绝对水平不受影响。
    """
    candidates = []
    op_cash = sustainability.get("op_cash_per_share")
    if op_cash is not None and float(op_cash) <= 0:
        candidates.append(
            _flag("negative_cash", "cashflow", float(op_cash), cfg["negative_cash_penalty"])
        )
    history = sustainability.get("cash_yoy_history")
    trend_current = _cash_trend_is_current(sustainability)
    repeated = _repeated(history, "yoy", cfg) if trend_current else None
    if repeated is not None:
        candidates.append(
            _flag(
                "cash_repeated", "cashflow", repeated,
                cfg["cash_repeated_penalty"], window=cfg["repeated_periods"],
            )
        )
    latest = _latest(history, "yoy") if trend_current else None
    if latest is not None and float(latest) < 0:
        candidates.append(
            _flag("cash_decline", "cashflow", float(latest), cfg["cash_decline_penalty"])
        )
    return _heaviest(candidates)


def _coverage_flags(sustainability: dict, cfg: dict) -> list[dict]:
    """分红覆盖维度：近 12 个月分红超过经营现金流（>100%）。"""
    cash_cover = sustainability.get("cash_cover")
    if cash_cover is None or float(cash_cover) <= 100:
        return []
    return [_flag("cash_cover", "coverage", float(cash_cover), cfg["cash_cover_penalty"])]


def _debt_flags(sustainability: dict, cfg: dict) -> list[dict]:
    """负债维度：杠杆跳升与利息保障不足高度相关（加杠杆往往同时压低保障）→ 取最重一条。

    只看**变化型**判据，不用资产负债率的绝对水平——绝对阈值会被银行
    （实测 90%+）永久误报；银行「利息支付倍数」恒缺失，利息保障判据因此天然
    跳过银行，无需行业分类。
    """
    candidates = []
    debt_ratio_yoy = sustainability.get("debt_ratio_yoy")
    if debt_ratio_yoy is not None and float(debt_ratio_yoy) >= float(
        cfg["debt_jump_threshold"]
    ):
        candidates.append(
            _flag("debt_jump", "debt", float(debt_ratio_yoy), cfg["debt_jump_penalty"])
        )
    interest_cover = sustainability.get("interest_cover")
    # 必须为正：利息净收入为正（倍数为负，如格力 -860）属财务健康，不得误报
    if (
        interest_cover is not None
        and float(interest_cover) > 0
        and float(interest_cover) < float(cfg["interest_cover_threshold"])
    ):
        candidates.append(
            _flag("interest_cover", "debt", float(interest_cover), cfg["interest_cover_penalty"])
        )
    return _heaviest(candidates)


_DIMENSION_FLAGS: dict[str, Callable[[dict, dict], list[dict]]] = {
    "profit": _profit_flags,
    "cashflow": _cashflow_flags,
    "coverage": _coverage_flags,
    "debt": _debt_flags,
}


def evaluate_sustainability(
    sustainability: dict | None, config: dict | None = None
) -> list[dict]:
    """财报红标的**唯一**口径来源：返回按维度顺序排列的触发明细。

    四个维度 ``profit`` / ``cashflow`` / ``coverage`` / ``debt`` 顺序固定；每条为
    ``{"key", "dimension", "value", "penalty"}``，``*_repeated`` 另带 ``"window"``。
    **每个维度只保留最重一条**——维度内各判据标的是同一件事（连续恶化 vs 单期下滑、
    杠杆跳升 vs 利息保障不足），不重复计入；平局取构造顺序靠前者。

    边界：``growth < 0`` / ``yoy < 0`` 为严格小于；``debt_ratio_yoy ≥`` 阈值含等号；
    ``interest_cover`` 必须为正且严格小于阈值。任一判据数据缺失 → 该判据不触发，
    其余照常评估；``sustainability`` 为假值或数据全缺 → ``[]``（不可评分，而非健康）。
    现金流的两条**趋势**判据（``cash_repeated``/``cash_decline``）另要求窗口锚在
    最新报告期（``cash_yoy_history[0]["period"] == latest_period``）：同季配对被剔除
    会让窗口整体前移到很旧的期，那等于没有当期趋势数据，按数据不足降级
    （决策 11 的延伸）；利润侧无此门禁（``eps_history`` 无基期依赖，且「最新期留空
    时向前回退」是既有语义）。
    """
    if not sustainability:
        return []
    cfg = {**DEFAULT_SUSTAINABILITY_PENALTY, **(config or {})}
    return [
        flag
        for dimension in _DIMENSIONS
        for flag in _DIMENSION_FLAGS[dimension](sustainability, cfg)
    ]


def apply_sustainability_penalty(
    total: float | None, sustainability: dict | None, config: dict | None = None
) -> tuple[float | None, float]:
    """按财报红标对总分扣分；返回 ``(调整后总分, 实际扣减百分比)``。

    红标明细一律由 ``evaluate_sustainability`` 判定（唯一口径来源），扣减为四个维度
    各自最重判据之和（每个维度最多一条），并按 ``max_penalty`` 封顶。
    ``total`` 为 None 或数据缺失 → 原样返回（0 扣减）。
    """
    if total is None or not sustainability:
        return total, 0.0
    cfg = {**DEFAULT_SUSTAINABILITY_PENALTY, **(config or {})}
    penalty = min(
        sum(
            float(flag["penalty"])
            for flag in evaluate_sustainability(sustainability, cfg)
        ),
        float(cfg["max_penalty"]),
    )
    if penalty <= 0:
        return total, 0.0
    return round(total * (1 - penalty / 100.0), 1), penalty
