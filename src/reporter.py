"""终端输出层（纯函数）：把打分结果渲染成股票表、基金表和摘要行。

消费 main.py 组装好的 result dict，用 tabulate 对齐输出；不联网、不读文件、
不 import akshare。

result dict 契约：
``{"code": str, "name": str, "total": float | None, "values": dict,
"scores": dict | None, "missing": list[str],
"tech": {"macd": str, "rsi": float | None} | None}``（tech 仅股票有）。
``total`` 为 None 表示该标的不可评分（数据不足）。
"""

from __future__ import annotations

from tabulate import tabulate

INDICATOR_LABELS: dict[str, str] = {
    "dividend_yield": "股息率",
    "dividend_years": "连续分红",
    "payout_ratio": "派息率",
    "pe_vs_industry": "PE估值",
    "pb_vs_industry": "PB估值",
    "ma60_position": "均线位置",
    "momentum_5d": "5日动量",
    "discount_rate": "折溢价",
    "nav_trend_20d": "净值趋势",
    "index_pe_vs_history": "指数估值",
    "dividend_frequency": "分红频率",
    "fund_size": "基金规模",
}

# 亮点/风险里的数值格式（指标单位各不相同；格式化失败时仅显示指标名）
INDICATOR_VALUE_FORMATS: dict[str, str] = {
    "dividend_yield": "{:.1f}%",
    "dividend_years": "{:.0f}年",
    "payout_ratio": "{:.0f}%",
    "pe_vs_industry": "{:+.0f}%",
    "pb_vs_industry": "{:+.0f}%",
    "ma60_position": "{:+.1f}%",
    "momentum_5d": "{:+.1f}%",
    "discount_rate": "{:+.2f}%",
    "nav_trend_20d": "{:+.1f}%",
    "index_pe_vs_history": "{:.0f}%",
    "dividend_frequency": "{:.0f}次",
    "fund_size": "{:.0f}亿",
}

# 120 日区间分位（calc_price_position）并入亮点/风险的阈值
POSITION_LOW = 30.0
POSITION_HIGH = 70.0

STOCK_HEADER = "【股票】"
FUND_HEADER = "【基金】"
EMPTY_SECTION = "(无)"
NO_SCORE_SIGNAL = "数据不足"
COLUMNS = ["排名", "代码", "名称", "总分", "覆盖", "信号", "亮点", "风险", "技术"]


def signal_for(
    rank: int,
    total_count: int,
    buy_top_n: int,
    avoid_bottom_n: int,
    has_score: bool,
) -> str:
    """按（并列）排名给出信号：无分 → 数据不足；前 N → 买入；末尾 N → 末位；否则观察。"""
    if not has_score:
        return NO_SCORE_SIGNAL
    if rank <= buy_top_n:
        return "买入"
    if rank > total_count - avoid_bottom_n:
        return "末位"
    return "观察"


def render_report(
    stock_results: list[dict],
    fund_results: list[dict],
    output_cfg: dict,
    elapsed_s: float,
    market: dict | None = None,
) -> str:
    """渲染完整报告：板块温度计（可选）、股票表、基金表、摘要行，以换行连接。"""
    all_results = (*stock_results, *fund_results)
    total = len(all_results)
    no_score = sum(1 for r in all_results if r["total"] is None)
    partial = sum(
        1
        for r in all_results
        if r["total"] is not None and (r.get("missing") or [])
    )
    summary = (
        f"扫描 {total} 只标的（股票 {len(stock_results)} / 基金 {len(fund_results)}），"
        f"数据不足 {no_score}，指标不全 {partial}，耗时 {elapsed_s:.1f}s"
    )
    blocks = []
    market_text = _market_text(market) if market else ""
    if market_text:
        blocks.append(market_text)
    blocks.extend(
        [
            STOCK_HEADER,
            _table(stock_results, output_cfg),
            FUND_HEADER,
            _table(fund_results, output_cfg),
            summary,
        ]
    )
    return "\n".join(blocks)


def _market_text(market: dict) -> str:
    """板块温度计一行：中证红利股息率、10Y 国债、利差、上证红利 PE 分位。

    缺失字段跳过；全部缺失时返回空串（调用方不显示该行）。
    """
    parts = []
    if market.get("dividend_yield") is not None:
        parts.append(f"中证红利股息率 {market['dividend_yield']:.2f}%")
    if market.get("bond_10y") is not None:
        parts.append(f"10Y国债 {market['bond_10y']:.2f}%")
    if market.get("spread") is not None:
        parts.append(f"利差 {market['spread']:+.2f}pct")
    if market.get("index_pe_position") is not None:
        parts.append(f"上证红利PE分位 {market['index_pe_position']:.0f}%")
    return f"【板块温度计】{'，'.join(parts)}" if parts else ""


def _table(results: list[dict], output_cfg: dict) -> str:
    """把一张表渲染成 tabulate 文本；无标的时给出占位符。"""
    if not results:
        return EMPTY_SECTION

    rows = []
    # 同分同名次（竞赛排名）：并列组共享名次与信号，边界同分不会被代码排序切开；
    # 数据不足的标的排最后同属一组，信号由 has_score 直接判定为「数据不足」，
    # 不参与买入/末位名额。
    items = _sorted_results(results)
    ranks = _competition_ranks(items)
    for (rank, tied_count), item in zip(ranks, items):
        total = item["total"]
        has_score = total is not None
        scores = item.get("scores") or {}
        values = item.get("values") or {}
        low_phrase, high_phrase = _position_phrases(item.get("position"))
        rank_text = f"{rank}(并列{tied_count})" if tied_count > 1 else str(rank)
        name_text = item["name"]
        if item.get("new_constituent"):
            name_text = f"{name_text}(新增)"
        rows.append(
            [
                rank_text,
                item["code"],
                name_text,
                f"{total:.1f}" if has_score else "N/A",
                _coverage_text(scores, item.get("missing")),
                signal_for(
                    rank,
                    len(results),
                    output_cfg["buy_top_n"],
                    output_cfg["avoid_bottom_n"],
                    has_score,
                ),
                _detail_text(scores, values, 1.0, low_phrase),
                _risk_text(scores, values, item.get("sustainability"), high_phrase),
                _tech_text(item.get("tech")),
            ]
        )
    return tabulate(rows, headers=COLUMNS, tablefmt="simple", floatfmt=".1f")


def _competition_ranks(items: list[dict]) -> list[tuple[int, int]]:
    """竞赛排名：同 ``total`` 同属一组，返回每条的 ``(名次, 并列数)``。

    名次为该并列组的起始位置；「数据不足」（total 为 None）的标的自成最后一组。
    """
    ranks: list[int] = []
    start = 0
    for index in range(len(items)):
        if index > 0 and items[index]["total"] != items[start]["total"]:
            start = index
        ranks.append(start + 1)
    sizes: dict[int, int] = {}
    for rank in ranks:
        sizes[rank] = sizes.get(rank, 0) + 1
    return [(rank, sizes[rank]) for rank in ranks]


def _coverage_text(scores: dict | None, missing: list[str] | None) -> str:
    """已评分/应有指标数，如 ``1/7``；``scores`` 为 None/缺席时按 0 个已评分计。"""
    scored = len(scores or {})
    return f"{scored}/{scored + len(missing or [])}"


def _sorted_results(results: list[dict]) -> list[dict]:
    """总分降序，None 最后；同分按 code 排序。"""
    return sorted(
        results,
        key=lambda r: (r["total"] is None, -(r["total"] or 0.0), r["code"]),
    )


def _label_text(scores: dict, tier: float) -> str:
    """取指定档位的指标中文名，最多 2 个，用「、」连接；无则为 ``-``。"""
    labels = [
        INDICATOR_LABELS.get(name, name)
        for name, value in scores.items()
        if value == tier
    ]
    return "、".join(labels[:2]) if labels else "-"


def _detail_text(
    scores: dict, values: dict, tier: float, extra: str | None = None
) -> str:
    """指定档位的「指标名 数值」明细（最多 2 项），可选追加一项（如位置短语）。

    数值缺失或格式未知时仅显示指标名；无任何内容则为 ``-``。
    """
    items = [
        _format_detail(name, values.get(name))
        for name, value in scores.items()
        if value == tier
    ][:2]
    if extra:
        items.append(extra)
    return "、".join(items) if items else "-"


def _format_detail(name: str, value) -> str:
    """``指标名 数值``；数值缺失或用例外的指标只显示名称。"""
    label = INDICATOR_LABELS.get(name, name)
    template = INDICATOR_VALUE_FORMATS.get(name)
    if value is None or template is None:
        return label
    try:
        return f"{label} {template.format(float(value))}"
    except (TypeError, ValueError):
        return label


def _risk_text(
    scores: dict, values: dict, sustainability: dict | None, high_phrase: str | None
) -> str:
    """风险列：可持续性警示（P1）优先，其后高位提示与零分档指标，最多 3 项。

    可持续性警示是财报硬事实（盈利下滑/现金流为负/分红超现金流），比分数档位
    更值得占用有限的展示空间；无任何内容时为 ``-``。
    """
    items = _sustainability_phrases(sustainability)
    if high_phrase:
        items.append(high_phrase)
    items.extend(
        _format_detail(name, values.get(name))
        for name, tier in scores.items()
        if tier == 0.0
    )
    return "、".join(items[:3]) if items else "-"


def _sustainability_phrases(sustainability: dict | None) -> list[str]:
    """分红可持续性警示：盈利下滑 / 经营现金流为负 / 分红超现金流。"""
    if not sustainability:
        return []
    phrases = []
    eps_growth = sustainability.get("eps_growth")
    if eps_growth is not None and float(eps_growth) < 0:
        phrases.append(f"盈利下滑 {abs(float(eps_growth)):.0f}%")
    op_cash = sustainability.get("op_cash_per_share")
    cash_cover = sustainability.get("cash_cover")
    if op_cash is not None and float(op_cash) <= 0:
        phrases.append("经营现金流为负")
    elif cash_cover is not None and float(cash_cover) > 100:
        phrases.append(f"分红超现金流 {float(cash_cover):.0f}%")
    return phrases


def _position_phrases(position) -> tuple[str | None, str | None]:
    """把 120 日区间分位转成（亮点追加, 风险追加）。

    ≤30 视为低位（亮点），≥70 视为高位（风险），中间档以中性文字并入亮点；
    ``position`` 缺失/非法 → 都不追加（如无 K 线数据的标的）。
    """
    try:
        value = float(position)
    except (TypeError, ValueError):
        return None, None
    if value <= POSITION_LOW:
        return f"120日低位 {value:.0f}%", None
    if value >= POSITION_HIGH:
        return None, f"120日高位 {value:.0f}%"
    return f"120日中位 {value:.0f}%", None


def _tech_text(tech: dict | None) -> str:
    """技术面摘要，如 ``MACD 金叉 RSI 56``；均缺失时为 ``-``。"""
    if not tech:
        return "-"
    parts = []
    if tech.get("macd"):
        parts.append(f"MACD {tech['macd']}")
    if tech.get("rsi") is not None:
        parts.append(f"RSI {tech['rsi']:g}")
    return " ".join(parts) if parts else "-"
