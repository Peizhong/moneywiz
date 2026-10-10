"""终端输出层（纯函数）：把打分结果渲染成股票表和摘要行。

消费 main.py 组装好的 result dict，用 tabulate 对齐输出；不联网、不读文件、
不 import akshare。

result dict 契约：
``{"code": str, "name": str, "total": float | None, "values": dict,
"scores": dict | None, "missing": list[str],
"tech": {"macd": str, "rsi": float | None} | None}``（tech 仅股票有）。
``"holding": {"cost": float, "pnl_pct": float | None} | None``（持仓成本与浮亏，仅持仓股有）。
``total`` 为 None 表示该标的不可评分（数据不足）。
"""

from __future__ import annotations

from tabulate import tabulate

from src.scorer import apply_sustainability_penalty, score_breakdown

INDICATOR_LABELS: dict[str, str] = {
    "dividend_yield": "股息率",
    "dividend_yield_percentile": "股息率分位",
    "dividend_years": "连续分红",
    "dividend_trend": "分红趋势",
    "payout_ratio": "派息率",
    "pe_vs_industry": "PE估值",
    "pb_vs_industry": "PB估值",
    "ma60_position": "均线位置",
    "momentum_5d": "5日动量",
    "volatility": "波动率",
    "max_drawdown": "最大回撤",
}

# 亮点/风险里的数值格式（指标单位各不相同；格式化失败时仅显示指标名）
INDICATOR_VALUE_FORMATS: dict[str, str] = {
    "dividend_yield": "{:.1f}%",
    "dividend_yield_percentile": "{:.0f}%",
    "dividend_years": "{:.0f}年",
    "dividend_trend": "{:+.0f}%",
    "payout_ratio": "{:.0f}%",
    "pe_vs_industry": "{:+.0f}%",
    "pb_vs_industry": "{:+.0f}%",
    "ma60_position": "{:+.1f}%",
    "momentum_5d": "{:+.1f}%",
    "volatility": "{:.1f}%",
    "max_drawdown": "{:.1f}%",
}

# 120 日区间分位（calc_price_position）并入亮点/风险的阈值
POSITION_LOW = 30.0
POSITION_HIGH = 70.0

# 风险提示默认阈值（rules.yaml 的 stocks.risk_hints 可覆盖）
TURNOVER_LOW_DEFAULT = 5000.0  # 近 60 日均成交额 < 该值（万元）→ 风险列提示
HOLDING_LOSS_DEFAULT = -10.0  # 持仓浮亏提示阈值（%，负数）：现价较成本亏损达到该幅度

STOCK_HEADER = "【股票】"
EMPTY_SECTION = "(无)"
NO_SCORE_SIGNAL = "数据不足"
COLUMNS = ["排名", "代码", "名称", "总分", "覆盖", "信号", "亮点", "风险", "技术"]


def _signals(
    items: list[dict],
    ranks: list[tuple[int, int]],
    total_count: int,
    buy_top_n: int,
    avoid_bottom_n: int,
) -> list[str]:
    """逐行分配信号（与 items 对齐）。

    买入：按名次顺序发给「有分且非高位」的标的，共 buy_top_n 个标签；处于
    高位（≥ ``POSITION_HIGH``，与「全收益高位」风险短语同一阈值）的标的
    不占名额，空出的名额由后续标的递补。同分组整组收发——不切开同分，递补时
    标签数可能略超 buy_top_n（与并列超发语义一致）。
    末位/数据不足的判定与位置无关；「数据不足」不占买入/末位名额。
    """
    signals: list[str] = []
    buy_granted = 0
    group_active = False  # 当前同分组的非高位成员是否处于买入档
    prev_rank: int | None = None
    for (rank, _tied_count), item in zip(ranks, items):
        if rank != prev_rank:
            prev_rank = rank
            group_active = buy_granted < buy_top_n
        if item["total"] is None:
            signals.append(NO_SCORE_SIGNAL)
            continue
        high = _position_phrases(item.get("position"))[1] is not None
        if group_active and not high:
            signals.append("买入")
            buy_granted += 1
            continue
        if rank <= buy_top_n:
            # 买入区内的非高位已在上方取走「买入」，到这里只可能是被高位剥夺的
            # 标的 → 观察。不落入末位区：表内标的少于 avoid_bottom_n 时末位区会
            # 覆盖全表（如表内仅 1 只标的、avoid=5），否则会被误标「末位」。
            signals.append("观察")
            continue
        if rank > total_count - avoid_bottom_n:
            signals.append("末位")
            continue
        signals.append("观察")
    return signals


def render_report(
    stock_results: list[dict],
    output_cfg: dict,
    elapsed_s: float,
    market: dict | None = None,
    risk_hints: dict | None = None,
    position_window: int = 120,
) -> str:
    """渲染完整报告：板块温度计（可选）、股票表、摘要行，以换行连接。

    ``position_window`` 只用于位置短语的窗口文案（来自 ``data.kline_days``），
    带默认值以保证既有调用方无需改动。
    """
    total = len(stock_results)
    no_score = sum(1 for r in stock_results if r["total"] is None)
    partial = sum(
        1
        for r in stock_results
        if r["total"] is not None and (r.get("missing") or [])
    )
    summary = (
        f"扫描 {total} 只股票，数据不足 {no_score}，指标不全 {partial}，"
        f"耗时 {elapsed_s:.1f}s"
    )
    blocks = []
    market_text = _market_text(market) if market else ""
    if market_text:
        blocks.append(market_text)
    blocks.extend(
        [
            STOCK_HEADER,
            # 股票位置由前复权 K 线算出 → 全收益口径
            _table(
                stock_results,
                output_cfg,
                risk_hints,
                position_window,
                position_basis="全收益",
            ),
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


def _table(
    results: list[dict],
    output_cfg: dict,
    risk_hints: dict | None = None,
    position_window: int = 120,
    position_basis: str = "全收益",
) -> str:
    """把一张表渲染成 tabulate 文本；无标的时给出占位符。

    ``position_window`` 与 ``position_basis`` 透传给位置短语（只影响文案）。
    口径词固定「全收益」，见 ``_position_phrases``。
    """
    if not results:
        return EMPTY_SECTION

    rows = []
    for row in ranked_rows(results, output_cfg):
        # 同分同名次（竞赛排名）：并列组共享名次与信号，边界同分不会被代码排序切开；
        # 数据不足的标的排最后同属一组，信号直接判定为「数据不足」，不参与名额。
        item, rank, tied_count, signal = (
            row["item"],
            row["rank"],
            row["tied_count"],
            row["signal"],
        )
        total = item["total"]
        has_score = total is not None
        scores = item.get("scores") or {}
        values = item.get("values") or {}
        low_phrase, high_phrase = _position_phrases(
            item.get("position"), position_window, position_basis
        )
        rank_text = _rank_text(rank, tied_count)
        name_text = item["name"]
        if item.get("holding"):
            name_text = f"{name_text}(持仓)"
        if item.get("new_constituent"):
            name_text = f"{name_text}(新增)"
        rows.append(
            [
                rank_text,
                item["code"],
                name_text,
                f"{total:.1f}" if has_score else "N/A",
                _coverage_text(scores, item.get("missing")),
                signal,
                _detail_text(scores, values, 1.0, low_phrase),
                _risk_text(
                    scores,
                    values,
                    item.get("sustainability"),
                    item.get("sustainability_flags"),
                    high_phrase,
                    _risk_hint_phrases(item, risk_hints),
                ),
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


def _rank_text(rank: int, tied_count: int) -> str:
    """名次显示：``1`` / ``1(并列6)``。"""
    return f"{rank}(并列{tied_count})" if tied_count > 1 else str(rank)


def ranked_rows(results: list[dict], output_cfg: dict) -> list[dict]:
    """排序 + 竞赛名次 + 信号：报告表格与交互式选择列表的同源结果。

    每项 ``{"item", "rank", "tied_count", "signal"}``。名次与信号含同分整组收发、
    120 日高位不出让买入名额等规则——表格与选择列表都走这里，避免各写一份。
    """
    items = _sorted_results(results)
    ranks = _competition_ranks(items)
    signals = _signals(
        items,
        ranks,
        len(results),
        output_cfg["buy_top_n"],
        output_cfg["avoid_bottom_n"],
    )
    return [
        {"item": item, "rank": rank, "tied_count": tied, "signal": signal}
        for (rank, tied), item, signal in zip(ranks, items, signals)
    ]


def row_label(row: dict) -> str:
    """选择列表的一行：``名次  代码 名称  总分  信号``。"""
    item = row["item"]
    total = f"{item['total']:.1f}" if item["total"] is not None else "N/A"
    return (
        f"{_rank_text(row['rank'], row['tied_count'])}  "
        f"{item['code']} {item['name']}  {total}  {row['signal']}"
    )


def render_detail(row: dict, rules_section: dict) -> str:
    """单只标的的详情（交互式查看用）：逐指标档位/权重/贡献 + 扣分链 + 缺失指标。

    只用 result dict 里已算好的数据，不额外取数、不新增字段。``row`` 为
    :func:`ranked_rows` 的一项（需要 item/rank/tied_count/signal）。
    """
    item = row["item"]
    breakdown = score_breakdown(item.get("values") or {}, rules_section)
    contributions = [
        entry["contribution"]
        for entry in breakdown
        if entry["contribution"] is not None
    ]
    # 加权小计 = 各贡献之和（与 score_instrument 的扣分前总分同值，构造上即一致）
    pre_total = round(sum(contributions), 1) if contributions else None
    _adjusted, penalty = apply_sustainability_penalty(
        pre_total, item.get("sustainability"), rules_section.get("sustainability")
    )
    total_text = f"{item['total']:.1f}" if item["total"] is not None else "N/A"
    header = (
        f"{item['code']} {item['name']}   总分 {total_text}   "
        f"排名 {_rank_text(row['rank'], row['tied_count'])}   信号 {row['signal']}"
    )
    holding = item.get("holding")
    if holding:
        header += f"   持仓 成本 {holding['cost']:.2f}"
        if holding["pnl_pct"] is not None:
            header += f" 盈亏 {holding['pnl_pct']:+.1f}%"
    table = tabulate(
        [
            [
                INDICATOR_LABELS.get(entry["name"], entry["name"]),
                _value_text(entry["name"], entry["value"]),
                "-" if entry["score"] is None else f"{entry['score']:.1f}",
                str(entry["weight"]),
                "-" if entry["contribution"] is None else f"{entry['contribution']:.1f}",
            ]
            for entry in breakdown
        ],
        headers=["指标", "数值", "档位", "权重", "贡献"],
        tablefmt="simple",
        colalign=("left", "right", "right", "right", "right"),
        disable_numparse=True,  # 数值已按各自单位格式化，勿再被 tabulate 重新解析（1.0→1）
    )
    if pre_total is None:
        chain = "加权小计 N/A：全部指标数据不足，无法评分"
    else:
        chain = f"加权小计 {pre_total:.1f}"
        if item.get("sustainability"):
            reason = "、".join(
                _sustainability_phrases(
                    item.get("sustainability_flags"), item["sustainability"]
                )
            )
            chain += f" → 可持续性扣分 {penalty:.0f}%" + (
                f"（{reason}）" if reason else ""
            )
        chain += f" → 总分 {total_text}"
    missing = [
        INDICATOR_LABELS.get(name, name) for name in item.get("missing") or []
    ]
    coverage = (
        f"缺失指标：{'、'.join(missing) if missing else '无'}"
        f"（覆盖 {_coverage_text(item.get('scores') or {}, item.get('missing'))}）"
    )
    return "\n".join([header, table, chain, coverage])


def _value_text(name: str, value) -> str:
    """指标数值按各自单位格式化；缺失 → ``-``（与亮点/风险列同一套格式表）。"""
    if value is None:
        return "-"
    template = INDICATOR_VALUE_FORMATS.get(name)
    try:
        return template.format(float(value)) if template else f"{float(value):g}"
    except (TypeError, ValueError):
        return "-"


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
    scores: dict,
    values: dict,
    sustainability: dict | None,
    flags: list[dict] | None,
    high_phrase: str | None,
    hint_phrases: list[str] | None = None,
) -> str:
    """风险列：红标警示优先，其后流动性提示、高位提示与零分档指标，最多 6 项。

    红标文案由 ``sustainability_flags``（``scorer.evaluate_sustainability`` 的
    输出）渲染，展示层不重判阈值；``sustainability`` 只用于取盈利明细的报告期。
    财报红标比分数档位更值得占用有限的展示空间；无任何内容时为 ``-``。
    """
    items = _sustainability_phrases(flags, sustainability)
    items.extend(hint_phrases or [])
    if high_phrase:
        items.append(high_phrase)
    items.extend(
        _format_detail(name, values.get(name))
        for name, tier in scores.items()
        if tier == 0.0
    )
    return "、".join(items[:6]) if items else "-"


def _risk_hint_phrases(item: dict, risk_hints: dict | None) -> list[str]:
    """流动性/持仓提示（只标注，不影响分数）：达到阈值才出现。

    波动率自成为打分指标后不再在此提示——0.0 档会经零分档展示进风险列，
    避免同一列出现两个口径的波动率数字。持仓浮亏紧随流动性提示之后。
    """
    hints = risk_hints or {}
    turnover_low = float(hints.get("turnover_low", TURNOVER_LOW_DEFAULT))
    holding_loss = float(hints.get("holding_loss", HOLDING_LOSS_DEFAULT))
    phrases = []
    turnover = item.get("turnover_wan")
    if turnover is not None and float(turnover) < turnover_low:
        phrases.append(f"日均成交 {_amount_text(float(turnover))}")
    holding = item.get("holding")
    pnl = holding.get("pnl_pct") if holding else None
    if pnl is not None and pnl <= holding_loss:
        phrases.append(f"持仓浮亏 {abs(pnl):.0f}%")
    return phrases


def _amount_text(wan: float) -> str:
    """万元金额的紧凑显示：不足 1 亿显示 ``X万``，否则 ``X.X亿``。"""
    return f"{wan:.0f}万" if wan < 10000 else f"{wan / 10000:.1f}亿"


_PERIOD_LABELS = {"03": "一季报", "06": "中报", "09": "三季报", "12": "年报"}


def _report_period_label(period) -> str | None:
    """报告期（``YYYY-MM-DD``）→「2026中报」样式；无法识别 → None。"""
    if not isinstance(period, str):
        return None
    parts = period.split("-")
    if len(parts) == 3 and parts[1] in _PERIOD_LABELS:
        return f"{parts[0]}{_PERIOD_LABELS[parts[1]]}"
    return None


def _sustainability_phrases(
    flags: list[dict] | None, sustainability: dict | None = None
) -> list[str]:
    """红标明细（``sustainability_flags``）→ 中文短语，保持 flags 的维度顺序。

    **只做 key → 文案的映射，不判断任何阈值**——阈值口径的唯一来源是
    :func:`scorer.evaluate_sustainability`，展示层重判一遍就是口径漂移。
    ``eps_decline`` 的报告期不在 flag 契约里（flag 只有 key/dimension/value/
    penalty），故从 ``sustainability["eps_period"]`` 取并标注（如「2026中报」），
    无法识别时不加括号；``eps_repeated``/``cash_repeated`` 的期数取 ``window``。
    未知 key 跳过（上游新增判据时不至于让报表崩掉）。
    """
    period = _report_period_label((sustainability or {}).get("eps_period"))
    suffix = f"（{period}）" if period else ""
    phrases = []
    for flag in flags or []:
        key = flag.get("key")
        value = flag.get("value")
        if key == "eps_decline":
            phrases.append(f"盈利下滑 {abs(float(value)):.0f}%{suffix}")
        elif key == "eps_repeated":
            phrases.append(f"盈利连续下滑 {value}/{flag.get('window')} 期")
        elif key == "negative_cash":
            phrases.append("经营现金流为负")
        elif key == "cash_decline":
            phrases.append(f"现金流下滑 {abs(float(value)):.0f}%")
        elif key == "cash_repeated":
            phrases.append(f"现金流连续下滑 {value}/{flag.get('window')} 期")
        elif key == "cash_cover":
            phrases.append(f"分红超现金流 {float(value):.0f}%")
        elif key == "debt_jump":
            phrases.append(f"负债率上升 {float(value):.0f}pp")
        elif key == "interest_cover":
            phrases.append(f"利息保障 {float(value):.1f} 倍")
    return phrases


def _position_phrases(
    position, window: int = 120, basis: str = "全收益"
) -> tuple[str | None, str | None]:
    """把区间分位转成（亮点追加, 风险追加）。

    ≤30 视为低位（亮点），≥70 视为高位（风险），中间档以中性文字并入亮点；
    ``position`` 无法转成数值 → 都不追加（如无 K 线数据的标的）。

    ``window`` 只影响文案里的窗口数（来自 ``data.kline_days``），不参与判定；
    非法值（非 int、``bool``、≤0）兜底为 120——排除 ``bool`` 是因为 ``True``
    也是 ``int`` 的实例，不排除会静默渲染成「1日」。

    ``basis`` 是口径词，固定「全收益」：``position`` 由**前复权** K 线算出，除息
    不是损失（钱以分红形式拿到），且与最大回撤的含分红总回报口径一致。这与波动率、
    最大回撤、股息率历史分位所用的**不复权**帧不同——四处取数分工是有意的，不是
    待统一的疏漏，理由见 plan 的「关键口径决策」第 5 条。
    """
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        window = 120
    try:
        value = float(position)
    except (TypeError, ValueError):
        return None, None
    if value <= POSITION_LOW:
        return f"{window}日{basis}低位 {value:.0f}%", None
    if value >= POSITION_HIGH:
        return None, f"{window}日{basis}高位 {value:.0f}%"
    return f"{window}日{basis}中位 {value:.0f}%", None


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
