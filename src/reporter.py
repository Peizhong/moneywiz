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

STOCK_HEADER = "【股票】"
FUND_HEADER = "【基金】"
EMPTY_SECTION = "(无)"
NO_SCORE_SIGNAL = "数据不足"
COLUMNS = ["排名", "代码", "名称", "总分", "信号", "亮点", "风险", "技术"]


def signal_for(
    rank: int,
    total_count: int,
    buy_top_n: int,
    avoid_bottom_n: int,
    has_score: bool,
) -> str:
    """按排名给出信号：无分 → 数据不足；前 N → 买入；末尾 N → 回避；否则观察。"""
    if not has_score:
        return NO_SCORE_SIGNAL
    if rank <= buy_top_n:
        return "买入"
    if rank > total_count - avoid_bottom_n:
        return "回避"
    return "观察"


def render_report(
    stock_results: list[dict],
    fund_results: list[dict],
    output_cfg: dict,
    elapsed_s: float,
) -> str:
    """渲染完整报告：股票表、基金表、摘要行，以换行连接。"""
    total = len(stock_results) + len(fund_results)
    no_score = sum(1 for r in (*stock_results, *fund_results) if r["total"] is None)
    summary = (
        f"扫描 {total} 只标的（股票 {len(stock_results)} / 基金 {len(fund_results)}），"
        f"数据不足 {no_score}，耗时 {elapsed_s:.1f}s"
    )
    return "\n".join(
        [
            STOCK_HEADER,
            _table(stock_results, output_cfg),
            FUND_HEADER,
            _table(fund_results, output_cfg),
            summary,
        ]
    )


def _table(results: list[dict], output_cfg: dict) -> str:
    """把一张表渲染成 tabulate 文本；无标的时给出占位符。"""
    if not results:
        return EMPTY_SECTION

    rows = []
    # 排名即表内位置，total_count 为表内标的总数；数据不足的标的排最后，
    # 其信号由 has_score 直接判定为「数据不足」，不参与买入/回避名额。
    for rank, item in enumerate(_sorted_results(results), start=1):
        total = item["total"]
        has_score = total is not None
        scores = item.get("scores") or {}
        rows.append(
            [
                rank,
                item["code"],
                item["name"],
                f"{total:.1f}" if has_score else "N/A",
                signal_for(
                    rank,
                    len(results),
                    output_cfg["buy_top_n"],
                    output_cfg["avoid_bottom_n"],
                    has_score,
                ),
                _label_text(scores, 1.0),
                _label_text(scores, 0.0),
                _tech_text(item.get("tech")),
            ]
        )
    return tabulate(rows, headers=COLUMNS, tablefmt="simple", floatfmt=".1f")


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
